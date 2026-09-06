"""Admission cancellation must not lose decoder capacity or deadlock handoff."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from chatwaifu_runtime.media.executor import MediaExecutionPool


@pytest.mark.asyncio
@pytest.mark.parametrize("during_handoff", [False, True])
async def test_cancelled_waiter_releases_exactly_its_owned_slot(
    monkeypatch: pytest.MonkeyPatch, during_handoff: bool
) -> None:
    pool = MediaExecutionPool(max_jobs=1, max_queue=1)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    entered_queue = asyncio.Event()
    calls: list[str] = []

    def blocker(**_kwargs: Any) -> str:
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        return "first"

    def next_job(**_kwargs: Any) -> str:
        calls.append("ran")
        return "next"

    async def queued() -> str:
        entered_queue.set()
        return await pool.run_bounded(next_job)

    active = asyncio.create_task(pool.run_bounded(blocker))
    waiting: asyncio.Task[str] | None = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        waiting = asyncio.create_task(queued())
        await asyncio.wait_for(entered_queue.wait(), 2)
        assert pool.queue_depth == 1
        if during_handoff:
            release_slot = pool._release_slot_locked

            def cancel_before_grant_callback() -> None:
                assert waiting is not None
                loop.call_soon_threadsafe(waiting.cancel)
                release_slot()

            monkeypatch.setattr(pool, "_release_slot_locked", cancel_before_grant_callback)
            release.set()
        else:
            waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiting, 2)
        assert pool.queue_depth == 0
        release.set()
        assert await asyncio.wait_for(active, 2) == "first"
        monkeypatch.undo()
        assert pool.active_jobs == 0
        assert calls == []
        assert await pool.run_bounded(next_job) == "next"
        assert pool.active_jobs == 0
    finally:
        release.set()
        if waiting is not None:
            waiting.cancel()
        await asyncio.gather(active, *([waiting] if waiting else []), return_exceptions=True)
        pool.stop()


@pytest.mark.asyncio
async def test_expired_admission_leaves_capacity_usable() -> None:
    pool = MediaExecutionPool(max_jobs=1, max_queue=1)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    def blocker(**_kwargs: Any) -> None:
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)

    active = asyncio.create_task(pool.run_bounded(blocker))
    try:
        await asyncio.wait_for(started.wait(), 2)
        with pytest.raises(TimeoutError):
            await pool.run_bounded(blocker, deadline=time.monotonic() - 1)
        assert pool.queue_depth == 0
        release.set()
        await asyncio.wait_for(active, 2)
        assert pool.active_jobs == 0
    finally:
        release.set()
        await asyncio.gather(active, return_exceptions=True)
        pool.stop()
