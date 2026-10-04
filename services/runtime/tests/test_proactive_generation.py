"""Early admission, revocation and surface boundaries for proactive generation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationSourceContext, ConversationTurnOptions
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig


@dataclass
class Model:
    kind: str = "proactive-test"
    supports_tool_calling: bool = True
    requests: list[LlmRequest] = field(default_factory=list[LlmRequest])

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        yield LlmTextDelta("休息一下也很好，我在这里。")
        yield LlmResponseCompleted("stop")


@asynccontextmanager
async def runtime(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[RuntimeContainer, Model, UUID]]:
    container = RuntimeContainer(settings)
    model = Model()

    def provider(_config: ModelRoleConfig) -> Model:
        return model

    monkeypatch.setattr(container.model_configurations, "create_chat_provider", provider)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        yield container, model, session.session_id
    finally:
        await container.stop()


@pytest.mark.parametrize("swallow_cancel", [False, True])
async def test_prepare_is_active_and_cancellable_before_any_model_request(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, swallow_cancel: bool
) -> None:
    async with runtime(runtime_settings, monkeypatch) as (container, model, session_id):
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = container.memory.retrieve_context

        async def blocked(
            sid: UUID,
            tid: UUID,
            character: str,
            query: str,
            *,
            token_budget: int = 700,
            limit: int = 12,
        ) -> MemoryContextPacket:
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

        monkeypatch.setattr(container.memory, "retrieve_context", blocked)
        turn_id, generation_id = uuid4(), uuid4()
        accepted = await container.conversation.submit_proactive(
            session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            options=ConversationTurnOptions(output_modes=frozenset({"text"})),
        )
        await asyncio.wait_for(entered.wait(), 2)
        assert accepted.generation_id == generation_id
        assert accepted.turn_id == turn_id
        assert container.conversation.active_generation_id(session_id) == generation_id
        cancel = asyncio.create_task(
            container.conversation.cancel(session_id, expected_generation_id=generation_id)
        )
        await asyncio.wait_for(cancelled.wait(), 2)
        release.set()
        assert await asyncio.wait_for(cancel, 2)
        assert model.requests == []
        result = await container.conversation_repository.generation_result(generation_id)
        assert result is not None and result.state is GenerationState.CANCELLED
        assert container.conversation.active_generation_id(session_id) is None


async def test_policy_revocation_during_prepare_blocks_model_and_hidden_source_is_durable(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with runtime(runtime_settings, monkeypatch) as (container, model, session_id):
        allowed = True
        entered, release = asyncio.Event(), asyncio.Event()
        original = container.memory.retrieve_context

        async def guard() -> bool:
            return allowed

        async def blocked(
            sid: UUID,
            tid: UUID,
            character: str,
            query: str,
            *,
            token_budget: int = 700,
            limit: int = 12,
        ) -> MemoryContextPacket:
            entered.set()
            await release.wait()
            return await original(
                sid, tid, character, query, token_budget=token_budget, limit=limit
            )

        monkeypatch.setattr(container.memory, "retrieve_context", blocked)
        source = ConversationSourceContext(
            provider_id="qq_napcat",
            connection_id=uuid4(),
            account_key="test-account",
            principal_scope="local",
            chat_type="direct",
            conversation_key="test-owner",
            sender_key="test-owner",
            outbound_intent_id=uuid4(),
            source_event_key="opaque-episode",
            policy_revision=1,
            route_revision=2,
        )
        subscription = container.event_hub.subscribe(
            lambda e: e.get("event_type") == "assistant.generation_cancelled", queue_size=8
        )
        try:
            accepted = await container.conversation.submit_proactive(
                session_id,
                options=ConversationTurnOptions(
                    output_modes=frozenset({"text"}),
                    source_context=source,
                    before_generation=guard,
                ),
            )
            await asyncio.wait_for(entered.wait(), 2)
            allowed = False
            release.set()
            event = await asyncio.wait_for(subscription.receive(), 2)
            assert event["generation_id"] == str(accepted.generation_id)
        finally:
            subscription.close()
        assert model.requests == []
        row = await container.database.fetchone(
            "SELECT role FROM turns WHERE turn_id = ?", (str(accepted.turn_id),)
        )
        assert row is not None and row["role"] == "system"
        result = await container.conversation_repository.generation_result(accepted.generation_id)
        assert result is not None and result.state is GenerationState.CANCELLED


async def test_proactive_text_completion_has_no_tools_audio_or_fake_user_event(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with runtime(runtime_settings, monkeypatch) as (container, model, session_id):
        events: list[dict[str, Any]] = []
        subscription = container.event_hub.subscribe(
            lambda e: e.get("session_id") == str(session_id), queue_size=256
        )
        try:
            accepted = await container.conversation.submit_proactive(
                session_id,
                options=ConversationTurnOptions(
                    output_modes=frozenset({"text"}),
                    allow_tools=True,
                ),
            )
            async with asyncio.timeout(3):
                while True:
                    event = await subscription.receive()
                    events.append(event)
                    if event["event_type"] == "assistant.generation_completed":
                        break
        finally:
            subscription.close()
        assert len(model.requests) == 1
        assert model.requests[0].tools == ()
        assert model.requests[0].trigger == "proactive"
        assert not any(e["event_type"] == "user.turn_committed" for e in events)
        assert not any("audio" in str(e["event_type"]) for e in events)
        result = await container.conversation_repository.generation_result(accepted.generation_id)
        assert result is not None and result.state is GenerationState.COMPLETED


async def test_ambient_predicate_blocks_manual_and_automatic_channel_session(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with runtime(runtime_settings, monkeypatch) as (container, model, session_id):

        async def denied(_session_id: UUID) -> bool:
            return False

        monkeypatch.setattr(container.ambient, "_session_allowed", denied)
        with pytest.raises(RuntimeError, match="channel-owned"):
            await container.ambient.trigger_manual(session_id)
        assert await container.ambient.evaluate_once() == 0
        assert model.requests == []
        assert await container.database.fetchone("SELECT 1 FROM ambient_actions") is None


def test_outbound_source_roundtrip_and_legacy_compatibility() -> None:
    source = ConversationSourceContext(
        provider_id="qq_napcat",
        connection_id=uuid4(),
        account_key="test-account",
        principal_scope="local",
        chat_type="direct",
        conversation_key="test-owner",
        sender_key="test-owner",
        outbound_intent_id=uuid4(),
        source_event_key="opaque-episode",
        policy_revision=1,
        route_revision=2,
    )
    assert ConversationSourceContext.from_json(source.to_json()) == source
    with pytest.raises(ValueError, match="lineage"):
        ConversationSourceContext(
            provider_id="qq_napcat",
            connection_id=uuid4(),
            account_key=None,
            principal_scope="local",
            chat_type="direct",
            conversation_key="test-owner",
            sender_key="test-owner",
            outbound_intent_id=uuid4(),
        )
