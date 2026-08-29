"""Fault injection for Runtime Skill terminal-state and cancellation guarantees."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.skills import SkillInvocation, SkillRunState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.runtime_skills import permissions as permission_module


def _status_invocation() -> SkillInvocation:
    return SkillInvocation(skill_id="runtime.status", capability="read", arguments={})


@pytest.mark.asyncio
async def test_create_run_commit_then_cancellation_is_compensated(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.runtime_skills
        repository = service._repository  # pyright: ignore[reportPrivateUsage]
        original_create = repository.create_run
        created_run_id: UUID | None = None

        async def commit_then_cancel(values: dict[str, object]) -> None:
            nonlocal created_run_id
            created_run_id = UUID(str(values["skill_run_id"]))
            await original_create(values)
            raise asyncio.CancelledError

        monkeypatch.setattr(repository, "create_run", commit_then_cancel)
        session = await container.sessions.create_session("default")

        with pytest.raises(asyncio.CancelledError):
            await service.invoke(session.session_id, _status_invocation())

        assert created_run_id is not None
        terminal = await asyncio.wait_for(service.wait_for_terminal(created_run_id), timeout=1)
        assert terminal.state is SkillRunState.FAILED
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_confirmation_commit_then_cancellation_terminalizes_detached_run(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.runtime_skills
        await service.install_example_plugin("local-echo")
        session = await container.sessions.create_session("default")
        waiting = await service.invoke(
            session.session_id,
            SkillInvocation(
                skill_id="local.echo",
                capability="append_note",
                arguments={"text": "must remain detached"},
            ),
        )
        assert waiting.confirmation_request_id is not None
        repository = service._repository  # pyright: ignore[reportPrivateUsage]
        original_decide = repository.decide_permission_request

        async def commit_then_cancel(**kwargs: object) -> bool:
            committed = await original_decide(**kwargs)  # pyright: ignore[reportArgumentType]
            assert committed
            raise asyncio.CancelledError

        monkeypatch.setattr(repository, "decide_permission_request", commit_then_cancel)

        with pytest.raises(asyncio.CancelledError):
            await service.decide_confirmation(waiting.confirmation_request_id, "allow_once")

        terminal = await asyncio.wait_for(
            service.wait_for_terminal(waiting.skill_run_id), timeout=1
        )
        assert terminal.state is SkillRunState.CANCELLED
        request = await repository.permission_request(waiting.confirmation_request_id)
        assert request is not None
        assert request["state"] == "decided"
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_started_event_failure_does_not_strand_running_run(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.runtime_skills
        original_emit = service._emit  # pyright: ignore[reportPrivateUsage]

        async def fail_started(
            session_id: UUID,
            event_type: str,
            payload: dict[str, object],
            run_id: UUID,
        ) -> None:
            if event_type == "skill.run_started":
                raise RuntimeError("injected event-store failure")
            await original_emit(session_id, event_type, payload, run_id)

        monkeypatch.setattr(service, "_emit", fail_started)
        session = await container.sessions.create_session("default")
        created = await service.invoke(session.session_id, _status_invocation())

        terminal = await asyncio.wait_for(
            service.wait_for_terminal(created.skill_run_id), timeout=1
        )
        assert terminal.state is SkillRunState.SUCCEEDED
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_noninterruptible_cancel_cas_cannot_cross_created_to_running_race(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    release_running = asyncio.Event()
    release_cancel_cas = asyncio.Event()
    release_adapter = asyncio.Event()
    await container.start()
    try:
        service = container.runtime_skills
        entry = service._registry.get("runtime.status")  # pyright: ignore[reportPrivateUsage]
        assert entry is not None
        service._registry._entries["runtime.status"] = replace(  # pyright: ignore[reportPrivateUsage]
            entry,
            definition=entry.definition.model_copy(update={"interruptible": False}),
        )
        repository = service._repository  # pyright: ignore[reportPrivateUsage]
        original_mark_running = repository.mark_run_running
        original_mark_cancelling = repository.mark_run_cancelling
        running_attempted = asyncio.Event()
        cancel_attempted = asyncio.Event()
        adapter_started = asyncio.Event()
        original_invoke = service._builtin.invoke  # pyright: ignore[reportPrivateUsage]

        async def paused_running(run_id: UUID, now: str) -> bool:
            running_attempted.set()
            await release_running.wait()
            return await original_mark_running(run_id, now)

        async def paused_cancel(run_id: UUID, now: str, *, allow_running: bool) -> bool:
            assert allow_running is False
            cancel_attempted.set()
            await release_cancel_cas.wait()
            return await original_mark_cancelling(run_id, now, allow_running=allow_running)

        async def delayed_status(name: str, arguments: JsonObject) -> JsonObject:
            adapter_started.set()
            await release_adapter.wait()
            return await original_invoke(name, arguments)

        monkeypatch.setattr(repository, "mark_run_running", paused_running)
        monkeypatch.setattr(repository, "mark_run_cancelling", paused_cancel)
        monkeypatch.setattr(service._builtin, "invoke", delayed_status)  # pyright: ignore[reportPrivateUsage]
        session = await container.sessions.create_session("default")
        created = await service.invoke(session.session_id, _status_invocation())
        await asyncio.wait_for(running_attempted.wait(), timeout=1)
        cancellation = asyncio.create_task(service.cancel(created.skill_run_id))
        await asyncio.wait_for(cancel_attempted.wait(), timeout=1)

        release_running.set()
        await asyncio.wait_for(adapter_started.wait(), timeout=1)
        release_cancel_cas.set()
        with pytest.raises(ValueError, match="not interruptible"):
            await cancellation

        release_adapter.set()
        terminal = await asyncio.wait_for(
            service.wait_for_terminal(created.skill_run_id), timeout=1
        )
        assert terminal.state is SkillRunState.SUCCEEDED
    finally:
        release_running.set()
        release_cancel_cas.set()
        release_adapter.set()
        await container.stop()


@pytest.mark.asyncio
async def test_cancel_cas_commit_then_cancellation_still_converges(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    release_adapter = asyncio.Event()
    await container.start()
    try:
        service = container.runtime_skills
        adapter_started = asyncio.Event()

        async def delayed_status(_: str, __: JsonObject) -> JsonObject:
            adapter_started.set()
            await release_adapter.wait()
            return {"ignored": True}

        monkeypatch.setattr(service._builtin, "invoke", delayed_status)  # pyright: ignore[reportPrivateUsage]
        session = await container.sessions.create_session("default")
        created = await service.invoke(session.session_id, _status_invocation())
        await asyncio.wait_for(adapter_started.wait(), timeout=1)
        repository = service._repository  # pyright: ignore[reportPrivateUsage]
        original_mark = repository.mark_run_cancelling

        async def commit_then_cancel(run_id: UUID, now: str, *, allow_running: bool) -> bool:
            transitioned = await original_mark(run_id, now, allow_running=allow_running)
            assert transitioned
            raise asyncio.CancelledError

        monkeypatch.setattr(repository, "mark_run_cancelling", commit_then_cancel)

        with pytest.raises(asyncio.CancelledError):
            await service.cancel(created.skill_run_id)

        terminal = await asyncio.wait_for(
            service.wait_for_terminal(created.skill_run_id), timeout=1
        )
        assert terminal.state is SkillRunState.CANCELLED
    finally:
        release_adapter.set()
        await container.stop()


@pytest.mark.asyncio
async def test_cancelled_run_never_emits_late_tool_completion(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    release_adapter = asyncio.Event()
    await container.start()
    try:
        service = container.runtime_skills
        adapter_started = asyncio.Event()
        cancellation_swallowed = asyncio.Event()
        original_invoke = service._builtin.invoke  # pyright: ignore[reportPrivateUsage]

        async def stubborn_status(name: str, arguments: JsonObject) -> JsonObject:
            adapter_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_swallowed.set()
            await release_adapter.wait()
            return await original_invoke(name, arguments)

        monkeypatch.setattr(service._builtin, "invoke", stubborn_status)  # pyright: ignore[reportPrivateUsage]
        subscription = container.event_hub.subscribe(
            lambda event: event.get("event_type") == "tool.call_completed",
            queue_size=4,
        )
        session = await container.sessions.create_session("default")
        created = await service.invoke(session.session_id, _status_invocation())
        await asyncio.wait_for(adapter_started.wait(), timeout=1)
        execution = service._tasks[created.skill_run_id]  # pyright: ignore[reportPrivateUsage]

        cancelled = await service.cancel(created.skill_run_id)
        assert cancelled.state is SkillRunState.CANCELLED
        await asyncio.wait_for(cancellation_swallowed.wait(), timeout=1)
        release_adapter.set()
        await asyncio.wait_for(execution, timeout=1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(subscription.receive(), timeout=0.05)
        container.event_hub.unsubscribe(subscription)
    finally:
        release_adapter.set()
        await container.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["audit", "event"])
async def test_post_side_effect_observability_failure_preserves_success(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.runtime_skills
        if fault == "audit":
            repository = service._repository  # pyright: ignore[reportPrivateUsage]

            async def fail_audit(_: dict[str, object]) -> bool:
                raise RuntimeError("injected audit failure")

            monkeypatch.setattr(repository, "finish_tool_call", fail_audit)
        else:
            original_emit = service._emit  # pyright: ignore[reportPrivateUsage]

            async def fail_completion_events(
                session_id: UUID,
                event_type: str,
                payload: dict[str, object],
                run_id: UUID,
            ) -> None:
                if event_type in {"tool.call_completed", "skill.run_completed"}:
                    raise RuntimeError("injected completion-event failure")
                await original_emit(session_id, event_type, payload, run_id)

            monkeypatch.setattr(service, "_emit", fail_completion_events)
        session = await container.sessions.create_session("default")
        created = await service.invoke(session.session_id, _status_invocation())

        terminal = await asyncio.wait_for(
            service.wait_for_terminal(created.skill_run_id), timeout=1
        )
        assert terminal.state is SkillRunState.SUCCEEDED
        assert terminal.result is not None
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_validated_outcome_commit_is_shielded_from_task_cancellation(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    release_commit = asyncio.Event()
    await container.start()
    try:
        service = container.runtime_skills
        repository = service._repository  # pyright: ignore[reportPrivateUsage]
        original_complete = repository.complete_run
        commit_started = asyncio.Event()

        async def delayed_complete(run_id: UUID, result_json: str, now: str) -> bool:
            commit_started.set()
            await release_commit.wait()
            return await original_complete(run_id, result_json, now)

        monkeypatch.setattr(repository, "complete_run", delayed_complete)
        session = await container.sessions.create_session("default")
        created = await service.invoke(session.session_id, _status_invocation())
        execution = service._tasks[created.skill_run_id]  # pyright: ignore[reportPrivateUsage]
        await asyncio.wait_for(commit_started.wait(), timeout=1)

        execution.cancel()
        release_commit.set()
        with pytest.raises(asyncio.CancelledError):
            await execution
        terminal = await asyncio.wait_for(
            service.wait_for_terminal(created.skill_run_id), timeout=1
        )
        assert terminal.state is SkillRunState.SUCCEEDED
        assert terminal.result is not None
    finally:
        release_commit.set()
        await container.stop()


@pytest.mark.asyncio
async def test_stop_is_bounded_and_terminalizes_task_that_ignores_cancellation(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    release_adapter = asyncio.Event()
    await container.start()
    try:
        service = container.runtime_skills
        adapter_started = asyncio.Event()

        async def stubborn_status(_: str, __: JsonObject) -> JsonObject:
            adapter_started.set()
            while not release_adapter.is_set():
                try:
                    await release_adapter.wait()
                except asyncio.CancelledError:
                    continue
            return {"ignored": True}

        monkeypatch.setattr(service._builtin, "invoke", stubborn_status)  # pyright: ignore[reportPrivateUsage]
        session = await container.sessions.create_session("default")
        created = await service.invoke(session.session_id, _status_invocation())
        await asyncio.wait_for(adapter_started.wait(), timeout=1)
        execution = service._tasks[created.skill_run_id]  # pyright: ignore[reportPrivateUsage]

        started_at = time.monotonic()
        await asyncio.wait_for(service.stop(), timeout=1)
        assert time.monotonic() - started_at < 0.9
        terminal = await service.get_run(created.skill_run_id)
        assert terminal.state is SkillRunState.CANCELLED

        release_adapter.set()
        await asyncio.wait_for(execution, timeout=1)
    finally:
        release_adapter.set()
        await container.stop()


@pytest.mark.asyncio
async def test_expiry_event_failure_does_not_break_pending_confirmation_read(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        service = container.runtime_skills
        await service.install_example_plugin("local-echo")
        session = await container.sessions.create_session("default")
        waiting = await service.invoke(
            session.session_id,
            SkillInvocation(
                skill_id="local.echo",
                capability="append_note",
                arguments={"text": "expire me"},
            ),
        )
        future = datetime.now(UTC) + timedelta(minutes=10)
        monkeypatch.setattr(permission_module, "_now", lambda: future)
        original_emit = service._emit  # pyright: ignore[reportPrivateUsage]

        async def fail_expiry(
            session_id: UUID,
            event_type: str,
            payload: dict[str, object],
            run_id: UUID,
        ) -> None:
            if event_type == "skill.run_expired":
                raise RuntimeError("injected expiry event failure")
            await original_emit(session_id, event_type, payload, run_id)

        monkeypatch.setattr(service, "_emit", fail_expiry)

        assert await service.pending_confirmations(session.session_id) == []
        terminal = await service.get_run(waiting.skill_run_id)
        assert terminal.state is SkillRunState.EXPIRED
    finally:
        await container.stop()
