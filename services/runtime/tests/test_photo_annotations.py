"""User statements bind to authoritative photo context and remain deletable evidence."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from chatwaifu_runtime.persistence.sqlite_photo_semantic import SQLitePhotoSemanticAdapter
from chatwaifu_runtime.photo_memory.annotations import (
    PhotoAnnotationCandidate,
    PhotoAnnotationService,
)
from chatwaifu_runtime.photo_memory.models import PhotoSaveCandidate
from chatwaifu_runtime.photo_memory.recall import _evidence
from chatwaifu_runtime.providers.model_config import ModelConfigurationService
from test_photo_memory_repository import PNG_1X1, _seed_source_chain


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    path = tmp_path / "annotations.db"
    database = Database(path, StorageConfig(database_path=path))
    await database.open()
    try:
        yield database
    finally:
        await database.close()


async def seeded(database: Database):
    repo = SQLitePhotoMemoryRepository(database)
    await repo.update_settings("user-1", "char-1", retention_enabled=True, expected_revision=0)
    cid, gid, _ = await _seed_source_chain(database)
    photo = await repo.save(
        "user-1",
        "char-1",
        PhotoSaveCandidate(
            data=PNG_1X1,
            mime_type="image/png",
            width=1,
            height=1,
            title="海边",
            description="海边的日落",
            confidence=0.95,
            keywords=("海边",),
            source_connection_id=UUID(cid),
            generation_id=UUID(gid),
        ),
        expected_revision=1,
    )
    assert photo is not None
    return repo, photo


async def followup(database: Database, session: UUID, text: str, delta: int):
    gid, tid = uuid4(), uuid4()
    now = (datetime.now(UTC) + timedelta(seconds=delta)).isoformat()
    async with database.transaction() as conn:
        await conn.execute(
            """INSERT INTO turns
                   (turn_id,session_id,role,committed_text,created_at,source_context_json)
                              VALUES (?,?,'user',?,?,?)""",
            (
                str(tid),
                str(session),
                text,
                now,
                json.dumps({"principal_scope": "user-1", "chat_type": "direct"}),
            ),
        )
        await conn.execute(
            """INSERT INTO generations
                   (generation_id,session_id,turn_id,state,backend_kind,started_at)
                              VALUES (?,?,?,'completed','local',?)""",
            (str(gid), str(session), str(tid), now),
        )
    return gid


async def test_followup_correction_recall_and_deletion(db: Database):
    repo, photo = await seeded(db)
    gid = await followup(db, photo.source_session_id, "这是去年生日拍的", 1)
    context = await repo.annotation_context(gid)
    assert context is not None and len(context.photos) == 1
    candidate = PhotoAnnotationCandidate(
        photo_id=photo.photo_id, quote="去年生日拍的", kind="date", confidence=0.98
    )
    assert await repo.save_annotation(context, candidate)
    assert not await repo.save_annotation(context, candidate)  # replay
    saved = (await repo.snapshot("user-1", "char-1")).items[0]
    note = saved.user_annotations[0]
    assert note.quote == "去年生日拍的" and note.observed_at.isoformat() == context.observed_at
    assert "去年生日拍的" in (_evidence(saved)["user_statements"] or "")
    assert (await repo.search("user-1", "char-1", "生日"))[0].photo_id == photo.photo_id
    gid2 = await followup(db, photo.source_session_id, "说错了，是前年生日拍的", 2)
    context2 = await repo.annotation_context(gid2)
    assert context2 is not None
    correction = PhotoAnnotationCandidate(
        photo_id=photo.photo_id,
        quote="前年生日拍的",
        kind="date",
        confidence=0.99,
        replaces_id=note.annotation_id,
    )
    assert await repo.save_annotation(context2, correction)
    saved2 = (await repo.get_photos("user-1", "char-1", [photo.photo_id]))[0]
    assert len(saved2.user_annotations) == 2
    assert saved2.user_annotations[0].superseded
    assert "去年生日拍的" not in (_evidence(saved2)["user_statements"] or "")
    assert "前年生日拍的" in (_evidence(saved2)["user_statements"] or "")
    await repo.delete("user-1", "char-1", photo.photo_id)
    assert not await repo.save_annotation(context2, correction)
    assert await repo.annotation_context(gid2) is None
    assert not (await repo.snapshot("user-1", "char-1")).items


async def test_unreferenced_intervening_turn_and_wrong_scope_do_not_bind(db: Database):
    repo, photo = await seeded(db)
    await followup(db, photo.source_session_id, "你好", 1)
    gid = await followup(db, photo.source_session_id, "这是去年拍的", 2)
    assert await repo.annotation_context(gid) is None
    other = await followup(db, photo.source_session_id, "这是去年拍的", 3)
    async with db.transaction() as conn:
        await conn.execute(
            """UPDATE turns SET source_context_json=? WHERE turn_id=
                              (SELECT turn_id FROM generations WHERE generation_id=?)""",
            (json.dumps({"principal_scope": "other", "chat_type": "direct"}), str(other)),
        )
    assert await repo.annotation_context(other) is None


async def test_revision_fence_and_stale_embedding_annotation_count(db: Database):
    repo, photo = await seeded(db)
    gid = await followup(db, photo.source_session_id, "这是生日拍的", 1)
    context = await repo.annotation_context(gid)
    assert context is not None
    candidate = PhotoAnnotationCandidate(
        photo_id=photo.photo_id, quote="生日拍的", kind="event", confidence=0.95
    )
    assert await repo.save_annotation(context, candidate)
    adapter = SQLitePhotoSemanticAdapter(db)
    assert not await adapter.upsert_embedding(
        "user-1",
        "char-1",
        photo.photo_id,
        photo.sha256,
        "photo_description_v1",
        "space",
        "model",
        0,
        [1.0, 0.0],
        expected_annotation_count=0,
    )
    assert await adapter.upsert_embedding(
        "user-1",
        "char-1",
        photo.photo_id,
        photo.sha256,
        "photo_description_v1",
        "space",
        "model",
        0,
        [1.0, 0.0],
        expected_annotation_count=1,
    )
    next_gid = await followup(db, photo.source_session_id, "和朋友一起拍的", 2)
    stale = await repo.annotation_context(next_gid)
    assert stale is not None
    await repo.update_settings("user-1", "char-1", retention_enabled=False, expected_revision=1)
    assert not await repo.save_annotation(stale, candidate)


async def test_service_rejects_invented_quote_and_cancels_hung_provider(db: Database):
    repo, photo = await seeded(db)
    gid = await followup(db, photo.source_session_id, "这是生日拍的", 1)
    entered, release = asyncio.Event(), asyncio.Event()

    class Config:
        enabled = True
        provider = "demo"

    class Models:
        hang = False

        def get(self, role: str):
            return Config()

        async def complete(self, role: str, **kwargs: object):
            entered.set()
            if self.hang:
                await release.wait()
            return json.dumps(
                {
                    "photo_id": str(photo.photo_id),
                    "quote": "这是月球拍的",
                    "kind": "context",
                    "confidence": 0.99,
                    "replaces_id": None,
                }
            )

    models = Models()
    service = PhotoAnnotationService(repo, cast(ModelConfigurationService, models))
    service.start()
    await service._run(gid)
    assert not (await repo.snapshot("user-1", "char-1")).items[0].user_annotations
    models.hang = True
    entered.clear()
    service.observe(gid)
    await asyncio.wait_for(entered.wait(), 1)
    await service.stop()
    assert not service._tasks
