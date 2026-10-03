"""QQ supervision and durable session recovery through real local OneBot sockets."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from typing import cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import (
    ChannelConnectionStatus,
    ChannelDeliveryStatus,
    ChannelTurnStatus,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.eventing.hub import EventSubscription
from chatwaifu_runtime.providers.contracts import SynthesisRequest, SynthesisResult
from chatwaifu_runtime.providers.model_config import ModelRoleConfig
from test_qq_channels import (
    OWNER,
    SPOKEN,
    TEXT_REPLY,
    _configure,
    _event,
    _Harness,
    _ingest,
    _Model,
    _pair,
    _runtime,
    _segments,
    _terminal,
)


def _completed_plans(container: RuntimeContainer) -> EventSubscription:
    return container.event_hub.subscribe(
        lambda event: event.get("event_type") == "channel.delivery_plan_completed", queue_size=8
    )


async def _plan_completed(subscription: EventSubscription, turn_id: UUID) -> None:
    event = await asyncio.wait_for(subscription.receive(), timeout=5)
    payload = event.get("payload")
    assert isinstance(payload, dict)
    assert cast(JsonObject, payload).get("channel_turn_id") == str(turn_id)


@pytest.mark.asyncio
async def test_supervisor_reconnects_same_account_without_duplicate_generation_or_delivery(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        subscription = _completed_plans(harness.container)
        repository = harness.container.external_channel_repository
        try:
            first = await _ingest(harness, connection_id, "普通文字", 41)
            original = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
            assert _segments(original) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
            await _plan_completed(subscription, first.channel_turn_id)
            old_scheduler = harness.container.qq_channels._schedulers[connection_id]
            disconnected = harness.peer.peers[-1]
            await disconnected.close()
            current_id, status, code = await asyncio.wait_for(harness.health.get(), timeout=3)
            assert current_id == connection_id
            assert status is ChannelConnectionStatus.DEGRADED and code == "qq_connection_lost"
            # The supervisor's real bounded backoff runs; synchronization uses its events.
            reconnected = await asyncio.wait_for(harness.peer.connected.get(), timeout=5)
            current_id, status, code = await asyncio.wait_for(harness.health.get(), timeout=3)
            assert current_id == connection_id
            assert status is ChannelConnectionStatus.READY and code is None
            assert reconnected is not disconnected
            assert disconnected.state.name == "CLOSED"
            assert harness.container.qq_channels._schedulers[connection_id] is not old_scheduler
            assert old_scheduler._running_task is None
            assert len(harness.container.qq_channels._clients) == 1
            assert len(harness.container.qq_channels._schedulers) == 1

            # A duplicate followed by a new message gives an ordered ingress barrier.
            await reconnected.send(json.dumps(_event("普通文字", 41)))
            await reconnected.send(json.dumps(_event("重连后继续", 42)))
            reply = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
            assert _segments(reply) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
            new = await repository.find_turn_by_external_message(connection_id, "42")
            assert new is not None
            await _plan_completed(subscription, new.channel_turn_id)
            duplicate = await repository.find_turn_by_external_message(connection_id, "41")
            assert duplicate is not None
            assert duplicate.channel_turn_id == first.channel_turn_id
            assert duplicate.generation_id == first.generation_id
            assert new.session_id == first.session_id
            assert new.status is ChannelTurnStatus.COMPLETED
            assert len(harness.model.requests) == 2
            assert not harness.synthesis
            assert harness.peer.sends.empty()
            assert sum(call["action"] == "send_private_msg" for call in harness.peer.calls) == 2
            assert not await repository.list_nonterminal_delivery_plans(connection_id)
            snapshot = await harness.container.external_channels.get_connection(connection_id)
            assert snapshot.status is ChannelConnectionStatus.READY
        finally:
            harness.container.event_hub.unsubscribe(subscription)


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", [False, True], ids=["text", "audio"])
async def test_recreated_runtime_restores_binding_session_and_history_without_confirmed_replay(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, voice: bool
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as first:
        connection_id = await _pair(first)
        subscription = _completed_plans(first.container)
        source = "请用语音回复我" if voice else "普通文字"
        expected = SPOKEN if voice else TEXT_REPLY
        try:
            receipt = await _ingest(first, connection_id, source, 51)
            original = await asyncio.wait_for(first.peer.sends.get(), timeout=5)
            assert _segments(original)[0]["type"] == ("record" if voice else "text")
            await _plan_completed(subscription, receipt.channel_turn_id)
            finished = await _terminal(first, connection_id, receipt.channel_turn_id)
            assert finished.status is ChannelTurnStatus.COMPLETED
            assert finished.reply_text == expected and finished.delivery_id is not None
            plan = await first.container.external_channel_repository.get_delivery_plan(
                finished.delivery_id
            )
            assert plan is not None and plan.status is ChannelDeliveryStatus.DELIVERED
            provider_id = plan.parts[0].provider_message_id
            assert provider_id is not None
            binding = await first.container.external_channel_repository.find_binding(
                connection_id, f"direct:{OWNER}"
            )
            assert binding is not None and binding.session_id == receipt.session_id
        finally:
            first.container.event_hub.unsubscribe(subscription)
        await first.container.stop()
        await asyncio.wait_for(first.peer.peers[-1].wait_closed(), timeout=3)

        restarted = RuntimeContainer(runtime_settings)
        health = _configure(restarted, monkeypatch, first.credentials)
        model = _Model()

        def create_model(_configuration: ModelRoleConfig) -> _Model:
            return model

        monkeypatch.setattr(restarted.model_configurations, "create_chat_provider", create_model)
        synthesis: list[SynthesisRequest] = []
        synthesize = restarted.providers.tts.synthesize

        async def record_synthesis(request: SynthesisRequest) -> SynthesisResult:
            synthesis.append(request)
            return await synthesize(request)

        monkeypatch.setattr(restarted.providers.tts, "synthesize", record_synthesis)
        await restarted.start()
        recovered = _Harness(restarted, first.peer, model, first.credentials, health, synthesis)
        subscription = _completed_plans(restarted)
        try:
            socket = await asyncio.wait_for(first.peer.connected.get(), timeout=3)
            current_id, status, code = await asyncio.wait_for(health.get(), timeout=3)
            assert current_id == connection_id
            assert status is ChannelConnectionStatus.READY and code is None
            repository = restarted.external_channel_repository
            restored_binding = await repository.find_binding(connection_id, f"direct:{OWNER}")
            assert restored_binding is not None
            assert restored_binding.binding_id == binding.binding_id
            assert restored_binding.session_id == binding.session_id
            restored = await repository.get_delivery_plan(finished.delivery_id)
            assert restored is not None and restored.status is ChannelDeliveryStatus.DELIVERED
            assert restored.parts[0].provider_message_id == provider_id
            history = await restarted.conversation_repository.recent_history(
                receipt.session_id, uuid4(), limit=8
            )
            assert [item.text for item in history if item.role == "assistant"] == [expected]
            assert not list(restarted.channel_voice.audio_root.glob("*.wav"))
            assert not model.requests and not synthesis
            assert not await repository.list_nonterminal_delivery_plans(connection_id)

            # Replayed provider input remains deduplicated after all Runtime objects were replaced.
            await socket.send(json.dumps(_event(source, 51)))
            await socket.send(json.dumps(_event("重启后继续", 52)))
            reply = await asyncio.wait_for(first.peer.sends.get(), timeout=5)
            assert _segments(reply) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
            new = await repository.find_turn_by_external_message(connection_id, "52")
            assert new is not None
            await _plan_completed(subscription, new.channel_turn_id)
            result = await _terminal(recovered, connection_id, new.channel_turn_id)
            assert result.status is ChannelTurnStatus.COMPLETED
            assert new.session_id == receipt.session_id and new.binding_id == binding.binding_id
            duplicate = await repository.find_turn_by_external_message(connection_id, "51")
            assert duplicate is not None
            assert duplicate.channel_turn_id == receipt.channel_turn_id
            assert duplicate.generation_id == receipt.generation_id
            assert duplicate.delivery_id == finished.delivery_id
            history = await restarted.conversation_repository.recent_history(
                receipt.session_id, uuid4(), limit=8
            )
            assert [item.text for item in history if item.role == "assistant"] == [
                expected,
                TEXT_REPLY,
            ]
            assert len(model.requests) == 1 and not synthesis
            assert ("assistant", expected) in model.requests[0].history
            assert first.peer.sends.empty()
            assert sum(call["action"] == "send_private_msg" for call in first.peer.calls) == 2
            assert not await repository.list_nonterminal_delivery_plans(connection_id)
        finally:
            restarted.event_hub.unsubscribe(subscription)
            await restarted.stop()
        assert not restarted.qq_channels._tasks and not restarted.qq_channels._schedulers
