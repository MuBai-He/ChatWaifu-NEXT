"""Persisted operator settings. Channel adapters consume live permission probes."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

from chatwaifu_protocol.channel_settings import (
    ChannelRuntimePolicy,
    ChannelRuntimeSettingsSnapshot,
    ChannelRuntimeSettingsUpdate,
)

from chatwaifu_runtime.external_channels.service import ChannelConflictError


class ChannelSettingsRepository(Protocol):
    async def get(self) -> ChannelRuntimeSettingsSnapshot | None: ...

    async def save(
        self, policy: ChannelRuntimePolicy, expected_revision: int, now: datetime
    ) -> ChannelRuntimeSettingsSnapshot | None: ...


class ChannelSettingsService:
    def __init__(
        self, repository: ChannelSettingsRepository, defaults: ChannelRuntimePolicy
    ) -> None:
        self._repository = repository
        self._current = ChannelRuntimeSettingsSnapshot(revision=0, policy=defaults)
        self._lock = asyncio.Lock()
        self._apply: (
            Callable[[ChannelRuntimePolicy, ChannelRuntimePolicy], Awaitable[None]] | None
        ) = None

    def set_apply_callback(
        self, callback: Callable[[ChannelRuntimePolicy, ChannelRuntimePolicy], Awaitable[None]]
    ) -> None:
        self._apply = callback

    def get(self) -> ChannelRuntimeSettingsSnapshot:
        return self._current

    async def start(self) -> None:
        async with self._lock:
            saved = await self._repository.get()
            if saved is not None:
                await self._publish(saved)

    async def update(self, request: ChannelRuntimeSettingsUpdate) -> ChannelRuntimeSettingsSnapshot:
        async with self._lock:
            operation = asyncio.create_task(self._update(request))
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                # A committed update must publish while the writer lock is held.
                while not operation.done():
                    try:
                        await asyncio.shield(operation)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not operation.cancelled():
                    operation.exception()
                raise

    async def _update(
        self, request: ChannelRuntimeSettingsUpdate
    ) -> ChannelRuntimeSettingsSnapshot:
        saved = await self._repository.save(
            request.policy, request.expected_revision, datetime.now(UTC)
        )
        if saved is None:
            # Refresh the local probe after another writer won CAS.
            current = await self._repository.get()
            if current is not None:
                await self._publish(current)
            raise ChannelConflictError("渠道设置版本已变化，请刷新后重新操作。")
        await self._publish(saved)
        return saved

    async def _publish(self, saved: ChannelRuntimeSettingsSnapshot) -> None:
        previous = self._current.policy
        self._current = saved  # Permission revocation is visible before any await.
        if self._apply is not None:
            await self._apply(previous, saved.policy)
