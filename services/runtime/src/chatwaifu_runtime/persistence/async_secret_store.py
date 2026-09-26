"""Nonblocking facade that finishes secret I/O before releasing caller locks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from chatwaifu_runtime.persistence.atomic_secret_store import AtomicSecretStore


async def _finish_io[T](operation: Callable[[], T]) -> T:
    task = asyncio.create_task(asyncio.to_thread(operation))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class AsyncSecretStore:
    def __init__(self, store: AtomicSecretStore) -> None:
        self._store = store

    async def get(self, name: str) -> str | None:
        return await _finish_io(lambda: self._store.get(name))

    async def set(self, name: str, value: str | None) -> None:
        await _finish_io(lambda: self._store.set(name, value))

    async def prune(self, retained_names: set[str]) -> None:
        await _finish_io(lambda: self._store.prune(retained_names))
