"""Cancellation-safe idle unloading with lazy model wake-up."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from chatwaifu_runtime.companion.activity import ActivityTracker
from chatwaifu_runtime.companion.models import ResourceStatus
from chatwaifu_runtime.companion.settings import CompanionSettingsService


class IdleTtsController(Protocol):
    async def deactivate_idle(self) -> bool: ...

    async def refresh_capabilities(self) -> dict[str, str]: ...


class IdleSttController(Protocol):
    async def deactivate(self) -> bool: ...


class ResourceLifecycleService:
    def __init__(
        self,
        settings: CompanionSettingsService,
        activity: ActivityTracker,
        tts: IdleTtsController,
        stt: IdleSttController,
        *,
        busy: Callable[[], bool] = lambda: False,
    ) -> None:
        self._settings = settings
        self._activity = activity
        self._tts = tts
        self._stt = stt
        self._busy = busy
        self._task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()
        self._state = "active"
        self._sleep_count = 0
        self._last_sleep_at: datetime | None = None
        self._last_wake_at: datetime | None = None
        self._activity_revision = 0

    def set_busy_probe(self, busy: Callable[[], bool]) -> None:
        self._busy = busy

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="companion-resource-lifecycle")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        self._state = "stopping"
        self._changed.set()
        if task is not None:
            task.cancel("runtime_stopping")
            try:
                await task
            except asyncio.CancelledError:
                pass

    def touch(self) -> None:
        self._activity.touch()
        self._activity_revision += 1
        if self._state == "sleeping":
            self._state = "active"
            self._last_wake_at = datetime.now(UTC)
        self._changed.set()

    async def sleep_now(self) -> ResourceStatus:
        if self._busy():
            raise RuntimeError("模型仍在处理当前回合，暂时不能休眠")
        activity_revision = self._activity_revision
        await self._tts.deactivate_idle()
        await self._stt.deactivate()
        if self._busy() or self._activity_revision != activity_revision:
            self.touch()
            raise RuntimeError("休眠期间出现了新的活动，已取消休眠")
        self._state = "sleeping"
        self._sleep_count += 1
        self._last_sleep_at = datetime.now(UTC)
        return self.status()

    async def wake(self) -> ResourceStatus:
        await self._tts.refresh_capabilities()
        self.touch()
        return self.status()

    def status(self) -> ResourceStatus:
        return ResourceStatus(
            state=self._state,  # type: ignore[arg-type]
            idle_seconds=int(self._activity.global_idle_seconds()),
            sleep_count=self._sleep_count,
            last_sleep_at=self._last_sleep_at,
            last_wake_at=self._last_wake_at,
        )

    async def _run(self) -> None:
        try:
            while True:
                settings = self._settings.get()
                timeout = settings.resource_idle_minutes * 60
                remaining = max(0.05, timeout - self._activity.global_idle_seconds())
                self._changed.clear()
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=remaining)
                    continue
                except TimeoutError:
                    pass
                if settings.resource_sleep_enabled and self._state == "active" and not self._busy():
                    try:
                        await self.sleep_now()
                    except RuntimeError:
                        continue
                else:
                    # Re-evaluate after one minute if work prevented this idle transition.
                    try:
                        await asyncio.wait_for(self._changed.wait(), timeout=60)
                    except TimeoutError:
                        pass
        except asyncio.CancelledError:
            raise
