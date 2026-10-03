"""NapCat pairing, secure enrollment, supervision and existing gateway integration."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from chatwaifu_protocol.channels import (
    ChannelConnectionConfiguration,
    ChannelConnectionSnapshot,
    ChannelConnectionStatus,
    ChannelDeliveryPartsCancelRequest,
    ChannelPairingSnapshot,
    ChannelPairingStartRequest,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.credentials import ChannelCredentialStore
from chatwaifu_runtime.external_channels.models import ChannelDeliveryPlanRecord
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.scheduler import ChannelDeliveryScheduler
from chatwaifu_runtime.external_channels.service import ExternalChannelError, ExternalChannelService

from .client import NapCatClient, NapCatError, validate_endpoint
from .delivery import NapCatDelivery
from .messages import normalize
from .registration import PROVIDER_ID

logger = logging.getLogger(__name__)


def credential_reference(connection_id: UUID) -> str:
    return f"qq_napcat:{connection_id}"


class NapCatManagement:
    def __init__(
        self,
        gateway: ExternalChannelService,
        repository: ExternalChannelRepository,
        credentials: ChannelCredentialStore,
        characters: CharacterService,
        publisher: EventPublisher,
        hub: EventHub,
        audio_root: Path,
        on_plan_terminal: Callable[[ChannelDeliveryPlanRecord], Awaitable[None]],
        client_factory: Callable[[str, str], NapCatClient] = NapCatClient,
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._credentials = credentials
        self._characters = characters
        self._publisher = publisher
        self._hub = hub
        self._audio_root = audio_root
        self._on_terminal = on_plan_terminal
        self._factory = client_factory
        self._pairings: dict[UUID, ChannelPairingSnapshot] = {}
        self._pair_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._schedulers: dict[UUID, ChannelDeliveryScheduler] = {}
        self._clients: dict[UUID, NapCatClient] = {}
        self._changed = asyncio.Condition()
        self._lock = asyncio.Lock()
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        for connection in await self._gateway.list_connections():
            if (
                connection.configuration.provider_id == PROVIDER_ID
                and connection.configuration.enabled
            ):
                self._start_connection(connection.configuration.connection_id)

    async def stop(self) -> None:
        self._stopping = True
        tasks = set(self._pair_tasks.values()) | set(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._pair_tasks.clear()

    async def begin_pairing(self, request: ChannelPairingStartRequest) -> ChannelPairingSnapshot:
        validate_endpoint(request.endpoint)
        if self._characters.get(request.character_id) is None:
            raise ValueError("Unknown character")
        if not await self._credentials.available():
            raise ValueError("安全凭据存储不可用，请先配置 Runtime 凭据存储。")
        async with self._lock:
            if any(item.status == "pending" for item in self._pairings.values()):
                raise ValueError("已有 QQ 配对正在进行。")
            # Bounded cache; completed connections live in the repository.
            self._pairings = dict(list(self._pairings.items())[-7:])
            snapshot = ChannelPairingSnapshot(
                pairing_id=uuid4(),
                status="pending",
                pairing_code=secrets.token_hex(6).upper(),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            self._pairings[snapshot.pairing_id] = snapshot
            task = asyncio.create_task(self._pair(request, snapshot.pairing_id), name="qq-pairing")
            self._pair_tasks[snapshot.pairing_id] = task
            return snapshot

    async def pairing(self, pairing_id: UUID, wait_seconds: float = 0) -> ChannelPairingSnapshot:
        async with self._changed:
            if pairing_id not in self._pairings:
                raise KeyError("QQ pairing not found")
            initial = self._pairings[pairing_id]
            if initial.status == "pending" and wait_seconds:
                try:
                    async with asyncio.timeout(min(wait_seconds, 25)):
                        await self._changed.wait_for(lambda: self._pairings[pairing_id] != initial)
                except TimeoutError:
                    pass
            return self._pairings[pairing_id]

    async def cancel_pairing(self, pairing_id: UUID) -> None:
        item = self._pairings.get(pairing_id)
        if item is None:
            raise KeyError("QQ pairing not found")
        if item.status != "pending":
            return
        task = self._pair_tasks.get(pairing_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _update_pairing(self, pairing_id: UUID, **changes: object) -> None:
        async with self._changed:
            self._pairings[pairing_id] = self._pairings[pairing_id].model_copy(update=changes)
            self._changed.notify_all()

    async def _pair(self, request: ChannelPairingStartRequest, pairing_id: UUID) -> None:
        client = self._factory(request.endpoint, request.access_token.get_secret_value())
        connection_id = uuid4()
        enrolled = False
        try:
            async with asyncio.timeout(300):
                await client.open()
                account = await self._account(client)
                for current in await self._gateway.list_connections():
                    if (
                        current.configuration.provider_id == PROVIDER_ID
                        and current.configuration.account_key == account
                    ):
                        raise ValueError("此 QQ 账号已有连接，请先解除原连接。")
                await self._update_pairing(pairing_id, account_label=account)
                while True:
                    event = await client.event()
                    message = normalize(
                        event, connection_id=connection_id, account=account, owner=None
                    )
                    code = self._pairings[pairing_id].pairing_code
                    if (
                        message is None
                        or code is None
                        or not hmac.compare_digest(message.text.encode(), f"CW2 {code}".encode())
                    ):
                        continue
                    access_token = secrets.token_urlsafe(32)
                    configuration = ChannelConnectionConfiguration(
                        connection_id=connection_id,
                        provider_id=PROVIDER_ID,
                        name="我的 QQ",
                        character_id=request.character_id,
                        principal_scope="local",
                        account_key=account,
                        allowed_sender_keys=[message.sender_key],
                    )
                    # Shield enrollment so cancellation cannot split secure storage and DB.
                    enrollment = asyncio.create_task(
                        self._enroll(request, configuration, access_token, pairing_id)
                    )
                    try:
                        await asyncio.shield(enrollment)
                    except asyncio.CancelledError:
                        await asyncio.gather(enrollment, return_exceptions=True)
                        if await self._repository.get_connection(connection_id) is not None:
                            await self._gateway.delete_connection(connection_id)
                        await self._update_pairing(
                            pairing_id, status="cancelled", pairing_code=None, connection=None
                        )
                        raise
                    enrolled = True
                    break
            # Close pairing socket before starting the one supervised connection.
            await client.close()
            self._start_connection(connection_id)
        except asyncio.CancelledError:
            if not enrolled:
                await self._update_pairing(pairing_id, status="cancelled", pairing_code=None)
            raise
        except TimeoutError:
            await self._update_pairing(pairing_id, status="expired", pairing_code=None)
        except Exception:
            await self._update_pairing(
                pairing_id,
                status="failed",
                pairing_code=None,
                error=StructuredError(
                    code="qq_pairing_failed",
                    retryable=True,
                    message="QQ 配对失败，请检查地址、Token 和账号登录状态。",
                    component="external_channels.qq",
                ),
            )
        finally:
            await client.close()
            if not enrolled:
                await self._credentials.delete(credential_reference(connection_id))
            self._pair_tasks.pop(pairing_id, None)

    async def _enroll(
        self,
        request: ChannelPairingStartRequest,
        configuration: ChannelConnectionConfiguration,
        access_token: str,
        pairing_id: UUID,
    ) -> None:
        connection_id = configuration.connection_id
        await self._credentials.set(
            credential_reference(connection_id),
            json.dumps(
                {
                    "endpoint": request.endpoint,
                    "token": request.access_token.get_secret_value(),
                    "gateway_token": access_token,
                }
            ),
        )
        await self._gateway.create_connection(configuration, access_token=access_token)
        snapshot = await self._gateway.get_connection(connection_id)
        await self._update_pairing(
            pairing_id, status="confirmed", pairing_code=None, connection=snapshot
        )

    @staticmethod
    async def _account(client: NapCatClient) -> str:
        info = await client.call("get_login_info", {})
        value = info.get("user_id")
        if (
            type(value) not in {int, str}
            or not re.fullmatch(r"[0-9]{1,20}", str(value))
            or int(str(value)) <= 0
        ):
            raise NapCatError("QQ login has no stable account")
        return str(value)

    def _start_connection(self, connection_id: UUID) -> None:
        if not self._stopping and (
            connection_id not in self._tasks or self._tasks[connection_id].done()
        ):
            self._tasks[connection_id] = asyncio.create_task(
                self._run(connection_id), name=f"qq-connection-{connection_id}"
            )

    async def _run(self, connection_id: UUID) -> None:
        retry = 0
        while not self._stopping:
            client: NapCatClient | None = None
            scheduler: ChannelDeliveryScheduler | None = None
            try:
                connection = await self._gateway.get_connection(connection_id)
                config = connection.configuration
                if not config.enabled:
                    return
                raw = await self._credentials.get(credential_reference(connection_id))
                if not raw:
                    await self._health(
                        connection_id, ChannelConnectionStatus.ERROR, "qq_credentials_missing"
                    )
                    return
                private: dict[str, str] = json.loads(raw)
                client = self._factory(private["endpoint"], private["token"])
                await client.open()
                account = await self._account(client)
                if account != config.account_key:
                    await self._health(
                        connection_id, ChannelConnectionStatus.ERROR, "qq_account_changed"
                    )
                    return
                await self._health(connection_id, ChannelConnectionStatus.READY)
                client.bind_account(account)
                self._clients[connection_id] = client
                retry = 0
                scheduler = ChannelDeliveryScheduler(
                    self._repository,
                    NapCatDelivery(
                        self._repository,
                        client,
                        connection_id,
                        config.allowed_sender_keys[0],
                        self._audio_root,
                    ),
                    self._publisher,
                    event_hub=self._hub,
                    connection_id=connection_id,
                    on_plan_terminal=self._on_terminal,
                )
                self._schedulers[connection_id] = scheduler
                await scheduler.start()
                while True:
                    # Periodically verify the login even when no messages arrive.
                    try:
                        async with asyncio.timeout(30):
                            event = await client.event()
                    except TimeoutError:
                        if await self._account(client) != account:
                            raise NapCatError("QQ account changed") from None
                        await self._health(connection_id, ChannelConnectionStatus.READY)
                        continue
                    message = normalize(
                        event,
                        connection_id=connection_id,
                        account=account,
                        owner=config.allowed_sender_keys[0],
                    )
                    if message is None:
                        continue
                    try:
                        await self._gateway.ingest(
                            message, access_token=private["gateway_token"], supersede_inflight=True
                        )
                    except ExternalChannelError as error:
                        logger.info(
                            "qq ingress rejected connection=%s code=%s", connection_id, error.code
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._health(
                    connection_id, ChannelConnectionStatus.DEGRADED, "qq_connection_lost"
                )
            finally:
                if scheduler:
                    await scheduler.stop()
                self._schedulers.pop(connection_id, None)
                self._clients.pop(connection_id, None)
                if client:
                    await client.close()
            retry += 1
            # Cancellable bounded reconnect backoff; not used for correctness synchronization.
            await asyncio.sleep(min(30, 2 ** min(retry, 5)))

    async def _health(
        self, connection_id: UUID, status: ChannelConnectionStatus, code: str | None = None
    ) -> None:
        error = (
            StructuredError(
                code=code,
                message="请检查 QQ 登录和 NapCat 连接。",
                component="external_channels.qq",
                retryable=status == ChannelConnectionStatus.DEGRADED,
            )
            if code
            else None
        )
        await self._repository.set_connection_status(
            connection_id, status=status, last_error=error, updated_at=datetime.now(UTC)
        )
        if status == ChannelConnectionStatus.READY:
            await self._repository.touch_connection(
                connection_id, status=status, seen_at=datetime.now(UTC)
            )

    async def configuration_changed(self, connection: ChannelConnectionSnapshot) -> None:
        connection_id = connection.configuration.connection_id
        await self._stop_connection(
            connection_id, cancel_pending=not connection.configuration.enabled
        )
        if connection.configuration.enabled:
            self._start_connection(connection_id)

    async def test_connection(self, connection_id: UUID) -> ChannelConnectionSnapshot:
        snapshot = await self._gateway.get_connection(connection_id)
        if not snapshot.configuration.enabled:
            return snapshot
        client = self._clients.get(connection_id)
        if client is None:
            await self._health(
                connection_id, ChannelConnectionStatus.DEGRADED, "qq_connection_lost"
            )
            self._start_connection(connection_id)
        else:
            try:
                account = await self._account(client)
                if account != snapshot.configuration.account_key:
                    await self._health(
                        connection_id, ChannelConnectionStatus.ERROR, "qq_account_changed"
                    )
                else:
                    await self._health(connection_id, ChannelConnectionStatus.READY)
            except NapCatError:
                await self._health(
                    connection_id, ChannelConnectionStatus.DEGRADED, "qq_connection_lost"
                )
        return await self._gateway.get_connection(connection_id)

    async def _stop_connection(self, connection_id: UUID, *, cancel_pending: bool = True) -> None:
        task = self._tasks.pop(connection_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if not cancel_pending:
            return
        raw = await self._credentials.get(credential_reference(connection_id))
        if raw:
            for turn in await self._repository.list_inflight_turns(connection_id):
                if turn.status in {ChannelTurnStatus.ACCEPTED, ChannelTurnStatus.PROCESSING}:
                    await self._gateway.interrupt(
                        connection_id,
                        turn.channel_turn_id,
                        access_token=json.loads(raw)["gateway_token"],
                        reason="qq_connection_stopped",
                    )
        active_plans = await self._repository.list_nonterminal_delivery_plans(
            connection_id, limit=10000
        )
        await self._repository.cancel_active_delivery_plans_for_connection(
            connection_id,
            ChannelDeliveryPartsCancelRequest(
                reason="qq_connection_stopped", requested_at=datetime.now(UTC)
            ),
        )
        for plan in active_plans:
            terminal = await self._repository.get_delivery_plan(plan.delivery_id)
            if terminal is not None:
                await self._on_terminal(terminal)

    async def remove(self, connection_id: UUID) -> None:
        await self._stop_connection(connection_id)
        await self._gateway.delete_connection(connection_id)
        await self._credentials.delete(credential_reference(connection_id))
