"""Cancellation at the SQLite worker/result handoff must not strand RETURNING cursors."""
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings


async def test_cancelled_sequence_allocation_does_not_poison_next_commit(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    retained_results: list[object] = []
    try:
        session = await container.sessions.create_session("default")
        connection = container.database._require_connection()
        original = cast(
            Callable[..., Awaitable[object]],
            connection._execute,  # pyright: ignore[reportUnknownMemberType]
        )
        returned = asyncio.Event()
        armed = True

        async def handoff(
            function: Callable[..., object], *args: object, **kwargs: object
        ) -> object:
            nonlocal armed
            result = await original(function, *args, **kwargs)
            if (
                armed
                and args
                and isinstance(args[0], str)
                and "RETURNING next_sequence - 1" in args[0]
            ):
                armed = False
                # Model a result retained by a queued worker callback/traceback while
                # its cancelled consumer hasn't received it. Do not rely on GC timing.
                retained_results.append(result)
                returned.set()
                await asyncio.Event().wait()
            return result

        monkeypatch.setattr(connection, "_execute", handoff)
        abandoned = GenericCoreEvent(
            event_id=uuid4(),
            event_type="assistant.audio_stream_started",
            occurred_at=datetime.now(UTC),
            session_id=session.session_id,
            source="test",
            payload={},
        )
        task = asyncio.create_task(container.event_store.append(abandoned))
        await asyncio.wait_for(returned.wait(), 2)
        task.cancel("test interruption during sequence allocation")
        with pytest.raises(asyncio.CancelledError):
            await task

        # The rollback restores sequence allocation and the next event/outbox commit
        # works even while the abandoned result is still retained above.
        accepted = await container.event_store.append(
            abandoned.model_copy(update={"event_id": uuid4()})
        )
        assert accepted.sequence == 2
        stream = await container.event_store.read_stream(session.session_id)
        assert [event["sequence"] for event in stream] == [1, 2]
        assert str(abandoned.event_id) not in [event["event_id"] for event in stream]
        pending = await container.event_store.pending_outbox()
        assert len(pending) == 1 and pending[0]["event_id"] == str(accepted.event_id)
    finally:
        await container.stop()
        retained_results.clear()
