"""Character agent to permissioned Runtime Skill integration tests."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_protocol.skills import (
    McpConnectionConfiguration,
    SkillInvocation,
    SkillResult,
    SkillRunSnapshot,
    SkillRunState,
)
from chatwaifu_runtime.agent.tool_calling import AgentTurnOrchestrator, ProjectedAgentTool
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallingUnavailableError,
    LlmToolCallRequested,
)
from chatwaifu_runtime.runtime_skills.agent_router import RuntimeSkillRouter

_LOCAL_ECHO_SERVER = (
    Path(__file__).resolve().parents[3] / "plugins" / "examples" / "local-echo" / "server.py"
)


@dataclass(frozen=True, slots=True)
class _Projection:
    name: str = "runtime_status_read"
    description: str = "Read Runtime status"
    input_schema: JsonObject = field(default_factory=lambda: {"type": "object"})

    def to_invocation(self, arguments: JsonObject) -> SkillInvocation:
        return SkillInvocation(skill_id="runtime.status", capability="read", arguments=arguments)


class _Router:
    def __init__(self, projections: tuple[ProjectedAgentTool, ...]) -> None:
        self.projections = projections
        self.queries: list[str] = []

    def select(
        self, query: str, *, limit: int = 8, schema_budget_bytes: int = 24_576
    ) -> tuple[ProjectedAgentTool, ...]:
        self.queries.append(query)
        return self.projections[:limit]


class _ScriptedLlm:
    kind = "scripted"
    supports_tool_calling = True

    def __init__(self, rounds: list[tuple[LlmStreamEvent, ...]]) -> None:
        self.rounds = rounds
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        for event in self.rounds.pop(0):
            await asyncio.sleep(0)
            yield event


class _ToolUnsupportedLlm(_ScriptedLlm):
    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        if request.tools:
            raise LlmToolCallingUnavailableError("tools unsupported")
        yield LlmTextDelta("普通文字")
        yield LlmResponseCompleted("stop")


class _Gateway:
    def __init__(self, terminal: SkillRunSnapshot) -> None:
        self.terminal = terminal
        self.invocations: list[tuple[UUID, SkillInvocation, str]] = []
        self.cancelled: list[UUID] = []

    async def invoke(
        self,
        session_id: UUID,
        invocation: SkillInvocation,
        *,
        principal: str = "local_user",
        turn_id: UUID | None = None,
        generation_id: UUID | None = None,
        origin: Literal["manual", "agent", "external_mcp"] = "manual",
        provider_tool_call_id: str | None = None,
    ) -> SkillRunSnapshot:
        self.invocations.append((session_id, invocation, principal))
        return _snapshot(SkillRunState.CREATED, run_id=self.terminal.skill_run_id)

    async def wait_for_terminal(self, run_id: UUID) -> SkillRunSnapshot:
        assert run_id == self.terminal.skill_run_id
        return self.terminal

    async def cancel(self, run_id: UUID) -> SkillRunSnapshot:
        self.cancelled.append(run_id)
        return _snapshot(SkillRunState.CANCELLED, run_id=run_id)


class _WaitingGateway(_Gateway):
    def __init__(self) -> None:
        super().__init__(_snapshot(SkillRunState.SUCCEEDED))
        self.wait_started = asyncio.Event()

    async def wait_for_terminal(self, run_id: UUID) -> SkillRunSnapshot:
        self.wait_started.set()
        await asyncio.Future()
        raise AssertionError("unreachable")


class _FailingWaitGateway(_Gateway):
    async def wait_for_terminal(self, run_id: UUID) -> SkillRunSnapshot:
        raise RuntimeError("terminal storage unavailable")


def _snapshot(
    state: SkillRunState,
    *,
    run_id: UUID | None = None,
    data: JsonValue = None,
) -> SkillRunSnapshot:
    now = datetime.now(UTC)
    result = (
        SkillResult(
            status="succeeded",
            data=data,
            provenance=["skill:runtime.status@1.2.0"],
        )
        if state is SkillRunState.SUCCEEDED
        else None
    )
    return SkillRunSnapshot(
        skill_run_id=run_id or uuid4(),
        skill_id="runtime.status",
        skill_version="1.2.0",
        capability="read",
        session_id=uuid4(),
        state=state,
        result=result,
        created_at=now,
        updated_at=now,
        completed_at=now if state in {SkillRunState.SUCCEEDED, SkillRunState.CANCELLED} else None,
    )


async def _collect(
    agent: AgentTurnOrchestrator, request: LlmRequest, session_id: UUID
) -> list[str]:
    return [
        text
        async for text in agent.stream(
            request,
            session_id=session_id,
            turn_id=uuid4(),
            ensure_current=lambda: None,
        )
    ]


@pytest.mark.asyncio
async def test_no_relevant_tools_preserves_incremental_text_streaming() -> None:
    llm = _ScriptedLlm([(LlmTextDelta("你"), LlmTextDelta("好"), LlmResponseCompleted("stop"))])
    gateway = _Gateway(_snapshot(SkillRunState.SUCCEEDED))
    router = _Router(())
    agent = AgentTurnOrchestrator(llm, gateway, router)

    chunks = await _collect(agent, _request("只是聊天"), uuid4())

    assert chunks == ["你", "好"]
    assert not gateway.invocations
    assert llm.requests[0].tools == ()


@pytest.mark.asyncio
async def test_tool_round_executes_through_gateway_and_only_streams_final_reply() -> None:
    call = LlmToolCall(call_id="call_status", name="runtime_status_read", arguments={})
    llm = _ScriptedLlm(
        [
            (
                LlmTextDelta("我先看一下。"),
                LlmToolCallRequested(call),
                LlmResponseCompleted("tool_calls"),
            ),
            (LlmTextDelta("Runtime "), LlmTextDelta("正常。"), LlmResponseCompleted("stop")),
        ]
    )
    terminal = _snapshot(SkillRunState.SUCCEEDED, data={"runtime": "ready"})
    gateway = _Gateway(terminal)
    agent = AgentTurnOrchestrator(llm, gateway, _Router((_Projection(),)))
    session_id = uuid4()

    chunks = await _collect(agent, _request("看看运行状态"), session_id)

    assert chunks == ["Runtime ", "正常。"]
    assert gateway.invocations[0][0] == session_id
    assert gateway.invocations[0][1].skill_id == "runtime.status"
    assert gateway.invocations[0][2] == "character_agent"
    assert llm.requests[0].tools[0].name == "runtime_status_read"
    assert llm.requests[1].tools == ()
    exchange = llm.requests[1].tool_exchanges[0]
    assert exchange.assistant_text == "我先看一下。"
    assert exchange.results[0].call_id == "call_status"
    assert exchange.results[0].is_error is False
    assert exchange.results[0].content["ok"] is True  # type: ignore[index]


@pytest.mark.asyncio
async def test_tool_relevant_turn_never_accepts_unverified_text_only_answer() -> None:
    llm = _ScriptedLlm([(LlmTextDelta("我已经联网查到了。"), LlmResponseCompleted("stop"))])
    gateway = _Gateway(_snapshot(SkillRunState.SUCCEEDED))
    agent = AgentTurnOrchestrator(llm, gateway, _Router((_Projection(),)))

    chunks = await _collect(agent, _request("联网查一下运行状态"), uuid4())

    assert "没有执行外部操作" in "".join(chunks)
    assert "联网查到了" not in "".join(chunks)
    assert not gateway.invocations


@pytest.mark.asyncio
async def test_tool_unsupported_provider_returns_explicit_unavailable_reply() -> None:
    llm = _ToolUnsupportedLlm([])
    gateway = _Gateway(_snapshot(SkillRunState.SUCCEEDED))
    agent = AgentTurnOrchestrator(llm, gateway, _Router((_Projection(),)))

    chunks = await _collect(agent, _request("联网查一下运行状态"), uuid4())

    assert "没有执行外部操作" in "".join(chunks)
    assert not gateway.invocations


@pytest.mark.asyncio
async def test_generation_cancellation_cancels_waiting_runtime_skill() -> None:
    call = LlmToolCall(call_id="call_status", name="runtime_status_read", arguments={})
    llm = _ScriptedLlm(
        [
            (
                LlmToolCallRequested(call),
                LlmResponseCompleted("tool_calls"),
            )
        ]
    )
    gateway = _WaitingGateway()
    agent = AgentTurnOrchestrator(llm, gateway, _Router((_Projection(),)))
    task = asyncio.create_task(_collect(agent, _request("看看运行状态"), uuid4()))
    await gateway.wait_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert gateway.cancelled == [gateway.terminal.skill_run_id]
    assert len(llm.requests) == 1


@pytest.mark.asyncio
async def test_terminal_wait_failure_cancels_active_runtime_skill() -> None:
    call = LlmToolCall(call_id="call_status", name="runtime_status_read", arguments={})
    llm = _ScriptedLlm(
        [
            (LlmToolCallRequested(call), LlmResponseCompleted("tool_calls")),
            (LlmTextDelta("工具暂时不可用。"), LlmResponseCompleted("stop")),
        ]
    )
    gateway = _FailingWaitGateway(_snapshot(SkillRunState.SUCCEEDED))
    agent = AgentTurnOrchestrator(llm, gateway, _Router((_Projection(),)))

    chunks = await _collect(agent, _request("看看运行状态"), uuid4())

    assert chunks == ["工具暂时不可用。"]
    assert gateway.cancelled == [gateway.terminal.skill_run_id]
    assert llm.requests[1].tool_exchanges[0].results[0].is_error is True


@pytest.mark.asyncio
async def test_agent_executes_builtin_through_real_runtime_skill_gateway(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        router = RuntimeSkillRouter(container.runtime_skills.list)
        projection = router.select("查看 Runtime 运行状态")[0]
        subscription = container.event_hub.subscribe(
            lambda event: event.get("event_type") == "skill.run_completed",
            queue_size=4,
        )
        call = LlmToolCall(call_id="call_real", name=projection.name, arguments={})
        llm = _ScriptedLlm(
            [
                (LlmToolCallRequested(call), LlmResponseCompleted("tool_calls")),
                (LlmTextDelta("运行正常。"), LlmResponseCompleted("stop")),
            ]
        )
        agent = AgentTurnOrchestrator(llm, container.runtime_skills, router)
        request = _request("查看 Runtime 运行状态")

        chunks = await _collect(agent, request, session.session_id)

        assert chunks == ["运行正常。"]
        exchange = llm.requests[1].tool_exchanges[0]
        assert exchange.results[0].is_error is False
        content = exchange.results[0].content
        assert isinstance(content, dict)
        assert content["ok"] is True
        data = content["data"]
        assert isinstance(data, dict)
        assert data["runtime_version"]
        runs = await container.runtime_skills.list_runs(session.session_id)
        assert runs[0].state is SkillRunState.SUCCEEDED
        assert runs[0].origin == "agent"
        assert runs[0].turn_id is not None
        assert runs[0].generation_id == request.generation_id
        assert runs[0].provider_tool_call_id == "call_real"
        completed_event = await asyncio.wait_for(subscription.receive(), timeout=1)
        assert completed_event["turn_id"] == str(runs[0].turn_id)
        assert completed_event["generation_id"] == str(request.generation_id)
        payload = completed_event["payload"]
        assert isinstance(payload, dict)
        assert payload["origin"] == "agent"
        assert payload["provider_tool_call_id"] == "call_real"
        container.event_hub.unsubscribe(subscription)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_agent_executes_connected_mcp_tool_through_confirmation_gateway(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        await container.runtime_skills.create_mcp_connection(
            McpConnectionConfiguration(
                connection_id=connection_id,
                name="Agent Echo",
                transport="stdio",
                command=[sys.executable, str(_LOCAL_ECHO_SERVER)],
                trust_level="trusted",
                sandbox_mode="disabled",
                network_policy="allow",
                timeout_seconds=5,
            )
        )
        ready = await container.runtime_skills.test_mcp_connection(connection_id)
        assert ready.status == "ready"

        session = await container.sessions.create_session("ayachi_nene")
        router = RuntimeSkillRouter(container.runtime_skills.list)
        projection = next(
            tool
            for tool in router.select("请让 Agent Echo 把 integration 原样返回")
            if tool.capability == "local_echo"
        )
        call = LlmToolCall(
            call_id="call_mcp_echo",
            name=projection.name,
            arguments={"text": "integration"},
        )
        llm = _ScriptedLlm(
            [
                (LlmToolCallRequested(call), LlmResponseCompleted("tool_calls")),
                (LlmTextDelta("MCP 已返回 integration。"), LlmResponseCompleted("stop")),
            ]
        )
        agent = AgentTurnOrchestrator(llm, container.runtime_skills, router)
        confirmation_events = container.event_hub.subscribe(
            lambda event: event.get("event_type") == "skill.confirmation_requested",
            queue_size=2,
        )
        turn: asyncio.Task[list[str]] | None = None
        try:
            turn = asyncio.create_task(
                _collect(
                    agent,
                    _request("请让 Agent Echo 把 integration 原样返回"),
                    session.session_id,
                )
            )

            requested = await asyncio.wait_for(confirmation_events.receive(), timeout=2)
            payload = cast(dict[str, object], requested["payload"])
            await container.runtime_skills.decide_confirmation(
                UUID(str(payload["request_id"])), "allow_once"
            )
            chunks = await asyncio.wait_for(turn, timeout=5)

            assert chunks == ["MCP 已返回 integration。"]
            exchange = llm.requests[1].tool_exchanges[0]
            assert exchange.results[0].is_error is False
            content = exchange.results[0].content
            assert isinstance(content, dict)
            assert content["ok"] is True
            data = content["data"]
            assert isinstance(data, dict)
            assert data["echo"] == "integration"
            runs = await container.runtime_skills.list_runs(session.session_id)
            assert runs[0].mcp_connection_id == connection_id
            assert runs[0].origin == "agent"
        finally:
            if turn is not None and not turn.done():
                turn.cancel()
                with suppress(asyncio.CancelledError):
                    await turn
            container.event_hub.unsubscribe(confirmation_events)
    finally:
        await container.stop()


def _request(text: str) -> LlmRequest:
    return LlmRequest(
        generation_id=uuid4(),
        user_text=text,
        system_prompt="你是绫地宁宁。",
        character_name="绫地宁宁",
    )
