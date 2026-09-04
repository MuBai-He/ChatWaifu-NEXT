"""Native external-channel authorization and adapter lifecycle service."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.channels import (
    ChannelAuthorizationMethod,
    ChannelAuthorizationSnapshot,
    ChannelAuthorizationStartRequest,
    ChannelAuthorizationStatus,
    ChannelAuthorizationVerificationRequest,
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionSnapshot,
    ChannelConnectionStatus,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryStatus,
    ChannelInboundTextMessage,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import WeixinILinkError
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinAuthorizationPoll,
    WeixinAuthorizationStart,
    WeixinAuthorizationState,
    WeixinCredentials,
    WeixinPendingContext,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.credentials import (
    ChannelCredentialStore,
    ChannelCredentialStoreError,
)
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.scheduler import (
    ChannelDeliveryScheduler,
    DeliveryPartExecutionResult,
    DeliveryPartExecutor,
    DeliveryPartOutcome,
)
from chatwaifu_runtime.external_channels.service import (
    ChannelBusyError,
    ChannelDeliveryBusyError,
    ChannelNotFoundError,
    CreatedChannelConnection,
    ExternalChannelError,
    ExternalChannelService,
)

logger = logging.getLogger(__name__)

WEIXIN_ILINK_PROVIDER_ID = "weixin_ilink"
_AUTH_TTL = timedelta(minutes=5)
_MAX_AUTH_SESSIONS = 8
_PENDING_ENROLLMENT_REFERENCE = "weixin_ilink:pending-enrollment"


class WeixinILinkTransport(Protocol):
    async def close(self) -> None: ...

    async def start_authorization(self) -> WeixinAuthorizationStart: ...

    async def poll_authorization(
        self,
        *,
        qrcode: str,
        poll_base_url: str,
        verification_code: str | None = None,
    ) -> WeixinAuthorizationPoll: ...

    async def notify_start(self, credentials: WeixinCredentials) -> None: ...

    async def notify_stop(self, credentials: WeixinCredentials) -> None: ...

    async def get_updates(self, credentials: WeixinCredentials, cursor: str) -> WeixinUpdates: ...

    async def send_text(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        context_token: str,
        client_id: str,
        text: str,
    ) -> str: ...


class ChannelManagementError(ExternalChannelError):
    code = "channel_management_error"


class ChannelManagementUnavailableError(ChannelManagementError):
    code = "channel_secure_store_unavailable"
    http_status = 503
    retryable = True


class ChannelProviderUnavailableError(ChannelManagementError):
    code = "channel_provider_unavailable"
    http_status = 503

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class ChannelAuthorizationNotFoundError(ChannelManagementError):
    code = "channel_authorization_not_found"
    http_status = 404


class ChannelAuthorizationConflictError(ChannelManagementError):
    code = "channel_authorization_conflict"
    http_status = 409


@dataclass(slots=True)
class _AuthorizationSession:
    request: ChannelAuthorizationStartRequest
    qrcode: str
    poll_base_url: str
    snapshot: ChannelAuthorizationSnapshot
    task: asyncio.Task[None] | None = None
    verification_code: str | None = None
    committing: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(frozen=True, slots=True)
class _PendingEnrollment:
    auth_session_id: UUID
    configuration: ChannelConnectionConfiguration
    credentials: WeixinCredentials

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema_version": "1.0",
                "auth_session_id": str(self.auth_session_id),
                "configuration": self.configuration.model_dump(mode="json"),
                "credentials": json.loads(self.credentials.to_json()),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, serialized: str) -> _PendingEnrollment:
        try:
            raw: object = json.loads(serialized)
            if not isinstance(raw, dict):
                raise ValueError("invalid enrollment document")
            data = cast(dict[str, object], raw)
            if data.get("schema_version") != "1.0":
                raise ValueError("invalid enrollment document")
            auth_session_id = UUID(str(data["auth_session_id"]))
            configuration = ChannelConnectionConfiguration.model_validate(data["configuration"])
            credentials = WeixinCredentials.from_json(
                json.dumps(data["credentials"], separators=(",", ":"))
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ChannelCredentialStoreError(
                "stored WeChat enrollment journal is invalid"
            ) from error
        return cls(
            auth_session_id=auth_session_id,
            configuration=configuration,
            credentials=credentials,
        )


class WeixinDeliveryPartExecutor(DeliveryPartExecutor):
    def __init__(
        self,
        management: ChannelManagementService,
        connection_id: UUID,
    ) -> None:
        self._management = management
        self._connection_id = connection_id

    async def execute_part(
        self,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
    ) -> DeliveryPartExecutionResult:
        return await self._management.execute_weixin_delivery_part(
            self._connection_id,
            plan,
            part,
        )


class ChannelManagementService:
    """Own QR authorization and native provider tasks around the generic Gateway.

    The service never calls conversation, memory, or model implementations. It
    translates provider-private messages into the existing Gateway contracts,
    then uses its durable delivery lease and acknowledgement state machine.
    """

    def __init__(
        self,
        external_channels: ExternalChannelService,
        repository: ExternalChannelRepository,
        credentials: ChannelCredentialStore,
        weixin: WeixinILinkTransport,
        *,
        event_hub: EventHub | None = None,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        self._external_channels = external_channels
        self._repository = repository
        self._credentials = credentials
        self._weixin = weixin
        self._event_hub = event_hub or getattr(external_channels, "event_hub", None)
        self._event_publisher = event_publisher or getattr(external_channels, "publisher", None)
        self._auth_sessions: dict[UUID, _AuthorizationSession] = {}
        self._connection_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._schedulers: dict[UUID, ChannelDeliveryScheduler] = {}
        self._auth_lock = asyncio.Lock()
        self._task_lock = asyncio.Lock()
        self._credential_mutation_locks: dict[UUID, asyncio.Lock] = {}
        self._terminal_events_task: asyncio.Task[None] | None = None
        self._stopping = False

    def _get_credential_lock(self, connection_id: UUID) -> asyncio.Lock:
        lock = self._credential_mutation_locks.get(connection_id)
        if lock is None:
            lock = asyncio.Lock()
            self._credential_mutation_locks[connection_id] = lock
        return lock

    def get_scheduler(self, connection_id: UUID) -> ChannelDeliveryScheduler | None:
        return self._schedulers.get(connection_id)

    async def start(self) -> None:
        self._stopping = False
        try:
            available = await self._credentials.available()
        except ChannelCredentialStoreError:
            available = False
        if not available:
            await self._mark_secure_store_unavailable()
            return
        try:
            await self._recover_pending_enrollment()
        except (ChannelCredentialStoreError, ExternalChannelError):
            logger.exception("secure WeChat enrollment recovery is unavailable")
            await self._mark_secure_store_unavailable()
            return
        if self._event_hub is not None and self._terminal_events_task is None:
            self._terminal_events_task = asyncio.create_task(
                self._listen_for_terminal_events(),
                name="weixin-terminal-events-consumer",
            )
        connections = await self._external_channels.list_connections()
        for connection in connections:
            if (
                connection.configuration.provider_id == WEIXIN_ILINK_PROVIDER_ID
                and connection.configuration.enabled
            ):
                await self._ensure_connection_task(connection.configuration.connection_id)

    async def stop(self) -> None:
        self._stopping = True
        async with self._auth_lock:
            auth_tasks = [item.task for item in self._auth_sessions.values() if item.task]
            for item in self._auth_sessions.values():
                if item.snapshot.status in _ACTIVE_AUTH_STATUSES and not item.committing:
                    self._set_auth_snapshot(
                        item,
                        status=ChannelAuthorizationStatus.CANCELLED,
                        status_message="连接流程已停止。",
                    )
            for task in auth_tasks:
                task.cancel()
        async with self._task_lock:
            connection_tasks = list(self._connection_tasks.values())
            for task in connection_tasks:
                task.cancel()
        if self._terminal_events_task is not None:
            self._terminal_events_task.cancel()
            await _gather_cancelled([self._terminal_events_task])
            self._terminal_events_task = None
        schedulers = list(self._schedulers.values())
        for sched in schedulers:
            await sched.stop()
        self._schedulers.clear()
        await _gather_cancelled(auth_tasks + connection_tasks)
        async with self._task_lock:
            self._connection_tasks.clear()
        await self._weixin.close()

    async def start_authorization(
        self, request: ChannelAuthorizationStartRequest
    ) -> ChannelAuthorizationSnapshot:
        if request.provider_id != WEIXIN_ILINK_PROVIDER_ID:
            raise ChannelAuthorizationConflictError(
                f"provider {request.provider_id!r} has no Runtime-owned authorization flow"
            )
        if request.method is not ChannelAuthorizationMethod.QR_CODE:
            raise ChannelAuthorizationConflictError("the provider supports QR authorization only")
        try:
            available = await self._credentials.available()
        except ChannelCredentialStoreError as error:
            raise ChannelManagementUnavailableError(
                "No secure operating-system credential store is available."
            ) from error
        if not available:
            raise ChannelManagementUnavailableError(
                "No secure operating-system credential store is available."
            )
        async with self._auth_lock:
            now = datetime.now(UTC)
            self._purge_authorizations(now)
            provider_active = any(
                item.request.provider_id == request.provider_id
                and item.snapshot.status in _ACTIVE_AUTH_STATUSES
                for item in self._auth_sessions.values()
            )
            if provider_active:
                raise ChannelAuthorizationConflictError(
                    "this provider already has an active authorization session"
                )
            active_count = sum(
                item.snapshot.status in _ACTIVE_AUTH_STATUSES
                for item in self._auth_sessions.values()
            )
            if active_count >= _MAX_AUTH_SESSIONS:
                raise ChannelAuthorizationConflictError(
                    "too many channel authorization sessions are already active"
                )
        try:
            pending_enrollment = await self._credentials.get(_PENDING_ENROLLMENT_REFERENCE)
        except ChannelCredentialStoreError as error:
            raise ChannelManagementUnavailableError(
                "No secure operating-system credential store is available."
            ) from error
        if pending_enrollment is not None:
            raise ChannelAuthorizationConflictError(
                "a previous WeChat enrollment still needs recovery"
            )
        try:
            started = await self._weixin.start_authorization()
        except WeixinILinkError as error:
            raise ChannelProviderUnavailableError(
                "WeChat authorization is currently unavailable.",
                retryable=error.retryable,
            ) from error
        now = datetime.now(UTC)
        auth_session_id = uuid4()
        snapshot = ChannelAuthorizationSnapshot(
            auth_session_id=auth_session_id,
            provider_id=request.provider_id,
            method=request.method,
            status=ChannelAuthorizationStatus.PENDING,
            qr_code_content=started.qr_code_content,
            verification_required=False,
            status_message="请使用手机微信扫码。",
            poll_after_ms=1_000,
            expires_at=now + _AUTH_TTL,
            created_at=now,
            updated_at=now,
        )
        session = _AuthorizationSession(
            request=request,
            qrcode=started.qrcode,
            poll_base_url="https://ilinkai.weixin.qq.com/",
            snapshot=snapshot,
        )
        async with self._auth_lock:
            self._purge_authorizations(now)
            if any(
                item.request.provider_id == request.provider_id
                and item.snapshot.status in _ACTIVE_AUTH_STATUSES
                for item in self._auth_sessions.values()
            ):
                raise ChannelAuthorizationConflictError(
                    "this provider already has an active authorization session"
                )
            self._auth_sessions[auth_session_id] = session
            session.task = asyncio.create_task(
                self._run_authorization(auth_session_id),
                name=f"channel-auth-{auth_session_id}",
            )
        return snapshot

    async def get_authorization(
        self, auth_session_id: UUID, *, wait_seconds: float = 0
    ) -> ChannelAuthorizationSnapshot:
        async with self._auth_lock:
            session = self._auth_sessions.get(auth_session_id)
            if session is None:
                raise ChannelAuthorizationNotFoundError(
                    f"unknown channel authorization session {auth_session_id}"
                )
            snapshot = session.snapshot
            changed = session.changed
        if wait_seconds > 0 and snapshot.status in _ACTIVE_AUTH_STATUSES:
            try:
                await asyncio.wait_for(changed.wait(), timeout=wait_seconds)
            except TimeoutError:
                pass
            async with self._auth_lock:
                session = self._auth_sessions.get(auth_session_id)
                if session is None:
                    raise ChannelAuthorizationNotFoundError(
                        f"unknown channel authorization session {auth_session_id}"
                    )
                snapshot = session.snapshot
        return snapshot

    async def submit_verification(
        self,
        auth_session_id: UUID,
        request: ChannelAuthorizationVerificationRequest,
    ) -> ChannelAuthorizationSnapshot:
        async with self._auth_lock:
            session = self._auth_sessions.get(auth_session_id)
            if session is None:
                raise ChannelAuthorizationNotFoundError(
                    f"unknown channel authorization session {auth_session_id}"
                )
            if session.snapshot.status is not ChannelAuthorizationStatus.VERIFICATION_REQUIRED:
                raise ChannelAuthorizationConflictError(
                    "this authorization session is not waiting for a verification code"
                )
            session.verification_code = request.verification_code
            session.changed.set()
            return session.snapshot

    async def cancel_authorization(self, auth_session_id: UUID) -> ChannelAuthorizationSnapshot:
        task: asyncio.Task[None] | None = None
        async with self._auth_lock:
            session = self._auth_sessions.get(auth_session_id)
            if session is None:
                raise ChannelAuthorizationNotFoundError(
                    f"unknown channel authorization session {auth_session_id}"
                )
            if session.snapshot.status in _ACTIVE_AUTH_STATUSES:
                if not session.committing:
                    self._set_auth_snapshot(
                        session,
                        status=ChannelAuthorizationStatus.CANCELLED,
                        status_message="已取消连接。",
                    )
                if session.task is not None:
                    session.task.cancel()
                    task = session.task
        if task is not None:
            await _gather_cancelled([task])
        async with self._auth_lock:
            session = self._auth_sessions.get(auth_session_id)
            if session is None:
                raise ChannelAuthorizationNotFoundError(
                    f"unknown channel authorization session {auth_session_id}"
                )
            return session.snapshot

    async def remove_connection(self, connection_id: UUID) -> None:
        await self._stop_connection_task(connection_id)
        credentials = await self._load_credentials(connection_id)
        if credentials is not None:
            inflight_turns = await self._repository.list_inflight_turns(connection_id)
            for turn in inflight_turns:
                try:
                    await self._external_channels.interrupt(
                        connection_id,
                        turn.channel_turn_id,
                        access_token=credentials.gateway_access_token,
                        reason="channel_adapter_stopped",
                    )
                except Exception:
                    pass
        await self._repository.cancel_active_delivery_plans_for_connection(
            connection_id,
            ChannelDeliveryPartsCancelRequest(
                reason="connection_removed",
                requested_at=datetime.now(UTC),
            ),
        )
        snapshot = await self._external_channels.get_connection(connection_id)
        if snapshot.configuration.provider_id != WEIXIN_ILINK_PROVIDER_ID:
            await self._external_channels.delete_connection(connection_id)
            return
        if snapshot.configuration.enabled:
            updated = await self._external_channels.update_connection(
                snapshot.configuration.model_copy(update={"enabled": False}),
                expected_revision=snapshot.revision,
                rotate_access_token=False,
            )
            snapshot = (
                updated.snapshot if isinstance(updated, CreatedChannelConnection) else updated
            )
        try:
            await self._credentials.delete(_credential_reference(connection_id))
        except ChannelCredentialStoreError as error:
            raise ChannelManagementUnavailableError(
                "The secure credential could not be removed; the connection remains disabled."
            ) from error
        self._credential_mutation_locks.pop(connection_id, None)
        await self._external_channels.delete_connection(connection_id)

    async def connection_configuration_changed(self, snapshot: ChannelConnectionSnapshot) -> None:
        if snapshot.configuration.provider_id != WEIXIN_ILINK_PROVIDER_ID:
            return
        if snapshot.configuration.enabled:
            await self._ensure_connection_task(snapshot.configuration.connection_id)
        else:
            await self._stop_connection_task(snapshot.configuration.connection_id)

    async def _run_authorization(self, auth_session_id: UUID) -> None:
        retries = 0
        try:
            while not self._stopping:
                async with self._auth_lock:
                    session = self._auth_sessions.get(auth_session_id)
                    if session is None or session.snapshot.status not in _ACTIVE_AUTH_STATUSES:
                        return
                    if datetime.now(UTC) >= session.snapshot.expires_at:
                        self._set_auth_snapshot(
                            session,
                            status=ChannelAuthorizationStatus.EXPIRED,
                            status_message="二维码已过期，请重新生成。",
                        )
                        return
                    verification_code = session.verification_code
                    if session.snapshot.status is ChannelAuthorizationStatus.VERIFICATION_REQUIRED:
                        if verification_code is None:
                            changed = session.changed
                        else:
                            changed = None
                            session.verification_code = None
                            session.changed = asyncio.Event()
                    else:
                        changed = None
                    poll_base_url = session.poll_base_url
                    qrcode = session.qrcode
                if changed is not None:
                    remaining = max(
                        0.0, (session.snapshot.expires_at - datetime.now(UTC)).total_seconds()
                    )
                    try:
                        await asyncio.wait_for(changed.wait(), timeout=remaining)
                    except TimeoutError:
                        continue
                    continue
                try:
                    result = await self._weixin.poll_authorization(
                        qrcode=qrcode,
                        poll_base_url=poll_base_url,
                        verification_code=verification_code,
                    )
                    retries = 0
                except asyncio.CancelledError:
                    raise
                except WeixinILinkError as error:
                    if not error.retryable:
                        await self._fail_authorization(
                            auth_session_id,
                            error.code,
                            "微信返回了无法识别的连接响应，请重新生成二维码。",
                            retryable=False,
                        )
                        return
                    retries += 1
                    await _wait_retry(_retry_delay(retries))
                    continue
                except Exception:
                    retries += 1
                    await _wait_retry(_retry_delay(retries))
                    continue
                if await self._apply_authorization_result(auth_session_id, result):
                    return
                if result.state is WeixinAuthorizationState.WAIT:
                    await _wait_retry(0.75)
        except asyncio.CancelledError:
            raise
        except ChannelCredentialStoreError:
            logger.exception("secure WeChat credential store failed during authorization")
            await self._fail_authorization(
                auth_session_id,
                "channel_secure_store_unavailable",
                "系统安全凭据存储当前不可用，请解锁后重试。",
                retryable=False,
            )
        except Exception:
            logger.exception("native WeChat authorization task failed")
            await self._fail_authorization(
                auth_session_id,
                "weixin.authorization_failed",
                "微信连接失败，请重新尝试。",
                retryable=False,
            )

    async def _apply_authorization_result(
        self, auth_session_id: UUID, result: WeixinAuthorizationPoll
    ) -> bool:
        if result.state is WeixinAuthorizationState.CONFIRMED:
            await self._confirm_authorization(auth_session_id, result)
            return True
        async with self._auth_lock:
            session = self._auth_sessions.get(auth_session_id)
            if session is None or session.snapshot.status not in _ACTIVE_AUTH_STATUSES:
                return True
            if result.state is WeixinAuthorizationState.REDIRECT:
                if result.redirect_base_url is None:
                    raise ChannelAuthorizationConflictError(
                        "WeChat authorization redirect did not include a valid host"
                    )
                session.poll_base_url = result.redirect_base_url
                self._set_auth_snapshot(
                    session,
                    status=ChannelAuthorizationStatus.SCANNED,
                    status_message="扫码成功，正在连接。",
                )
                return False
            if result.state is WeixinAuthorizationState.WAIT:
                self._set_auth_snapshot(
                    session,
                    status=ChannelAuthorizationStatus.PENDING,
                    status_message="请使用手机微信扫码。",
                )
                return False
            if result.state is WeixinAuthorizationState.SCANNED:
                self._set_auth_snapshot(
                    session,
                    status=ChannelAuthorizationStatus.SCANNED,
                    status_message="扫码成功，正在确认。",
                )
                return False
            if result.state is WeixinAuthorizationState.VERIFICATION_REQUIRED:
                self._set_auth_snapshot(
                    session,
                    status=ChannelAuthorizationStatus.VERIFICATION_REQUIRED,
                    status_message="请输入手机微信显示的配对码。",
                )
                return False
            if result.state is WeixinAuthorizationState.EXPIRED:
                self._set_auth_snapshot(
                    session,
                    status=ChannelAuthorizationStatus.EXPIRED,
                    status_message="二维码已过期，请重新生成。",
                )
                return True
            if result.state is WeixinAuthorizationState.VERIFICATION_BLOCKED:
                self._set_auth_snapshot(
                    session,
                    status=ChannelAuthorizationStatus.FAILED,
                    status_message="配对码错误次数过多，请重新连接。",
                    error=_structured_error(
                        "weixin.verification_blocked",
                        "WeChat blocked further verification attempts for this QR code.",
                        retryable=False,
                    ),
                )
                return True
            if result.state is WeixinAuthorizationState.ALREADY_BOUND:
                self._set_auth_snapshot(
                    session,
                    status=ChannelAuthorizationStatus.FAILED,
                    status_message="该微信连接已绑定到其他客户端，请先在微信中解除后重试。",
                    error=_structured_error(
                        "weixin.already_bound",
                        "The scanned WeChat connection is already bound.",
                        retryable=False,
                    ),
                )
                return True
        return False

    async def _confirm_authorization(
        self, auth_session_id: UUID, result: WeixinAuthorizationPoll
    ) -> None:
        async with self._auth_lock:
            session = self._auth_sessions.get(auth_session_id)
            if session is None or session.snapshot.status not in _ACTIVE_AUTH_STATUSES:
                return
            request = session.request
        bot_token = result.bot_token
        bot_id = result.bot_id
        user_id = result.user_id
        base_url = result.base_url
        if bot_token is None or bot_id is None or user_id is None or base_url is None:
            await self._fail_authorization(
                auth_session_id,
                "weixin.authorization_result_incomplete",
                "微信没有返回完整的连接信息，请重新尝试。",
                retryable=False,
            )
            return
        configuration = ChannelConnectionConfiguration(
            connection_id=uuid4(),
            provider_id=WEIXIN_ILINK_PROVIDER_ID,
            name=request.connection_name or "我的微信",
            character_id=request.character_id,
            principal_scope=request.principal_scope,
            account_key=bot_id,
            allowed_sender_keys=[user_id],
            enabled=True,
        )
        gateway_access_token = secrets.token_urlsafe(32)
        credentials = WeixinCredentials(
            bot_token=bot_token,
            bot_id=bot_id,
            user_id=user_id,
            base_url=base_url,
            gateway_access_token=gateway_access_token,
        )
        pending = _PendingEnrollment(
            auth_session_id=auth_session_id,
            configuration=configuration,
            credentials=credentials,
        )

        async with self._auth_lock:
            session = self._auth_sessions.get(auth_session_id)
            if session is None or session.snapshot.status not in _ACTIVE_AUTH_STATUSES:
                return
            session.committing = True

        commit_task = asyncio.create_task(
            self._commit_pending_enrollment(pending),
            name=f"channel-enrollment-{configuration.connection_id}",
        )
        try:
            created = await asyncio.shield(commit_task)
        except asyncio.CancelledError:
            # Provider confirmation is irreversible. Once credentials have been
            # returned, finish the secure journal transaction before honouring
            # cancellation so a bound account can never lose its local token.
            created = await commit_task
            await self._finish_authorization_commit(auth_session_id, created.snapshot)
            await self._ensure_connection_task(configuration.connection_id)
            raise
        except BaseException:
            async with self._auth_lock:
                session = self._auth_sessions.get(auth_session_id)
                if session is not None:
                    session.committing = False
            await self._set_connection_error(
                configuration.connection_id,
                "channel_secure_store_unavailable",
                "The secure WeChat enrollment could not be completed.",
            )
            raise

        await self._finish_authorization_commit(auth_session_id, created.snapshot)
        await self._ensure_connection_task(configuration.connection_id)

    async def _commit_pending_enrollment(
        self, pending: _PendingEnrollment
    ) -> CreatedChannelConnection:
        if await self._credentials.get(_PENDING_ENROLLMENT_REFERENCE) is not None:
            raise ChannelAuthorizationConflictError(
                "another WeChat enrollment is already pending recovery"
            )
        await self._credentials.set(_PENDING_ENROLLMENT_REFERENCE, pending.to_json())
        created = await self._external_channels.create_connection(
            pending.configuration,
            access_token=pending.credentials.gateway_access_token,
        )
        await self._credentials.set(
            _credential_reference(pending.configuration.connection_id),
            pending.credentials.to_json(),
        )
        await self._credentials.delete(_PENDING_ENROLLMENT_REFERENCE)
        return created

    async def _recover_pending_enrollment(self) -> None:
        serialized = await self._credentials.get(_PENDING_ENROLLMENT_REFERENCE)
        if serialized is None:
            return
        pending = _PendingEnrollment.from_json(serialized)
        try:
            snapshot = await self._external_channels.get_connection(
                pending.configuration.connection_id
            )
        except ChannelNotFoundError:
            created = await self._external_channels.create_connection(
                pending.configuration,
                access_token=pending.credentials.gateway_access_token,
            )
            snapshot = created.snapshot
        if snapshot.configuration != pending.configuration:
            raise ChannelCredentialStoreError(
                "stored WeChat enrollment does not match the durable connection"
            )
        await self._credentials.set(
            _credential_reference(pending.configuration.connection_id),
            pending.credentials.to_json(),
        )
        await self._credentials.delete(_PENDING_ENROLLMENT_REFERENCE)

    async def _finish_authorization_commit(
        self,
        auth_session_id: UUID,
        connection: ChannelConnectionSnapshot,
    ) -> None:
        async with self._auth_lock:
            session = self._auth_sessions.get(auth_session_id)
            if session is None:
                return
            session.committing = False
            self._set_auth_snapshot(
                session,
                status=ChannelAuthorizationStatus.CONFIRMED,
                status_message="微信已连接。",
                connection=connection,
            )

    async def _fail_authorization(
        self,
        auth_session_id: UUID,
        code: str,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        async with self._auth_lock:
            session = self._auth_sessions.get(auth_session_id)
            if session is None or session.snapshot.status not in _ACTIVE_AUTH_STATUSES:
                return
            self._set_auth_snapshot(
                session,
                status=ChannelAuthorizationStatus.FAILED,
                status_message=message,
                error=_structured_error(code, message, retryable=retryable),
            )

    async def _ensure_connection_task(self, connection_id: UUID) -> None:
        if self._stopping:
            return
        async with self._task_lock:
            existing = self._connection_tasks.get(connection_id)
            if existing is not None and not existing.done():
                return
            task = asyncio.create_task(
                self._run_connection(connection_id),
                name=f"weixin-ilink-{connection_id}",
            )
            self._connection_tasks[connection_id] = task
            task.add_done_callback(
                lambda completed, owned_id=connection_id: self._connection_task_done(
                    owned_id, completed
                )
            )

    def _connection_task_done(self, connection_id: UUID, task: asyncio.Task[None]) -> None:
        current = self._connection_tasks.get(connection_id)
        if current is task:
            self._connection_tasks.pop(connection_id, None)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("native WeChat connection task failed")

    async def _stop_connection_task(self, connection_id: UUID) -> None:
        async with self._task_lock:
            task = self._connection_tasks.pop(connection_id, None)
            if task is not None:
                task.cancel()
        if task is not None:
            await _gather_cancelled([task])
        active_sched = self._schedulers.pop(connection_id, None)
        if active_sched is not None:
            await active_sched.stop()

    async def _run_connection(self, connection_id: UUID) -> None:
        try:
            credentials = await self._load_credentials(connection_id)
        except ChannelCredentialStoreError:
            await self._set_connection_error(
                connection_id,
                "channel_secure_store_unavailable",
                "The operating-system credential store is unavailable.",
            )
            return
        if credentials is None:
            await self._set_connection_error(
                connection_id,
                "channel_credentials_missing",
                "The secure WeChat credentials are missing.",
            )
            return
        await self._reconcile_pending_contexts(connection_id)
        credentials = await self._load_credentials(connection_id)
        if credentials is None:
            return
        retries = 0
        executor = WeixinDeliveryPartExecutor(self, connection_id)
        publisher = (
            self._event_publisher
            if self._event_publisher is not None
            else getattr(self._external_channels, "publisher", None)
        )
        scheduler = ChannelDeliveryScheduler(
            repository=self._repository,
            publisher=publisher,
            executor=executor,
            event_hub=self._event_hub,
            connection_id=connection_id,
            on_plan_terminal=lambda plan: self._handle_plan_terminal(connection_id, plan),
        )
        self._schedulers[connection_id] = scheduler
        try:
            try:
                await self._weixin.notify_start(credentials)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._set_connection_health(
                    connection_id,
                    "weixin.notify_start_failed",
                    "WeChat did not accept the connection start notification.",
                    status=ChannelConnectionStatus.DEGRADED,
                    retryable=True,
                )
            await scheduler.start()
            while not self._stopping:
                cursor = await self._repository.get_adapter_cursor(connection_id)
                try:
                    credentials = await self._load_credentials(connection_id)
                    if credentials is None:
                        await self._set_connection_error(
                            connection_id,
                            "channel_credentials_missing",
                            "The secure WeChat credentials are missing.",
                        )
                        return
                    updates = await self._weixin.get_updates(credentials, cursor)
                    await self._process_updates(connection_id, updates)
                    # The checkpoint advances only after every normalized message
                    # in this batch reached durable admission.
                    await self._repository.set_adapter_cursor(
                        connection_id,
                        cursor=updates.cursor,
                        updated_at=datetime.now(UTC),
                    )
                    await self._repository.touch_connection(
                        connection_id,
                        status=ChannelConnectionStatus.READY,
                        seen_at=datetime.now(UTC),
                    )
                    retries = 0
                except asyncio.CancelledError:
                    raise
                except WeixinILinkError as error:
                    if not error.retryable:
                        await self._set_connection_health(
                            connection_id,
                            error.code,
                            "WeChat returned a response that cannot be processed.",
                            status=ChannelConnectionStatus.ERROR,
                            retryable=False,
                        )
                        return
                    retries += 1
                except (ChannelBusyError, ChannelDeliveryBusyError):
                    retries += 1
                except Exception:
                    retries += 1
                    logger.warning(
                        "native WeChat update cycle failed for connection %s",
                        connection_id,
                    )
                if retries:
                    await self._set_connection_health(
                        connection_id,
                        "weixin.connection_retrying",
                        "The WeChat connection is retrying after a temporary failure.",
                        status=ChannelConnectionStatus.DEGRADED,
                        retryable=True,
                    )
                    await _wait_retry(_retry_delay(retries))
                else:
                    await asyncio.sleep(0)
        finally:
            active_sched = self._schedulers.pop(connection_id, None)
            if active_sched is not None:
                await active_sched.stop()
            try:
                creds = await self._load_credentials(connection_id)
                if creds is not None:
                    await self._weixin.notify_stop(creds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "native WeChat stop notification failed for connection %s",
                    connection_id,
                )

    async def _process_updates(
        self,
        connection_id: UUID,
        updates: WeixinUpdates,
    ) -> None:
        connection = await self._external_channels.get_connection(connection_id)
        credentials = await self._load_credentials(connection_id)
        if credentials is None:
            return
        for message in updates.messages:
            if message.sender_user_id != credentials.user_id:
                # Gateway policy would also reject this. Reject here before
                # saving any provider-private reply context.
                continue
            await self._remember_context(
                connection_id,
                message.external_message_id,
                WeixinPendingContext(
                    context_token=message.context_token,
                    recipient_user_id=message.sender_user_id,
                ),
            )
            inbound = ChannelInboundTextMessage(
                connection_id=connection_id,
                account_key=credentials.bot_id,
                external_message_id=message.external_message_id,
                conversation_key=message.sender_user_id,
                sender_key=message.sender_user_id,
                principal_scope=connection.configuration.principal_scope,
                chat_type=ChannelChatType.DIRECT,
                text=message.text,
                received_at=message.received_at,
            )
            try:
                await self._external_channels.ingest(
                    inbound, access_token=credentials.gateway_access_token
                )
            except (ChannelBusyError, ChannelDeliveryBusyError):
                raise
            except ExternalChannelError:
                logger.warning(
                    "failed to ingest inbound WeChat message %s",
                    message.external_message_id,
                )
                await self._forget_context(connection_id, message.external_message_id)
                continue
            scheduler = self.get_scheduler(connection_id)
            if scheduler is not None:
                scheduler.wake()

    async def _handle_plan_terminal(
        self,
        connection_id: UUID,
        plan: ChannelDeliveryPlanRecord,
    ) -> None:
        turn = await self._repository.get_turn(plan.delivery.channel_turn_id)
        if turn is None:
            return
        await self._forget_context(connection_id, turn.external_message_id)

    async def reconcile_pending_contexts(self, connection_id: UUID) -> None:
        await self._reconcile_pending_contexts(connection_id)

    async def _reconcile_pending_contexts(self, connection_id: UUID) -> None:
        async with self._get_credential_lock(connection_id):
            credentials = await self._load_credentials(connection_id)
            if credentials is None or not credentials.pending_contexts:
                return
            stale_message_ids: list[str] = []
            for external_message_id in list(credentials.pending_contexts.keys()):
                turn = await self._repository.find_turn_by_external_message(
                    connection_id, external_message_id
                )
                if turn is None:
                    stale_message_ids.append(external_message_id)
                    continue
                if turn.delivery_id is not None:
                    plan = await self._repository.get_delivery_plan(turn.delivery_id)
                    if plan is None or plan.status in (
                        ChannelDeliveryStatus.DELIVERED,
                        ChannelDeliveryStatus.FAILED,
                        ChannelDeliveryStatus.CANCELLED,
                    ):
                        stale_message_ids.append(external_message_id)
                elif turn.status in (
                    ChannelTurnStatus.FAILED,
                    ChannelTurnStatus.CANCELLED,
                    ChannelTurnStatus.COMPLETED,
                ):
                    stale_message_ids.append(external_message_id)

            if stale_message_ids:
                pending = dict(credentials.pending_contexts)
                for msg_id in stale_message_ids:
                    pending.pop(msg_id, None)
                updated = replace(credentials, pending_contexts=pending)
                await self._credentials.set(_credential_reference(connection_id), updated.to_json())
                logger.info(
                    "reconciled %d stale pending WeChat contexts for connection %s",
                    len(stale_message_ids),
                    connection_id,
                )

    async def _listen_for_terminal_events(self) -> None:
        if self._event_hub is None:
            return
        terminal_event_types = {
            "channel.delivery_plan_completed",
            "channel.delivery_plan_cancelled",
            "channel.delivery_plan_failed",
        }

        def _is_terminal_event(event: dict[str, object]) -> bool:
            return str(event.get("event_type")) in terminal_event_types

        subscription = self._event_hub.subscribe(_is_terminal_event)
        try:
            while not self._stopping:
                event_payload = await subscription.receive()
                if self._stopping:
                    break
                try:
                    await self._on_delivery_plan_terminal_event(event_payload)
                except Exception:
                    logger.exception("failed to process terminal delivery plan event")
        except asyncio.CancelledError:
            pass
        finally:
            self._event_hub.unsubscribe(subscription)

    async def _on_delivery_plan_terminal_event(self, event: dict[str, object]) -> None:
        raw_payload = event.get("payload")
        if not isinstance(raw_payload, dict):
            return
        payload = cast(dict[str, object], raw_payload)
        connection_id_str = payload.get("connection_id")
        channel_turn_id_str = payload.get("channel_turn_id")
        delivery_id_str = payload.get("delivery_id")

        turn: ChannelTurnRecord | None = None
        if channel_turn_id_str:
            try:
                turn = await self._repository.get_turn(UUID(str(channel_turn_id_str)))
            except Exception:
                pass

        if turn is None and delivery_id_str:
            try:
                plan = await self._repository.get_delivery_plan(UUID(str(delivery_id_str)))
                if plan is not None:
                    turn = await self._repository.get_turn(plan.delivery.channel_turn_id)
            except Exception:
                pass

        if turn is None:
            return

        conn_id: UUID | None = None
        if connection_id_str:
            try:
                conn_id = UUID(str(connection_id_str))
            except (ValueError, TypeError):
                pass
        if conn_id is None:
            conn_id = turn.connection_id

        await self._forget_context(conn_id, turn.external_message_id)

    async def _load_credentials(self, connection_id: UUID) -> WeixinCredentials | None:
        serialized = await self._credentials.get(_credential_reference(connection_id))
        if serialized is None:
            return None
        try:
            return WeixinCredentials.from_json(serialized)
        except ValueError as error:
            raise ChannelCredentialStoreError("stored WeChat credentials are invalid") from error

    async def _remember_context(
        self,
        connection_id: UUID,
        external_message_id: str,
        context: WeixinPendingContext,
    ) -> None:
        async with self._get_credential_lock(connection_id):
            credentials = await self._load_credentials(connection_id)
            if credentials is None:
                raise ChannelCredentialStoreError("cannot record context for missing credentials")
            pending = dict(credentials.pending_contexts)
            if external_message_id not in pending and len(pending) >= 16:
                raise RuntimeError("too many pending WeChat reply contexts")
            pending[external_message_id] = context
            updated = replace(credentials, pending_contexts=pending)
            await self._credentials.set(_credential_reference(connection_id), updated.to_json())

    async def _forget_context(
        self,
        connection_id: UUID,
        external_message_id: str,
    ) -> None:
        async with self._get_credential_lock(connection_id):
            credentials = await self._load_credentials(connection_id)
            if credentials is None:
                return
            if external_message_id not in credentials.pending_contexts:
                return
            pending = dict(credentials.pending_contexts)
            pending.pop(external_message_id, None)
            updated = replace(credentials, pending_contexts=pending)
            await self._credentials.set(_credential_reference(connection_id), updated.to_json())

    async def _set_connection_error(
        self,
        connection_id: UUID,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        await self._set_connection_health(
            connection_id,
            code,
            message,
            status=ChannelConnectionStatus.ERROR,
            retryable=retryable,
        )

    async def _mark_secure_store_unavailable(self) -> None:
        connections = await self._external_channels.list_connections()
        for connection in connections:
            if (
                connection.configuration.provider_id == WEIXIN_ILINK_PROVIDER_ID
                and connection.configuration.enabled
            ):
                await self._set_connection_error(
                    connection.configuration.connection_id,
                    "channel_secure_store_unavailable",
                    "The operating-system credential store is unavailable.",
                )

    async def _set_connection_health(
        self,
        connection_id: UUID,
        code: str,
        message: str,
        *,
        status: ChannelConnectionStatus,
        retryable: bool,
    ) -> None:
        try:
            await self._repository.set_connection_status(
                connection_id,
                status=status,
                last_error=_structured_error(code, message, retryable=retryable),
                updated_at=datetime.now(UTC),
            )
        except KeyError:
            return

    def _set_auth_snapshot(
        self,
        session: _AuthorizationSession,
        *,
        status: ChannelAuthorizationStatus,
        status_message: str,
        connection: ChannelConnectionSnapshot | None = None,
        error: StructuredError | None = None,
    ) -> None:
        previous_event = session.changed
        payload = session.snapshot.model_dump(mode="python")
        payload.update(
            {
                "status": status,
                "verification_required": (
                    status is ChannelAuthorizationStatus.VERIFICATION_REQUIRED
                ),
                "connection": connection,
                "error": error,
                "status_message": status_message,
                "poll_after_ms": 1_000 if status in _ACTIVE_AUTH_STATUSES else None,
                "updated_at": datetime.now(UTC),
            }
        )
        session.snapshot = ChannelAuthorizationSnapshot.model_validate(payload)
        session.changed = asyncio.Event()
        previous_event.set()

    def _purge_authorizations(self, now: datetime) -> None:
        stale = [
            auth_session_id
            for auth_session_id, session in self._auth_sessions.items()
            if session.snapshot.status not in _ACTIVE_AUTH_STATUSES
            and now - session.snapshot.updated_at > timedelta(minutes=10)
        ]
        for auth_session_id in stale:
            self._auth_sessions.pop(auth_session_id, None)

    async def execute_weixin_delivery_part(
        self,
        connection_id: UUID,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
    ) -> DeliveryPartExecutionResult:
        if part.kind is not ChannelDeliveryPartKind.TEXT:
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.FATAL_ERROR,
                error=_structured_error(
                    "unsupported_delivery_part_kind",
                    f"Delivery part kind {part.kind} is not supported by WeChat adapter.",
                    retryable=False,
                ),
            )
        try:
            credentials = await self._load_credentials(connection_id)
        except Exception as exc:
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.RETRYABLE_ERROR,
                error=_structured_error(
                    "channel_credentials_load_failed",
                    str(exc),
                    retryable=True,
                ),
            )
        if credentials is None:
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.FATAL_ERROR,
                error=_structured_error(
                    "channel_credentials_missing",
                    "The secure WeChat credentials are missing.",
                    retryable=False,
                ),
            )
        turn = await self._repository.get_turn(plan.delivery.channel_turn_id)
        if turn is None:
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.FATAL_ERROR,
                error=_structured_error(
                    "channel_turn_missing",
                    "The channel turn was not found.",
                    retryable=False,
                ),
            )
        context = credentials.pending_contexts.get(turn.external_message_id)
        if context is None:
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.FATAL_ERROR,
                error=_structured_error(
                    "channel_context_missing",
                    "WeChat reply context disappeared before delivery.",
                    retryable=False,
                ),
            )
        client_id = part.provider_client_id
        text = part.payload.text
        try:
            provider_message_id = await self._weixin.send_text(
                credentials,
                recipient_user_id=context.recipient_user_id,
                context_token=context.context_token,
                client_id=client_id,
                text=text,
            )
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.DELIVERED,
                provider_message_id=provider_message_id,
            )
        except asyncio.CancelledError:
            raise
        except WeixinILinkError as error:
            outcome = (
                DeliveryPartOutcome.RETRYABLE_ERROR
                if error.retryable
                else DeliveryPartOutcome.FATAL_ERROR
            )
            return DeliveryPartExecutionResult(
                outcome=outcome,
                error=_structured_error(
                    error.code,
                    error.message,
                    retryable=error.retryable,
                ),
            )
        except Exception as exc:
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.RETRYABLE_ERROR,
                error=_structured_error(
                    "weixin.send_failed",
                    str(exc) or "WeChat did not accept the reply part.",
                    retryable=True,
                ),
            )


_ACTIVE_AUTH_STATUSES = {
    ChannelAuthorizationStatus.PENDING,
    ChannelAuthorizationStatus.SCANNED,
    ChannelAuthorizationStatus.VERIFICATION_REQUIRED,
}


def _credential_reference(connection_id: UUID) -> str:
    return f"weixin_ilink:{connection_id}"


def _structured_error(code: str, message: str, *, retryable: bool) -> StructuredError:
    return StructuredError(
        code=code,
        message=message,
        retryable=retryable,
        component="external_channels",
    )


def _retry_delay(attempt: int) -> float:
    """Return capped exponential backoff with bounded positive jitter."""

    exponent = max(0, min(attempt - 1, 6))
    base = min(30.0, 0.5 * (2**exponent))
    jitter_factor = 0.75 + (secrets.randbelow(501) / 1_000)
    return max(0.25, base * jitter_factor)


async def _wait_retry(seconds: float) -> None:
    event = asyncio.Event()
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except TimeoutError:
        return


async def _gather_cancelled(tasks: list[asyncio.Task[None]]) -> None:
    if not tasks:
        return
    await asyncio.gather(*tasks, return_exceptions=True)
