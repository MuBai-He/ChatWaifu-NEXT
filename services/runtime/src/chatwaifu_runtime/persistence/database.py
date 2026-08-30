"""Single-process SQLite lifecycle and transaction boundary."""

import asyncio
import hashlib
from collections.abc import AsyncGenerator, Awaitable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.migrations import MIGRATIONS

type Migration = tuple[int, str]


class MigrationError(RuntimeError):
    """Base class for migration catalog and compatibility failures."""


class MigrationCatalogError(MigrationError):
    """The application migration catalog is invalid or older than the database."""


class MigrationChecksumError(MigrationError):
    """An already-applied migration no longer matches its immutable script."""


class Database:
    def __init__(
        self,
        path: Path,
        config: StorageConfig,
        *,
        migrations: Sequence[Migration] = MIGRATIONS,
    ) -> None:
        self._path = path
        self._config = config
        self._migrations = tuple(migrations)
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._connection is not None:
            return
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self._path)
        try:
            connection.row_factory = aiosqlite.Row
            await _execute_and_close(
                connection, f"PRAGMA busy_timeout={self._config.busy_timeout_ms}"
            )
            await _execute_and_close(
                connection,
                f"PRAGMA foreign_keys={'ON' if self._config.foreign_keys else 'OFF'}",
            )
            if str(self._path) != ":memory:":
                await _execute_and_close(
                    connection, f"PRAGMA journal_mode={self._config.journal_mode.upper()}"
                )
            self._connection = connection
            await self.migrate()
        except BaseException:
            self._connection = None
            await _finish_before_cancelling(connection.close())
            raise

    async def close(self) -> None:
        async with self._lock:
            connection = self._connection
            if connection is None:
                return
            await _finish_before_cancelling(connection.close())
            self._connection = None

    async def migrate(self) -> None:
        connection = self._require_connection()
        async with self._lock:
            catalog = _validated_catalog(self._migrations)
            await self._reject_unknown_ledger_versions(connection, catalog)
            await self._prepare_migration_ledger(connection)
            rows = await connection.execute_fetchall(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            )
            applied = {int(row["version"]): row["checksum"] for row in rows}
            known_versions = {version for version, _script, _checksum in catalog}
            unknown_versions = sorted(set(applied) - known_versions)
            if unknown_versions:
                raise MigrationCatalogError(
                    "database contains migration versions newer than this Runtime: "
                    + ", ".join(str(version) for version in unknown_versions)
                )

            for version, _script, checksum in catalog:
                recorded = applied.get(version)
                if recorded is None:
                    continue
                if str(recorded) != checksum:
                    raise MigrationChecksumError(
                        f"migration {version} checksum mismatch; applied migrations are immutable"
                    )

            for version, script, checksum in catalog:
                if version in applied:
                    continue
                await self._apply_migration(connection, version, script, checksum)

    async def _reject_unknown_ledger_versions(
        self,
        connection: aiosqlite.Connection,
        catalog: tuple[tuple[int, str, str], ...],
    ) -> None:
        """Inspect an existing ledger without mutating a possibly newer database."""

        table = await connection.execute_fetchall(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        )
        if not table:
            return
        columns = {
            str(row["name"])
            for row in await connection.execute_fetchall("PRAGMA table_info(schema_migrations)")
        }
        if "version" not in columns:
            raise MigrationCatalogError("schema_migrations ledger has no version column")
        rows = await connection.execute_fetchall("SELECT version FROM schema_migrations")
        known_versions = {version for version, _script, _checksum in catalog}
        unknown_versions = sorted(
            int(row["version"]) for row in rows if row["version"] not in known_versions
        )
        if unknown_versions:
            raise MigrationCatalogError(
                "database contains migration versions newer than this Runtime: "
                + ", ".join(str(version) for version in unknown_versions)
            )

    async def _prepare_migration_ledger(self, connection: aiosqlite.Connection) -> None:
        try:
            await _finish_before_cancelling(
                connection.executescript(
                    """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    checksum TEXT,
                    applied_at TEXT
                );
                COMMIT;
                """
                )
            )
        except BaseException:
            await _finish_before_cancelling(connection.rollback())
            raise
        columns = {
            str(row["name"])
            for row in await connection.execute_fetchall("PRAGMA table_info(schema_migrations)")
        }
        additions: list[str] = []
        if "checksum" not in columns:
            additions.append("ALTER TABLE schema_migrations ADD COLUMN checksum TEXT;")
        if "applied_at" not in columns:
            additions.append("ALTER TABLE schema_migrations ADD COLUMN applied_at TEXT;")
        if additions:
            script = "BEGIN IMMEDIATE;\n" + "\n".join(additions) + "\nCOMMIT;"
            try:
                await _finish_before_cancelling(connection.executescript(script))
            except BaseException:
                await _finish_before_cancelling(connection.rollback())
                raise

        rows = await connection.execute_fetchall(
            "SELECT version FROM schema_migrations WHERE checksum IS NULL OR applied_at IS NULL"
        )
        if not rows:
            return
        catalog = {
            version: checksum for version, _script, checksum in _validated_catalog(self._migrations)
        }
        now = datetime.now(UTC).isoformat()
        try:
            await _execute_and_close(connection, "BEGIN IMMEDIATE")
            for row in rows:
                version = int(row["version"])
                checksum = catalog.get(version)
                if checksum is None:
                    raise MigrationCatalogError(
                        f"database contains unknown migration version {version}"
                    )
                await _execute_and_close(
                    connection,
                    """
                        UPDATE schema_migrations
                        SET checksum = COALESCE(checksum, ?),
                            applied_at = COALESCE(applied_at, ?)
                        WHERE version = ?
                        """,
                    (checksum, now, version),
                )
            await _finish_before_cancelling(connection.commit())
        except BaseException:
            await _finish_before_cancelling(connection.rollback())
            raise

    async def _apply_migration(
        self,
        connection: aiosqlite.Connection,
        version: int,
        script: str,
        checksum: str,
    ) -> None:
        applied_at = datetime.now(UTC).isoformat()
        ledger_insert = (
            "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES "
            f"({version}, '{checksum}', '{_sql_literal(applied_at)}');"
        )
        atomic_script = f"BEGIN IMMEDIATE;\n{script.rstrip()}\n{ledger_insert}\nCOMMIT;"
        try:
            await _finish_before_cancelling(connection.executescript(atomic_script))
        except BaseException:
            await _finish_before_cancelling(connection.rollback())
            raise

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[aiosqlite.Connection]:
        connection = self._require_connection()
        async with self._lock:
            try:
                cursor = await _finish_before_cancelling(connection.execute("BEGIN IMMEDIATE"))
                await _finish_before_cancelling(cursor.close())
            except asyncio.CancelledError:
                await _finish_before_cancelling(connection.rollback())
                raise
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

    async def execute(self, query: str, parameters: Sequence[object] = ()) -> int:
        """Execute one mutation inside the shared atomic transaction boundary."""

        async with self.transaction() as connection:
            cursor = await connection.execute(query, parameters)
            rowcount = cursor.rowcount
            await cursor.close()
        return rowcount

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("database is not open")
        return self._connection


def _validated_catalog(migrations: Sequence[Migration]) -> tuple[tuple[int, str, str], ...]:
    catalog: list[tuple[int, str, str]] = []
    previous = 0
    for version, script in migrations:
        if version <= 0 or version <= previous:
            raise MigrationCatalogError(
                "migration versions must be unique positive integers in ascending order"
            )
        if not script.strip():
            raise MigrationCatalogError(f"migration {version} script must not be blank")
        catalog.append((version, script, hashlib.sha256(script.encode("utf-8")).hexdigest()))
        previous = version
    return tuple(catalog)


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


async def _finish_before_cancelling[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    """Finish a queued SQLite operation before propagating task cancellation."""

    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def _execute_and_close(
    connection: aiosqlite.Connection,
    query: str,
    parameters: Sequence[object] = (),
) -> None:
    cursor = await _finish_before_cancelling(connection.execute(query, parameters))
    await _finish_before_cancelling(cursor.close())
