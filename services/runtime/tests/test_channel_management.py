"""Native channel authorization, credential, and cancellation lifecycle tests."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelAuthorizationMethod,
    ChannelAuthorizationStartRequest,
    ChannelAuthorizationStatus,
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelInboundTextMessage,
)
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
from chatwaifu_runtime.external_channels.scheduler import ChannelDeliveryScheduler
from chatwaifu_runtime.external_channels.service import ChannelConflictError, ChannelNotFoundError


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
