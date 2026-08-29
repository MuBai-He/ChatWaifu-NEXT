"""Persistence, sequence, and outbox tests."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.persistence.database import (
    Database,
    MigrationCatalogError,
    MigrationChecksumError,
)


@pytest.mark.asyncio
async def test_concurrent_append_assigns_unique_monotonic_sequences(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")

        async def append(index: int) -> int:
            event = await container.event_store.append(
                GenericCoreEvent(
                    event_id=uuid4(),
                    event_type="assistant.text_delta",
                    session_id=session.session_id,
                    occurred_at=datetime.now(UTC),
                    source="test",
                    privacy=PrivacyLevel.LOCAL,
                    payload={"text": f"message-{index}"},
                )
            )
            assert event.sequence is not None
            return event.sequence

        sequences = await asyncio.gather(*(append(index) for index in range(20)))
        assert sorted(sequences) == list(range(2, 22))
        stored = await container.event_store.read_stream(session.session_id, limit=100)
        assert [event["sequence"] for event in stored] == list(range(1, 22))
        pending = await container.event_store.pending_outbox(limit=100)
        assert len(pending) == 20
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_failed_migration_rolls_back_schema_and_can_retry(tmp_path: Path) -> None:
    path = tmp_path / "atomic-migration.db"
    storage = StorageConfig(database_path=path)
    first = "CREATE TABLE stable_record (value TEXT NOT NULL);"
    broken = """
    CREATE TABLE rolled_back_record (value TEXT NOT NULL);
    INSERT INTO table_that_does_not_exist(value) VALUES ('boom');
    """
    database = Database(path, storage, migrations=((1, first), (2, broken)))

    with pytest.raises(aiosqlite.OperationalError):
        await database.open()

    with sqlite3.connect(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        applied = list(connection.execute("SELECT version FROM schema_migrations ORDER BY version"))
    assert "stable_record" in tables
    assert "rolled_back_record" not in tables
    assert applied == [(1,)]

    retry = Database(
        path,
        storage,
        migrations=(
            (1, first),
            (2, "CREATE TABLE rolled_back_record (value TEXT NOT NULL);"),
        ),
    )
    await retry.open()
    await retry.close()
    with sqlite3.connect(path) as connection:
        assert list(
            connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ) == [(1,), (2,)]
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rolled_back_record'"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_applied_migration_checksum_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "checksum-migration.db"
    storage = StorageConfig(database_path=path)
    database = Database(
        path,
        storage,
        migrations=((1, "CREATE TABLE immutable_record (value TEXT NOT NULL);"),),
    )
    await database.open()
    await database.close()

    changed = Database(
        path,
        storage,
        migrations=((1, "CREATE TABLE immutable_record (value INTEGER NOT NULL);"),),
    )
    with pytest.raises(MigrationChecksumError, match="immutable"):
        await changed.open()


@pytest.mark.asyncio
async def test_legacy_migration_ledger_is_upgraded_and_newer_database_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-migration.db"
    script = "CREATE TABLE legacy_record (value TEXT NOT NULL);"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
        connection.execute(script)

    storage = StorageConfig(database_path=path)
    database = Database(path, storage, migrations=((1, script),))
    await database.open()
    await database.close()
    with sqlite3.connect(path) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(schema_migrations)")
        }
        ledger = connection.execute(
            "SELECT checksum, applied_at FROM schema_migrations WHERE version = 1"
        ).fetchone()
        connection.execute(
            "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (99, 'x', 'now')"
        )
    assert {"version", "checksum", "applied_at"}.issubset(columns)
    assert ledger is not None and all(ledger)

    older_runtime = Database(path, storage, migrations=((1, script),))
    with pytest.raises(MigrationCatalogError, match="newer"):
        await older_runtime.open()
