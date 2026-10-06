"""Data-preserving operational cutover against the real SQLite 40 catalog."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database

from tools.merge_qq_runtime_database import catalog, merge_database

OLD = "2026-10-03T00:00:00+00:00"
NEW = "2026-10-04T00:00:00+00:00"


def insert(db: sqlite3.Connection, table: str, **values: object) -> None:
    columns = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    db.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values()))


def session(db: sqlite3.Connection, identifier: str, *, shared: bool = False) -> None:
    insert(
        db,
        "sessions",
        session_id=identifier,
        character_id="default",
        state="active",
        conversation_state="idle",
        revision=1,
        next_sequence=1,
        created_at=NEW,
        updated_at=NEW,
        participant_id="local",
        scene_id="scene-one" if shared else None,
        scene_kind="shared" if shared else "private",
        audience_json='["local","guest"]' if shared else '["local"]',
        user_scope="scene:scene-one" if shared else "local",
        state_scope="scene_member:scene-one:local" if shared else "local",
    )


def memory(db: sqlite3.Connection, identifier: str, session_id: str, *, shared: bool) -> None:
    insert(
        db,
        "events",
        event_id=identifier + "-event",
        session_id=session_id,
        sequence=1,
        event_type="conversation.user_text_committed",
        schema_version="1.0.0",
        occurred_at=NEW,
        source="test",
        payload_json="{}",
        envelope_json="{}",
    )
    insert(
        db,
        "memory_records",
        memory_id=identifier,
        namespace="character/default/user/scene:scene-one"
        if shared
        else "character/default/user/local",
        kind="semantic.preference",
        subject_id="participant:local" if shared else "user",
        text="shared tea" if shared else "private sentinel",
        normalized_text="shared tea" if shared else "private sentinel",
        search_terms="tea" if shared else "sentinel",
        observed_at=NEW,
        confidence=1.0,
        importance=0.5,
        sensitivity="private",
        state="active",
        created_at=NEW,
        updated_at=NEW,
    )
    insert(
        db,
        "memory_sources",
        source_id=identifier + "-source",
        memory_id=identifier,
        source_event_id=identifier + "-event",
        session_id=session_id,
        source_kind="user_text",
        created_at=NEW,
        channel_attribution_json=json.dumps({"fixture": identifier}),
    )


@pytest.fixture
async def databases(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = (tmp_path / "primary.sqlite", tmp_path / "qq.sqlite", tmp_path / "merged.sqlite")
    for path in paths[:2]:
        db = Database(path, StorageConfig())
        await db.open()
        await db.close()
    with sqlite3.connect(paths[0]) as db:
        session(db, "primary-session")
        insert(
            db,
            "model_role_configs",
            role="chat",
            provider="demo",
            model="primary-model",
            base_url="http://127.0.0.1:10000",
            timeout_seconds=60,
            context_window=8192,
            enabled=1,
            updated_at=OLD,
        )
        insert(
            db,
            "relationship_states",
            character_id="default",
            user_scope="local",
            familiarity=0.3,
            trust=0.3,
            affinity=0.3,
            comfort=0.3,
            recent_tension=0,
            interaction_count=14,
            stage="familiar",
            revision=33,
            updated_at=OLD,
        )
    with sqlite3.connect(paths[1]) as db:
        insert(db, "participants", participant_id="guest", display_name="Guest", created_at=NEW)
        insert(
            db,
            "conversation_scenes",
            scene_id="scene-one",
            display_name="Shared scene",
            participant_ids_json='["local","guest"]',
            created_at=NEW,
        )
        session(db, "qq-private")
        session(db, "qq-shared", shared=True)
        insert(
            db,
            "channel_connections",
            connection_id="qq-connection",
            provider_id="qq_napcat",
            name="QQ fixture",
            character_id="default",
            principal_scope="local",
            access_token_hash="fixture-no-secret",
            created_at=NEW,
            updated_at=NEW,
        )
        memory(db, "private-memory", "qq-private", shared=False)
        memory(db, "shared-memory", "qq-shared", shared=True)
        insert(
            db,
            "relationship_states",
            character_id="default",
            user_scope="local",
            familiarity=0.4,
            trust=0.4,
            affinity=0.4,
            comfort=0.4,
            recent_tension=0,
            interaction_count=26,
            stage="familiar",
            revision=26,
            updated_at=NEW,
        )
    return paths


def fingerprints(paths: tuple[Path, Path, Path]) -> list[str]:
    return [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths[:2]]


def test_merges_without_rewriting_identity_or_provenance(
    databases: tuple[Path, Path, Path],
) -> None:
    before = fingerprints(databases)
    report = merge_database(*databases)
    assert report["all_business_rows_verified"]
    assert fingerprints(databases) == before
    with sqlite3.connect(databases[2]) as db, sqlite3.connect(databases[0]) as original:
        assert catalog(db) == catalog(original)
        assert db.execute("SELECT count(*) FROM sessions").fetchone() == (3,)
        assert db.execute("SELECT count(*) FROM participants").fetchone() == (2,)
        assert db.execute("SELECT model FROM model_role_configs WHERE role='chat'").fetchone() == (
            "primary-model",
        )
        assert db.execute(
            "SELECT interaction_count,revision,trust FROM relationship_states"
        ).fetchone() == (
            40,
            34,
            0.4,
        )
        assert db.execute(
            "SELECT text FROM memory_records "
            "WHERE namespace='character/default/user/scene:scene-one'"
        ).fetchall() == [("shared tea",)]
        assert db.execute(
            "SELECT m.text,s.session_id,s.channel_attribution_json FROM memory_records m "
            "JOIN memory_sources s ON s.memory_id=m.memory_id WHERE m.memory_id='private-memory'"
        ).fetchone() == ("private sentinel", "qq-private", '{"fixture": "private-memory"}')
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_refuses_to_replace_an_existing_output(databases: tuple[Path, Path, Path]) -> None:
    databases[2].write_text("protected", encoding="utf-8")
    with pytest.raises(ValueError, match="new file"):
        merge_database(*databases)
    assert databases[2].read_text(encoding="utf-8") == "protected"


def test_refuses_overlapping_sessions(databases: tuple[Path, Path, Path]) -> None:
    with sqlite3.connect(databases[1]) as db:
        session(db, "primary-session")
    before = fingerprints(databases)
    with pytest.raises(ValueError, match="session identities overlap"):
        merge_database(*databases)
    assert fingerprints(databases) == before
    assert not databases[2].exists()


def test_refuses_running_generation(databases: tuple[Path, Path, Path]) -> None:
    with sqlite3.connect(databases[1]) as db:
        insert(
            db,
            "turns",
            turn_id="busy-turn",
            session_id="qq-private",
            role="user",
            committed_text="in progress",
            created_at=NEW,
        )
        insert(
            db,
            "generations",
            generation_id="busy-generation",
            session_id="qq-private",
            turn_id="busy-turn",
            state="streaming",
            backend_kind="demo",
            started_at=NEW,
        )
    before = fingerprints(databases)
    with pytest.raises(ValueError, match="nonterminal work"):
        merge_database(*databases)
    assert fingerprints(databases) == before
    assert not databases[2].exists()


def test_rolls_back_unexpected_source_data(databases: tuple[Path, Path, Path]) -> None:
    with sqlite3.connect(databases[1]) as db:
        insert(
            db,
            "assistant_devices",
            device_id="unexpected-device",
            name="Unknown source",
            secret_hash="fixture-no-secret",
            revoked=0,
        )
    before = fingerprints(databases)
    with pytest.raises(ValueError, match="unexpected QQ source data"):
        merge_database(*databases)
    assert fingerprints(databases) == before
    assert not databases[2].exists()


def test_rolls_back_conflicting_participant_identity(databases: tuple[Path, Path, Path]) -> None:
    with sqlite3.connect(databases[1]) as db:
        db.execute(
            "UPDATE participants SET display_name='Different owner' WHERE participant_id='local'"
        )
    before = fingerprints(databases)
    with pytest.raises(ValueError, match="participant identity differs"):
        merge_database(*databases)
    assert fingerprints(databases) == before
    assert not databases[2].exists()
