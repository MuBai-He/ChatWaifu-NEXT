"""End-to-end task composition, actual readback, restart fencing and cancellation."""

import asyncio
import json

# pyright: reportPrivateUsage=false
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from chatwaifu_protocol.agent import (
    AgentEvent,
    AgentTask,
    AgentTaskAction,
    AgentTaskCreate,
    AgentTaskState,
    TaskAuthorization,
)
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallRequested,
)


class ComposingModel:
    kind = "scripted"
    supports_tool_calling = True

    def __init__(self) -> None:
        self.phase = 0

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if any(t.name == "verify_completion" for t in request.tools):
            data = json.loads(request.user_text)
            reads = [s for s in data["operations"] if s["capability"] == "read"]
            yield LlmToolCallRequested(
                LlmToolCall(
                    "verify",
                    "verify_completion",
                    {
                        "complete": bool(reads),
                        "evidence_keys": [s["step_key"] for s in reads],
                        "remaining_work": "" if reads else "Readback missing",
                    },
                )
            )
            yield LlmResponseCompleted("tool_calls")
            return
        phase = self.phase
        self.phase += 1
        if phase == 0:
            name, arguments = (
                "activate_capabilities",
                {
                    "capability_ids": ["workspace.files/write", "workspace.files/read"],
                },
            )
        elif phase == 1:
            name = next(
                t.name
                for t in request.tools
                if "text" in cast(JsonObject, t.input_schema.get("properties", {}))
            )
            arguments = {"path": "notes/result.txt", "text": "实际完成"}
        elif phase == 2:
            name = next(
                t.name
                for t in request.tools
                if t.name != "inspect_capability"
                and t.input_schema.get("required") == ["path"]
                and "text" not in cast(JsonObject, t.input_schema.get("properties", {}))
            )
            arguments = {"path": "notes/result.txt"}
        else:
            yield LlmTextDelta("文件已写入并读取确认。")
            yield LlmResponseCompleted("stop")
            return
        yield LlmToolCallRequested(LlmToolCall(str(phase), name, cast(JsonObject, arguments)))
        yield LlmResponseCompleted("tool_calls")


class RepairingModel(ComposingModel):
    def __init__(self) -> None:
        super().__init__()
        self.protocol_rejected = False
        self.retried = False
        self.empty_repaired = False

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        from chatwaifu_runtime.providers.contracts import LlmToolCallProtocolError

        if self.phase == 2 and not self.protocol_rejected:
            self.protocol_rejected = True
            raise LlmToolCallProtocolError("unknown_tool")
        if self.phase == 2 and not self.empty_repaired:
            self.empty_repaired = True
            yield LlmResponseCompleted("stop")
            return
        if self.phase == 2 and not self.retried:
            self.phase = 1
            self.retried = True
        async for event in super().stream(request):
            yield event


async def test_task_repairs_rejected_native_batch_and_retries_no_effect_write(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        model = RepairingModel()
        container.agent_tasks.model_factory = lambda: model
        original = container.runtime_skills._builtin._session_handlers["workspace_write"]
        attempts = 0

        async def fail_once(sid: str, arguments: JsonObject) -> JsonObject:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return await original(sid, {**arguments, "expected_sha256": "0" * 64})
            return await original(sid, arguments)

        container.runtime_skills._builtin._session_handlers["workspace_write"] = fail_once
        task = await container.agent_tasks.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal="写 notes/result.txt 并核实",
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    resource_roots=["notes"],
                    allow_writes=True,
                    source_ref="owner:repair",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        final = await await_state(container, task.task_id)
        assert final.state is AgentTaskState.SUCCEEDED, final.blocked_reason
        assert model.protocol_rejected and model.empty_repaired and model.retried and attempts == 2
        assert len(await container.agent_tasks.repository.steps(task.task_id)) == 3
    finally:
        await container.stop()


async def await_state(container: RuntimeContainer, task_id: UUID) -> AgentTask:
    async with asyncio.timeout(5):
        while True:
            task = await container.agent_tasks.repository.get(task_id)
            if task and task.state not in {AgentTaskState.RUNNING, AgentTaskState.QUEUED}:
                return task
            await asyncio.sleep(0.01)


async def test_durable_wake_duplicate_events_and_owner_continuation(
    runtime_settings: Settings,
) -> None:
    from uuid import uuid4

    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.agent_tasks
        await service.stop()
        session = await container.sessions.create_session("default")
        task = await service.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal="延后完成文件，并回读确认",
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    resource_roots=["notes"],
                    allow_writes=True,
                    source_ref="owner:wake-test",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        deferred = await service.action(
            task.task_id,
            session.session_id,
            AgentTaskAction(
                expected_revision=task.revision,
                action="defer",
                wake_at=datetime.now(UTC) + timedelta(milliseconds=80),
                input_text="请保留原目标",
            ),
        )
        assert deferred.state is AgentTaskState.WAITING_EVENT
        service.llm, service.model_factory = ComposingModel(), None
        await service.start()
        completed = await await_state(container, task.task_id)
        # A waiting event is itself non-running, so wait for the timer and terminal goal.
        async with asyncio.timeout(5):
            while completed.state is AgentTaskState.WAITING_EVENT:
                await asyncio.sleep(0.01)
                completed = await await_state(container, task.task_id)
        assert completed.state is AgentTaskState.SUCCEEDED
        assert completed.continuation == "请保留原目标"
        assert completed.active_seconds < 5
        event = AgentEvent(
            event_id=uuid4(),
            session_id=session.session_id,
            task_id=task.task_id,
            scope="local",
            kind="work",
            occurred_at=datetime.now(UTC),
            expires_at=task.authorization.expires_at,
            source_refs=["actual-run:fixture"],
        )
        assert await service.receive(event)
        assert not await service.receive(event)
        with pytest.raises(ValueError, match="conflicting"):
            await service.receive(event.model_copy(update={"source_refs": ["different"]}))
        assert len(await service.repository.steps(task.task_id)) == 2
        artifacts = await container.artifacts.repository.list("local", session.session_id)
        assert artifacts and all(a.task_id == task.task_id for a in artifacts)
    finally:
        await container.stop()


async def test_task_write_read_verify_and_cross_surface_owner_access(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        container.agent_tasks.llm = ComposingModel()
        container.agent_tasks.model_factory = None
        task = await container.agent_tasks.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal="把结果保存到工作区文件，并读取确认",
                completion_criteria=["notes/result.txt 中包含实际完成"],
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    resource_roots=["notes"],
                    allow_writes=True,
                    source_ref="owner-test",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        completed = await await_state(container, task.task_id)
        assert completed.state == AgentTaskState.SUCCEEDED  # type: ignore[attr-defined]
        assert completed.tool_calls == 3  # type: ignore[attr-defined]
        steps = await container.agent_tasks.repository.steps(task.task_id)
        assert [s["capability"] for s in steps] == ["write", "read"]
        assert all(s["ok"] and s["state"] == "settled" for s in steps)
        assert all("arguments" not in s for s in steps)
        other_owner_session = await container.sessions.create_session("default")
        assert (
            await container.agent_tasks.get(task.task_id, other_owner_session.session_id)
        ).goal == task.goal
        participant = await container.sessions.create_participant("Other")
        other = await container.sessions.create_session(
            "default", participant_id=participant.participant_id
        )
        with pytest.raises(KeyError):
            await container.agent_tasks.get(task.task_id, other.session_id)
    finally:
        await container.stop()


async def test_restart_never_replays_unknown_write_and_actions_are_cas(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        await container.agent_tasks.stop()
        task = await container.agent_tasks.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal="不可重复提交",
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    resource_roots=["."],
                    source_ref="owner-test",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        await container.agent_tasks.repository.begin_step(
            task.task_id,
            "unknown",
            {
                "step_key": "unknown",
                "side_effect": "write",
                "state": "started",
                "ok": False,
            },
        )
        await container.agent_tasks.start()
        current = await container.agent_tasks.get(task.task_id, session.session_id)
        assert current.state is AgentTaskState.WAITING_INPUT
        assert current.blocked_reason == "operation_outcome_unknown"
        with pytest.raises(ValueError, match="reconcile"):
            await container.agent_tasks.action(
                task.task_id,
                session.session_id,
                AgentTaskAction(expected_revision=current.revision, action="resume"),
            )
        with pytest.raises(ValueError, match="revision"):
            await container.agent_tasks.action(
                task.task_id,
                session.session_id,
                AgentTaskAction(expected_revision=0, action="cancel"),
            )
        cancelled = await container.agent_tasks.action(
            task.task_id,
            session.session_id,
            AgentTaskAction(expected_revision=current.revision, action="cancel"),
        )
        assert cancelled.state is AgentTaskState.CANCELLED
    finally:
        await container.stop()


@pytest.mark.parametrize("outside", ["../escape", "/tmp/escape", "private/secret"])
async def test_task_resource_scope_is_enforced(runtime_settings: Settings, outside: str) -> None:
    from chatwaifu_protocol.skills import SkillInvocation
    from chatwaifu_runtime.agent.tasks import _TaskGateway

    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        await container.agent_tasks.stop()
        task = await container.agent_tasks.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal="只访问 notes",
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    resource_roots=["notes"],
                    source_ref="owner-test",
                    allow_writes=True,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        container.agent_tasks._closing = False
        task = await container.agent_tasks.update_checkpoint(
            task.task_id, state=AgentTaskState.RUNNING
        )
        gateway = _TaskGateway(container.agent_tasks, task)
        with pytest.raises(PermissionError, match="resource"):
            await gateway.invoke(
                session.session_id,
                SkillInvocation(
                    skill_id="workspace.files",
                    capability="write",
                    arguments={"path": outside, "text": "no"},
                ),
            )
        assert not (await container.agent_tasks.repository.steps(task.task_id))[0]["ok"]
    finally:
        await container.stop()


async def test_confirmed_write_cache_survives_reordered_arguments_and_new_gateway(
    runtime_settings: Settings,
) -> None:
    from chatwaifu_protocol.skills import SkillInvocation, SkillRunState
    from chatwaifu_runtime.agent.tasks import _TaskGateway

    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.agent_tasks
        await service.stop()
        session = await container.sessions.create_session("default")
        task = await service.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal="保存一次并核实，不重复写入",
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    resource_roots=["notes"],
                    allow_writes=True,
                    source_ref="owner:dedup-order",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        service._closing = False
        task = await service.update_checkpoint(task.task_id, state=AgentTaskState.RUNNING)
        gateway = _TaskGateway(service, task)
        first = await gateway.invoke(
            session.session_id,
            SkillInvocation(
                skill_id="workspace.files",
                capability="write",
                arguments={"path": "notes/one.txt", "text": "只执行一次"},
            ),
        )
        first = await gateway.wait_for_terminal(first.skill_run_id)
        assert first.state is SkillRunState.SUCCEEDED
        # A recovered generation may serialize the same native parameters in a
        # different order. It must reuse the confirmed receipt, not submit again.
        recovered = _TaskGateway(service, await service.get(task.task_id, session.session_id))
        second = await recovered.invoke(
            session.session_id,
            SkillInvocation(
                skill_id="workspace.files",
                capability="write",
                arguments={"text": "只执行一次", "path": "notes/one.txt"},
            ),
        )
        second = await recovered.wait_for_terminal(second.skill_run_id)
        assert second.state is SkillRunState.SUCCEEDED
        assert second.skill_run_id == first.skill_run_id
        assert len(await service.repository.steps(task.task_id)) == 1
    finally:
        await container.stop()
