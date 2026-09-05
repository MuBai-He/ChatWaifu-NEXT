"""Photo persistence source authorization, dedupe and recall redactions."""

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from chatwaifu_runtime.photo_memory.models import (
    PhotoMemoryRevisionConflict,
    PhotoSaveCandidate,
)

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    db_path = tmp_path / "test.db"
    database = Database(
        path=db_path,
        config=StorageConfig(database_path=db_path, busy_timeout_ms=5000),
    )
    await database.open()

    yield database
    await database.close()


async def _seed_source_chain(
    db: Database,
    *,
    scope: str = "user-1",
    character_id: str = "char-1",
    connection_id: str | None = None,
    generation_id: str | None = None,
    connection_enabled: bool = True,
    connection_deleted: bool = False,
    generation_status: str = "completed",
    turn_generation_status: str = "completed",
    provider_id: str = "weixin_ilink",
    session_state: str = "active",
    turn_role: str = "user",
    source_context: dict[str, str] | None = None,
    source_context_null: bool = False,
) -> tuple[str, str, str]:
    conn_id = connection_id or str(uuid4())
    gen_id = generation_id or str(uuid4())
    session_id = str(uuid4())
    turn_id = str(uuid4())
    binding_id = str(uuid4())
    channel_turn_id = str(uuid4())
    now = datetime.now(UTC).isoformat()

    if source_context_null:
        sc_json = None
    else:
        sc = {"connection_id": conn_id, "principal_scope": scope, "chat_type": "direct"}
        if source_context:
            sc.update(source_context)
        sc_json = json.dumps(sc)

    async with db.transaction() as conn:
        await conn.execute(
            """
            INSERT OR IGNORE INTO channel_connections (
                connection_id, provider_id, name, character_id, principal_scope,
                enabled, access_token_hash, created_at, updated_at, deleted_at
            ) VALUES (?, ?, 'test-conn', ?, ?, ?, 'hash', ?, ?, ?)
            """,
            (
                conn_id,
                provider_id,
                character_id,
                scope,
                1 if connection_enabled else 0,
                now,
                now,
                now if connection_deleted else None,
            ),
        )
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state, conversation_state, created_at, updated_at
            ) VALUES (?, ?, ?, 'ready', ?, ?)
            """,
            (session_id, character_id, session_state, now, now),
        )
        await conn.execute(
            """
            INSERT INTO turns (
                turn_id, session_id, role, committed_text, created_at, source_context_json
            ) VALUES (?, ?, ?, 'my photo caption', ?, ?)
            """,
            (turn_id, session_id, turn_role, now, sc_json),
        )
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind, started_at
            ) VALUES (?, ?, ?, ?, 'local', ?)
            """,
            (gen_id, session_id, turn_id, generation_status, now),
        )
        await conn.execute(
            """
            INSERT INTO channel_bindings (
                binding_id, connection_id, conversation_key, sender_key, session_id,
                created_at, updated_at
            ) VALUES (?, ?, 'conv-key', 'sender-key', ?, ?, ?)
            """,
            (binding_id, conn_id, session_id, now, now),
        )
        await conn.execute(
            """
            INSERT INTO channel_turns (
                channel_turn_id, connection_id, binding_id, external_message_id,
                content_sha256, conversation_key, sender_key, principal_scope,
                session_id, turn_id, generation_id, status, accepted_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'hash', 'conv-key', 'sender-key', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_turn_id,
                conn_id,
                binding_id,
                str(uuid4()),
                scope,
                session_id,
                turn_id,
                gen_id,
                turn_generation_status,
                now,
                now,
                now,
            ),
        )
    return conn_id, gen_id, session_id


def make_candidate(conn_id: str, gen_id: str) -> PhotoSaveCandidate:
    return PhotoSaveCandidate(
        data=PNG_1X1,
        mime_type="image/png",
        width=1,
        height=1,
        title="Test Title",
        description="Test Description",
        confidence=0.9,
        keywords=("test", "keyword"),
        source_connection_id=UUID(conn_id),
        generation_id=UUID(gen_id),
    )


@pytest.mark.asyncio
async def test_settings_cas(db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(db)

    settings = await repo.get_settings("s1", "c1")
    assert not settings.retention_enabled
    assert settings.revision == 0

    with pytest.raises(PhotoMemoryRevisionConflict):
        await repo.update_settings("s1", "c1", retention_enabled=True, expected_revision=1)

    s2 = await repo.update_settings("s1", "c1", retention_enabled=True, expected_revision=0)
    assert s2.retention_enabled
    assert s2.revision == 1


@pytest.mark.asyncio
async def test_independent_mismatches(db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(db)
    await repo.update_settings("s1", "c1", retention_enabled=True, expected_revision=0)

    # turn mismatch
    c1, g1, _ = await _seed_source_chain(
        db,
        scope="s1",
        character_id="c1",
        generation_status="active",
        turn_generation_status="completed",
    )
    assert await repo.save("s1", "c1", make_candidate(c1, g1), expected_revision=1) is None

    # role mismatch
    c2, g2, _ = await _seed_source_chain(db, scope="s1", character_id="c1", turn_role="assistant")
    assert await repo.save("s1", "c1", make_candidate(c2, g2), expected_revision=1) is None

    # chat_type mismatch
    c3, g3, _ = await _seed_source_chain(
        db, scope="s1", character_id="c1", source_context={"chat_type": "group"}
    )
    assert await repo.save("s1", "c1", make_candidate(c3, g3), expected_revision=1) is None

    # scope mismatch
    c4, g4, _ = await _seed_source_chain(
        db, scope="s1", character_id="c1", source_context={"principal_scope": "other"}
    )
    assert await repo.save("s1", "c1", make_candidate(c4, g4), expected_revision=1) is None

    # success
    c5, g5, _ = await _seed_source_chain(db, scope="s1", character_id="c1")
    assert await repo.save("s1", "c1", make_candidate(c5, g5), expected_revision=1) is not None


@pytest.mark.asyncio
async def test_dedupe_source_refs(db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(db)
    await repo.update_settings("s1", "c1", retention_enabled=True, expected_revision=0)

    c1, g1, _s1 = await _seed_source_chain(db, scope="s1", character_id="c1")
    photo = await repo.save("s1", "c1", make_candidate(c1, g1), expected_revision=1)
    assert photo is not None

    c2, g2, _s2 = await _seed_source_chain(db, scope="s1", character_id="c1")
    photo2 = await repo.save("s1", "c1", make_candidate(c2, g2), expected_revision=1)
    assert photo2 is not None
    assert photo.photo_id == photo2.photo_id

    async with db.transaction() as conn:
        cursor = await conn.execute(
            "SELECT generation_id FROM photo_references WHERE photo_id = ?", (str(photo.photo_id),)
        )
        rows = list(await cursor.fetchall())
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_search_chinese_natural(db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(db)
    await repo.update_settings("s1", "c1", retention_enabled=True, expected_revision=0)

    c1, g1, _ = await _seed_source_chain(db, scope="s1", character_id="c1")
    cand = make_candidate(c1, g1)
    cand = PhotoSaveCandidate(
        data=PNG_1X1,
        mime_type="image/png",
        width=1,
        height=1,
        title="海边落日",
        description="test desc",
        confidence=0.9,
        keywords=("sunset",),
        source_connection_id=UUID(c1),
        generation_id=UUID(g1),
    )
    photo = await repo.save("s1", "c1", cand, expected_revision=1)

    # Should match because we filter out stop words and OR match the bigrams of 海边/落日
    res = await repo.search("s1", "c1", "之前那张海边照片里有什么")
    assert len(res) == 1
    assert photo is not None
    assert res[0].photo_id == photo.photo_id

    # Unrelated
    res2 = await repo.search("s1", "c1", "山顶风景")
    assert len(res2) == 0


@pytest.mark.asyncio
async def test_deletion_redaction_descendants(db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(db)
    await repo.update_settings("s1", "c1", retention_enabled=True, expected_revision=0)

    c1, g1, _s1 = await _seed_source_chain(db, scope="s1", character_id="c1")
    photo = await repo.save("s1", "c1", make_candidate(c1, g1), expected_revision=1)

    # Add descendants
    g2, g3, g4 = str(uuid4()), str(uuid4()), str(uuid4())
    t2, t3, t4 = str(uuid4()), str(uuid4()), str(uuid4())
    async with db.transaction() as conn:
        now = datetime.now(UTC).isoformat()
        await conn.execute(
            "INSERT INTO turns(turn_id, session_id, role, created_at) VALUES(?, ?, 'user', ?)",
            (t2, _s1, now),
        )
        await conn.execute(
            "INSERT INTO turns(turn_id, session_id, role, created_at) VALUES(?, ?, 'user', ?)",
            (t3, _s1, now),
        )
        await conn.execute(
            "INSERT INTO turns(turn_id, session_id, role, created_at) VALUES(?, ?, 'user', ?)",
            (t4, _s1, now),
        )
        await conn.execute(
            "INSERT INTO generations(generation_id, session_id, turn_id, "
            "state, backend_kind) VALUES(?, ?, ?, 'completed', 'local')",
            (g2, _s1, t2),
        )
        await conn.execute(
            "INSERT INTO generations(generation_id, session_id, turn_id, "
            "state, backend_kind) VALUES(?, ?, ?, 'completed', 'local')",
            (g3, _s1, t3),
        )
        await conn.execute(
            "INSERT INTO generations(generation_id, session_id, turn_id, "
            "state, backend_kind) VALUES(?, ?, ?, 'completed', 'local')",
            (g4, _s1, t4),
        )

        await conn.execute(
            "INSERT INTO "
            "conversation_history_dependencies(source_generation_id, "
            "derived_generation_id) VALUES(?, ?)",
            (g1, g2),
        )
        await conn.execute(
            "INSERT INTO "
            "conversation_history_dependencies(source_generation_id, "
            "derived_generation_id) VALUES(?, ?)",
            (g2, g3),
        )

    assert photo is not None
    deletion = await repo.delete("s1", "c1", photo.photo_id)
    assert deletion.result.deleted

    affected = [str(x.generation_id) for x in deletion.affected_generations]
    assert g1 in affected
    assert g2 in affected
    assert g3 in affected
    assert g4 not in affected

    async with db.transaction() as conn:
        # no photo data left
        c = await conn.execute("SELECT COUNT(*) FROM photo_assets")
        row = await c.fetchone()
        assert row is not None and row[0] == 0
        await c.close()
        c = await conn.execute("SELECT COUNT(*) FROM photo_references")
        row = await c.fetchone()
        assert row is not None and row[0] == 0
        await c.close()
        c = await conn.execute("SELECT COUNT(*) FROM photo_assets_fts")
        row = await c.fetchone()
        assert row is not None and row[0] == 0
        await c.close()


@pytest.mark.asyncio
async def test_register_recall_desktop(db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(db)
    await repo.update_settings("local", "c1", retention_enabled=True, expected_revision=0)

    c1, g1, _ = await _seed_source_chain(db, scope="local", character_id="c1")
    photo = await repo.save("local", "c1", make_candidate(c1, g1), expected_revision=1)

    assert photo is not None
    # local generation running
    _c2, g2, _ = await _seed_source_chain(
        db, scope="local", character_id="c1", generation_status="running", source_context_null=True
    )

    recalled = await repo.register_recall("local", "c1", (photo.photo_id,), generation_id=UUID(g2))
    assert len(recalled) == 1
