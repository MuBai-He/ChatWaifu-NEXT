"""Owner reconciliation, checkpoint privacy, cancellation and idle model admission."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

# pyright: reportPrivateUsage=false
from uuid import UUID

import pytest
from chatwaifu_protocol.agent import (
    AgentTaskAction,
    AgentTaskCreate,
    AgentTaskState,
    DecisionRecord,
    TaskAuthorization,
    TaskAuthorizationUpdate,
    TaskReconciliation,
)
from chatwaifu_protocol.skills import SkillInvocation
from chatwaifu_runtime.agent.tasks import _TaskGateway
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.companion.activity import ActivityTracker
from chatwaifu_runtime.companion.models import CompanionSettingsUpdate
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.runtime_skills.audit import checkpoint_payload


async def test_unknown_write_owner_reconciliation_never_resubmits(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.agent_tasks
        await service.stop()
        session = await container.sessions.create_session("default")
        task = await service.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal="核对文件操作",
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    resource_roots=["notes"],
                    allow_writes=True,
                    source_ref="owner:test",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        invocation = SkillInvocation(
            skill_id="workspace.files",
            capability="write",
            arguments={"path": "notes/a.txt", "text": "one"},
        )
        import hashlib

        key = hashlib.sha256(
            json.dumps(
                invocation.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        await service.repository.begin_step(
            task.task_id,
            key,
            {
                "step_key": key,
                "side_effect": "write",
                "state": "unknown",
                "ok": False,
                "skill_id": "workspace.files",
                "capability": "write",
            },
        )
        task = await service.update_checkpoint(task.task_id, state=AgentTaskState.WAITING_INPUT)
        changed = await service.reconcile(
            task.task_id,
            session.session_id,
            TaskReconciliation(
                expected_revision=task.revision,
                step_key=key,
                outcome="accepted",
                evidence_ref="owner checked actual resource notes/a.txt",
            ),
        )
        assert changed.state is AgentTaskState.PAUSED
        step = (await service.repository.steps(task.task_id))[0]
        assert step["reconciled"] and not step["verified"]
        service._closing = False
        task = await service.update_checkpoint(task.task_id, state=AgentTaskState.RUNNING)
        with pytest.raises(PermissionError, match="already attempted"):
            await _TaskGateway(service, task).invoke(session.session_id, invocation)
        assert not (container.workspace_skills.root / "notes/a.txt").exists()
        task = await service.get(task.task_id, session.session_id)
        with pytest.raises(ValueError, match="revision"):
            await service.update_authorization(
                task.task_id,
                session.session_id,
                TaskAuthorizationUpdate(expected_revision=0, authorization=task.authorization),
            )
        changed = await service.update_authorization(
            task.task_id,
            session.session_id,
            TaskAuthorizationUpdate(
                expected_revision=task.revision,
                authorization=task.authorization.model_copy(update={"allow_writes": False}),
            ),
        )
        assert changed.state is AgentTaskState.PAUSED and not changed.authorization.allow_writes
        cancelled = await service.action(
            task.task_id,
            session.session_id,
            AgentTaskAction(expected_revision=changed.revision, action="cancel"),
        )
        # Late model or tool completion cannot resurrect a cancelled goal.
        late = await service.update_checkpoint(task.task_id, state=AgentTaskState.SUCCEEDED)
        assert late.state is cancelled.state is AgentTaskState.CANCELLED
    finally:
        await container.stop()


def test_checkpoint_preserves_evidence_and_removes_credentials() -> None:
    result = checkpoint_payload(
        {
            "text": "actual result",
            "api_key": "do-not-save",
            "nested": {"access_token": "do-not-save", "ordinary": 12},
            "protected": "do-not-save",
        },
        {"type": "object", "properties": {"protected": {"type": "string", "writeOnly": True}}},
    )
    nested = result["nested"]
    assert isinstance(nested, dict)
    assert result["text"] == "actual result" and nested["ordinary"] == 12
    assert "do-not-save" not in str(result)


async def test_definitely_rejected_write_can_retry_but_success_is_never_replayed(
    runtime_settings: Settings,
) -> None:
    import hashlib

    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.agent_tasks
        await service.stop()
        session = await container.sessions.create_session("default")
        task = await service.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal="修正未执行的写入",
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    resource_roots=["notes"],
                    allow_writes=True,
                    source_ref="owner:test",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            )
        )
        invocation = SkillInvocation(
            skill_id="workspace.files",
            capability="write",
            arguments={"path": "notes/retry.txt", "text": "actual"},
        )
        key = hashlib.sha256(
            json.dumps(
                invocation.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        await service.repository.begin_step(
            task.task_id,
            key,
            {
                "step_key": key,
                "side_effect": "write",
                "state": "settled",
                "ok": False,
                "effect_possible": False,
                "skill_id": "workspace.files",
                "capability": "write",
            },
        )
        service._closing = False
        task = await service.update_checkpoint(task.task_id, state=AgentTaskState.RUNNING)
        gateway = _TaskGateway(service, task)
        run = await gateway.invoke(session.session_id, invocation)
        result = await gateway.wait_for_terminal(run.skill_run_id)
        assert result.state.value == "succeeded", result.error
        again = await gateway.invoke(session.session_id, invocation)
        assert again.skill_run_id == result.skill_run_id
        assert len(await service.repository.steps(task.task_id)) == 2
        other = await container.sessions.create_session("default")
        assert result.result is not None and isinstance(result.result.data, dict)
        artifact = result.result.data["artifact"]
        assert isinstance(artifact, dict)
        visible = await container.artifacts.repository.list("local", other.session_id)
        assert str(visible[0].artifact_id) == artifact["artifact_id"]
    finally:
        await container.stop()


async def test_artifact_ancestor_symlink_and_content_growth_are_rejected(
    runtime_settings: Settings,
    tmp_path: Path,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        artifact = await container.artifacts.save(
            session.session_id, "test.txt", "text/plain", b"one"
        )
        _, path = await container.artifacts.resolve(session.session_id, artifact.artifact_id)
        path.write_bytes(b"changed")
        with pytest.raises(ValueError, match="changed"):
            await container.artifacts.read(session.session_id, artifact.artifact_id)
        path.write_bytes(b"one")
        moved = tmp_path / "moved"
        path.parent.rename(moved)
        path.parent.symlink_to(moved, target_is_directory=True)
        with pytest.raises(ValueError, match="symlink"):
            await container.artifacts.resolve(session.session_id, artifact.artifact_id)
    finally:
        await container.stop()


@pytest.mark.parametrize("mode", ["shadow", "model"])
async def test_idle_wait_is_judged_once_and_new_input_invalidates_decision(
    runtime_settings: Settings,
    mode: Literal["shadow", "model"],
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        await container.ambient.stop()
        clock = [0.0]
        activity = ActivityTracker(lambda: clock[0])
        container.ambient._activity = activity
        session = await container.sessions.create_session("default")
        activity.touch(session.session_id)
        body = container.companion_settings.get().model_dump(
            exclude={"schema_version", "updated_at"}
        )
        body.update(
            proactive_enabled=True,
            proactive_decision_mode=mode,
            proactive_idle_minutes=1,
            quiet_hours_enabled=False,
        )
        await container.companion_settings.update(CompanionSettingsUpdate.model_validate(body))
        clock[0] = 120
        calls: list[str] = []

        async def wait_decision(sid: UUID, source: str) -> DecisionRecord:
            calls.append(source)
            return DecisionRecord(action="wait", reason="nothing new", source_refs=[source])

        container.ambient.model_decider = wait_decision
        assert await container.ambient.evaluate_once() == 0
        assert await container.ambient.evaluate_once() == 0
        assert len(calls) == 1
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(sid: UUID, source: str) -> DecisionRecord:
            entered.set()
            await release.wait()
            return DecisionRecord(action="respond", reason="useful check-in", source_refs=[source])

        activity.touch(session.session_id)
        clock[0] += 120
        container.ambient.model_decider = delayed
        evaluation = asyncio.create_task(container.ambient.evaluate_once())
        await asyncio.wait_for(entered.wait(), 2)
        activity.touch(session.session_id)
        release.set()
        assert await evaluation == 0
        assert (await container.ambient.status()).proactive_today == 0
    finally:
        await container.stop()
