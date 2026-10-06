# pyright: reportPrivateUsage=false
"""Completed empty model replies never persist a successful blank assistant turn."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from chatwaifu_protocol.events import ErrorRaisedEvent
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationTurnOptions
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmToolCall,
    LlmToolCallRequested,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig
from chatwaifu_runtime.runtime_skills.agent_router import RuntimeSkillRouter


class _EmptyProvider:
    kind = "scripted"
    supports_tool_calling = False

    def __init__(self, tool_name: str | None = None) -> None:
        self.requests: list[LlmRequest] = []
        self.tool_name = tool_name
        self.supports_tool_calling = tool_name is not None

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        if self.tool_name is not None and request.tools and not request.tool_exchanges:
            assert any(tool.name == self.tool_name for tool in request.tools)
            yield LlmToolCallRequested(LlmToolCall("read-status", self.tool_name, {}))
            yield LlmResponseCompleted("tool_calls")
            return
        yield LlmResponseCompleted("stop")


@pytest.mark.asyncio
@pytest.mark.parametrize("after_tool", [False, True])
async def test_conversation_records_empty_answer_failure_and_no_assistant_turn(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, after_tool: bool
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        user_text = "查询 runtime status" if after_tool else "合成空回复测试"
        tool_name = (
            next(
                tool.name
                for tool in RuntimeSkillRouter(container.runtime_skills.list).select(user_text)
                if tool.skill_id == "runtime.status"
            )
            if after_tool
            else None
        )
        provider = _EmptyProvider(tool_name)

        def factory(config: ModelRoleConfig) -> LlmProvider:
            return provider

        monkeypatch.setattr(container.model_configurations, "create_chat_provider", factory)
        accepted = await container.conversation.submit_text(
            session.session_id,
            user_text,
            options=ConversationTurnOptions(
                output_modes=frozenset({"text"}), allow_tools=after_tool
            ),
        )
        active = container.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)
        generation = await container.database.fetchone(
            "SELECT state, error_code FROM generations WHERE generation_id = ?",
            (str(accepted.generation_id),),
        )
        assert generation is not None and generation["state"] == "failed"
        assert generation["error_code"] == "empty_model_response"
        turns = await container.database.fetchall(
            "SELECT role FROM turns WHERE generation_id = ?", (str(accepted.generation_id),)
        )
        assert not any(row["role"] == "assistant" for row in turns)
        events = await container.event_store.read_stream(session.session_id, limit=100)
        assert not any(event["event_type"] == "assistant.generation_completed" for event in events)
        failure = ErrorRaisedEvent.model_validate(
            next(event for event in events if event["event_type"] == "system.error_raised")
        )
        assert failure.payload.error.code == "empty_model_response"
        assert failure.payload.error.retryable is (not after_tool)
        assert failure.generation_id == accepted.generation_id
        assert len(provider.requests) == (2 if after_tool else 1)
        assert container.conversation.active_count == 0
        runs = await container.runtime_skills.list_runs(session.session_id)
        assert len(runs) == int(after_tool)
        if after_tool:
            assert runs[0].state.value == "succeeded" and runs[0].result is not None
    finally:
        await container.stop()
