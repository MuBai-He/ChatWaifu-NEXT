"""Persistence, sequence, and outbox tests."""

import asyncio
import hashlib
import json
import sqlite3
import sys
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
    DatabaseIntegrityError,
    MigrationCatalogError,
    MigrationChecksumError,
)
from chatwaifu_runtime.persistence.migrations import MIGRATIONS


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
async def test_startup_drains_more_than_one_outbox_page(runtime_settings: Settings) -> None:
    seeded = RuntimeContainer(runtime_settings)
    await seeded.start()
    try:
        session = await seeded.sessions.create_session("default")
        for index in range(105):
            await seeded.event_store.append(
                GenericCoreEvent(
                    event_id=uuid4(),
                    event_type="assistant.text_delta",
                    session_id=session.session_id,
                    occurred_at=datetime.now(UTC),
                    source="test.outbox_recovery",
                    privacy=PrivacyLevel.LOCAL,
                    payload={"text": f"pending-{index}"},
                )
            )
        assert len(await seeded.event_store.pending_outbox(limit=200)) == 105
    finally:
        await seeded.stop()

    restarted = RuntimeContainer(runtime_settings)
    published: list[str] = []
    original_publish = restarted.event_hub.publish

    async def observe_publish(event: dict[str, object]) -> None:
        published.append(str(event["event_id"]))
        await original_publish(event)

    restarted.event_hub.publish = observe_publish  # type: ignore[method-assign]
    await restarted.start()
    try:
        assert len(published) == 105
        assert len(set(published)) == 105
        assert await restarted.event_store.pending_outbox(limit=200) == []
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_startup_outbox_publish_failure_retries_remaining_rows_on_next_start(
    runtime_settings: Settings,
) -> None:
    seeded = RuntimeContainer(runtime_settings)
    await seeded.start()
    try:
        session = await seeded.sessions.create_session("default")
        for index in range(105):
            await seeded.event_store.append(
                GenericCoreEvent(
                    event_id=uuid4(),
                    event_type="assistant.text_delta",
                    session_id=session.session_id,
                    occurred_at=datetime.now(UTC),
                    source="test.outbox_recovery",
                    privacy=PrivacyLevel.LOCAL,
                    payload={"text": f"retry-{index}"},
                )
            )
    finally:
        await seeded.stop()

    interrupted = RuntimeContainer(runtime_settings)
    original_publish = interrupted.event_hub.publish
    publish_attempts = 0

    async def fail_mid_page(event: dict[str, object]) -> None:
        nonlocal publish_attempts
        publish_attempts += 1
        if publish_attempts == 51:
            raise RuntimeError("injected outbox publish failure")
        await original_publish(event)

    interrupted.event_hub.publish = fail_mid_page  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="outbox publish failure"):
        await interrupted.start()
    assert publish_attempts == 51

    with sqlite3.connect(runtime_settings.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox WHERE published_at IS NULL"
        ).fetchone() == (55,)

    recovered = RuntimeContainer(runtime_settings)
    recovered_ids: list[str] = []
    recovered_publish = recovered.event_hub.publish

    async def observe_recovery(event: dict[str, object]) -> None:
        recovered_ids.append(str(event["event_id"]))
        await recovered_publish(event)

    recovered.event_hub.publish = observe_recovery  # type: ignore[method-assign]
    await recovered.start()
    try:
        assert len(recovered_ids) == 55
        assert len(set(recovered_ids)) == 55
        assert await recovered.event_store.pending_outbox(limit=200) == []
    finally:
        await recovered.stop()


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
async def test_channel_memory_attribution_migration_backfills_legacy_sources(
    tmp_path: Path,
) -> None:
    path = tmp_path / "channel-memory-attribution.db"
    storage = StorageConfig(database_path=path)
    legacy = Database(
        path,
        storage,
        migrations=tuple(item for item in MIGRATIONS if item[0] <= 17),
    )
    await legacy.open()
    await legacy.close()

    session_id = str(uuid4())
    turn_id = str(uuid4())
    event_id = str(uuid4())
    memory_id = str(uuid4())
    source_id = str(uuid4())
    occurred_at = datetime(2026, 8, 31, 8, 1, tzinfo=UTC).isoformat()
    source_created_at = datetime(2026, 8, 31, 8, 2, tzinfo=UTC).isoformat()
    source_context = {
        "provider_id": "weixin_ilink",
        "connection_id": str(uuid4()),
        "account_key": "wechat-owner-account",
        "chat_type": "direct",
        "conversation_key": "wechat-direct-owner",
        "sender_key": "wechat-owner-sender",
        "conversation_label": "与木白的微信私聊",
        "sender_display_name": "木白",
    }
    envelope = {
        "event_id": event_id,
        "event_type": "user.turn_committed",
        "schema_version": "1.0",
        "session_id": session_id,
        "turn_id": turn_id,
        "occurred_at": occurred_at,
        "source": "test",
        "payload": {"text": "请记住晚上继续聊 Python"},
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO sessions(
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'default', 'ready', 'idle', 0, 2, ?, ?)
            """,
            (session_id, occurred_at, occurred_at),
        )
        connection.execute(
            """
            INSERT INTO turns(
                turn_id, session_id, role, committed_text, committed_at,
                created_at, source_context_json
            ) VALUES (?, ?, 'user', '请记住晚上继续聊 Python', ?, ?, ?)
            """,
            (
                turn_id,
                session_id,
                occurred_at,
                occurred_at,
                json.dumps(source_context, ensure_ascii=False),
            ),
        )
        connection.execute(
            """
            INSERT INTO events(
                event_id, session_id, sequence, event_type, schema_version,
                occurred_at, source, payload_json, envelope_json
            ) VALUES (?, ?, 1, 'user.turn_committed', '1.0', ?, 'test', ?, ?)
            """,
            (
                event_id,
                session_id,
                occurred_at,
                json.dumps(envelope["payload"], ensure_ascii=False),
                json.dumps(envelope, ensure_ascii=False),
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_records(
                memory_id, namespace, kind, subject_id, predicate, value_json,
                text, normalized_text, search_terms, observed_at, valid_from,
                confidence, importance, sensitivity, state, pinned,
                created_at, updated_at
            ) VALUES (
                ?, 'character/default/user/local', 'semantic.fact', 'user',
                'conversation.plan', 'null', '晚上继续聊 Python',
                '晚上继续聊 python', '晚上 继续 聊 python', ?, ?,
                0.9, 0.8, 'private', 'active', 0, ?, ?
            )
            """,
            (memory_id, occurred_at, occurred_at, occurred_at, occurred_at),
        )
        connection.execute(
            """
            INSERT INTO memory_sources(
                source_id, memory_id, source_event_id, session_id, turn_id,
                source_kind, created_at
            ) VALUES (?, ?, ?, ?, ?, 'user_turn', ?)
            """,
            (source_id, memory_id, event_id, session_id, turn_id, source_created_at),
        )

    upgraded = Database(path, storage)
    await upgraded.open()
    await upgraded.close()
    with sqlite3.connect(path) as connection:
        raw = connection.execute(
            "SELECT channel_attribution_json FROM memory_sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    assert raw is not None
    attribution = json.loads(str(raw[0]))
    assert attribution["schema_version"] == "1.0"
    assert attribution["provider_id"] == "weixin_ilink"
    assert attribution["principal_scope"] == "local"
    assert attribution["conversation_key"] == "wechat-direct-owner"
    assert attribution["sender_key"] == "wechat-owner-sender"
    assert attribution["received_at"] == source_created_at


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


@pytest.mark.asyncio
async def test_unknown_legacy_migration_is_rejected_before_ledger_upgrade(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unknown-legacy-migration.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO schema_migrations(version) VALUES (99)")

    storage = StorageConfig(database_path=path)
    database = Database(
        path,
        storage,
        migrations=((1, "CREATE TABLE known_record (value TEXT NOT NULL);"),),
    )
    with pytest.raises(MigrationCatalogError, match="newer"):
        await database.open()

    with sqlite3.connect(path) as connection:
        columns = [
            str(row[1]) for row in connection.execute("PRAGMA table_info(schema_migrations)")
        ]
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert columns == ["version"]
    assert "known_record" not in tables


@pytest.mark.asyncio
async def test_database_enforces_durable_wal_settings(tmp_path: Path) -> None:
    path = tmp_path / "durable-wal.db"
    database = Database(
        path,
        StorageConfig(
            database_path=path,
            synchronous="full",
            wal_autocheckpoint_pages=37,
        ),
    )
    await database.open()
    try:
        journal_mode = await database.fetchone("PRAGMA journal_mode")
        synchronous = await database.fetchone("PRAGMA synchronous")
        autocheckpoint = await database.fetchone("PRAGMA wal_autocheckpoint")
        cell_size_check = await database.fetchone("PRAGMA cell_size_check")
        assert journal_mode is not None and journal_mode[0] == "wal"
        assert synchronous is not None and synchronous[0] == 2
        assert autocheckpoint is not None and autocheckpoint[0] == 37
        assert cell_size_check is not None and cell_size_check[0] == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_corrupt_database_fails_closed_before_migration_writes(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE protected_records(value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO protected_records(value) VALUES (?)",
            ((f"record-{index}-" + "x" * 2048,) for index in range(50)),
        )
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        root_page = int(
            connection.execute(
                "SELECT rootpage FROM sqlite_master WHERE name = 'protected_records'"
            ).fetchone()[0]
        )

    with path.open("r+b") as database_file:
        database_file.seek((root_page - 1) * page_size)
        database_file.write(b"\0" * page_size)
    digest_before = hashlib.sha256(path.read_bytes()).hexdigest()

    database = Database(
        path,
        StorageConfig(database_path=path),
        migrations=((1, "CREATE TABLE must_not_exist(value TEXT NOT NULL);"),),
    )
    with pytest.raises(DatabaseIntegrityError, match="startup integrity check failed"):
        await database.open()

    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest_before
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'must_not_exist'"
            ).fetchone()
            is None
        )


@pytest.mark.skipif(sys.platform != "win32", reason="validates Windows TerminateProcess recovery")
@pytest.mark.asyncio
async def test_windows_force_exit_recovers_wal_without_partial_domain_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "force-exit.db"
    fixture = Path(__file__).parent / "fixtures" / "sqlite_force_exit_writer.py"

    for _cycle in range(8):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            str(path),
            "8",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        try:
            line = (await process.stdout.readline()).decode("utf-8").strip()
            stderr = ""
            if line != "READY_TO_TERMINATE":
                stderr = (await process.stderr.read()).decode("utf-8", errors="replace")
            assert line == "READY_TO_TERMINATE", (
                f"force-exit fixture failed before its commit boundary: {line!r}; stderr={stderr}"
            )
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=10)
        finally:
            if process.returncode is None:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=10)

        recovered = Database(path, StorageConfig(database_path=path))
        await recovered.open()
        try:
            integrity = await recovered.fetchone("PRAGMA quick_check(1)")
            assert integrity is not None and integrity[0] == "ok"
            counts = await recovered.fetchone(
                """
                SELECT
                    (SELECT COUNT(*) FROM events),
                    (SELECT COUNT(*) FROM outbox),
                    (SELECT COUNT(*) FROM playback_ack_commands)
                """
            )
            assert counts is not None
            assert counts[0] == counts[1] == counts[2]
        finally:
            await recovered.close()


@pytest.mark.asyncio
async def test_migration_20_to_21_non_empty_db_handles_null_origin_proposal_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "migration-20-21.db"
    storage = StorageConfig(database_path=path)
    legacy = Database(
        path,
        storage,
        migrations=tuple(item for item in MIGRATIONS if item[0] <= 20),
    )
    await legacy.open()
    await legacy.close()

    now = datetime.now(UTC).isoformat()
    record_id_1 = str(uuid4())
    record_id_2 = str(uuid4())

    with sqlite3.connect(path) as connection:
        for record_id, text in [(record_id_1, "Fact 1"), (record_id_2, "Fact 2")]:
            connection.execute(
                """
                INSERT INTO memory_records(
                    memory_id, namespace, kind, subject_id, predicate, value_json,
                    text, normalized_text, search_terms, observed_at, valid_from,
                    confidence, importance, sensitivity, state, pinned,
                    created_at, updated_at
                ) VALUES (
                    ?, 'character/default/user/local', 'semantic.fact', 'user',
                    'fact', 'null', ?,
                    ?, ?, ?, ?,
                    0.9, 0.8, 'private', 'active', 0, ?, ?
                )
                """,
                (record_id, text, text.lower(), text.lower(), now, now, now, now),
            )

    migrated = Database(path, storage)
    await migrated.open()
    try:
        rows = await migrated.fetchall(
            "SELECT memory_id, origin_proposal_id FROM memory_records ORDER BY memory_id"
        )
        assert len(rows) == 2
        for row in rows:
            assert row[1] is None

        proposal_id_1 = str(uuid4())
        record_id_3 = str(uuid4())
        await migrated.execute(
            """
            INSERT INTO memory_records(
                memory_id, namespace, kind, subject_id, predicate, value_json,
                text, normalized_text, search_terms, observed_at, valid_from,
                confidence, importance, sensitivity, state, pinned,
                created_at, updated_at, origin_proposal_id
            ) VALUES (
                ?, 'character/default/user/local', 'semantic.fact', 'user',
                'fact', 'null', 'Fact 3',
                'fact 3', 'fact 3', ?, ?,
                0.9, 0.8, 'private', 'active', 0, ?, ?, ?
            )
            """,
            (record_id_3, now, now, now, now, proposal_id_1),
        )

        record_id_4 = str(uuid4())
        with pytest.raises(sqlite3.IntegrityError):
            await migrated.execute(
                """
                INSERT INTO memory_records(
                    memory_id, namespace, kind, subject_id, predicate, value_json,
                    text, normalized_text, search_terms, observed_at, valid_from,
                    confidence, importance, sensitivity, state, pinned,
                    created_at, updated_at, origin_proposal_id
                ) VALUES (
                    ?, 'character/default/user/local', 'semantic.fact', 'user',
                    'fact', 'null', 'Fact 4',
                    'fact 4', 'fact 4', ?, ?,
                    0.9, 0.8, 'private', 'active', 0, ?, ?, ?
                )
                """,
                (record_id_4, now, now, now, now, proposal_id_1),
            )
    finally:
        await migrated.close()
