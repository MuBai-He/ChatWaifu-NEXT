from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.windows import recover_runtime_database as recovery

NOW = "2026-09-01T08:00:00+00:00"
SESSION_ID = "00000000-0000-4000-8000-000000000001"
TURN_ID = "00000000-0000-4000-8000-000000000002"
GENERATION_ID = "00000000-0000-4000-8000-000000000003"
EVENT_ID = "00000000-0000-4000-8000-000000000004"
MEMORY_ID = "00000000-0000-4000-8000-000000000005"


def test_recovery_preserves_durable_truth_and_reconstructs_missing_session(
    tmp_path: Path,
) -> None:
    source = tmp_path / "chatwaifu.db"
    target = tmp_path / "chatwaifu-recovered.db"
    backup = tmp_path / "source-backup"
    _seed_source(source, delete_session=True)
    source_digest = _sha256(source)

    report = recovery.recover_runtime_database(
        source,
        target,
        backup,
        runtime_stopped=True,
    )

    assert _sha256(source) == source_digest
    assert target.is_file()
    assert (backup / "source-family" / source.name).read_bytes() == source.read_bytes()
    on_disk_report = json.loads((backup / "recovery-report.json").read_text("utf-8"))
    assert on_disk_report["status"] == "complete"
    assert report["sessions"]["reconstructed"] == 1  # type: ignore[index]
    assert report["sessions"]["copied"] == 0  # type: ignore[index]
    assert report["sessions"]["lookup_not_found"] == 1  # type: ignore[index]
    assert report["legacy_tables_not_copied"]["memory_items"]["row_count"] == 1  # type: ignore[index]
    assert report["rebuildable_tables_not_copied"]["memory_embeddings"]["row_count"] == 1  # type: ignore[index]

    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    try:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        session = connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (SESSION_ID,)
        ).fetchone()
        assert session is not None
        assert session["character_id"] == "ayachi_nene"
        assert session["next_sequence"] == 8
        assert _count(connection, "turns") == 1
        assert _count(connection, "generations") == 1
        assert _count(connection, "events") == 1
        assert _count(connection, "memory_records") == 1
        assert _count(connection, "memory_sources") == 1
        assert _count(connection, "memory_proposals") == 1
        assert _count(connection, "model_role_configs") == 1
        assert _count(connection, "memory_records_fts") == 1
        assert _count(connection, "memory_items") == 0
        assert _count(connection, "memory_embeddings") == 0
        for table in recovery.TRANSIENT_TABLES:
            assert _count(connection, table) == 0
    finally:
        connection.close()


def test_recovery_refuses_existing_target_without_creating_backup(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    backup = tmp_path / "backup"
    _seed_source(source)
    target.write_bytes(b"do not overwrite")

    with pytest.raises(recovery.RecoveryError, match="target must not exist"):
        recovery.recover_runtime_database(
            source,
            target,
            backup,
            runtime_stopped=True,
        )

    assert target.read_bytes() == b"do not overwrite"
    assert not backup.exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_recovery_refuses_to_create_target_at_a_source_sidecar_path(
    tmp_path: Path, suffix: str
) -> None:
    source = tmp_path / "source.db"
    _seed_source(source)
    sidecar_target = source.with_name(source.name + suffix)

    with pytest.raises(recovery.RecoveryError, match="source database family"):
        recovery.recover_runtime_database(
            source,
            sidecar_target,
            tmp_path / "backup",
            runtime_stopped=True,
        )

    assert not sidecar_target.exists()


def test_target_created_during_recovery_is_not_overwritten_or_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    backup = tmp_path / "backup"
    _seed_source(source)
    original_publish = recovery._publish_new_target  # pyright: ignore[reportPrivateUsage]

    def publish_after_racer(building_target: Path, destination: Path) -> None:
        destination.write_bytes(b"created by another process")
        original_publish(building_target, destination)

    monkeypatch.setattr(recovery, "_publish_new_target", publish_after_racer)

    with pytest.raises(recovery.RecoveryError, match="appeared during recovery"):
        recovery.recover_runtime_database(
            source,
            target,
            backup,
            runtime_stopped=True,
        )

    assert target.read_bytes() == b"created by another process"
    assert (backup / "recovery-failure.json").is_file()


def test_target_modified_after_no_replace_publication_is_not_blessed_or_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    backup = tmp_path / "backup"
    _seed_source(source)
    original_publish = recovery._publish_new_target  # pyright: ignore[reportPrivateUsage]

    def publish_then_modify(building_target: Path, destination: Path) -> None:
        original_publish(building_target, destination)
        destination.write_bytes(b"modified after publication")

    monkeypatch.setattr(recovery, "_publish_new_target", publish_then_modify)

    with pytest.raises(recovery.RecoveryError, match="changed before it could be verified"):
        recovery.recover_runtime_database(
            source,
            target,
            backup,
            runtime_stopped=True,
        )

    assert target.read_bytes() == b"modified after publication"
    assert not (backup / "recovery-report.json").exists()
    assert (backup / "recovery-failure.json").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Win32 file sharing")
def test_recovery_rejects_a_live_sqlite_writer(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed_source(source)
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(recovery.RecoveryError, match="Runtime may still be running"):
            recovery.recover_runtime_database(
                source,
                tmp_path / "target.db",
                tmp_path / "backup",
                runtime_stopped=True,
            )
    finally:
        writer.rollback()
        writer.close()
    assert not (tmp_path / "backup").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows hard-link enumeration")
def test_recovery_rejects_independent_sidecars_for_a_main_file_alias(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    alias_root = tmp_path / "alternate-namespace"
    alias_root.mkdir()
    alias = alias_root / source.name
    _seed_source(source)
    os.link(source, alias)
    selected_wal = source.with_name(source.name + "-wal")
    alternate_wal = alias.with_name(alias.name + "-wal")
    selected_wal.write_bytes(b"selected namespace WAL")
    alternate_wal.write_bytes(b"alternate namespace WAL")

    with pytest.raises(recovery.RecoveryError, match="independent SQLite sidecar"):
        recovery.recover_runtime_database(
            source,
            tmp_path / "target.db",
            tmp_path / "backup",
            runtime_stopped=True,
        )

    assert selected_wal.read_bytes() == b"selected namespace WAL"
    assert alternate_wal.read_bytes() == b"alternate namespace WAL"
    assert not (tmp_path / "backup").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows hard-link enumeration")
def test_backup_cannot_create_an_alternate_namespace_sidecar_directory(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    alias_root = tmp_path / "alternate-namespace"
    alias_root.mkdir()
    alias = alias_root / source.name
    _seed_source(source)
    os.link(source, alias)
    forbidden_backup = alias.with_name(alias.name + "-wal")

    with pytest.raises(recovery.RecoveryError, match="source-alias family"):
        recovery.recover_runtime_database(
            source,
            tmp_path / "target.db",
            forbidden_backup,
            runtime_stopped=True,
        )

    assert not forbidden_backup.exists()


def test_recovery_requires_runtime_stopped_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _seed_source(source)

    with pytest.raises(recovery.RecoveryError, match="Runtime is stopped"):
        recovery.recover_runtime_database(
            source,
            tmp_path / "target.db",
            tmp_path / "backup",
            runtime_stopped=False,
        )


def test_copied_session_sequence_is_rebased_to_recovered_event_cursor(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _seed_source(source)
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "UPDATE sessions SET next_sequence = 324 WHERE session_id = ?", (SESSION_ID,)
        )
        connection.commit()
    finally:
        connection.close()

    report = recovery.recover_runtime_database(
        source,
        target,
        tmp_path / "backup",
        runtime_stopped=True,
    )

    connection = sqlite3.connect(target)
    try:
        assert connection.execute(
            "SELECT next_sequence FROM sessions WHERE session_id = ?", (SESSION_ID,)
        ).fetchone() == (8,)
    finally:
        connection.close()
    assert report["sessions"]["adjusted"] == 1  # type: ignore[index]


def test_historical_event_lineage_survives_designed_conversation_reset(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _seed_source(source)
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("DELETE FROM turns WHERE turn_id = ?", (TURN_ID,))
        connection.commit()
    finally:
        connection.close()

    report = recovery.recover_runtime_database(
        source,
        target,
        tmp_path / "backup",
        runtime_stopped=True,
    )

    assert report["historical_lineage"]["missing_event_turn_id"] == 1  # type: ignore[index]
    assert report["historical_lineage"]["missing_event_generation_id"] == 1  # type: ignore[index]
    assert report["historical_lineage"]["missing_skill_run_turn_id"] == 1  # type: ignore[index]
    assert report["historical_lineage"]["missing_skill_run_generation_id"] == 1  # type: ignore[index]
    connection = sqlite3.connect(target)
    try:
        assert _count(connection, "turns") == 0
        assert _count(connection, "generations") == 0
        assert _count(connection, "events") == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_recovery_copies_main_wal_shm_family_and_reads_committed_wal(tmp_path: Path) -> None:
    source = tmp_path / "wal-source.db"
    target = tmp_path / "target.db"
    backup = tmp_path / "backup"
    writer = Path(__file__).parent / "fixtures" / "sqlite_wal_crash_writer.py"
    subprocess.run([sys.executable, str(writer), str(source)], check=True)
    source_wal = source.with_name(source.name + "-wal")
    source_shm = source.with_name(source.name + "-shm")
    assert source_wal.stat().st_size > 0
    assert source_shm.stat().st_size > 0
    family_digests = {
        path.name: _sha256(path) for path in (source, source_wal, source_shm)
    }

    report = recovery.recover_runtime_database(
        source,
        target,
        backup,
        runtime_stopped=True,
    )

    for name, digest in family_digests.items():
        assert _sha256(tmp_path / name) == digest
        assert _sha256(backup / "source-family" / name) == digest
    assert set(report["source_family"]) == {"main", "-wal", "-shm"}  # type: ignore[arg-type]
    connection = sqlite3.connect(target)
    try:
        model = connection.execute(
            "SELECT model FROM model_role_configs WHERE role = 'chat'"
        ).fetchone()
        assert model == ("wal-only-model",)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_invalid_durable_json_fails_closed_and_leaves_raw_backup(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    backup = tmp_path / "backup"
    _seed_source(source)
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            "UPDATE tts_provider_configs SET configuration_json = 'not-json'"
        )
        connection.commit()
    finally:
        connection.close()
    source_digest = _sha256(source)

    with pytest.raises(recovery.RecoveryError, match="invalid JSON"):
        recovery.recover_runtime_database(
            source,
            target,
            backup,
            runtime_stopped=True,
        )

    assert not target.exists()
    assert _sha256(source) == source_digest
    assert _sha256(backup / "source-family" / source.name) == source_digest
    failure = json.loads((backup / "recovery-failure.json").read_text("utf-8"))
    assert failure["status"] == "failed"
    assert failure["source_family"]["main"]["sha256"] == source_digest
    assert failure["raw_backup_family"]["main"]["sha256"] == source_digest


def test_missing_session_with_conflicting_character_provenance_fails_closed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _seed_source(source, delete_session=True)
    other_memory_id = "00000000-0000-4000-8000-000000000099"
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            """
            INSERT INTO memory_records(
                memory_id, namespace, kind, subject_id, predicate, value_json, text,
                normalized_text, search_terms, observed_at, valid_from, valid_to,
                confidence, importance, sensitivity, state, supersedes, pinned,
                created_at, updated_at, tombstoned_at
            )
            SELECT
                ?, 'character/other_character/user/local', kind, subject_id, predicate,
                value_json, '另一条记忆', '另一条记忆', '另一条 记忆', observed_at,
                valid_from, valid_to, confidence, importance, sensitivity, state,
                supersedes, pinned, created_at, updated_at, tombstoned_at
            FROM memory_records WHERE memory_id = ?
            """,
            (other_memory_id, MEMORY_ID),
        )
        connection.execute(
            """
            INSERT INTO memory_sources(
                source_id, memory_id, source_event_id, session_id, turn_id,
                source_kind, created_at, channel_attribution_json
            ) VALUES ('source-2', ?, ?, ?, ?, 'conversation', ?, NULL)
            """,
            (other_memory_id, EVENT_ID, SESSION_ID, TURN_ID, NOW),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(recovery.RecoveryError, match="ambiguous character provenance"):
        recovery.recover_runtime_database(
            source,
            target,
            tmp_path / "backup",
            runtime_stopped=True,
        )

    assert not target.exists()


def test_missing_memory_provenance_event_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _seed_source(source)
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DELETE FROM events WHERE event_id = ?", (EVENT_ID,))
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(recovery.RecoveryError, match="required provenance event is missing"):
        recovery.recover_runtime_database(
            source,
            target,
            tmp_path / "backup",
            runtime_stopped=True,
        )

    assert not target.exists()


def test_malformed_historical_event_lineage_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _seed_source(source)
    connection = sqlite3.connect(source)
    try:
        envelope = json.loads(
            connection.execute(
                "SELECT envelope_json FROM events WHERE event_id = ?", (EVENT_ID,)
            ).fetchone()[0]
        )
        envelope["turn_id"] = "not-a-uuid"
        connection.execute(
            "UPDATE events SET envelope_json = ? WHERE event_id = ?",
            (json.dumps(envelope, ensure_ascii=False), EVENT_ID),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(recovery.RecoveryError, match="envelope turn_id is invalid"):
        recovery.recover_runtime_database(
            source,
            target,
            tmp_path / "backup",
            runtime_stopped=True,
        )

    assert not target.exists()


def test_allowlist_explicitly_excludes_operational_and_projection_tables() -> None:
    copied = set(recovery.DURABLE_TABLES)

    assert copied.isdisjoint(recovery.TRANSIENT_TABLES)
    assert copied.isdisjoint(recovery.REBUILDABLE_TABLES)
    assert copied.isdisjoint(recovery.LEGACY_TABLES)
    assert "sessions" not in copied
    assert "events" not in copied
    assert "schema_migrations" not in copied


def _seed_source(path: Path, *, delete_session: bool = False) -> None:
    connection = sqlite3.connect(path)
    try:
        recovery._create_current_schema(connection)  # pyright: ignore[reportPrivateUsage]
        connection.execute("PRAGMA foreign_keys=ON")
        payload = {"text": "请记住我喜欢抹茶。"}
        envelope = {
            "event_id": EVENT_ID,
            "session_id": SESSION_ID,
            "sequence": 7,
            "event_type": "user.turn_committed",
            "schema_version": "1.0",
            # Pydantic serializes UTC as Z while the scalar column uses isoformat().
            "occurred_at": "2026-09-01T08:00:00Z",
            "source": "runtime",
            "correlation_id": None,
            "causation_id": None,
            "turn_id": TURN_ID,
            "generation_id": GENERATION_ID,
            "payload": payload,
        }
        connection.execute(
            """
            INSERT INTO sessions(
                session_id, character_id, state, conversation_state, revision,
                next_sequence, created_at, updated_at
            ) VALUES (?, 'ayachi_nene', 'ready', 'idle', 3, 8, ?, ?)
            """,
            (SESSION_ID, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO turns(
                turn_id, session_id, role, committed_text, committed_at, created_at,
                source_context_json
            ) VALUES (?, ?, 'user', '请记住我喜欢抹茶。', ?, ?, NULL)
            """,
            (TURN_ID, SESSION_ID, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO generations(
                generation_id, session_id, turn_id, state, backend_kind, started_at,
                completed_at, invalidated_at, output_text, error_code, audio_stream_id,
                spoken_text
            ) VALUES (?, ?, ?, 'completed', 'fake', ?, ?, NULL, '好的。', NULL, NULL, '好的。')
            """,
            (GENERATION_ID, SESSION_ID, TURN_ID, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO skill_runs(
                skill_run_id, session_id, skill_id, skill_version, capability, state,
                arguments_json, cancel_requested, created_at, updated_at, turn_id,
                generation_id, origin
            ) VALUES (
                'skill-run-1', ?, 'test.skill', '1.0.0', 'test', 'completed', '{}',
                0, ?, ?, ?, ?, 'agent'
            )
            """,
            (SESSION_ID, NOW, NOW, TURN_ID, GENERATION_ID),
        )
        connection.execute(
            """
            INSERT INTO events(
                event_id, session_id, sequence, event_type, schema_version, occurred_at,
                source, correlation_id, causation_id, payload_json, envelope_json
            ) VALUES (?, ?, 7, 'user.turn_committed', '1.0', ?, 'runtime', NULL, NULL, ?, ?)
            """,
            (
                EVENT_ID,
                SESSION_ID,
                NOW,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(envelope, ensure_ascii=False),
            ),
        )
        connection.execute(
            "INSERT INTO outbox(event_id, envelope_json, created_at) VALUES (?, ?, ?)",
            (EVENT_ID, json.dumps(envelope, ensure_ascii=False), NOW),
        )
        connection.execute(
            """
            INSERT INTO memory_records(
                memory_id, namespace, kind, subject_id, predicate, value_json, text,
                normalized_text, search_terms, observed_at, valid_from, valid_to,
                confidence, importance, sensitivity, state, supersedes, pinned,
                created_at, updated_at, tombstoned_at
            ) VALUES (
                ?, 'character/ayachi_nene/user/local', 'semantic.preference', 'user',
                'likes', '"matcha"', '用户喜欢抹茶', '用户喜欢抹茶', '喜欢 抹茶 matcha',
                ?, NULL, NULL, 0.95, 0.8, 'private', 'active', NULL, 1, ?, ?, NULL
            )
            """,
            (MEMORY_ID, NOW, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO memory_sources(
                source_id, memory_id, source_event_id, session_id, turn_id,
                source_kind, created_at, channel_attribution_json
            ) VALUES ('source-1', ?, ?, ?, ?, 'conversation', ?, NULL)
            """,
            (MEMORY_ID, EVENT_ID, SESSION_ID, TURN_ID, NOW),
        )
        connection.execute(
            """
            INSERT INTO memory_proposals(
                proposal_id, operation, candidate_json, target_memory_id,
                evidence_event_ids_json, confidence, rationale, status, created_at, decided_at
            ) VALUES ('proposal-1', 'create', '{}', ?, ?, 0.95, 'explicit', 'accepted', ?, ?)
            """,
            (MEMORY_ID, json.dumps([EVENT_ID]), NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO memory_items(
                memory_id, content, normalized_content, state, source_session_id,
                source_turn_id, created_at, updated_at, tombstoned_at
            ) VALUES (?, '用户喜欢抹茶', '用户喜欢抹茶', 'active', ?, ?, ?, ?, NULL)
            """,
            (MEMORY_ID, SESSION_ID, TURN_ID, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO memory_embeddings(memory_id, model_fingerprint, vector_json, updated_at)
            VALUES (?, 'test-model', '[0.1,0.2]', ?)
            """,
            (MEMORY_ID, NOW),
        )
        connection.execute(
            """
            INSERT INTO model_role_configs(
                role, provider, model, base_url, timeout_seconds, context_window,
                enabled, updated_at
            ) VALUES ('chat', 'openai_compatible', 'test', 'http://127.0.0.1', 30, 4096, 1, ?)
            """,
            (NOW,),
        )
        connection.execute(
            """
            INSERT INTO character_states(
                character_id, user_scope, valence, arousal, energy, attention,
                embarrassment, tension, revision, updated_at
            ) VALUES ('ayachi_nene', 'local', 0, 0.5, 0.5, 0.5, 0, 0, 1, ?)
            """,
            (NOW,),
        )
        connection.execute(
            """
            INSERT INTO tts_provider_configs(
                provider_id, schema_version, configuration_json, updated_at
            )
            VALUES ('qwen3_tts_nene', '1.0', '{"voice":"ayachi_nene_local"}', ?)
            """,
            (NOW,),
        )
        connection.execute(
            """
            INSERT INTO playback_segments(
                segment_id, stream_id, session_id, generation_id, segment_index, text,
                duration_ms, state, played_pts_ms, buffered_ms, client_clock_ms, transport,
                stop_reason, queued_at, started_at, stopped_at, duration_finalized
            ) VALUES (
                'segment-1', 'stream-1', ?, ?, 0, '好的。', 500, 'completed',
                500, 0, 500, 'audio_element', NULL, ?, ?, ?, 1
            )
            """,
            (SESSION_ID, GENERATION_ID, NOW, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO playback_ack_commands(command_id, segment_id, phase, received_at)
            VALUES ('ack-1', 'segment-1', 'ended', ?)
            """,
            (NOW,),
        )
        connection.commit()
        if delete_session:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DELETE FROM sessions WHERE session_id = ?", (SESSION_ID,))
            connection.commit()
    finally:
        connection.close()


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
