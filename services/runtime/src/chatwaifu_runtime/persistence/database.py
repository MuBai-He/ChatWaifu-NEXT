"""Single-process SQLite lifecycle and transaction boundary."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.migrations import MIGRATIONS


class Database:
    def __init__(self, path: Path, config: StorageConfig) -> None:
        self._path = path
        self._config = config
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._connection is not None:
            return
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self._path)
        connection.row_factory = aiosqlite.Row
        await connection.execute(f"PRAGMA busy_timeout={self._config.busy_timeout_ms}")
        await connection.execute(
            f"PRAGMA foreign_keys={'ON' if self._config.foreign_keys else 'OFF'}"
        )
        if str(self._path) != ":memory:":
            await connection.execute(f"PRAGMA journal_mode={self._config.journal_mode.upper()}")
        self._connection = connection
        await self.migrate()

    async def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            await connection.close()

    async def migrate(self) -> None:
        connection = self._require_connection()
        async with self._lock:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
            )
            rows = await connection.execute_fetchall("SELECT version FROM schema_migrations")
            applied = {int(row["version"]) for row in rows}
            for version, script in MIGRATIONS:
                if version in applied:
                    continue
                await connection.executescript(script)
                await connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                )
            await connection.commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[aiosqlite.Connection]:
        connection = self._require_connection()
        async with self._lock:
            try:
                cursor = await _finish_before_cancelling(connection.execute("BEGIN IMMEDIATE"))
            except asyncio.CancelledError:
                await _finish_before_cancelling(connection.rollback())
                raise
            await cursor.close()
            try:
                yield connection
            except BaseException:
                await _finish_before_cancelling(connection.rollback())
                raise
            else:
                await _finish_before_cancelling(connection.commit())

    async def fetchone(self, query: str, parameters: Sequence[object] = ()) -> aiosqlite.Row | None:
        connection = self._require_connection()
        async with self._lock:
            cursor = await connection.execute(query, parameters)
            row = await cursor.fetchone()
            await cursor.close()
            return row

    async def fetchall(self, query: str, parameters: Sequence[object] = ()) -> list[aiosqlite.Row]:
        connection = self._require_connection()
        async with self._lock:
            cursor = await connection.execute(query, parameters)
            rows = await cursor.fetchall()
            await cursor.close()
            return list(rows)

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("database is not open")
        return self._connection


async def _finish_before_cancelling[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    """Finish a queued SQLite operation before propagating task cancellation."""

    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
