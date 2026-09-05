"""Tests for SQLite learned sticker library repository."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_sticker_library import SqliteStickerLibraryRepository
from chatwaifu_runtime.sticker_library.models import (
    StickerLibraryRevisionConflict,
    StickerSaveCandidate,
)

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


async def _init_db(tmp_path: Path) -> Database:
    db_path = tmp_path / "test.db"
    db = Database(
        path=db_path,
        config=StorageConfig(database_path=db_path, busy_timeout_ms=5000),
    )
    await db.open()
    return db


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
) -> tuple[str, str]:
    conn_id = connection_id or str(uuid4())
    gen_id = generation_id or str(uuid4())
    session_id = str(uuid4())
    turn_id = str(uuid4())
    binding_id = str(uuid4())
    channel_turn_id = str(uuid4())
    now = datetime.now(UTC).isoformat()

    async with db.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO channel_connections (
                connection_id, provider_id, name, character_id, principal_scope,
                enabled, access_token_hash, created_at, updated_at, deleted_at
            ) VALUES (?, 'test-provider', 'test-conn', ?, ?, ?, 'hash', ?, ?, ?)
            """,
            (
                conn_id,
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
            ) VALUES (?, ?, 'active', 'ready', ?, ?)
            """,
            (session_id, character_id, now, now),
        )
        await conn.execute(
            """
            INSERT INTO turns (
                turn_id, session_id, role, created_at
            ) VALUES (?, ?, 'user', ?)
            """,
            (turn_id, session_id, now),
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
            ) VALUES (?, ?, ?, 'ext-msg', 'hash', 'conv-key', 'sender-key', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_turn_id,
                conn_id,
                binding_id,
                scope,
                session_id,
                turn_id,
                gen_id,
                generation_status,
                now,
                now,
                now,
            ),
        )

    return conn_id, gen_id


@pytest.mark.asyncio
async def test_disabled_by_default(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        settings = await repo.get_settings("scope-1", "char-1")
        assert not settings.learning_enabled
        assert settings.revision == 0

        snap = await repo.snapshot("scope-1", "char-1")
        assert not snap.settings.learning_enabled
        assert snap.settings.revision == 0
        assert snap.items == []
        assert snap.total_bytes == 0
        assert snap.capacity == 100
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cas_update_and_conflict(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        settings = await repo.get_settings("scope-1", "char-1")
        assert settings.revision == 0

        with pytest.raises(StickerLibraryRevisionConflict):
            await repo.update_settings(
                "scope-1", "char-1", learning_enabled=True, expected_revision=5
            )

        updated = await repo.update_settings(
            "scope-1", "char-1", learning_enabled=True, expected_revision=0
        )
        assert updated.learning_enabled is True
        assert updated.revision == 1

        updated2 = await repo.update_settings(
            "scope-1", "char-1", learning_enabled=True, expected_revision=1
        )
        assert updated2.learning_enabled is True
        assert updated2.revision == 2

        with pytest.raises(StickerLibraryRevisionConflict):
            await repo.update_settings(
                "scope-1", "char-1", learning_enabled=False, expected_revision=1
            )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_save_deduplication(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        await repo.update_settings("scope-1", "char-1", learning_enabled=True, expected_revision=0)

        c_id, g_id = await _seed_source_chain(db, scope="scope-1", character_id="char-1")
        candidate = StickerSaveCandidate(
            data=PNG_1X1,
            label="test sticker",
            description="a cute test sticker",
            expression="happy",
            source_connection_id=UUID(c_id),
            generation_id=UUID(g_id),
        )

        saved1 = await repo.save("scope-1", "char-1", candidate, expected_revision=1)
        assert saved1 is not None
        assert saved1.label == "test sticker"

        candidate2 = StickerSaveCandidate(
            data=PNG_1X1,
            label="different label",
            description="another description",
            expression="happy",
            source_connection_id=UUID(c_id),
            generation_id=UUID(g_id),
        )
        saved2 = await repo.save("scope-1", "char-1", candidate2, expected_revision=1)
        assert saved2 is not None
        assert saved2.sticker_id == saved1.sticker_id
        assert saved2.label == saved1.label

        snap = await repo.snapshot("scope-1", "char-1")
        assert len(snap.items) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cross_scope_isolation(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        await repo.update_settings("scope-a", "char-1", learning_enabled=True, expected_revision=0)
        await repo.update_settings("scope-b", "char-1", learning_enabled=True, expected_revision=0)

        c_a, g_a = await _seed_source_chain(db, scope="scope-a", character_id="char-1")
        c_b, g_b = await _seed_source_chain(db, scope="scope-b", character_id="char-1")

        cand_a = StickerSaveCandidate(
            data=PNG_1X1,
            label="sticker a",
            description="description a",
            expression="happy",
            source_connection_id=UUID(c_a),
            generation_id=UUID(g_a),
        )
        saved_a = await repo.save("scope-a", "char-1", cand_a, expected_revision=1)
        assert saved_a is not None

        img_cross = await repo.get_image("scope-b", "char-1", saved_a.sticker_id)
        assert img_cross is None

        snap_b = await repo.snapshot("scope-b", "char-1")
        assert len(snap_b.items) == 0

        cand_b = StickerSaveCandidate(
            data=PNG_1X1,
            label="sticker b",
            description="description b",
            expression="happy",
            source_connection_id=UUID(c_b),
            generation_id=UUID(g_b),
        )
        saved_b = await repo.save("scope-b", "char-1", cand_b, expected_revision=1)
        assert saved_b is not None
        assert saved_b.sticker_id != saved_a.sticker_id
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_delete_invalidates_inflight_revision(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        await repo.update_settings("scope-1", "char-1", learning_enabled=True, expected_revision=0)

        c_id, g_id = await _seed_source_chain(db, scope="scope-1", character_id="char-1")
        candidate = StickerSaveCandidate(
            data=PNG_1X1,
            label="test sticker",
            description="a cute test sticker",
            expression="happy",
            source_connection_id=UUID(c_id),
            generation_id=UUID(g_id),
        )
        saved = await repo.save("scope-1", "char-1", candidate, expected_revision=1)
        assert saved is not None

        del_result = await repo.delete("scope-1", "char-1", saved.sticker_id)
        assert del_result.deleted is True
        assert del_result.revision == 2

        img = await repo.get_image("scope-1", "char-1", saved.sticker_id)
        assert img is None

        with pytest.raises(StickerLibraryRevisionConflict):
            await repo.save("scope-1", "char-1", candidate, expected_revision=1)

        saved_again = await repo.save("scope-1", "char-1", candidate, expected_revision=2)
        assert saved_again is not None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_restart_persists(tmp_path: Path) -> None:
    db_path = tmp_path / "persist.db"
    db1 = Database(path=db_path, config=StorageConfig(database_path=db_path, busy_timeout_ms=5000))
    await db1.open()
    c_id, g_id = await _seed_source_chain(db1, scope="scope-1", character_id="char-1")
    repo1 = SqliteStickerLibraryRepository(db1)
    await repo1.update_settings("scope-1", "char-1", learning_enabled=True, expected_revision=0)

    candidate = StickerSaveCandidate(
        data=PNG_1X1,
        label="persisted sticker",
        description="persisted across restarts",
        expression="happy",
        source_connection_id=UUID(c_id),
        generation_id=UUID(g_id),
    )
    saved = await repo1.save("scope-1", "char-1", candidate, expected_revision=1)
    assert saved is not None
    await db1.close()

    db2 = Database(path=db_path, config=StorageConfig(database_path=db_path, busy_timeout_ms=5000))
    await db2.open()
    try:
        repo2 = SqliteStickerLibraryRepository(db2)
        settings = await repo2.get_settings("scope-1", "char-1")
        assert settings.learning_enabled is True
        assert settings.revision == 1

        img = await repo2.get_image("scope-1", "char-1", saved.sticker_id)
        assert img == PNG_1X1
    finally:
        await db2.close()


@pytest.mark.asyncio
async def test_source_generation_cancelled_or_disabled_connection_prevents_save(
    tmp_path: Path,
) -> None:
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        await repo.update_settings("scope-1", "char-1", learning_enabled=True, expected_revision=0)

        # 1. Cancelled generation
        c1, g1 = await _seed_source_chain(
            db, scope="scope-1", character_id="char-1", generation_status="cancelled"
        )
        res1 = await repo.save(
            "scope-1",
            "char-1",
            StickerSaveCandidate(
                data=PNG_1X1,
                label="cand 1",
                description="cand 1",
                expression="happy",
                source_connection_id=UUID(c1),
                generation_id=UUID(g1),
            ),
            expected_revision=1,
        )
        assert res1 is None

        # 2. Disabled connection
        c2, g2 = await _seed_source_chain(
            db, scope="scope-1", character_id="char-1", connection_enabled=False
        )
        res2 = await repo.save(
            "scope-1",
            "char-1",
            StickerSaveCandidate(
                data=PNG_1X1,
                label="cand 2",
                description="cand 2",
                expression="happy",
                source_connection_id=UUID(c2),
                generation_id=UUID(g2),
            ),
            expected_revision=1,
        )
        assert res2 is None

        # 3. Deleted connection
        c3, g3 = await _seed_source_chain(
            db, scope="scope-1", character_id="char-1", connection_deleted=True
        )
        res3 = await repo.save(
            "scope-1",
            "char-1",
            StickerSaveCandidate(
                data=PNG_1X1,
                label="cand 3",
                description="cand 3",
                expression="happy",
                source_connection_id=UUID(c3),
                generation_id=UUID(g3),
            ),
            expected_revision=1,
        )
        assert res3 is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hash_corruption_fail_closed(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        await repo.update_settings("scope-1", "char-1", learning_enabled=True, expected_revision=0)

        c_id, g_id = await _seed_source_chain(db, scope="scope-1", character_id="char-1")
        candidate = StickerSaveCandidate(
            data=PNG_1X1,
            label="corrupt me",
            description="corrupt test",
            expression="happy",
            source_connection_id=UUID(c_id),
            generation_id=UUID(g_id),
        )
        saved = await repo.save("scope-1", "char-1", candidate, expected_revision=1)
        assert saved is not None

        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE learned_stickers SET data = ? WHERE sticker_id = ?",
                (b"corrupted data"[: len(PNG_1X1)].ljust(len(PNG_1X1), b"x"), saved.sticker_id),
            )

        img = await repo.get_image("scope-1", "char-1", saved.sticker_id)
        assert img is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capacity_atomic(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        await repo.update_settings("scope-1", "char-1", learning_enabled=True, expected_revision=0)

        c_id, g_id = await _seed_source_chain(db, scope="scope-1", character_id="char-1")

        now = datetime.now(UTC).isoformat()
        async with db.transaction() as conn:
            for i in range(100):
                dummy_sha = f"{i:064x}"
                dummy_id = f"learned_{i:032x}"
                await conn.execute(
                    """
                    INSERT INTO learned_stickers (
                        sticker_id, principal_scope, character_id, sha256,
                        mime_type, label, description, expression, byte_size,
                        data, source_connection_id, generation_id, learned_at
                    ) VALUES (
                        ?, 'scope-1', 'char-1', ?, 'image/png', 'label', 'desc', 'happy',
                        10, ?, ?, ?, ?
                    )
                    """,
                    (dummy_id, dummy_sha, b"fake_bytes", c_id, g_id, now),
                )

        candidate = StickerSaveCandidate(
            data=PNG_1X1,
            label="101st sticker",
            description="over capacity",
            expression="happy",
            source_connection_id=UUID(c_id),
            generation_id=UUID(g_id),
        )
        saved = await repo.save("scope-1", "char-1", candidate, expected_revision=1)
        assert saved is None, "Capacity exceeded should return None without auto eviction"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_invalid_source_cannot_reuse_dedup_save_result(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        await repo.update_settings("scope-1", "char-1", learning_enabled=True, expected_revision=0)
        c_id, g_id = await _seed_source_chain(db, scope="scope-1", character_id="char-1")
        candidate = StickerSaveCandidate(
            data=PNG_1X1,
            label="existing",
            description="existing sticker",
            expression="happy",
            source_connection_id=UUID(c_id),
            generation_id=UUID(g_id),
        )
        saved = await repo.save("scope-1", "char-1", candidate, expected_revision=1)
        assert saved is not None
        await db.execute(
            "UPDATE channel_connections SET enabled = 0 WHERE connection_id = ?", (c_id,)
        )
        assert await repo.save("scope-1", "char-1", candidate, expected_revision=1) is None
        assert (await repo.snapshot("scope-1", "char-1")).items == [saved]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_byte_capacity_keeps_existing_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chatwaifu_runtime.persistence.sqlite_sticker_library as repository_module

    monkeypatch.setattr(repository_module, "MAX_TOTAL_BYTES", len(PNG_1X1))
    db = await _init_db(tmp_path)
    try:
        repo = SqliteStickerLibraryRepository(db)
        await repo.update_settings("scope-1", "char-1", learning_enabled=True, expected_revision=0)
        c_id, g_id = await _seed_source_chain(db, scope="scope-1", character_id="char-1")
        candidate = StickerSaveCandidate(
            data=PNG_1X1,
            label="existing",
            description="existing sticker",
            expression="happy",
            source_connection_id=UUID(c_id),
            generation_id=UUID(g_id),
        )
        saved = await repo.save("scope-1", "char-1", candidate, expected_revision=1)
        assert saved is not None
        # Duplicate at capacity must not evict or create a new identifier.
        assert await repo.save("scope-1", "char-1", candidate, expected_revision=1) == saved
        from dataclasses import replace

        assert (
            await repo.save(
                "scope-1", "char-1", replace(candidate, data=PNG_1X1 + b"new"), expected_revision=1
            )
            is None
        )
        assert await repo.get_image("scope-1", "char-1", saved.sticker_id) == PNG_1X1
    finally:
        await db.close()
