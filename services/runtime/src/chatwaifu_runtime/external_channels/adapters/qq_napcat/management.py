"""NapCat pairing, secure enrollment, supervision and existing gateway integration."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channel_groups import ChannelGroupPauseReason
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionSnapshot,
    ChannelConnectionStatus,
    ChannelDeliveryPartsCancelRequest,
    ChannelPairingSnapshot,
    ChannelPairingStartRequest,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.agent.artifacts import ArtifactService
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.credentials import ChannelCredentialStore
from chatwaifu_runtime.external_channels.group_models import (
    GROUP_MENTION_ONLY_TEXT,
    ChannelGroupAudienceDetails,
    ChannelGroupInboundDescriptor,
)
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPlanRecord,
    ChannelInboundAudioInput,
    ChannelInboundImageInput,
    ChannelTranscriptionIdentity,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.scheduler import ChannelDeliveryScheduler
from chatwaifu_runtime.external_channels.service import ExternalChannelError, ExternalChannelService
from chatwaifu_runtime.external_channels.stickers import PresetStickerCatalog
from chatwaifu_runtime.realtime.contracts import SttBackend
from chatwaifu_runtime.realtime.stt import DisabledSttBackend
from chatwaifu_runtime.sticker_library.service import StickerLibraryService

from .audio import NapCatAudioTranscriber, audio_input
from .client import NapCatClient, NapCatError, validate_endpoint
from .delivery import NapCatDelivery, reconcile_known_sends
from .favorites import NapCatStickerFavorites
from .groups import NapCatGroupMembershipNotice, normalize_group_notice, qq_group_identifier
from .media import image_input
from .messages import normalize, normalize_group_inbound, normalize_inbound, normalize_poke
from .registration import PROVIDER_ID

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from chatwaifu_runtime.external_channels.groups import ChannelGroupService


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
        *,
        sticker_catalog: PresetStickerCatalog | None = None,
        sticker_library: StickerLibraryService | None = None,
        stt_backend: SttBackend | None = None,
        proactive_authorization: Callable[[ChannelDeliveryPlanRecord], Awaitable[bool]]
        | None = None,
        proactive_on_terminal: Callable[[ChannelDeliveryPlanRecord], Awaitable[None]] | None = None,
        groups: ChannelGroupService | None = None,
        private_voice_enabled: Callable[[], bool] = lambda: True,
        native_favorites_enabled: Callable[[], bool] = lambda: True,
        artifacts: ArtifactService | None = None,
        catalog_versions: frozenset[str] = frozenset({"4.18.33"}),
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._credentials = credentials
        self._characters = characters
        self._publisher = publisher
        self._hub = hub
        self._audio_root = audio_root
        self._artifacts = artifacts
        self._catalog_versions = catalog_versions
        self.task_authorization: Callable[[ChannelDeliveryPlanRecord], Awaitable[bool]] | None = (
            None
        )
        self._on_terminal = on_plan_terminal
        self._proactive_authorization = (
            proactive_authorization or gateway.authorize_proactive_delivery
        )
        self._proactive_on_terminal = proactive_on_terminal or gateway.proactive_delivery_terminal
        self._groups = groups
        self._private_voice_enabled = private_voice_enabled
        self.free_chat_enabled: Callable[[], bool] = lambda: False
        self.group_free_chat: Callable[[ChannelGroupInboundDescriptor], Awaitable[bool]] | None = (
            None
        )
        self._native_favorites_enabled = native_favorites_enabled
        self._factory = client_factory
        self._sticker_catalog = sticker_catalog
        self._sticker_library = sticker_library
        self._favorites: dict[UUID, NapCatStickerFavorites] = {}
        self._audio_transcriber = NapCatAudioTranscriber(stt_backend or DisabledSttBackend())
        self._pairings: dict[UUID, ChannelPairingSnapshot] = {}
        self._pair_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._schedulers: dict[UUID, ChannelDeliveryScheduler] = {}
        self._clients: dict[UUID, NapCatClient] = {}
        self._changed = asyncio.Condition()
        self._lock = asyncio.Lock()
        self._stopping = False
        self._journal_locks: dict[UUID, asyncio.Lock] = {}
        self._journal_cursor: UUID | None = None
        self._journal_task: asyncio.Task[None] | None = None
        self._group_ingress_tasks: dict[asyncio.Task[None], UUID] = {}
        self._group_observation_tasks: dict[asyncio.Task[None], tuple[UUID, str, str]] = {}
        self._group_ingress_order: dict[tuple[UUID, str], asyncio.Task[None]] = {}
        self._group_notice_pending: set[UUID] = set()
        self._group_stop_reasons: dict[UUID, ChannelGroupPauseReason] = {}
        if groups is not None:
            groups.set_audience_reader(self._group_audience)
            groups.set_transport_ready(self._group_transport_ready)
            groups.set_scheduler_wake_callback(self._wake_scheduler)

    def _wake_scheduler(self, connection_id: UUID) -> None:
        scheduler = self._schedulers.get(connection_id)
        if scheduler is not None:
            scheduler.wake()

    def _group_transport_ready(self, connection_id: UUID) -> bool:
        client = self._clients.get(connection_id)
        return (
            connection_id not in self._group_notice_pending
            and client is not None
            and client.group_dispatch_ready
        )

    async def _group_audience(
        self, connection_id: UUID, group_id: str
    ) -> ChannelGroupAudienceDetails:
        client = self._clients.get(connection_id)
        if client is None or not self._group_transport_ready(connection_id):
            raise NapCatError("QQ group transport is unavailable")
        members = await client.get_group_member_list(group_id)
        if self._clients.get(connection_id) is not client or not self._group_transport_ready(
            connection_id
        ):
            raise NapCatError("QQ group transport changed during audience observation")
        return ChannelGroupAudienceDetails(
            members.account_key, members.member_ids, members.display_names
        )

    async def scoped_agent_call(
        self,
        connection_id: UUID,
        account_key: str,
        action: str,
        params: JsonObject,
        guard: Callable[[], Awaitable[bool]],
    ) -> JsonObject:
        return await self._checked_agent_call(
            connection_id, account_key, action, params, guard, self._catalog_versions
        )

    async def account_agent_call(
        self,
        connection_id: UUID,
        account_key: str,
        action: str,
        params: JsonObject,
        guard: Callable[[], Awaitable[bool]],
        version: str,
    ) -> JsonObject:
        return await self._checked_agent_call(
            connection_id, account_key, action, params, guard, frozenset({version})
        )

    async def _checked_agent_call(
        self,
        connection_id: UUID,
        account_key: str,
        action: str,
        params: JsonObject,
        guard: Callable[[], Awaitable[bool]],
        versions: frozenset[str],
    ) -> JsonObject:
        client = self._clients.get(connection_id)
        if client is None or not await guard():
            raise NapCatError("QQ request unavailable")
        login = await client.call("get_login_info", {})
        if str(login.get("user_id")) != account_key or not await guard():
            raise NapCatError("QQ account changed")
        if "group_id" in params and not self._group_transport_ready(connection_id):
            raise NapCatError("QQ group transport unavailable")
        version = await client.call("get_version_info", {})
        if str(version.get("app_version", "")).removeprefix("v") not in versions:
            raise NapCatError(
                "QQ OpenAPI version differs from reviewed catalog; adapt catalog first"
            )
        if (
            self._clients.get(connection_id) is not client
            or ("group_id" in params and not self._group_transport_ready(connection_id))
            or not await guard()
        ):
            raise NapCatError("QQ authorization changed during version preflight")
        data = await client.call(action, params)
        if self._clients.get(connection_id) is not client or not await guard():
            raise NapCatError("QQ request changed during operation")
        return data

    @property
    def agent_available(self) -> bool:
        return bool(self._clients)

    async def authorize_task_delivery(self, plan: ChannelDeliveryPlanRecord) -> bool:
        return self.task_authorization is not None and await self.task_authorization(plan)

    def _group_notice_observed(
        self, connection_id: UUID, notice: NapCatGroupMembershipNotice
    ) -> None:
        self._group_notice_pending.add(connection_id)
        if self._groups is not None:
            self._groups.fence_connection(
                connection_id, reason="membership_changed", group_id=notice.group_id
            )

    def _group_transport_invalidated(self, connection_id: UUID) -> None:
        self._group_notice_pending.add(connection_id)
        self._gateway.fence_recent_images(connection_id)
        if self._groups is not None:
            self._groups.fence_connection(connection_id, reason="reconnect")

    async def _cancel_group_ingress(self, connection_id: UUID | None = None) -> None:
        tasks = [
            task
            for task, owner in self._group_ingress_tasks.items()
            if connection_id is None or owner == connection_id
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _dispatch_group(
        self, event: JsonObject, connection_id: UUID, account: str, access_token: str
    ) -> None:
        if self._groups is None or not self._group_transport_ready(connection_id):
            return
        group_id = qq_group_identifier(event.get("group_id"))
        if group_id is None:
            return
        message = normalize_group_inbound(
            event,
            connection_id=connection_id,
            account=account,
            group_id=group_id,
            allowed_senders=None,
            allow_unmentioned_images=True,
            allow_unmentioned_text=True,
            allow_audio=self.free_chat_enabled(),
        )
        if message is None:
            return
        if len(self._group_ingress_tasks) >= 32:
            logger.info("QQ group admission capacity exceeded")
            return
        observation_key = (connection_id, group_id, message.sender_key)
        if not message.bot_mentioned and (
            len(self._group_observation_tasks) >= 24
            or sum(key == observation_key for key in self._group_observation_tasks.values()) >= 3
        ):
            # Reserve admission slots for mentions and other members; these
            # transport bounds do not grant authority to an unresolved sender.
            return
        client = self._clients.get(connection_id)
        favorite = self._favorites.get(connection_id)
        incoming_image = (
            image_input(
                client,
                message.images,
                on_sticker_saved=favorite.observe_saved if favorite is not None else None,
            )
            if client is not None and message.images
            else None
        )
        descriptor = ChannelGroupInboundDescriptor(
            message.connection_id,
            message.account_key,
            message.group_id,
            message.sender_key,
            message.external_message_id,
            GROUP_MENTION_ONLY_TEXT if message.mention_only else message.text,
            message.received_at,
            incoming_image.source_fingerprint if incoming_image is not None else None,
            mention_only=message.mention_only,
        )
        incoming_audio = (
            audio_input(client, message.record, self._audio_transcriber)
            if client is not None and message.record is not None
            else None
        )

        async def verify_audio() -> None:
            if client is None or self._clients.get(connection_id) is not client:
                raise NapCatError("group voice transport changed")
            original = await client.call("get_msg", {"message_id": message.external_message_id})
            # Filename lookup is account-global. First prove the reference belongs to
            # this exact group message/speaker; never transcribe a private asset here.
            sender = original.get("sender")
            if (
                original.get("message_type") != "group"
                or qq_group_identifier(original.get("group_id")) != group_id
                or not isinstance(sender, dict)
                or qq_group_identifier(sender.get("user_id")) != message.sender_key
            ):
                raise NapCatError("group voice source does not match")
            original.update(
                {
                    "post_type": "message",
                    "self_id": account,
                    "sub_type": "normal",
                    "user_id": message.sender_key,
                }
            )
            checked = normalize_group_inbound(
                original,
                connection_id=connection_id,
                account=account,
                group_id=group_id,
                allowed_senders=frozenset({message.sender_key}),
                allow_audio=True,
                allow_unmentioned_text=True,
            )
            if (
                checked is None
                or checked.record != message.record
                or checked.external_message_id != message.external_message_id
            ):
                raise NapCatError("group voice reference does not match")

        key = (connection_id, group_id)
        predecessor = self._group_ingress_order.get(key)

        async def ordered_admission() -> None:
            if predecessor is not None:
                await asyncio.shield(predecessor)
            await self._ingest_group(
                descriptor,
                access_token,
                incoming_image,
                mentioned=message.bot_mentioned,
                incoming_audio=incoming_audio,
                verify_audio=verify_audio,
            )

        task = asyncio.create_task(ordered_admission(), name="qq-group-admission")
        self._group_ingress_order[key] = task
        self._group_ingress_tasks[task] = connection_id
        if not message.bot_mentioned:
            self._group_observation_tasks[task] = observation_key

        def finished_admission(finished: asyncio.Task[None]) -> None:
            self._group_ingress_tasks.pop(finished, None)
            self._group_observation_tasks.pop(finished, None)
            if self._group_ingress_order.get(key) is finished:
                self._group_ingress_order.pop(key, None)

        task.add_done_callback(finished_admission)

    async def _ingest_group(
        self,
        descriptor: ChannelGroupInboundDescriptor,
        access_token: str,
        incoming_image: ChannelInboundImageInput | None = None,
        *,
        mentioned: bool = True,
        incoming_audio: ChannelInboundAudioInput | None = None,
        verify_audio: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        assert self._groups is not None
        try:
            free = self.group_free_chat is not None and await self.group_free_chat(descriptor)
            if incoming_audio is not None:
                if not free or verify_audio is None:
                    return

                async def load_transcript() -> str:
                    assert self.group_free_chat is not None
                    if not await self.group_free_chat(descriptor):
                        raise NapCatError("group voice policy changed")
                    await verify_audio()
                    transcript = await incoming_audio.load(
                        ChannelTranscriptionIdentity(
                            session_id=uuid4(),
                            turn_id=uuid4(),
                            generation_id=uuid4(),
                        )
                    )
                    if not await self.group_free_chat(descriptor):
                        raise NapCatError("group voice policy changed")
                    return transcript

                await self._groups.observe_group_audio(
                    descriptor,
                    access_token=access_token,
                    text_loader=load_transcript,
                )
            elif mentioned and not free:
                await self._groups.ingest_group(
                    descriptor, access_token=access_token, image_input=incoming_image
                )
            elif incoming_image is not None:
                await self._groups.observe_group_image_reference(
                    descriptor, access_token=access_token, image_input=incoming_image
                )
                if free:
                    await self._groups.observe_group_text(
                        replace(descriptor, image_fingerprint=None),
                        access_token=access_token,
                    )
            else:
                if descriptor.mention_only:
                    descriptor = replace(descriptor, mention_only=False)
                await self._groups.observe_group_text(descriptor, access_token=access_token)
        except asyncio.CancelledError:
            raise
        except ExternalChannelError as error:
            logger.info("QQ group admission rejected code=%s", error.code)
        except Exception:
            logger.error("QQ group admission failed")

    async def start(self) -> None:
        self._stopping = False
        await self._reconcile_journals_once()
        self._journal_task = asyncio.create_task(
            self._reconcile_journals(), name="qq-send-receipt-reconciliation"
        )
        for connection in await self._gateway.list_connections():
            if (
                connection.configuration.provider_id == PROVIDER_ID
                and connection.configuration.enabled
            ):
                self._start_connection(connection.configuration.connection_id)

    async def stop(self) -> None:
        self._stopping = True
        for connection_id in tuple(self._clients):
            self._group_transport_invalidated(connection_id)
        await self._cancel_group_ingress()
        tasks = set(self._pair_tasks.values()) | set(self._tasks.values())
        if self._journal_task is not None:
            tasks.add(self._journal_task)
            self._journal_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._pair_tasks.clear()
        self._group_notice_pending.clear()
        self._group_stop_reasons.clear()

    async def _terminal(self, plan: ChannelDeliveryPlanRecord) -> None:
        try:
            await self._on_terminal(plan)
        finally:
            try:
                await self._proactive_on_terminal(plan)
            finally:
                if self._groups is not None:
                    await self._groups.on_plan_terminal(plan)

    async def _reconcile_connection(self, connection_id: UUID) -> None:
        lock = self._journal_locks.setdefault(connection_id, asyncio.Lock())
        await reconcile_known_sends(
            self._repository, connection_id, self._publisher, self._terminal, journal_lock=lock
        )

    async def _reconcile_journals_once(self) -> None:
        connections = await self._repository.list_send_journal_connection_ids(
            limit=32, after_connection_id=self._journal_cursor
        )
        self._journal_cursor = connections[-1] if len(connections) == 32 else None
        for connection_id in connections:
            await self._reconcile_connection(connection_id)

    async def _reconcile_journals(self) -> None:
        while not self._stopping:
            await asyncio.sleep(15)
            try:
                await self._reconcile_journals_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("QQ send receipt reconciliation failed")

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
                if self._groups is not None:
                    self._group_transport_invalidated(connection_id)
                    await self._groups.pause_connection(
                        connection_id, reason=ChannelGroupPauseReason.RECONNECT
                    )
                    self._group_notice_pending.discard(connection_id)
                    client.set_group_observers(
                        membership_notice=lambda notice: self._group_notice_observed(
                            connection_id, notice
                        ),
                        transport_invalidated=lambda: self._group_transport_invalidated(
                            connection_id
                        ),
                    )
                await client.open()
                account = await self._account(client)
                if account != config.account_key:
                    self._group_stop_reasons[connection_id] = (
                        ChannelGroupPauseReason.ACCOUNT_CHANGED
                    )
                    await self._health(
                        connection_id, ChannelConnectionStatus.ERROR, "qq_account_changed"
                    )
                    return
                await self._health(connection_id, ChannelConnectionStatus.READY)
                client.bind_account(account)
                self._clients[connection_id] = client
                if self._sticker_library is not None:
                    self._favorites[connection_id] = NapCatStickerFavorites(
                        self._repository,
                        self._sticker_library,
                        client,
                        connection_id,
                        journal_lock=self._journal_locks.setdefault(connection_id, asyncio.Lock()),
                        enabled=self._native_favorites_enabled,
                        group_authorization=self._groups.authorize_sticker_source
                        if self._groups is not None
                        else None,
                    )
                retry = 0
                scheduler = ChannelDeliveryScheduler(
                    self._repository,
                    NapCatDelivery(
                        self._repository,
                        client,
                        connection_id,
                        config.allowed_sender_keys[0],
                        self._audio_root,
                        artifacts=self._artifacts,
                        task_authorization=self.authorize_task_delivery,
                        sticker_catalog=self._sticker_catalog,
                        sticker_library=self._sticker_library,
                        proactive_authorization=self._proactive_authorization,
                        private_voice_enabled=self._private_voice_enabled,
                        group_authorization=self._groups.authorize_delivery
                        if self._groups is not None
                        else None,
                        journal_lock=self._journal_locks.setdefault(connection_id, asyncio.Lock()),
                    ),
                    self._publisher,
                    event_hub=self._hub,
                    connection_id=connection_id,
                    on_plan_terminal=self._terminal,
                    before_claim=self._gateway.authorize_channel_delivery
                    if self._groups is not None
                    else self._proactive_authorization,
                    reconcile_receipts=lambda: self._reconcile_connection(connection_id),
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
                            self._group_stop_reasons[connection_id] = (
                                ChannelGroupPauseReason.ACCOUNT_CHANGED
                            )
                            raise NapCatError("QQ account changed") from None
                        await self._health(connection_id, ChannelConnectionStatus.READY)
                        continue
                    if event.get("post_type") == "notice" and event.get("sub_type") == "poke":
                        if not self.free_chat_enabled():
                            continue
                        poke = normalize_poke(event, account=account)
                        if poke is None:
                            continue
                        event = poke
                    if self._groups is not None and event.get("post_type") == "notice":
                        group_id = qq_group_identifier(event.get("group_id"))
                        notice = (
                            normalize_group_notice(event, account=account, group_id=group_id)
                            if group_id is not None
                            else None
                        )
                        if notice is not None:
                            self._group_notice_observed(connection_id, notice)
                            await self._groups.pause_connection(
                                connection_id,
                                reason=ChannelGroupPauseReason.MEMBERSHIP_CHANGED,
                                group_id=notice.group_id,
                            )
                            self._group_notice_pending.discard(connection_id)
                        continue
                    if event.get("message_type") == "group":
                        self._dispatch_group(
                            event, connection_id, account, private["gateway_token"]
                        )
                        continue
                    inbound = normalize_inbound(
                        event,
                        connection_id=connection_id,
                        account=account,
                        owner=config.allowed_sender_keys[0],
                    )
                    if inbound is None:
                        continue
                    try:
                        await self._gateway.ingest(
                            inbound.message,
                            access_token=private["gateway_token"],
                            supersede_inflight=True,
                            image_input=image_input(
                                client,
                                inbound.images,
                                on_sticker_saved=self._favorites[connection_id].observe_saved
                                if connection_id in self._favorites
                                else None,
                            )
                            if inbound.images
                            else None,
                            image_retention_allowed=False,
                            sticker_learning_allowed=True,
                            recent_image_context=True,
                            audio_input=audio_input(client, inbound.record, self._audio_transcriber)
                            if inbound.record is not None
                            else None,
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
                self._group_transport_invalidated(connection_id)
                try:
                    await self._cancel_group_ingress(connection_id)
                    if self._groups is not None:
                        await self._groups.pause_connection(
                            connection_id,
                            reason=self._group_stop_reasons.pop(
                                connection_id, ChannelGroupPauseReason.RECONNECT
                            ),
                        )
                finally:
                    try:
                        if scheduler:
                            await scheduler.stop()
                    finally:
                        self._schedulers.pop(connection_id, None)
                        if self._sticker_library is not None:
                            await self._sticker_library.cancel_connection(connection_id)
                        self._favorites.pop(connection_id, None)
                        self._clients.pop(connection_id, None)
                        try:
                            if client:
                                await client.close()
                        finally:
                            self._group_notice_pending.discard(connection_id)
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
            connection_id,
            cancel_pending=not connection.configuration.enabled,
            group_reason=ChannelGroupPauseReason.CONFIGURATION_CHANGED
            if connection.configuration.enabled
            else ChannelGroupPauseReason.CONNECTION_DISABLED,
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
                    if self._groups is not None:
                        self._group_transport_invalidated(connection_id)
                        await self._groups.pause_connection(
                            connection_id, reason=ChannelGroupPauseReason.ACCOUNT_CHANGED
                        )
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

    async def _stop_connection(
        self,
        connection_id: UUID,
        *,
        cancel_pending: bool = True,
        group_reason: ChannelGroupPauseReason = ChannelGroupPauseReason.RECONNECT,
    ) -> None:
        self._gateway.fence_recent_images(connection_id)
        if self._groups is not None:
            self._group_stop_reasons[connection_id] = group_reason
            self._group_transport_invalidated(connection_id)
            await self._groups.pause_connection(connection_id, reason=group_reason)
            await self._cancel_group_ingress(connection_id)
        if cancel_pending:
            await self._gateway.cancel_proactive_connection(
                connection_id, reason="qq_connection_stopped"
            )
        task = self._tasks.pop(connection_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._group_stop_reasons.pop(connection_id, None)
        self._group_notice_pending.discard(connection_id)
        if not cancel_pending:
            return
        raw = await self._credentials.get(credential_reference(connection_id))
        if raw:
            for turn in await self._repository.list_inflight_turns(connection_id):
                if turn.chat_type is not ChannelChatType.GROUP and turn.status in {
                    ChannelTurnStatus.ACCEPTED,
                    ChannelTurnStatus.PROCESSING,
                }:
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
                await self._terminal(terminal)

    async def remove(self, connection_id: UUID) -> None:
        await self._stop_connection(
            connection_id, group_reason=ChannelGroupPauseReason.CONNECTION_DELETED
        )
        await self._gateway.delete_connection(connection_id)
        await self._credentials.delete(credential_reference(connection_id))
