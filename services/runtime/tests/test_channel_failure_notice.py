# pyright: reportPrivateUsage=false
"""Durable system notices and native inbound preemption after model failures."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartStatus,
    ChannelTurnStatus,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from test_external_channels import _configuration, _message


@pytest.mark.asyncio
async def test_failed_generation_notice_survives_restart_and_replay(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)

    async def fail(request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        raise RuntimeError("temporary model failure")
        yield LlmTextDelta("")

    monkeypatch.setattr(container.model_configurations.chat, "stream", fail)
    await container.start()
    connection_id = uuid4()
    created = await container.external_channels.create_connection(_configuration(connection_id))
    message = _message(connection_id)
    try:
        receipt = await container.external_channels.ingest(
            message, access_token=created.access_token
        )
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert snapshot.status is ChannelTurnStatus.FAILED
        assert snapshot.reply_text is None
        assert snapshot.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(snapshot.delivery_id)
        assert plan is not None and len(plan.parts) == 1
        assert plan.parts[0].payload.text.startswith("【系统提示】")
        stable_id = plan.parts[0].provider_client_id
        generation = await container.conversation_repository.generation_result(
            receipt.generation_id
        )
        assert generation is not None and generation.output_text is None
        events = await container.event_store.read_stream(receipt.session_id, limit=200)
        assert not any(e["event_type"] == "assistant.text_committed" for e in events)
    finally:
        await container.stop()
    recovered = RuntimeContainer(runtime_settings)
    await recovered.start()
    try:
        replay = await recovered.external_channels.ingest(
            message, access_token=created.access_token
        )
        assert replay.duplicate and replay.channel_turn_id == receipt.channel_turn_id
        plan = await recovered.external_channel_repository.get_delivery_plan(snapshot.delivery_id)
        assert plan is not None and plan.parts[0].provider_client_id == stable_id
        claim = await recovered.external_channel_repository.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=plan.delivery_id, lease_id=uuid4(), lease_seconds=30
            ),
            claimed_at=datetime.now(UTC),
        )
        assert claim is not None and claim.part is not None and claim.part.lease_id is not None
        ack = ChannelDeliveryPartAcknowledgement(
            delivery_id=plan.delivery_id,
            part_id=claim.part.part_id,
            lease_id=claim.part.lease_id,
            status=ChannelDeliveryPartStatus.DELIVERED,
            acknowledged_at=datetime.now(UTC),
        )
        await recovered.external_channel_repository.acknowledge_delivery_part(
            ack, updated_at=datetime.now(UTC)
        )
        await recovered.external_channel_repository.acknowledge_delivery_part(
            ack, updated_at=datetime.now(UTC)
        )
        assert (
            await recovered.external_channel_repository.claim_next_delivery_part(
                ChannelDeliveryPartClaimRequest(
                    delivery_id=plan.delivery_id, lease_id=uuid4(), lease_seconds=30
                ),
                claimed_at=datetime.now(UTC),
            )
            is None
        )
    finally:
        await recovered.stop()


@pytest.mark.asyncio
async def test_new_inbound_cancels_generation_and_no_old_failure_notice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def stream(request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if request.user_text == "old":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        yield LlmTextDelta("收到。")
        yield LlmResponseCompleted("stop")

    monkeypatch.setattr(container.model_configurations.chat, "stream", stream)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        old_message = _message(connection_id, text="old")
        old = await container.external_channels.ingest(
            old_message, access_token=created.access_token, supersede_inflight=True
        )
        await asyncio.wait_for(entered.wait(), 3)
        duplicate = await container.external_channels.ingest(
            old_message, access_token=created.access_token, supersede_inflight=True
        )
        assert duplicate.duplicate and not cancelled.is_set()
        new = await container.external_channels.ingest(
            _message(connection_id, external_message_id="new", text="停一下"),
            access_token=created.access_token,
            supersede_inflight=True,
        )
        assert cancelled.is_set()
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            new.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert snapshot.status is ChannelTurnStatus.COMPLETED
        previous = await container.external_channel_repository.get_turn(old.channel_turn_id)
        assert previous is not None and previous.status is ChannelTurnStatus.CANCELLED
        assert previous.delivery_id is None
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_native_notice_keeps_context_until_ack_and_then_cleans(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
        WeixinCredentials,
        WeixinInboundText,
        WeixinUpdates,
    )
    from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
    from test_channel_management import _configuration as native_configuration
    from test_channel_management import _credentials, _FakeWeixin, _replace_management

    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    sending, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Transport(_FakeWeixin):
        async def send_text(
            self,
            credentials: WeixinCredentials,
            *,
            recipient_user_id: str,
            context_token: str,
            client_id: str,
            text: str,
        ) -> str:
            assert context_token == "failure-context"
            assert text.startswith("【系统提示】")
            sending.set()
            await release.wait()
            return await super().send_text(
                credentials,
                recipient_user_id=recipient_user_id,
                context_token=context_token,
                client_id=client_id,
                text=text,
            )

    transport = Transport()
    management = _replace_management(container, store, transport)
    original_forget = management._forget_context

    async def forget(connection_id: UUID, message_id: str) -> None:
        await original_forget(connection_id, message_id)
        if message_id == "failure":
            cleaned.set()

    monkeypatch.setattr(management, "_forget_context", forget)

    async def fail(request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        raise RuntimeError("model unavailable")
        yield LlmTextDelta("")

    monkeypatch.setattr(container.model_configurations.chat, "stream", fail)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(
            native_configuration(connection_id)
        )
        await store.set(
            f"weixin_ilink:{connection_id}", _credentials(created.access_token).to_json()
        )
        await management.connection_configuration_changed(created.snapshot)
        update = WeixinUpdates(
            cursor="failure-cursor",
            messages=(
                WeixinInboundText(
                    external_message_id="failure",
                    sender_user_id="owner-1",
                    recipient_bot_id="bot-1",
                    text="hello",
                    context_token="failure-context",
                    received_at=datetime.now(UTC),
                ),
            ),
        )
        await transport.updates.put(update)
        await asyncio.wait_for(sending.wait(), 5)
        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert raw is not None and "failure" in WeixinCredentials.from_json(raw).pending_contexts
        assert not cleaned.is_set()
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "failure"
        )
        assert turn is not None and turn.delivery_id is not None
        await management._on_delivery_plan_terminal_event(
            {
                "event_type": "channel.turn_failed",
                "payload": {
                    "channel_turn_id": str(turn.channel_turn_id),
                    "connection_id": str(connection_id),
                },
            }
        )
        assert not cleaned.is_set()
        release.set()
        await asyncio.wait_for(cleaned.wait(), 5)
        assert len(transport.sent_messages) == 1
        raw = await store.get(f"weixin_ilink:{connection_id}")
        assert (
            raw is not None and "failure" not in WeixinCredentials.from_json(raw).pending_contexts
        )
        await management._process_updates(connection_id, update)
        assert len(transport.sent_messages) == 1
    finally:
        release.set()
        await container.stop()


@pytest.mark.asyncio
async def test_new_message_cancels_unsent_failure_notice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatwaifu_protocol.channels import ChannelDeliveryStatus

    container = RuntimeContainer(runtime_settings)

    async def stream(request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if request.user_text == "fail":
            raise RuntimeError("model unavailable")
        yield LlmTextDelta("收到。")
        yield LlmResponseCompleted("stop")

    monkeypatch.setattr(container.model_configurations.chat, "stream", stream)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        old = await container.external_channels.ingest(
            _message(connection_id, text="fail"), access_token=created.access_token
        )
        failed = await container.external_channels.wait_for_turn(
            connection_id,
            old.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert failed.delivery_id is not None
        await container.external_channels.ingest(
            _message(connection_id, external_message_id="next", text="hello"),
            access_token=created.access_token,
            supersede_inflight=True,
        )
        plan = await container.external_channel_repository.get_delivery_plan(failed.delivery_id)
        assert plan is not None and plan.status is ChannelDeliveryStatus.CANCELLED
        assert plan.parts[0].attempt == 0
        assert (
            await container.external_channel_repository.claim_next_delivery_part(
                ChannelDeliveryPartClaimRequest(
                    delivery_id=plan.delivery_id, lease_id=uuid4(), lease_seconds=30
                ),
                claimed_at=datetime.now(UTC),
            )
            is None
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_cancellation_wins_race_before_notice_transaction(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatwaifu_protocol.errors import StructuredError
    from chatwaifu_runtime.external_channels.models import CompleteTurnResult

    container = RuntimeContainer(runtime_settings)
    original = container.external_channel_repository.fail_turn_with_notice

    async def race(
        channel_turn_id: UUID,
        *,
        error: StructuredError,
        notice_text: str,
        delivery_id: UUID,
        completed_at: datetime,
    ) -> CompleteTurnResult:
        await container.external_channel_repository.set_turn_cancelling(
            channel_turn_id,
            updated_at=completed_at,
        )
        return await original(
            channel_turn_id,
            error=error,
            notice_text=notice_text,
            delivery_id=delivery_id,
            completed_at=completed_at,
        )

    async def fail(request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        raise RuntimeError("model unavailable")
        yield LlmTextDelta("")

    monkeypatch.setattr(container.model_configurations.chat, "stream", fail)
    monkeypatch.setattr(container.external_channel_repository, "fail_turn_with_notice", race)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        receipt = await container.external_channels.ingest(
            _message(connection_id), access_token=created.access_token
        )
        result = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert result.status is ChannelTurnStatus.CANCELLED
        assert result.delivery_id is None
        events = await container.event_store.read_stream(receipt.session_id, limit=200)
        assert any(e["event_type"] == "channel.turn_cancelled" for e in events)
        assert not any(e["event_type"] == "channel.turn_failed" for e in events)
    finally:
        await container.stop()
