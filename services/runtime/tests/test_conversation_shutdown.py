"""Shutdown joins irrevocable completion and its memory projection tail."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import json
from typing import Literal

import pytest
from chatwaifu_protocol.events import EventModel
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationTurnOptions
from chatwaifu_runtime.memory.service import UserTurnMemoryObservation


@pytest.mark.parametrize("phase", ["completion_publication", "memory_projection"])
async def test_shutdown_joins_completing_generation_before_releasing_its_dependencies(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    phase: Literal["completion_publication", "memory_projection"],
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    completion_blocked = asyncio.Event()
    release = asyncio.Event()
    completion_cancelled = asyncio.Event()
    stop_entered = asyncio.Event()
    stop_returned = asyncio.Event()
    original_publish = container.event_publisher.publish_persisted
    original_enqueue = container.memory.enqueue_user_turn
    original_stop = container.conversation.stop
    stop_task: asyncio.Task[None] | None = None
    generation_task: asyncio.Task[None] | None = None

    async def hold_completion() -> None:
        completion_blocked.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            completion_cancelled.set()
            raise

    async def publish(event: EventModel) -> None:
        if (
            phase == "completion_publication"
            and event.event_type == "assistant.generation_completed"
        ):
            await hold_completion()
        await original_publish(event)

    async def enqueue(observation: UserTurnMemoryObservation) -> None:
        if phase == "memory_projection":
            await hold_completion()
        await original_enqueue(observation)

    async def stop() -> None:
        stop_entered.set()
        await original_stop()
        stop_returned.set()

    monkeypatch.setattr(container.event_publisher, "publish_persisted", publish)
    monkeypatch.setattr(container.memory, "enqueue_user_turn", enqueue)
    monkeypatch.setattr(container.conversation, "stop", stop)
    try:
        session = await container.sessions.create_session("default")
        accepted = await container.conversation.submit_text(
            session.session_id,
            "A local deterministic shutdown regression.",
            options=ConversationTurnOptions(output_modes=frozenset({"text"}), allow_tools=False),
        )
        await asyncio.wait_for(completion_blocked.wait(), 15)
        active = container.conversation._active[session.session_id]
        generation_task = active.task
        assert active.completing and generation_task is not None

        stop_task = asyncio.create_task(container.conversation.stop())
        await asyncio.wait_for(stop_entered.wait(), 15)
        # Entering stop runs its synchronous fence before this waiter resumes.
        # It must then join the held completion instead of releasing ownership.
        assert not stop_returned.is_set()
        assert container.conversation._active[session.session_id] is active
        assert not generation_task.done() and not completion_cancelled.is_set()

        release.set()
        await asyncio.wait_for(stop_task, 15)
        assert generation_task.done() and not generation_task.cancelled()
        assert not completion_cancelled.is_set()
        assert container.conversation.active_count == 0
        result = await container.conversation_repository.generation_result(accepted.generation_id)
        assert result is not None and result.state is GenerationState.COMPLETED
        rows = await container.database.fetchall(
            "SELECT event_type, envelope_json FROM events WHERE session_id = ?",
            (str(session.session_id),),
        )
        event_types = [
            str(row["event_type"])
            for row in rows
            if json.loads(str(row["envelope_json"])).get("generation_id")
            == str(accepted.generation_id)
        ]
        assert event_types.count("assistant.generation_completed") == 1
        assert "assistant.generation_cancelled" not in event_types
        assert "conversation.interrupted" not in event_types
    finally:
        release.set()
        if generation_task is not None:
            await asyncio.gather(generation_task, return_exceptions=True)
        if stop_task is not None:
            await asyncio.gather(stop_task, return_exceptions=True)
        await container.stop()
