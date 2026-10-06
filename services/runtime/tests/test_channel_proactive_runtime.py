"""Owner proactive text uses real SQLite, Conversation and a local OneBot socket."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channel_proactive import (
    ChannelOutboundIntentCancelRequest,
    ChannelOutboundIntentStatus,
    ChannelProactivePolicy,
    ChannelProactivePolicyUpdate,
    ChannelProactiveReason,
)
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryStatus,
    ChannelTurnReceipt,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationTurnOptions
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPlanRecord,
    DeliveryTransitionResult,
)
from chatwaifu_runtime.external_channels.proactive import ChannelProactiveService, _source_context
from chatwaifu_runtime.external_channels.proactive_models import (
    ChannelOutboundIntentRecord,
    ChannelOutboundReservationResult,
    ChannelProactivePolicyRecord,
)
from chatwaifu_runtime.persistence.sqlite_channel_proactive import SQLiteChannelProactiveRepository
from test_qq_channels import (
    TEXT_REPLY,
    _Harness,
    _ingest,
    _pair,
    _runtime,
    _segments,
    _terminal,
)


@dataclass
class Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Harness:
    qq: _Harness
    service: ChannelProactiveService
    repository: SQLiteChannelProactiveRepository
    clock: Clock
    connection_id: UUID


async def owner_activity(
    qq: _Harness, connection_id: UUID, text: str, message_id: int
) -> ChannelTurnReceipt:
    subscription = qq.container.event_hub.subscribe(
        lambda event: event.get("event_type") == "channel.delivery_part_delivered", queue_size=16
    )
    try:
        turn = await _ingest(qq, connection_id, text, message_id)
        await _terminal(qq, connection_id, turn.channel_turn_id)
        await asyncio.wait_for(qq.peer.sends.get(), 3)
        await asyncio.wait_for(subscription.receive(), 3)
        return turn
    finally:
        subscription.close()


@asynccontextmanager
async def configured(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[Harness]:
    async with _runtime(settings, monkeypatch) as qq:
        connection_id = await _pair(qq)
        await owner_activity(qq, connection_id, "主人活动", 10)
        clock = Clock(datetime.now(UTC))
        monkeypatch.setattr(qq.container.qq_channels._schedulers[connection_id], "_clock", clock)
        repository = qq.container.channel_proactive_repository
        service = qq.container.channel_proactive
        await service.stop()
        monkeypatch.setattr(service, "_clock", clock)

        # Tests drive exact ticks; no wall-clock sleeps or ambient races.
        async def blocked_scheduler() -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(service, "_run", blocked_scheduler)
        qq.container.external_channels.set_proactive_service(service)
        await service.start()
        try:
            yield Harness(qq, service, repository, clock, connection_id)
        finally:
            await service.stop()


async def enable_and_anchor(harness: Harness) -> None:
    await harness.service.update_policy(
        harness.connection_id,
        ChannelProactivePolicyUpdate(
            expected_revision=0,
            policy=ChannelProactivePolicy(
                enabled=True,
                idle_minutes=1,
                cooldown_minutes=1,
                daily_budget=2,
                quiet_hours_enabled=False,
                ttl_minutes=1,
            ),
        ),
    )
    assert (
        await harness.service.preview(harness.connection_id)
    ).reason is ChannelProactiveReason.NO_OWNER_ACTIVITY
    turn = await owner_activity(harness.qq, harness.connection_id, "保存后的主人活动", 11)
    record = await harness.qq.container.external_channel_repository.get_turn(turn.channel_turn_id)
    assert record is not None
    harness.clock.now = record.accepted_at + timedelta(minutes=1, seconds=1)


async def planned(harness: Harness) -> UUID:
    hub = harness.qq.container.event_hub

    def matches(event: dict[str, object]) -> bool:
        payload = event.get("payload")
        return (
            event.get("event_type") == "channel.delivery_plan_created"
            and isinstance(payload, dict)
            and cast(dict[str, object], payload).get("outbound_intent_id") is not None
        )

    subscription = hub.subscribe(matches, queue_size=16)
    try:
        assert await harness.service.evaluate_once() == 1
        event = await asyncio.wait_for(subscription.receive(), 3)
    finally:
        subscription.close()
    payload = cast(dict[str, object], event["payload"])
    return UUID(str(payload["outbound_intent_id"]))


async def test_default_off_preview_and_new_owner_episode_delivers_single_tool_free_text(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        before = len(h.qq.model.requests)
        snapshot = await h.service.get_policy(h.connection_id)
        assert (
            snapshot.revision == 0
            and not snapshot.policy.enabled
            and snapshot.binding_id is not None
        )
        assert (await h.service.preview(h.connection_id)).reason is ChannelProactiveReason.DISABLED
        assert await h.service.evaluate_once() == 0
        assert (await h.service.list_intents(h.connection_id)).items == []
        assert len(h.qq.model.requests) == before
        await enable_and_anchor(h)
        h.qq.model.voice_decision = True
        request_id = await planned(h)
        sent = await asyncio.wait_for(h.qq.peer.sends.get(), 3)
        assert _segments(sent) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
        proactive = [request for request in h.qq.model.requests if request.trigger == "proactive"]
        assert len(proactive) == 1 and proactive[0].tools == ()
        assert h.qq.synthesis == []
        intent = await h.repository.get_intent(request_id)
        assert intent is not None and intent.delivery_id is not None
        plan = await h.qq.container.external_channel_repository.get_delivery_plan(
            intent.delivery_id
        )
        assert (
            plan is not None
            and plan.channel_turn_id is None
            and plan.outbound_intent_id == request_id
        )
        assert len(plan.parts) == 1 and plan.parts[0].kind.value == "text"
        result = await h.qq.container.conversation_repository.generation_result(
            intent.generation_id
        )
        assert result is not None and result.turn_id == intent.turn_id
        assert result.audio_stream_id == intent.audio_stream_id
        assert await h.service.evaluate_once() == 0
        assert len([r for r in h.qq.model.requests if r.trigger == "proactive"]) == 1


@pytest.mark.parametrize("swallow_cancel", [False, True])
async def test_owner_input_cancels_prepare_and_late_return_without_proactive_model_call(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    swallow_cancel: bool,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = h.qq.container.memory.retrieve_context

        async def blocked(
            sid: UUID,
            tid: UUID,
            character: str,
            query: str,
            *,
            token_budget: int = 700,
            limit: int = 12,
        ) -> MemoryContextPacket:
            if query == "轻声主动关心用户":
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    if not swallow_cancel:
                        raise
                    await release.wait()
            return await original(
                sid, tid, character, query, token_budget=token_budget, limit=limit
            )

        monkeypatch.setattr(h.qq.container.memory, "retrieve_context", blocked)
        assert await h.service.evaluate_once() == 1
        await asyncio.wait_for(entered.wait(), 3)
        new_owner = asyncio.create_task(_ingest(h.qq, h.connection_id, "现在请回复文字", 12))
        await asyncio.wait_for(cancelled.wait(), 3)
        release.set()
        turn = await asyncio.wait_for(new_owner, 3)
        await _terminal(h.qq, h.connection_id, turn.channel_turn_id)
        assert not [r for r in h.qq.model.requests if r.trigger == "proactive"]
        page = await h.service.list_intents(h.connection_id)
        assert len(page.items) == 1 and page.items[0].status is ChannelOutboundIntentStatus.SETTLED
        result = await h.qq.container.conversation_repository.generation_result(
            page.items[0].generation_id
        )
        assert result is not None and result.state is GenerationState.CANCELLED


@pytest.mark.parametrize("action", ["stop", "owner_input"])
async def test_lifecycle_during_durable_reservation_return_gap_does_not_start_model(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        committed, release = asyncio.Event(), asyncio.Event()
        original = h.repository.reserve_intent

        async def reserve(
            connection_id: UUID, *, as_of: datetime, generation_active: bool = False
        ) -> ChannelOutboundReservationResult:
            result = await original(connection_id, as_of=as_of, generation_active=generation_active)
            if result.created:
                committed.set()
                await release.wait()
            return result

        monkeypatch.setattr(h.repository, "reserve_intent", reserve)
        evaluation = asyncio.create_task(h.service.evaluate_once())
        try:
            await asyncio.wait_for(committed.wait(), 15)
            if action == "stop":
                await asyncio.wait_for(h.service.stop(), 15)
            else:
                await owner_activity(h.qq, h.connection_id, "主人刚发来新消息", 13)
            release.set()
            assert await asyncio.wait_for(evaluation, 15) == 0
            assert h.service.active_count == 0
            assert not [r for r in h.qq.model.requests if r.trigger == "proactive"]
            page = await h.service.list_intents(h.connection_id)
            assert (
                len(page.items) == 1 and page.items[0].status is ChannelOutboundIntentStatus.SETTLED
            )
        finally:
            await h.service.stop()
            release.set()
            await asyncio.gather(evaluation, return_exceptions=True)


async def test_durable_audio_preprocessing_defers_proactive_generation(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from chatwaifu_runtime.external_channels.adapters.qq_napcat.management import (
        credential_reference,
    )
    from test_channel_audio_ingress import _Audio, _message

    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        audio = _Audio()
        raw = await h.qq.credentials.get(credential_reference(h.connection_id))
        assert raw is not None
        receipt = await h.qq.container.external_channels.ingest(
            _message(h.connection_id, 81),
            access_token=str(json.loads(raw)["gateway_token"]),
            audio_input=audio.input(),
            supersede_inflight=True,
        )
        try:
            await asyncio.wait_for(audio.entered.wait(), 3)
            assert h.qq.container.external_channels.active_preprocessing_count == 1
            assert h.qq.container.conversation.active_generation_id(receipt.session_id) is None
            assert (await h.service.preview(h.connection_id)).reason is (
                ChannelProactiveReason.CONVERSATION_BUSY
            )
            assert await h.service.evaluate_once() == 0
            assert not [r for r in h.qq.model.requests if r.trigger == "proactive"]
            assert (await h.service.list_intents(h.connection_id)).items == []
        finally:
            audio.release.set()
            await _terminal(h.qq, h.connection_id, receipt.channel_turn_id)


async def test_scope_reset_fences_planned_delivery_and_old_owner_anchor_after_restart(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        scheduler = h.qq.container.qq_channels._schedulers[h.connection_id]
        await scheduler.stop()
        request_id = await planned(h)
        intent = await h.repository.get_intent(request_id)
        assert intent is not None
        subscription = h.qq.container.event_hub.subscribe(
            lambda event: event.get("event_type") == "channel.outbound_intent_settled",
            queue_size=8,
        )
        try:
            await h.qq.container.conversation.reset(intent.session_id)
            await asyncio.wait_for(subscription.receive(), 3)
        finally:
            subscription.close()
        updated = await h.repository.get_intent(request_id)
        assert updated is not None and updated.status is ChannelOutboundIntentStatus.SETTLED
        assert not (await h.repository.authorize_intent(request_id, as_of=h.clock())).allowed
        assert h.qq.peer.sends.empty()
        before = len(h.qq.model.requests)
        await h.service.stop()
        await h.service.start()
        assert await h.service.evaluate_once() == 0
        assert len(h.qq.model.requests) == before
        assert (await h.service.preview(h.connection_id)).reason is (
            ChannelProactiveReason.NO_OWNER_ACTIVITY
        )


@pytest.mark.parametrize("boundary", ["generation_claim", "part_claim"])
async def test_expired_claim_publishes_durable_settlement_without_provider_send(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        subscription = h.qq.container.event_hub.subscribe(
            lambda event: event.get("event_type") == "channel.outbound_intent_settled",
            queue_size=8,
        )
        scheduler = h.qq.container.qq_channels._schedulers[h.connection_id]
        if boundary == "generation_claim":
            original = h.repository.reserve_intent

            async def reserve(
                connection_id: UUID, *, as_of: datetime, generation_active: bool = False
            ) -> ChannelOutboundReservationResult:
                result = await original(
                    connection_id, as_of=as_of, generation_active=generation_active
                )
                if result.created and result.intent is not None:
                    h.clock.now = result.intent.expires_at
                return result

            monkeypatch.setattr(h.repository, "reserve_intent", reserve)
        else:
            await scheduler.stop()
            request_id = await planned(h)
            intent = await h.repository.get_intent(request_id)
            assert intent is not None

            async def advance_at_claim(_plan: ChannelDeliveryPlanRecord) -> bool:
                h.clock.now = intent.expires_at
                return True

            monkeypatch.setattr(scheduler, "_before_claim", advance_at_claim)
        try:
            if boundary == "generation_claim":
                assert await h.service.evaluate_once() == 1
            else:
                assert not await scheduler.step()
            event = await asyncio.wait_for(subscription.receive(), 3)
            payload = cast(dict[str, object], event["payload"])
            updated = await h.repository.get_intent(UUID(str(payload["outbound_intent_id"])))
            assert updated is not None and updated.status is ChannelOutboundIntentStatus.SETTLED
            assert updated.settled_reason == ChannelProactiveReason.IDLE_WINDOW_EXPIRED.value
            assert h.qq.peer.sends.empty()
            if boundary == "generation_claim":
                assert not [r for r in h.qq.model.requests if r.trigger == "proactive"]
            else:
                assert updated.delivery_id is not None
                plan = await h.qq.container.external_channel_repository.get_delivery_plan(
                    updated.delivery_id
                )
                assert plan is not None and plan.status is ChannelDeliveryStatus.CANCELLED
                assert plan.parts[0].attempt == 0
        finally:
            subscription.close()


async def test_fixed_expiry_cancels_live_prepare_and_does_not_reserve_old_anchor_again(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        entered = asyncio.Event()
        original = h.qq.container.memory.retrieve_context

        async def blocked(
            sid: UUID,
            tid: UUID,
            character: str,
            query: str,
            *,
            token_budget: int = 700,
            limit: int = 12,
        ) -> MemoryContextPacket:
            if query == "轻声主动关心用户":
                entered.set()
                await asyncio.Event().wait()
            return await original(
                sid, tid, character, query, token_budget=token_budget, limit=limit
            )

        monkeypatch.setattr(h.qq.container.memory, "retrieve_context", blocked)
        assert await h.service.evaluate_once() == 1
        await asyncio.wait_for(entered.wait(), 3)
        page = await h.service.list_intents(h.connection_id)
        h.clock.now = page.items[0].expires_at
        assert await asyncio.wait_for(h.service.evaluate_once(), 3) == 0
        assert not [r for r in h.qq.model.requests if r.trigger == "proactive"]
        assert (await h.service.list_intents(h.connection_id)).items[
            0
        ].status is ChannelOutboundIntentStatus.SETTLED
        assert await h.service.evaluate_once() == 0


async def test_policy_revocation_after_account_preflight_blocks_actual_onebot_send(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        entered, release = asyncio.Event(), asyncio.Event()
        client = h.qq.container.qq_channels._clients[h.connection_id]
        original = client.call

        async def call(action: str, params: JsonObject) -> JsonObject:
            result = await original(action, params)
            if action == "get_login_info":
                entered.set()
                await release.wait()
            return result

        monkeypatch.setattr(client, "call", call)
        request_id = await planned(h)
        await asyncio.wait_for(entered.wait(), 3)
        policy = await h.service.get_policy(h.connection_id)
        await h.service.update_policy(
            h.connection_id,
            ChannelProactivePolicyUpdate(
                expected_revision=policy.revision,
                policy=policy.policy.model_copy(update={"enabled": False}),
            ),
        )
        release.set()
        # Scheduler's bounded step finishes the rejected preflight; no send RPC.
        await h.qq.container.qq_channels._reconcile_connection(h.connection_id)
        intent = await h.repository.get_intent(request_id)
        assert intent is not None and intent.status is ChannelOutboundIntentStatus.SETTLED
        assert not intent.provider_receipt_present
        assert h.qq.peer.sends.empty()


async def test_confirmed_send_is_reconciled_after_policy_cancel_without_another_send(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        entered, release = asyncio.Event(), asyncio.Event()
        deliveries = h.qq.container.external_channel_repository
        original = deliveries.acknowledge_delivery_part

        async def ack(
            request: ChannelDeliveryPartAcknowledgement, *, updated_at: datetime
        ) -> DeliveryTransitionResult:
            entered.set()
            await release.wait()
            return await original(request, updated_at=updated_at)

        monkeypatch.setattr(deliveries, "acknowledge_delivery_part", ack)
        request_id = await planned(h)
        await asyncio.wait_for(h.qq.peer.sends.get(), 3)
        await asyncio.wait_for(entered.wait(), 3)
        policy = await h.service.get_policy(h.connection_id)
        await h.service.update_policy(
            h.connection_id,
            ChannelProactivePolicyUpdate(
                expected_revision=policy.revision,
                policy=policy.policy.model_copy(update={"enabled": False}),
            ),
        )
        try:
            await h.qq.container.qq_channels._reconcile_connection(h.connection_id)
            intent = await h.repository.get_intent(request_id)
            assert intent is not None and intent.provider_receipt_present
            assert intent.delivery_status is ChannelDeliveryStatus.DELIVERED
            assert h.qq.peer.sends.empty()
        finally:
            release.set()
        assert await h.service.evaluate_once() == 0
        assert len([r for r in h.qq.model.requests if r.trigger == "proactive"]) == 1


async def test_unknown_provider_result_survives_service_restart_without_send_or_regeneration(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatUncertain

    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        client = h.qq.container.qq_channels._clients[h.connection_id]
        original = client.call

        async def lose_response(action: str, params: JsonObject) -> JsonObject:
            response = await original(action, params)
            if action == "send_private_msg":
                raise NapCatUncertain("fixture response lost")
            return response

        monkeypatch.setattr(client, "call", lose_response)
        subscription = h.qq.container.event_hub.subscribe(
            lambda event: event.get("event_type") == "channel.outbound_intent_settled",
            queue_size=8,
        )
        try:
            request_id = await planned(h)
            await asyncio.wait_for(h.qq.peer.sends.get(), 3)
            await asyncio.wait_for(subscription.receive(), 3)
        finally:
            subscription.close()
        updated = await h.repository.get_intent(request_id)
        assert updated is not None and updated.status is ChannelOutboundIntentStatus.SETTLED
        assert updated.delivery_id is not None and not updated.provider_receipt_present
        deliveries = h.qq.container.external_channel_repository
        plan = await deliveries.get_delivery_plan(updated.delivery_id)
        assert plan is not None and plan.parts[0].last_error is not None
        assert plan.parts[0].last_error.code == "qq_delivery_unknown"
        journal = json.loads(await deliveries.get_adapter_cursor(h.connection_id))
        assert journal[plan.parts[0].provider_client_id] == "unknown"
        before_calls, before_models = len(h.qq.peer.calls), len(h.qq.model.requests)
        await h.service.stop()
        await h.service.start()
        await h.qq.container.qq_channels._reconcile_connection(h.connection_id)
        assert await h.service.evaluate_once() == 0
        assert len(h.qq.peer.calls) == before_calls
        assert len(h.qq.model.requests) == before_models
        assert h.qq.peer.sends.empty()
        assert json.loads(await deliveries.get_adapter_cursor(h.connection_id)) == journal


async def test_restart_recovers_completed_generation_once_without_regeneration(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        # Simulate a fresh startup without a competing container-owned scheduler.
        await h.service.stop()
        reservation = await h.repository.reserve_intent(h.connection_id, as_of=h.clock())
        assert reservation.intent is not None
        intent = await h.repository.claim_generation(
            reservation.intent.request_id,
            expected_revision=reservation.intent.revision,
            claimed_at=h.clock(),
        )
        assert intent is not None
        subscription = h.qq.container.event_hub.subscribe(
            lambda event: (
                event.get("event_type") == "assistant.generation_completed"
                and event.get("generation_id") == str(intent.generation_id)
            ),
            queue_size=8,
        )
        try:
            await h.qq.container.conversation.submit_proactive(
                intent.session_id,
                turn_id=intent.turn_id,
                generation_id=intent.generation_id,
                audio_stream_id=intent.audio_stream_id,
                options=ConversationTurnOptions(
                    output_modes=frozenset({"text"}), source_context=_source_context(intent)
                ),
            )
            await asyncio.wait_for(subscription.receive(), 3)
        finally:
            subscription.close()
        before = len(h.qq.model.requests)
        await h.service.start()
        try:
            sent = await asyncio.wait_for(h.qq.peer.sends.get(), 3)
            assert _segments(sent) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
            assert len(h.qq.model.requests) == before
            assert await h.service.evaluate_once() == 0
        finally:
            await h.service.stop()


@pytest.mark.parametrize("stage", ["generating", "planned"])
@pytest.mark.parametrize("mutation", ["policy", "connection"])
async def test_rejected_revision_write_preserves_existing_proactive_work(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    mutation: str,
) -> None:
    from chatwaifu_runtime.external_channels.service import ChannelConflictError

    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        entered = asyncio.Event()
        if stage == "generating":
            original = h.qq.container.memory.retrieve_context

            async def blocked(
                sid: UUID,
                tid: UUID,
                character: str,
                query: str,
                *,
                token_budget: int = 700,
                limit: int = 12,
            ) -> MemoryContextPacket:
                if query == "轻声主动关心用户":
                    entered.set()
                    await asyncio.Event().wait()
                return await original(
                    sid, tid, character, query, token_budget=token_budget, limit=limit
                )

            monkeypatch.setattr(h.qq.container.memory, "retrieve_context", blocked)
            assert await h.service.evaluate_once() == 1
            await asyncio.wait_for(entered.wait(), 3)
        else:

            async def deny(_plan: ChannelDeliveryPlanRecord) -> bool:
                return False

            monkeypatch.setattr(h.qq.container.qq_channels, "_proactive_authorization", deny)
            monkeypatch.setattr(
                h.qq.container.qq_channels._schedulers[h.connection_id], "_before_claim", deny
            )
            await planned(h)
        before = (await h.repository.list_intents(h.connection_id)).items[0]
        policy_before = await h.service.get_policy(h.connection_id)
        generation_before = await h.qq.container.conversation_repository.generation_result(
            before.generation_id
        )
        active_before = h.qq.container.conversation.active_generation_id(before.session_id)
        with pytest.raises(ChannelConflictError):
            if mutation == "policy":
                await h.service.update_policy(
                    h.connection_id,
                    ChannelProactivePolicyUpdate(
                        expected_revision=0,
                        policy=policy_before.policy.model_copy(update={"enabled": False}),
                    ),
                )
            else:
                connection = await h.qq.container.external_channels.get_connection(h.connection_id)
                await h.qq.container.external_channels.update_connection(
                    connection.configuration,
                    expected_revision=connection.revision + 1,
                    rotate_access_token=False,
                )
        assert await h.service.get_policy(h.connection_id) == policy_before
        assert await h.repository.get_intent(before.request_id) == before
        assert (
            await h.qq.container.conversation_repository.generation_result(before.generation_id)
            == generation_before
        )
        assert h.qq.container.conversation.active_generation_id(before.session_id) == active_before
        assert h.qq.peer.sends.empty()


async def test_policy_commit_return_gap_preserves_new_revision_owner_work(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        scheduler = h.qq.container.qq_channels._schedulers[h.connection_id]

        async def hold_outbound(plan: ChannelDeliveryPlanRecord) -> bool:
            return plan.outbound_intent_id is None

        monkeypatch.setattr(scheduler, "_before_claim", hold_outbound)
        old_id = await planned(h)
        policy = await h.service.get_policy(h.connection_id)
        committed, return_policy = asyncio.Event(), asyncio.Event()
        preparing, release_prepare = asyncio.Event(), asyncio.Event()
        original_update = h.repository.update_policy

        async def update(
            connection_id: UUID,
            body: ChannelProactivePolicyUpdate,
            *,
            updated_at: datetime,
        ) -> ChannelProactivePolicyRecord:
            record = await original_update(connection_id, body, updated_at=updated_at)
            committed.set()
            await return_policy.wait()
            return record

        original_memory = h.qq.container.memory.retrieve_context

        async def memory(
            sid: UUID,
            tid: UUID,
            character: str,
            query: str,
            *,
            token_budget: int = 700,
            limit: int = 12,
        ) -> MemoryContextPacket:
            if query == "轻声主动关心用户":
                preparing.set()
                await release_prepare.wait()
            return await original_memory(
                sid, tid, character, query, token_budget=token_budget, limit=limit
            )

        monkeypatch.setattr(h.repository, "update_policy", update)
        monkeypatch.setattr(h.qq.container.memory, "retrieve_context", memory)
        # Policy activation must precede the real accepted timestamp below.
        h.clock.now = datetime.now(UTC)
        saving = asyncio.create_task(
            h.service.update_policy(
                h.connection_id,
                ChannelProactivePolicyUpdate(
                    expected_revision=policy.revision,
                    policy=policy.policy.model_copy(update={"idle_minutes": 2}),
                ),
            )
        )
        subscription = h.qq.container.event_hub.subscribe(
            lambda event: event.get("event_type") == "channel.delivery_plan_created", queue_size=8
        )
        try:
            await asyncio.wait_for(committed.wait(), 3)
            fresh = await owner_activity(h.qq, h.connection_id, "新策略保存后的一条主人输入", 14)
            owner = await h.qq.container.external_channel_repository.get_turn(fresh.channel_turn_id)
            assert owner is not None
            h.clock.now = owner.accepted_at + timedelta(minutes=2, seconds=1)
            assert await h.service.evaluate_once() == 1
            await asyncio.wait_for(preparing.wait(), 3)
            new = next(
                intent
                for intent in (await h.repository.list_intents(h.connection_id)).items
                if intent.request_id != old_id
            )
            assert new.policy_revision == policy.revision + 1
            return_policy.set()
            saved = await asyncio.wait_for(saving, 3)
            assert saved.revision == new.policy_revision
            assert not h.service._workflows[new.request_id][1].cancelling()
            release_prepare.set()
            # The earlier normal owner delivery also emitted a plan event.
            while True:
                event = await asyncio.wait_for(subscription.receive(), 3)
                payload = cast(dict[str, object], event["payload"])
                if payload.get("outbound_intent_id") == str(new.request_id):
                    break
            current = await h.repository.get_intent(new.request_id)
            assert current is not None and current.status is ChannelOutboundIntentStatus.PLANNED
            assert current.delivery_id is not None
            plan = await h.qq.container.external_channel_repository.get_delivery_plan(
                current.delivery_id
            )
            assert plan is not None and await h.service.authorize_delivery(plan)
            old = await h.repository.get_intent(old_id)
            assert old is not None and old.status is ChannelOutboundIntentStatus.SETTLED
            assert h.qq.peer.sends.empty()
        finally:
            return_policy.set()
            release_prepare.set()
            subscription.close()
            await asyncio.gather(saving, return_exceptions=True)


async def test_unknown_connection_public_reads_return_not_found(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import uuid4

    from chatwaifu_runtime.external_channels.service import ChannelNotFoundError

    async with configured(runtime_settings, monkeypatch) as h:
        unknown = uuid4()
        with pytest.raises(ChannelNotFoundError):
            await h.service.get_policy(unknown)
        with pytest.raises(ChannelNotFoundError):
            await h.service.preview(unknown)
        with pytest.raises(ChannelNotFoundError):
            await h.service.list_intents(unknown)


async def test_repeated_fence_joins_once_and_preserves_terminal_generation_audit(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        entered = asyncio.Event()
        original = h.qq.container.memory.retrieve_context

        async def blocked(
            sid: UUID,
            tid: UUID,
            character: str,
            query: str,
            *,
            token_budget: int = 700,
            limit: int = 12,
        ) -> MemoryContextPacket:
            if query == "轻声主动关心用户":
                entered.set()
                await asyncio.Event().wait()
            return await original(
                sid, tid, character, query, token_budget=token_budget, limit=limit
            )

        monkeypatch.setattr(h.qq.container.memory, "retrieve_context", blocked)
        assert await h.service.evaluate_once() == 1
        await asyncio.wait_for(entered.wait(), 3)
        intent = (await h.repository.list_intents(h.connection_id)).items[0]
        h.service.fence_connection(h.connection_id, "first")
        h.service.fence_connection(h.connection_id, "second")
        await h.service.cancel_for_connection(h.connection_id, reason="third")
        result = await h.qq.container.conversation_repository.generation_result(
            intent.generation_id
        )
        assert result is not None and result.state is GenerationState.CANCELLED
        row = await h.qq.container.database.fetchone(
            "SELECT COUNT(*) AS n FROM events WHERE "
            "json_extract(envelope_json,'$.generation_id')=? "
            "AND event_type='assistant.generation_cancelled'",
            (str(intent.generation_id),),
        )
        assert row is not None and row["n"] == 1
        assert h.service.active_count == 0


async def test_operator_cancel_is_cas_idempotent_and_does_not_request_model(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatwaifu_protocol.channel_proactive import ChannelOutboundIntentCancelRequest
    from chatwaifu_runtime.external_channels.service import ChannelConflictError

    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        entered = asyncio.Event()
        original = h.qq.container.memory.retrieve_context

        async def blocked(
            sid: UUID,
            tid: UUID,
            character: str,
            query: str,
            *,
            token_budget: int = 700,
            limit: int = 12,
        ) -> MemoryContextPacket:
            if query == "轻声主动关心用户":
                entered.set()
                await asyncio.Event().wait()
            return await original(
                sid, tid, character, query, token_budget=token_budget, limit=limit
            )

        monkeypatch.setattr(h.qq.container.memory, "retrieve_context", blocked)
        assert await h.service.evaluate_once() == 1
        await asyncio.wait_for(entered.wait(), 3)
        intent = (await h.service.list_intents(h.connection_id)).items[0]
        result = await h.service.cancel_intent(
            h.connection_id,
            intent.request_id,
            ChannelOutboundIntentCancelRequest(expected_revision=intent.revision),
        )
        assert result.status is ChannelOutboundIntentStatus.SETTLED
        duplicate = await h.service.cancel_intent(
            h.connection_id,
            intent.request_id,
            ChannelOutboundIntentCancelRequest(expected_revision=result.revision),
        )
        assert duplicate == result
        with pytest.raises(ChannelConflictError):
            await h.service.cancel_intent(
                h.connection_id,
                intent.request_id,
                ChannelOutboundIntentCancelRequest(expected_revision=intent.revision),
            )
        assert not [r for r in h.qq.model.requests if r.trigger == "proactive"]
        assert h.service.active_count == 0


async def test_cancel_cas_race_preserves_generation_and_advanced_plan(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chatwaifu_runtime.external_channels.service import ChannelConflictError

    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        preparing, release_prepare = asyncio.Event(), asyncio.Event()
        entering_cas, release_cas = asyncio.Event(), asyncio.Event()
        memory = h.qq.container.memory.retrieve_context

        async def prepare(
            sid: UUID,
            tid: UUID,
            character: str,
            query: str,
            *,
            token_budget: int = 700,
            limit: int = 12,
        ) -> MemoryContextPacket:
            if query == "轻声主动关心用户":
                preparing.set()
                await release_prepare.wait()
            return await memory(sid, tid, character, query, token_budget=token_budget, limit=limit)

        original = h.repository.settle_intent

        async def settle(
            request_id: UUID,
            *,
            reason: str,
            settled_at: datetime,
            cancel: bool = False,
            expected_revision: int | None = None,
            error: StructuredError | None = None,
        ) -> ChannelOutboundIntentRecord:
            if expected_revision is not None:
                entering_cas.set()
                await release_cas.wait()
            return await original(
                request_id,
                reason=reason,
                settled_at=settled_at,
                cancel=cancel,
                expected_revision=expected_revision,
                error=error,
            )

        async def hold_delivery(_plan: ChannelDeliveryPlanRecord) -> bool:
            return False

        monkeypatch.setattr(h.qq.container.memory, "retrieve_context", prepare)
        monkeypatch.setattr(h.repository, "settle_intent", settle)
        monkeypatch.setattr(
            h.qq.container.qq_channels._schedulers[h.connection_id], "_before_claim", hold_delivery
        )
        assert await h.service.evaluate_once() == 1
        await asyncio.wait_for(preparing.wait(), 3)
        intent = (await h.repository.list_intents(h.connection_id)).items[0]
        task = asyncio.create_task(
            h.service.cancel_intent(
                h.connection_id,
                intent.request_id,
                ChannelOutboundIntentCancelRequest(expected_revision=intent.revision),
            )
        )
        subscription = h.qq.container.event_hub.subscribe(
            lambda event: event.get("event_type") == "channel.delivery_plan_created", queue_size=8
        )
        try:
            await asyncio.wait_for(entering_cas.wait(), 3)
            # No cancellation may happen before the SQL CAS succeeds.
            assert not h.service._workflows[intent.request_id][1].cancelling()
            assert h.qq.container.conversation.active_generation_id(intent.session_id) == (
                intent.generation_id
            )
            release_prepare.set()
            await asyncio.wait_for(subscription.receive(), 3)
            advanced = await h.repository.get_intent(intent.request_id)
            assert advanced is not None and advanced.status is ChannelOutboundIntentStatus.PLANNED
            assert advanced.revision > intent.revision
            release_cas.set()
            with pytest.raises(ChannelConflictError):
                await asyncio.wait_for(task, 3)
            assert await h.repository.get_intent(intent.request_id) == advanced
            generation = await h.qq.container.conversation_repository.generation_result(
                intent.generation_id
            )
            assert generation is not None and generation.state is GenerationState.COMPLETED
            assert len([r for r in h.qq.model.requests if r.trigger == "proactive"]) == 1
            assert h.qq.peer.sends.empty()
        finally:
            release_prepare.set()
            release_cas.set()
            subscription.close()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("result_state", ["missing", "running"])
async def test_recovery_consumes_unfinished_episode_without_resubmitting_model(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    result_state: str,
) -> None:
    from chatwaifu_runtime.conversation.repository import ConversationGenerationRecord

    async with configured(runtime_settings, monkeypatch) as h:
        await enable_and_anchor(h)
        reserved = await h.repository.reserve_intent(h.connection_id, as_of=h.clock())
        assert reserved.intent is not None
        intent = await h.repository.claim_generation(
            reserved.intent.request_id,
            expected_revision=reserved.intent.revision,
            claimed_at=h.clock(),
        )
        assert intent is not None
        original = h.qq.container.conversation_repository.generation_result

        async def generation(generation_id: UUID) -> ConversationGenerationRecord | None:
            if generation_id == intent.generation_id:
                if result_state == "missing":
                    return None
                return ConversationGenerationRecord(
                    intent.generation_id,
                    intent.session_id,
                    intent.turn_id,
                    GenerationState.RUNNING,
                    None,
                    None,
                    intent.audio_stream_id,
                )
            return await original(generation_id)

        monkeypatch.setattr(h.qq.container.conversation_repository, "generation_result", generation)
        before = len(h.qq.model.requests)
        await h.service._restore_generation(intent)
        updated = await h.repository.get_intent(intent.request_id)
        assert updated is not None and updated.status is ChannelOutboundIntentStatus.SETTLED
        assert updated.settled_reason == "generation_not_completed"
        assert await h.service.evaluate_once() == 0
        assert len(h.qq.model.requests) == before
        assert h.qq.peer.sends.empty()
