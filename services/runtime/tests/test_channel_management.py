"""Native channel authorization, credential, and cancellation lifecycle tests."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelAuthorizationMethod,
    ChannelAuthorizationStartRequest,
    ChannelAuthorizationStatus,
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelInboundTextMessage,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import WeixinILinkError
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinAuthorizationPoll,
    WeixinAuthorizationStart,
    WeixinAuthorizationState,
    WeixinCredentials,
    WeixinInboundText,
    WeixinPendingContext,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.credentials import (
    ChannelCredentialStoreError,
    InMemoryChannelCredentialStore,
)
from chatwaifu_runtime.external_channels.management import (
    ChannelManagementService,
    ChannelManagementUnavailableError,
    ChannelProviderUnavailableError,
    _PendingEnrollment,
)
from chatwaifu_runtime.external_channels.models import ChannelTurnRecord
from chatwaifu_runtime.external_channels.scheduler import ChannelDeliveryScheduler
from chatwaifu_runtime.external_channels.service import (
    ChannelConflictError,
    ChannelNotFoundError,
    SingleTextDeliveryPlanFactory,
)
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.demo_llm import DemoLlmProvider


class _FakeWeixin:
    def __init__(self) -> None:
        self.authorization_results: asyncio.Queue[WeixinAuthorizationPoll] = asyncio.Queue()
        self.updates: asyncio.Queue[WeixinUpdates] = asyncio.Queue()
        self.poll_cancelled = asyncio.Event()
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.sent = asyncio.Event()
        self.sent_messages: list[dict[str, str]] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def start_authorization(self) -> WeixinAuthorizationStart:
        return WeixinAuthorizationStart(qrcode="opaque-qr", qr_code_content="qr-content")

    async def poll_authorization(
        self,
        *,
        qrcode: str,
        poll_base_url: str,
        verification_code: str | None = None,
    ) -> WeixinAuthorizationPoll:
        del qrcode, poll_base_url, verification_code
        try:
            return await self.authorization_results.get()
        except asyncio.CancelledError:
            self.poll_cancelled.set()
            raise

    async def notify_start(self, credentials: WeixinCredentials) -> None:
        del credentials
        self.started.set()

    async def notify_stop(self, credentials: WeixinCredentials) -> None:
        del credentials
        self.stopped.set()

    async def get_updates(self, credentials: WeixinCredentials, cursor: str) -> WeixinUpdates:
        del credentials, cursor
        return await self.updates.get()

    async def send_text(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        context_token: str,
        client_id: str,
        text: str,
    ) -> str:
        del credentials
        self.sent_messages.append(
            {
                "recipient_user_id": recipient_user_id,
                "context_token": context_token,
                "client_id": client_id,
                "text": text,
            }
        )
        self.sent.set()
        return client_id


class _JournalReadFailureStore(InMemoryChannelCredentialStore):
    async def get(self, reference: str) -> str | None:
        del reference
        raise ChannelCredentialStoreError("credential backend is locked")


class _FormalReadFailureStore(InMemoryChannelCredentialStore):
    async def get(self, reference: str) -> str | None:
        if reference == "weixin_ilink:pending-enrollment":
            return None
        raise ChannelCredentialStoreError("credential backend became unavailable")


class _FormalWriteFailureStore(InMemoryChannelCredentialStore):
    async def set(self, reference: str, value: str) -> None:
        if reference != "weixin_ilink:pending-enrollment":
            raise ChannelCredentialStoreError("credential backend became read-only")
        await super().set(reference, value)


class _StartFailureWeixin(_FakeWeixin):
    async def start_authorization(self) -> WeixinAuthorizationStart:
        raise WeixinILinkError(
            "weixin.request_failed",
            "provider detail that must not cross the API",
            retryable=True,
        )


def _configuration(connection_id: UUID) -> ChannelConnectionConfiguration:
    return ChannelConnectionConfiguration(
        connection_id=connection_id,
        provider_id="weixin_ilink",
        name="我的微信",
        character_id="default",
        principal_scope="local",
        account_key="bot-1",
        allowed_sender_keys=["owner-1"],
        enabled=True,
    )


def _credentials(access_token: str) -> WeixinCredentials:
    return WeixinCredentials(
        bot_token="provider-token",
        bot_id="bot-1",
        user_id="owner-1",
        base_url="https://api.weixin.qq.com/",
        gateway_access_token=access_token,
    )


def _replace_management(
    container: RuntimeContainer,
    store: InMemoryChannelCredentialStore,
    transport: _FakeWeixin,
) -> ChannelManagementService:
    service = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
        event_hub=container.event_hub,
        event_publisher=container.event_publisher,
    )
    container.channel_management = service
    return service


@pytest.mark.asyncio
async def test_start_recovers_secure_pending_enrollment_journal(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    _replace_management(container, store, transport)
    connection_id = uuid4()
    access_token = "g" * 43
    pending = _PendingEnrollment(
        auth_session_id=uuid4(),
        configuration=_configuration(connection_id),
        credentials=_credentials(access_token),
    )
    await store.set("weixin_ilink:pending-enrollment", pending.to_json())

    await container.start()
    try:
        recovered = await container.external_channels.get_connection(connection_id)
        assert recovered.configuration == pending.configuration
        assert await store.get("weixin_ilink:pending-enrollment") is None
        formal = await store.get(f"weixin_ilink:{connection_id}")
        assert formal is not None
        assert WeixinCredentials.from_json(formal).gateway_access_token == access_token
        await asyncio.wait_for(transport.started.wait(), timeout=1)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_qr_confirmation_commits_keyring_before_exposing_connection(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    try:
        await transport.authorization_results.put(
            WeixinAuthorizationPoll(
                state=WeixinAuthorizationState.CONFIRMED,
                bot_token="provider-token",
                bot_id="bot-1",
                user_id="owner-1",
                base_url="https://api.weixin.qq.com/",
            )
        )
        started = await management.start_authorization(
            ChannelAuthorizationStartRequest(
                provider_id="weixin_ilink",
                method=ChannelAuthorizationMethod.QR_CODE,
                character_id="default",
            )
        )
        snapshot = await management.get_authorization(started.auth_session_id, wait_seconds=2)

        assert snapshot.status is ChannelAuthorizationStatus.CONFIRMED
        assert snapshot.connection is not None
        connection_id = snapshot.connection.configuration.connection_id
        assert await store.get("weixin_ilink:pending-enrollment") is None
        assert await store.get(f"weixin_ilink:{connection_id}") is not None
        await asyncio.wait_for(transport.started.wait(), timeout=1)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_provider_start_failure_is_normalized_as_safe_503(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    management = _replace_management(
        container,
        InMemoryChannelCredentialStore(),
        _StartFailureWeixin(),
    )
    await container.start()
    try:
        with pytest.raises(ChannelProviderUnavailableError) as caught:
            await management.start_authorization(
                ChannelAuthorizationStartRequest(
                    provider_id="weixin_ilink",
                    character_id="default",
                )
            )
        assert caught.value.http_status == 503
        assert caught.value.retryable is True
        assert "provider detail" not in str(caught.value)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_enrollment_keyring_failure_becomes_safe_failed_snapshot(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = _FormalWriteFailureStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    try:
        await transport.authorization_results.put(
            WeixinAuthorizationPoll(
                state=WeixinAuthorizationState.CONFIRMED,
                bot_token="provider-token",
                bot_id="bot-1",
                user_id="owner-1",
                base_url="https://api.weixin.qq.com/",
            )
        )
        started = await management.start_authorization(
            ChannelAuthorizationStartRequest(
                provider_id="weixin_ilink",
                character_id="default",
            )
        )
        snapshot = await management.get_authorization(started.auth_session_id, wait_seconds=2)

        assert snapshot.status is ChannelAuthorizationStatus.FAILED
        assert snapshot.error is not None
        assert snapshot.error.code == "channel_secure_store_unavailable"
        assert "read-only" not in (snapshot.status_message or "")
        assert await store.get("weixin_ilink:pending-enrollment") is not None
        connections = await container.external_channels.list_connections()
        assert len(connections) == 1
        assert connections[0].status.value == "error"
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_locked_credential_store_does_not_abort_runtime_start_and_auth_is_503(
    runtime_settings: Settings,
) -> None:
    seed = RuntimeContainer(runtime_settings)
    _replace_management(seed, InMemoryChannelCredentialStore(), _FakeWeixin())
    await seed.start()
    connection_id = uuid4()
    await seed.external_channels.create_connection(
        _configuration(connection_id), access_token="g" * 43
    )
    await seed.stop()

    container = RuntimeContainer(runtime_settings)
    management = _replace_management(container, _JournalReadFailureStore(), _FakeWeixin())
    await container.start()
    try:
        snapshot = await container.external_channels.get_connection(connection_id)
        assert snapshot.status.value == "error"
        assert snapshot.last_error is not None
        assert snapshot.last_error.code == "channel_secure_store_unavailable"
        with pytest.raises(ChannelManagementUnavailableError) as caught:
            await management.start_authorization(
                ChannelAuthorizationStartRequest(
                    provider_id="weixin_ilink",
                    character_id="default",
                )
            )
        assert caught.value.http_status == 503
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_connection_credential_read_failure_is_isolated_to_connection(
    runtime_settings: Settings,
) -> None:
    seed = RuntimeContainer(runtime_settings)
    _replace_management(seed, InMemoryChannelCredentialStore(), _FakeWeixin())
    await seed.start()
    connection_id = uuid4()
    await seed.external_channels.create_connection(
        _configuration(connection_id), access_token="g" * 43
    )
    await seed.stop()

    container = RuntimeContainer(runtime_settings)
    _replace_management(container, _FormalReadFailureStore(), _FakeWeixin())
    await container.start()
    try:
        snapshot = await container.external_channels.get_connection(connection_id)
        for _ in range(20):
            if snapshot.last_error is not None:
                break
            await asyncio.sleep(0)
            snapshot = await container.external_channels.get_connection(connection_id)
        assert snapshot.status.value == "error"
        assert snapshot.last_error is not None
        assert snapshot.last_error.code == "channel_secure_store_unavailable"
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_cancel_authorization_waits_for_poll_task_teardown(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    try:
        started = await management.start_authorization(
            ChannelAuthorizationStartRequest(
                provider_id="weixin_ilink",
                method=ChannelAuthorizationMethod.QR_CODE,
                character_id="default",
            )
        )
        await asyncio.sleep(0)
        cancelled = await management.cancel_authorization(started.auth_session_id)

        assert cancelled.status is ChannelAuthorizationStatus.CANCELLED
        assert transport.poll_cancelled.is_set()
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_adapter_stop_interrupts_admitted_turn_before_remove_and_cursor_advance(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    connection_id = uuid4()
    access_token = "g" * 43
    created = await container.external_channels.create_connection(
        _configuration(connection_id), access_token=access_token
    )
    await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())
    entered_wait = asyncio.Event()
    interrupted = asyncio.Event()
    original_wait = container.external_channels.wait_for_turn
    original_interrupt = container.external_channels.interrupt

    async def blocked_wait(*args: object, **kwargs: object) -> object:
        del args, kwargs
        entered_wait.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def observed_interrupt(
        target_connection_id: UUID,
        channel_turn_id: UUID,
        *,
        access_token: str,
        reason: str,
    ) -> object:
        interrupted.set()
        return await original_interrupt(
            target_connection_id,
            channel_turn_id,
            access_token=access_token,
            reason=reason,
        )

    monkeypatch.setattr(container.external_channels, "wait_for_turn", blocked_wait)
    monkeypatch.setattr(container.external_channels, "interrupt", observed_interrupt)
    await management.connection_configuration_changed(created.snapshot)
    await transport.updates.put(
        WeixinUpdates(
            cursor="cursor-after",
            messages=(
                WeixinInboundText(
                    external_message_id="message-1",
                    sender_user_id="owner-1",
                    recipient_bot_id="bot-1",
                    text="晚上继续聊 Python。",
                    context_token="reply-context-token",
                    received_at=datetime.now(UTC),
                ),
            ),
        )
    )
    try:
        await asyncio.wait_for(entered_wait.wait(), timeout=2)
        for _ in range(20):
            if (
                await container.external_channel_repository.get_adapter_cursor(connection_id)
                == "cursor-after"
            ):
                break
            await asyncio.sleep(0.05)
        assert (
            await container.external_channel_repository.get_adapter_cursor(connection_id)
            == "cursor-after"
        )

        await management.remove_connection(connection_id)

        assert interrupted.is_set()
        assert await store.get(f"weixin_ilink:{connection_id}") is None
        assert (
            await container.external_channel_repository.get_adapter_cursor(connection_id)
            == "cursor-after"
        )
        with pytest.raises(ChannelNotFoundError):
            await container.external_channels.get_connection(connection_id)
    finally:
        monkeypatch.setattr(container.external_channels, "wait_for_turn", original_wait)
        await container.stop()


@pytest.mark.asyncio
async def test_native_bubbles_render_without_separator_lines_but_persist_losslessly(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    paragraphs = (
        "First paragraph stays complete while we rest and relax after a long day.",
        "Second paragraph offers a warm dinner with enough detail to keep together.",
        "Third paragraph closes the conversation with a calm and friendly goodnight.",
    )
    canonical = "\n\n".join(paragraphs)

    async def reply(self: DemoLlmProvider, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        del self, request
        yield LlmTextDelta(canonical)
        yield LlmResponseCompleted("stop")

    monkeypatch.setattr(DemoLlmProvider, "stream", reply)
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    completed = container.event_hub.subscribe(
        lambda event: event.get("event_type") == "channel.delivery_plan_completed"
    )
    try:
        connection_id = uuid4()
        configuration = _configuration(connection_id).model_copy(
            update={
                "presentation_policy": ChannelPresentationPolicy(
                    profile=ChannelPresentationProfile.INSTANT_MESSAGE, cadence_enabled=False
                )
            }
        )
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            configuration, access_token=access_token
        )
        await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())
        await management.connection_configuration_changed(created.snapshot)
        await transport.updates.put(
            WeixinUpdates(
                cursor="paragraph-cursor",
                messages=(
                    WeixinInboundText(
                        external_message_id="paragraph-message",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Tell me about relaxing, dinner and bedtime.",
                        context_token="paragraph-context",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )
        await asyncio.wait_for(completed.receive(), timeout=5)
        assert [message["text"] for message in transport.sent_messages] == list(paragraphs)
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "paragraph-message"
        )
        assert turn is not None and turn.delivery_id is not None
        assert turn.reply_text == canonical
        plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert plan is not None and plan.status is ChannelDeliveryStatus.DELIVERED
        assert "".join(part.payload.text for part in plan.parts) == canonical
        assert plan.parts[0].payload.text.endswith("\n\n")
        assert plan.parts[1].payload.text.endswith("\n\n")
        assert all(part.attempt == 1 for part in plan.parts)
        assert [message["client_id"] for message in transport.sent_messages] == [
            part.provider_client_id for part in plan.parts
        ]
    finally:
        container.event_hub.unsubscribe(completed)
        await container.stop()


@pytest.mark.asyncio
async def test_native_adapter_delivers_reply_then_advances_cursor_and_clears_context(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    connection_id = uuid4()
    access_token = "g" * 43
    created = await container.external_channels.create_connection(
        _configuration(connection_id), access_token=access_token
    )
    await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())
    cursor_advanced = asyncio.Event()
    original_set_cursor = container.external_channel_repository.set_adapter_cursor

    async def observed_set_cursor(
        target_connection_id: UUID,
        *,
        cursor: str,
        updated_at: datetime,
    ) -> None:
        await original_set_cursor(
            target_connection_id,
            cursor=cursor,
            updated_at=updated_at,
        )
        cursor_advanced.set()

    monkeypatch.setattr(
        container.external_channel_repository,
        "set_adapter_cursor",
        observed_set_cursor,
    )
    await management.connection_configuration_changed(created.snapshot)
    await transport.updates.put(
        WeixinUpdates(
            cursor="cursor-after",
            messages=(
                WeixinInboundText(
                    external_message_id="happy-message-1",
                    sender_user_id="owner-1",
                    recipient_bot_id="bot-1",
                    text="晚上继续聊 Python。",
                    context_token="reply-context-token",
                    received_at=datetime.now(UTC),
                ),
            ),
        )
    )
    try:
        await asyncio.wait_for(cursor_advanced.wait(), timeout=5)
        await asyncio.wait_for(transport.sent.wait(), timeout=5)

        assert len(transport.sent_messages) == 1
        sent = transport.sent_messages[0]
        assert sent["recipient_user_id"] == "owner-1"
        assert sent["context_token"] == "reply-context-token"
        assert sent["client_id"].startswith("chatwaifu-")
        assert sent["text"]
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "happy-message-1"
        )
        assert turn is not None
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            turn.channel_turn_id,
            access_token=access_token,
            wait_seconds=0,
        )
        assert snapshot.delivery_status is not None
        assert snapshot.delivery_status.value == "delivered"
        assert (
            await container.external_channel_repository.get_adapter_cursor(connection_id)
            == "cursor-after"
        )
        serialized: str | None = None
        for _ in range(50):
            serialized = await store.get(f"weixin_ilink:{connection_id}")
            if (
                serialized is not None
                and WeixinCredentials.from_json(serialized).pending_contexts == {}
            ):
                break
            await asyncio.sleep(0.1)
        assert serialized is not None
        assert WeixinCredentials.from_json(serialized).pending_contexts == {}
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_weixin_pending_contexts_concurrent_rmw_with_barrier(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement P0-1: Pending contexts mutation concurrency with barrier.
    Context A already exists.
    Poller concurrently adds B.
    Scheduler concurrently removes A.
    The final store must strictly contain {B}; it cannot be empty, and cannot resurrect A.
    """
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        base_creds = _credentials(access_token)
        context_a = WeixinPendingContext(context_token="tok-A", recipient_user_id="user-1")
        context_b = WeixinPendingContext(context_token="tok-B", recipient_user_id="user-1")

        # Initial state: Keyring contains only Context A
        initial_creds = replace(base_creds, pending_contexts={"msg-A": context_a})
        await store.set(f"weixin_ilink:{connection_id}", initial_creds.to_json())

        original_set = store.set

        async def slow_set(ref: str, value: str) -> None:
            # Yield to event loop to expose any race condition if locks were missing
            await asyncio.sleep(0.01)
            await original_set(ref, value)

        monkeypatch.setattr(store, "set", slow_set)

        barrier = asyncio.Barrier(2)

        async def poller_add_b() -> None:
            await barrier.wait()
            await management._remember_context(connection_id, "msg-B", context_b)

        async def scheduler_remove_a() -> None:
            await barrier.wait()
            await management._forget_context(connection_id, "msg-A")

        await asyncio.gather(poller_add_b(), scheduler_remove_a())

        final_raw = await store.get(f"weixin_ilink:{connection_id}")
        assert final_raw is not None
        final_creds = WeixinCredentials.from_json(final_raw)
        # Final store must strictly contain only B!
        assert "msg-B" in final_creds.pending_contexts
        assert "msg-A" not in final_creds.pending_contexts
        assert len(final_creds.pending_contexts) == 1
        assert final_creds.pending_contexts["msg-B"].context_token == "tok-B"
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_native_adapter_cross_batch_preserves_pending_contexts(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement P0-1: Cross-batch get_updates tests.
    Batch 1 adds message-1, cursor advances.
    Batch 2 adds message-2, cursor advances.
    Both pending contexts must be preserved and not overwritten.
    """
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    connection_id = uuid4()
    access_token = "g" * 43
    created = await container.external_channels.create_connection(
        _configuration(connection_id), access_token=access_token
    )
    await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())

    batch1_cursor_advanced = asyncio.Event()
    batch2_cursor_advanced = asyncio.Event()
    original_set_cursor = container.external_channel_repository.set_adapter_cursor

    async def observed_set_cursor(
        target_connection_id: UUID,
        *,
        cursor: str,
        updated_at: datetime,
    ) -> None:
        await original_set_cursor(
            target_connection_id,
            cursor=cursor,
            updated_at=updated_at,
        )
        if cursor == "cursor-batch-1":
            batch1_cursor_advanced.set()
        elif cursor == "cursor-batch-2":
            batch2_cursor_advanced.set()

    monkeypatch.setattr(
        container.external_channel_repository,
        "set_adapter_cursor",
        observed_set_cursor,
    )
    await management.connection_configuration_changed(created.snapshot)

    # Batch 1
    await transport.updates.put(
        WeixinUpdates(
            cursor="cursor-batch-1",
            messages=(
                WeixinInboundText(
                    external_message_id="msg-batch-1",
                    sender_user_id="owner-1",
                    recipient_bot_id="bot-1",
                    text="消息 1",
                    context_token="context-token-1",
                    received_at=datetime.now(UTC),
                ),
            ),
        )
    )
    await asyncio.wait_for(batch1_cursor_advanced.wait(), timeout=5)

    # Batch 2
    await transport.updates.put(
        WeixinUpdates(
            cursor="cursor-batch-2",
            messages=(
                WeixinInboundText(
                    external_message_id="msg-batch-2",
                    sender_user_id="owner-1",
                    recipient_bot_id="bot-1",
                    text="消息 2",
                    context_token="context-token-2",
                    received_at=datetime.now(UTC),
                ),
            ),
        )
    )
    await asyncio.wait_for(batch2_cursor_advanced.wait(), timeout=5)

    # Wait for both sent messages
    for _ in range(50):
        if len(transport.sent_messages) >= 2:
            break
        await asyncio.sleep(0.1)
    assert len(transport.sent_messages) == 2

    # After both deliveries complete and terminal handlers run, pending_contexts becomes empty
    for _ in range(50):
        raw = await store.get(f"weixin_ilink:{connection_id}")
        if raw is not None and WeixinCredentials.from_json(raw).pending_contexts == {}:
            break
        await asyncio.sleep(0.1)
    raw = await store.get(f"weixin_ilink:{connection_id}")
    assert raw is not None
    assert WeixinCredentials.from_json(raw).pending_contexts == {}
    await container.stop()


@pytest.mark.asyncio
async def test_remove_connection_cancels_active_delivery_plans_and_rejects_raw_delete(
    runtime_settings: Settings,
) -> None:
    """Requirement P1-1:
    - Direct soft-delete on connection with active delivery plan raises
      ValueError / ChannelConflictError.
    - remove_connection() cancels all active delivery plans (both parent and child parts),
      cleans up credentials, and soft-deletes the connection.
    """
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())

        # Ingest a message to create an active delivery plan
        receipt = await container.external_channels.ingest(
            ChannelInboundTextMessage(
                connection_id=connection_id,
                account_key="bot-1",
                external_message_id="msg-del-test",
                conversation_key="owner-1",
                sender_key="owner-1",
                principal_scope=created.snapshot.configuration.principal_scope,
                chat_type=ChannelChatType.DIRECT,
                text="测试删除活跃计划",
                received_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        snap = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap.delivery_id is not None
        delivery_id = snap.delivery_id

        # Verify delivery plan is active (pending)
        plan = await container.external_channel_repository.get_delivery_plan(delivery_id)
        assert plan is not None
        assert plan.status in {ChannelDeliveryStatus.PENDING, ChannelDeliveryStatus.SENDING}

        # 1. Direct raw delete must be rejected because of active delivery plan!
        with pytest.raises(ChannelConflictError) as exc_info:
            await container.external_channels.delete_connection(connection_id)
        assert "cannot delete a channel connection with an active delivery plan" in str(
            exc_info.value
        )

        # 2. remove_connection must cancel the plan and succeed
        await management.remove_connection(connection_id)

        # Verify delivery plan and parts are CANCELLED
        updated_plan = await container.external_channel_repository.get_delivery_plan(delivery_id)
        assert updated_plan is not None
        assert updated_plan.status is ChannelDeliveryStatus.CANCELLED
        assert all(p.status is ChannelDeliveryPartStatus.CANCELLED for p in updated_plan.parts)

        # Verify credentials removed
        assert await store.get(f"weixin_ilink:{connection_id}") is None

        # Verify connection is soft-deleted
        with pytest.raises(ChannelNotFoundError):
            await container.external_channels.get_connection(connection_id)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_run_connection_calls_notify_start_before_scheduler_start(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement P1-2: notify_start must be invoked before scheduler starts."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())

        events: list[str] = []
        original_notify_start = transport.notify_start

        async def observed_notify_start(credentials: WeixinCredentials) -> None:
            events.append("notify_start")
            await original_notify_start(credentials)

        transport.notify_start = observed_notify_start  # type: ignore

        original_scheduler_start = ChannelDeliveryScheduler.start

        async def observed_scheduler_start(sched_self: ChannelDeliveryScheduler) -> None:
            events.append("scheduler_start")
            await original_scheduler_start(sched_self)

        monkeypatch.setattr(ChannelDeliveryScheduler, "start", observed_scheduler_start)

        await management.connection_configuration_changed(created.snapshot)
        for _ in range(50):
            if "scheduler_start" in events:
                break
            await asyncio.sleep(0.05)

        assert events == ["notify_start", "scheduler_start"]
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_pending_context_cleaned_up_when_plan_cancelled_by_new_message(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: Plan A pending, message B arrives and cancels Plan A
    -> Context A is deleted, Plan A is CANCELLED."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)

    class _TwoPartFactory:
        def create_parts(
            self,
            reply_text: str,
            policy: ChannelPresentationPolicy | None = None,
        ) -> tuple[ChannelDeliveryPartDraft, ...]:
            return (
                ChannelDeliveryPartDraft(
                    ordinal=0,
                    kind=ChannelDeliveryPartKind.TEXT,
                    payload=ChannelTextDeliveryPartPayload(text="Part 0"),
                    required=True,
                ),
                ChannelDeliveryPartDraft(
                    ordinal=1,
                    kind=ChannelDeliveryPartKind.TEXT,
                    payload=ChannelTextDeliveryPartPayload(text="Part 1"),
                    required=True,
                ),
            )

    container.external_channels.delivery_plan_factory = _TwoPartFactory()

    hold_send = asyncio.Event()
    send_called = asyncio.Event()
    original_send = transport.send_text

    async def delayed_send(*args: Any, **kwargs: Any) -> str:
        send_called.set()
        await hold_send.wait()
        return await original_send(*args, **kwargs)

    monkeypatch.setattr(transport, "send_text", delayed_send)
    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        creds = _credentials(access_token)
        await store.set(f"weixin_ilink:{connection_id}", creds.to_json())
        await management.connection_configuration_changed(created.snapshot)

        # Message A arrives
        await transport.updates.put(
            WeixinUpdates(
                cursor="c1",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-plan-a",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Message A",
                        context_token="ctx-token-a",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        await asyncio.wait_for(send_called.wait(), timeout=5.0)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-plan-a" in loaded.pending_contexts

        # Message B arrives on same binding
        await transport.updates.put(
            WeixinUpdates(
                cursor="c2",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-plan-b",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Message B",
                        context_token="ctx-token-b",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait until Message B is admitted (triggers cancel_remaining_delivery_parts on Plan A)
        turn_b: ChannelTurnRecord | None = None
        for _ in range(50):
            turn_b = await container.external_channel_repository.find_turn_by_external_message(
                connection_id, "msg-plan-b"
            )
            if turn_b is not None:
                break
            await asyncio.sleep(0.05)
        assert turn_b is not None

        hold_send.set()

        for _ in range(100):
            raw = await store.get(f"weixin_ilink:{connection_id}")
            if raw is not None:
                loaded = WeixinCredentials.from_json(raw)
                if "msg-plan-a" not in loaded.pending_contexts:
                    break
            await asyncio.sleep(0.1)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-plan-a" not in loaded.pending_contexts

        # Verify Plan A actually transitioned to CANCELLED (Part 0 delivered, Part 1 cancelled)
        turn_a = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "msg-plan-a"
        )
        assert turn_a is not None
        assert turn_a.delivery_id is not None
        plan_a = await container.external_channel_repository.get_delivery_plan(turn_a.delivery_id)
        assert plan_a is not None
        assert plan_a.status is ChannelDeliveryStatus.CANCELLED
        assert plan_a.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
        assert plan_a.parts[1].status is ChannelDeliveryPartStatus.CANCELLED
    finally:
        hold_send.set()
        await container.stop()


@pytest.mark.asyncio
async def test_terminal_ack_cleans_context_even_if_eventhub_publish_fails(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: ACK transaction succeeds, inject EventHub Publish failure
    -> Context still cleaned up, Durable Outbox retained."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)

    hold_send = asyncio.Event()
    send_started = asyncio.Event()
    original_send = transport.send_text

    async def _controlled_send(*args: Any, **kwargs: Any) -> str:
        send_started.set()
        await hold_send.wait()
        return await original_send(*args, **kwargs)

    monkeypatch.setattr(transport, "send_text", _controlled_send)
    container.external_channels.delivery_plan_factory = SingleTextDeliveryPlanFactory()

    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())
        await management.connection_configuration_changed(created.snapshot)

        # Ingest via update batch so context is recorded and turn is admitted
        await transport.updates.put(
            WeixinUpdates(
                cursor="c-fail-pub",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-fail-publish",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Test fail publish",
                        context_token="ctx-fail-pub",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait for delivery plan to be created
        turn: ChannelTurnRecord | None = None
        for _ in range(100):
            turn = await container.external_channel_repository.find_turn_by_external_message(
                connection_id, "msg-fail-publish"
            )
            if turn is not None and turn.delivery_id is not None:
                break
            await asyncio.sleep(0.1)
        assert turn is not None
        assert turn.delivery_id is not None

        # Verify context is stored
        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-fail-publish" in loaded.pending_contexts

        # Wait until send_text is entered (part has been claimed and is waiting on hold_send)
        await asyncio.wait_for(send_started.wait(), timeout=10.0)

        # Inject EventHub publish failure in event_publisher.publish_persisted
        async def _failing_publish_persisted(event: Any) -> None:
            raise RuntimeError("Injected EventHub failure")

        monkeypatch.setattr(
            container.event_publisher, "publish_persisted", _failing_publish_persisted
        )

        # Release send_text to let scheduler execute and ACK the part
        hold_send.set()
        scheduler = management.get_scheduler(connection_id)
        if scheduler is not None:
            scheduler.wake()

        # Context must still be cleaned up via finally block
        for _ in range(100):
            raw = await store.get(f"weixin_ilink:{connection_id}")
            if raw is not None:
                loaded = WeixinCredentials.from_json(raw)
                if "msg-fail-publish" not in loaded.pending_contexts:
                    break
            if scheduler is not None:
                scheduler.wake()
            await asyncio.sleep(0.1)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-fail-publish" not in loaded.pending_contexts

        # Durable Outbox event must be in DB and unpublished
        async with container.database.transaction() as conn:
            cursor = await conn.execute(
                "SELECT e.event_id, o.published_at FROM events e "
                "JOIN outbox o ON o.event_id = e.event_id "
                "WHERE e.event_type = 'channel.delivery_plan_completed' "
                "AND json_extract(e.payload_json, '$.delivery_id') = ?",
                (str(turn.delivery_id),),
            )
            rows = tuple(await cursor.fetchall())
            assert len(rows) == 1
            assert rows[0]["published_at"] is None
    finally:
        hold_send.set()
        await container.stop()


@pytest.mark.asyncio
async def test_startup_reconciliation_cleans_stale_contexts_after_crash(
    runtime_settings: Settings,
) -> None:
    """Requirement: Simulate crash after terminal commit
    -> restart connection reconciles and cleans stale context."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        receipt = await container.external_channels.ingest(
            ChannelInboundTextMessage(
                connection_id=connection_id,
                account_key="bot-1",
                external_message_id="msg-terminal",
                conversation_key="owner-1",
                sender_key="owner-1",
                principal_scope="local",
                chat_type=ChannelChatType.DIRECT,
                text="Terminal message",
                received_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        turn: ChannelTurnRecord | None = None
        for _ in range(50):
            turn = await container.external_channel_repository.get_turn(receipt.channel_turn_id)
            if turn is not None and turn.delivery_id is not None:
                break
            await asyncio.sleep(0.05)
        assert turn is not None and turn.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert plan is not None

        now = datetime.now(UTC)
        # Manually transition plan to DELIVERED in DB
        # to simulate terminal commit before context delete
        async with container.database.transaction() as conn:
            await conn.execute(
                "UPDATE channel_deliveries SET status = 'delivered' WHERE delivery_id = ?",
                (str(plan.delivery_id),),
            )
            await conn.execute(
                (
                    "UPDATE channel_delivery_parts SET status = 'delivered',"
                    " delivered_at = ? WHERE delivery_id = ?"
                ),
                (now.isoformat(), str(plan.delivery_id)),
            )

        # Ingest active message
        await container.external_channels.ingest(
            ChannelInboundTextMessage(
                connection_id=connection_id,
                account_key="bot-1",
                external_message_id="msg-active",
                conversation_key="owner-1",
                sender_key="owner-1",
                principal_scope="local",
                chat_type=ChannelChatType.DIRECT,
                text="Active message",
                received_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )

        creds = WeixinCredentials(
            bot_token="provider-token",
            bot_id="bot-1",
            user_id="owner-1",
            base_url="https://api.weixin.qq.com/",
            gateway_access_token=access_token,
            pending_contexts={
                "msg-terminal": WeixinPendingContext(
                    context_token="ctx-term", recipient_user_id="owner-1"
                ),
                "msg-ghost": WeixinPendingContext(
                    context_token="ctx-ghost", recipient_user_id="owner-1"
                ),
                "msg-active": WeixinPendingContext(
                    context_token="ctx-active", recipient_user_id="owner-1"
                ),
            },
        )
        await store.set(f"weixin_ilink:{connection_id}", creds.to_json())

        # Trigger reconciliation directly
        await management.reconcile_pending_contexts(connection_id)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-terminal" not in loaded.pending_contexts
        assert "msg-ghost" not in loaded.pending_contexts
        assert "msg-active" in loaded.pending_contexts
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_twenty_consecutive_cancellations_never_hit_context_limit(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: Run 20 iterations of generate then cancel by new message
    -> never hit 16 context limit."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)

    async def _delayed_send(*args: Any, **kwargs: Any) -> str:
        await asyncio.sleep(0.5)
        return "fake-provider-id"

    monkeypatch.setattr(transport, "send_text", _delayed_send)

    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())
        await management.connection_configuration_changed(created.snapshot)

        for i in range(20):
            msg_id = f"msg-burst-{i}"
            await transport.updates.put(
                WeixinUpdates(
                    cursor=f"cursor-{i}",
                    messages=(
                        WeixinInboundText(
                            external_message_id=msg_id,
                            sender_user_id="owner-1",
                            recipient_bot_id="bot-1",
                            text=f"Burst message {i}",
                            context_token=f"ctx-token-{i}",
                            received_at=datetime.now(UTC),
                        ),
                    ),
                )
            )
            await asyncio.sleep(0.08)
            raw = await store.get(f"weixin_ilink:{connection_id}")
            assert raw is not None
            loaded = WeixinCredentials.from_json(raw)
            assert len(loaded.pending_contexts) < 16, (
                f"Contexts exceeded limit at iteration {i}: {len(loaded.pending_contexts)}"
            )

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert len(loaded.pending_contexts) <= 4
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_terminal_event_replay_is_idempotent_for_context_cleanup(
    runtime_settings: Settings,
) -> None:
    """Requirement: Terminal events replayed multiple times must be strictly idempotent
    and not crash or corrupt pending_contexts."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)

    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        creds = WeixinCredentials(
            bot_token="provider-token",
            bot_id="bot-1",
            user_id="owner-1",
            base_url="https://api.weixin.qq.com/",
            gateway_access_token=access_token,
            pending_contexts={
                "msg-idempotent": WeixinPendingContext(
                    context_token="ctx-idempotent", recipient_user_id="owner-1"
                ),
            },
        )
        await store.set(f"weixin_ilink:{connection_id}", creds.to_json())
        await management.connection_configuration_changed(created.snapshot)

        receipt = await container.external_channels.ingest(
            ChannelInboundTextMessage(
                connection_id=connection_id,
                account_key="bot-1",
                external_message_id="msg-idempotent",
                conversation_key="owner-1",
                sender_key="owner-1",
                principal_scope="local",
                chat_type=ChannelChatType.DIRECT,
                text="Idempotent test message",
                received_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )

        turn = await container.external_channel_repository.get_turn(receipt.channel_turn_id)
        assert turn is not None

        # Pause scheduler so context is deleted exclusively by manual terminal event dispatch
        sched = management.get_scheduler(connection_id)
        if sched is not None:
            await sched.stop()

        # Ensure context is present in Keyring right before dispatch
        await store.set(f"weixin_ilink:{connection_id}", creds.to_json())
        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-idempotent" in loaded.pending_contexts

        delivery_id = turn.delivery_id or uuid4()
        terminal_event: dict[str, object] = {
            "event_type": "channel.delivery_plan_completed",
            "payload": {
                "connection_id": str(connection_id),
                "channel_turn_id": str(turn.channel_turn_id),
                "delivery_id": str(delivery_id),
                "external_message_id": "msg-idempotent",
            },
        }

        # First delivery plan terminal event: cleans up msg-idempotent
        await management._on_delivery_plan_terminal_event(terminal_event)
        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-idempotent" not in loaded.pending_contexts

        # Replay the exact same event 3 more times: idempotent and no error
        for _ in range(3):
            await management._on_delivery_plan_terminal_event(terminal_event)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-idempotent" not in loaded.pending_contexts

        # Replaying event for non-existent turn/connection must be a safe no-op
        ghost_event: dict[str, object] = {
            "event_type": "channel.delivery_plan_cancelled",
            "payload": {
                "connection_id": str(uuid4()),
                "channel_turn_id": str(uuid4()),
                "delivery_id": str(uuid4()),
            },
        }
        await management._on_delivery_plan_terminal_event(ghost_event)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_generation_failure_before_plan_cleans_pending_context(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path A: Generation fails before DeliveryPlan created
    -> ChannelTurn=FAILED -> Context cleaned."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)

    original_gen_result = container.conversation_repository.generation_result

    async def _failed_gen_result(generation_id: UUID) -> Any:
        res = await original_gen_result(generation_id)
        if res is not None:
            return replace(res, state=GenerationState.FAILED, error_code="llm_timeout")
        return None

    monkeypatch.setattr(container.conversation_repository, "generation_result", _failed_gen_result)

    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        creds = _credentials(access_token)
        await store.set(f"weixin_ilink:{connection_id}", creds.to_json())
        await management.connection_configuration_changed(created.snapshot)

        await transport.updates.put(
            WeixinUpdates(
                cursor="c-fail-gen",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-fail-gen",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Will fail generation",
                        context_token="ctx-fail-gen",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait until turn is admitted
        turn: ChannelTurnRecord | None = None
        for _ in range(100):
            turn = await container.external_channel_repository.find_turn_by_external_message(
                connection_id, "msg-fail-gen"
            )
            if turn is not None:
                break
            await asyncio.sleep(0.05)
        assert turn is not None

        # Wait until turn reaches terminal FAILED and context is cleaned up
        for _ in range(100):
            turn = await container.external_channel_repository.find_turn_by_external_message(
                connection_id, "msg-fail-gen"
            )
            if turn is not None and turn.status is ChannelTurnStatus.FAILED:
                raw = await store.get(f"weixin_ilink:{connection_id}")
                if raw is not None:
                    loaded = WeixinCredentials.from_json(raw)
                    if "msg-fail-gen" not in loaded.pending_contexts:
                        break
            await asyncio.sleep(0.05)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-fail-gen" not in loaded.pending_contexts

        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "msg-fail-gen"
        )
        assert turn is not None
        assert turn.status is ChannelTurnStatus.FAILED
        assert turn.delivery_id is None
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_replayed_already_terminal_message_cleans_context_immediately(
    runtime_settings: Settings,
) -> None:
    """Path B: Already completed message replayed by provider
    -> duplicate receipt -> Context deleted immediately."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)

    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        creds = _credentials(access_token)
        await store.set(f"weixin_ilink:{connection_id}", creds.to_json())
        await management.connection_configuration_changed(created.snapshot)

        # Ingest first time and let it deliver
        await transport.updates.put(
            WeixinUpdates(
                cursor="c-replay-1",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-replay",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Replay me",
                        context_token="ctx-replay-1",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait until delivery is DELIVERED and context cleaned
        for _ in range(100):
            raw = await store.get(f"weixin_ilink:{connection_id}")
            if raw is not None:
                loaded = WeixinCredentials.from_json(raw)
                if "msg-replay" not in loaded.pending_contexts:
                    break
            await asyncio.sleep(0.1)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-replay" not in loaded.pending_contexts

        # Now simulate provider replaying the same message (at-least-once replay)
        await transport.updates.put(
            WeixinUpdates(
                cursor="c-replay-2",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-replay",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Replay me",
                        context_token="ctx-replay-2",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Context must be immediately cleaned on duplicate detection
        for _ in range(50):
            raw = await store.get(f"weixin_ilink:{connection_id}")
            if raw is not None:
                loaded = WeixinCredentials.from_json(raw)
                if "msg-replay" not in loaded.pending_contexts:
                    break
            await asyncio.sleep(0.05)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-replay" not in loaded.pending_contexts
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_cancel_requested_sending_part_lease_expiration_cleans_context(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path C: SENDING part under cancel request expires
    -> Recovery makes plan CANCELLED -> Context cleaned."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)

    hang_send = asyncio.Event()

    async def _hung_send(*args: Any, **kwargs: Any) -> str:
        await hang_send.wait()
        return "hung-msg-id"

    monkeypatch.setattr(transport, "send_text", _hung_send)

    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        creds = _credentials(access_token)
        await store.set(f"weixin_ilink:{connection_id}", creds.to_json())
        await management.connection_configuration_changed(created.snapshot)

        await transport.updates.put(
            WeixinUpdates(
                cursor="c-lease-exp",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-lease-exp",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Lease expire test",
                        context_token="ctx-lease-exp",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait for delivery plan to be created and claimed (SENDING)
        turn: ChannelTurnRecord | None = None
        for _ in range(100):
            turn = await container.external_channel_repository.find_turn_by_external_message(
                connection_id, "msg-lease-exp"
            )
            if turn is not None and turn.delivery_id is not None:
                plan = await container.external_channel_repository.get_delivery_plan(
                    turn.delivery_id
                )
                if plan is not None and plan.status is ChannelDeliveryStatus.SENDING:
                    break
            await asyncio.sleep(0.1)

        assert turn is not None
        assert turn.delivery_id is not None

        # Request cancellation of the delivery plan
        await container.external_channels.cancel_remaining_delivery_parts(
            connection_id,
            turn.delivery_id,
            ChannelDeliveryPartsCancelRequest(
                reason="User cancelled", requested_at=datetime.now(UTC)
            ),
            access_token=access_token,
        )

        # Advance past lease_expires_at in SQLite
        expired_time = datetime.now(UTC) - timedelta(seconds=10)
        async with container.database.transaction() as conn:
            await conn.execute(
                "UPDATE channel_delivery_parts SET lease_expires_at = ? WHERE delivery_id = ?",
                (expired_time.isoformat(), str(turn.delivery_id)),
            )

        # Trigger scheduler step or recovery
        scheduler = management.get_scheduler(connection_id)
        assert scheduler is not None
        await scheduler.step()

        # Context must be cleaned up via on_plan_terminal and Outbox event
        for _ in range(50):
            raw = await store.get(f"weixin_ilink:{connection_id}")
            if raw is not None:
                loaded = WeixinCredentials.from_json(raw)
                if "msg-lease-exp" not in loaded.pending_contexts:
                    break
            await asyncio.sleep(0.05)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-lease-exp" not in loaded.pending_contexts

        plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert plan is not None
        assert plan.status is ChannelDeliveryStatus.CANCELLED
    finally:
        hang_send.set()
        await container.stop()


@pytest.mark.asyncio
async def test_sending_part_retryable_error_under_cancel_request_cancels_plan_and_cleans_context(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: SENDING + cancel_requested + RETRYABLE_ERROR
    -> defer -> CANCELLED -> Context cleaned up without reboot, durable event published."""
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)

    class _TwoPartFactory:
        def create_parts(
            self,
            reply_text: str,
            policy: ChannelPresentationPolicy | None = None,
        ) -> tuple[ChannelDeliveryPartDraft, ...]:
            return (
                ChannelDeliveryPartDraft(
                    ordinal=0,
                    kind=ChannelDeliveryPartKind.TEXT,
                    payload=ChannelTextDeliveryPartPayload(text="Part 0"),
                    required=True,
                ),
                ChannelDeliveryPartDraft(
                    ordinal=1,
                    kind=ChannelDeliveryPartKind.TEXT,
                    payload=ChannelTextDeliveryPartPayload(text="Part 1"),
                    required=True,
                ),
            )

    container.external_channels.delivery_plan_factory = _TwoPartFactory()

    hold_send = asyncio.Event()
    send_started = asyncio.Event()

    async def _failing_retryable_send(*args: Any, **kwargs: Any) -> str:
        send_started.set()
        await hold_send.wait()
        raise WeixinILinkError(
            "network_timeout", "Temporary upstream network timeout", retryable=True
        )

    monkeypatch.setattr(transport, "send_text", _failing_retryable_send)

    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        creds = _credentials(access_token)
        await store.set(f"weixin_ilink:{connection_id}", creds.to_json())
        await management.connection_configuration_changed(created.snapshot)

        # Message A arrives
        await transport.updates.put(
            WeixinUpdates(
                cursor="c1",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-retryable-cancel",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Message A multi-part",
                        context_token="ctx-retryable-cancel",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait until Part 0 is in SENDING and hits our mock send_text
        await asyncio.wait_for(send_started.wait(), timeout=5.0)

        # Context A must be in keyring
        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-retryable-cancel" in loaded.pending_contexts

        # Message B arrives on same binding -> triggers cancel_remaining_delivery_parts on Plan A
        await transport.updates.put(
            WeixinUpdates(
                cursor="c2",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-interrupt-b",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="Message B interrupting",
                        context_token="ctx-interrupt-b",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait until Message B is admitted
        turn_b: ChannelTurnRecord | None = None
        for _ in range(50):
            turn_b = await container.external_channel_repository.find_turn_by_external_message(
                connection_id, "msg-interrupt-b"
            )
            if turn_b is not None:
                break
            await asyncio.sleep(0.05)
        assert turn_b is not None

        # Release send_text to raise the retryable error
        hold_send.set()

        # Context A must be cleaned up without reboot
        for _ in range(100):
            raw = await store.get(f"weixin_ilink:{connection_id}")
            if raw is not None:
                loaded = WeixinCredentials.from_json(raw)
                if "msg-retryable-cancel" not in loaded.pending_contexts:
                    break
            await asyncio.sleep(0.05)

        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None
        loaded = WeixinCredentials.from_json(raw)
        assert "msg-retryable-cancel" not in loaded.pending_contexts

        # Assert Parent A and all parts are CANCELLED
        turn_a = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "msg-retryable-cancel"
        )
        assert turn_a is not None
        assert turn_a.delivery_id is not None
        plan_a = await container.external_channel_repository.get_delivery_plan(turn_a.delivery_id)
        assert plan_a is not None
        assert plan_a.status is ChannelDeliveryStatus.CANCELLED
        assert all(p.status is ChannelDeliveryPartStatus.CANCELLED for p in plan_a.parts)

        # Assert exactly one channel.delivery_plan_cancelled event in EventStore/Outbox
        async with container.database.transaction() as conn:
            cursor = await conn.execute(
                "SELECT e.event_id, o.published_at FROM events e "
                "JOIN outbox o ON o.event_id = e.event_id "
                "WHERE e.event_type = 'channel.delivery_plan_cancelled' "
                "AND json_extract(e.payload_json, '$.delivery_id') = ?",
                (str(plan_a.delivery_id),),
            )
            rows = tuple(await cursor.fetchall())
            assert len(rows) == 1
    finally:
        hold_send.set()
        await container.stop()
