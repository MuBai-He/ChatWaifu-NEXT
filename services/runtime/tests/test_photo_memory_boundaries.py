"""Independent storage review regressions for retention and deletion fences."""
# pyright: reportPrivateUsage=false

import io
from dataclasses import replace
from datetime import datetime
from uuid import UUID

import pytest
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from chatwaifu_runtime.photo_memory.models import PhotoMemoryRevisionConflict
from PIL import Image
from test_photo_memory_repository import _seed_source_chain, db, make_candidate

__all__ = ["db"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"title": "x" * 81},
        {"description": "x" * 601},
        {"title": " "},
        {"keywords": ("",)},
        {"keywords": (" ",)},
        {"keywords": ("x" * 41,)},
        {"confidence": float("nan")},
        {"confidence": 1.01},
        {"confidence": 0.89},
        {"width": 2},
        {"height": 2},
        {"mime_type": "image/jpeg"},
    ],
)
async def test_invalid_observation_never_mutates_storage(
    db: Database, changes: dict[str, object]
) -> None:
    repo = SQLitePhotoMemoryRepository(db)
    await repo.update_settings("local", "default", retention_enabled=True, expected_revision=0)
    conn, gen, _ = await _seed_source_chain(db, scope="local", character_id="default")
    assert (
        await repo.save(
            "local", "default", replace(make_candidate(conn, gen), **changes), expected_revision=1
        )
        is None
    )
    assert not (await repo.snapshot("local", "default")).items


@pytest.mark.asyncio
async def test_disable_delete_fence_and_only_new_upload_can_restore(db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(db)
    conn, gen, _ = await _seed_source_chain(db, scope="local", character_id="default")
    candidate = make_candidate(conn, gen)
    assert await repo.save("local", "default", candidate, expected_revision=0) is None
    await repo.update_settings("local", "default", retention_enabled=True, expected_revision=0)
    photo = await repo.save("local", "default", candidate, expected_revision=1)
    assert photo is not None
    await repo.update_settings("local", "default", retention_enabled=False, expected_revision=1)
    assert (await repo.snapshot("local", "default")).items
    with pytest.raises(PhotoMemoryRevisionConflict):
        await repo.save("local", "default", candidate, expected_revision=1)
    deleted = await repo.delete("local", "default", photo.photo_id)
    assert deleted.result.revision == 3
    await repo.update_settings("local", "default", retention_enabled=True, expected_revision=3)
    assert await repo.save("local", "default", candidate, expected_revision=4) is None
    newer_conn, newer_gen, _ = await _seed_source_chain(db, scope="local", character_id="default")
    newer = await repo.save(
        "local", "default", make_candidate(newer_conn, newer_gen), expected_revision=4
    )
    assert newer is not None and newer.photo_id != photo.photo_id


@pytest.mark.asyncio
async def test_capacity_dedupe_fts_after_duplicate_and_scope(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chatwaifu_runtime.persistence.sqlite_photo_memory as storage

    repo = SQLitePhotoMemoryRepository(db)
    await repo.update_settings("local", "default", retention_enabled=True, expected_revision=0)
    c1, g1, _ = await _seed_source_chain(db, scope="local", character_id="default")
    photo = await repo.save("local", "default", make_candidate(c1, g1), expected_revision=1)
    assert photo is not None
    monkeypatch.setattr(storage, "MAX_CAPACITY", 1)
    c2, g2, _ = await _seed_source_chain(db, scope="local", character_id="default")
    duplicate = await repo.save("local", "default", make_candidate(c2, g2), expected_revision=1)
    assert duplicate is not None and duplicate.photo_id == photo.photo_id
    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), "blue").save(buffer, "PNG")
    c3, g3, _ = await _seed_source_chain(db, scope="local", character_id="default")
    different = replace(
        make_candidate(c3, g3), data=buffer.getvalue(), width=2, height=3, title="蓝色海边"
    )
    assert await repo.save("local", "default", different, expected_revision=1) is None
    monkeypatch.setattr(storage, "MAX_CAPACITY", 200)
    monkeypatch.setattr(storage, "MAX_TOTAL_BYTES", photo.byte_size + len(different.data) - 1)
    assert await repo.save("local", "default", different, expected_revision=1) is None
    monkeypatch.setattr(storage, "MAX_TOTAL_BYTES", photo.byte_size + len(different.data))
    second = await repo.save("local", "default", different, expected_revision=1)
    assert second is not None
    assert len(await repo.search("local", "default", "海边", limit=-1)) == 1
    assert not await repo.search("foreign", "default", "海边")
    assert await repo.get_image("foreign", "default", second.photo_id) is None
    assert not (await repo.delete("foreign", "default", second.photo_id)).result.deleted
    await repo.delete("local", "default", photo.photo_id)
    assert [p.photo_id for p in await repo.search("local", "default", "海边")] == [second.photo_id]


@pytest.mark.asyncio
async def test_source_received_time_and_recall_route_scope(db: Database) -> None:
    repo = SQLitePhotoMemoryRepository(db)
    await repo.update_settings("local", "default", retention_enabled=True, expected_revision=0)
    original_time = "2026-09-01T10:00:00+08:00"
    c1, g1, _ = await _seed_source_chain(
        db, scope="local", character_id="default", source_context={"received_at": original_time}
    )
    photo = await repo.save("local", "default", make_candidate(c1, g1), expected_revision=1)
    assert photo is not None and photo.received_at == datetime.fromisoformat(original_time)
    for context in ({"principal_scope": "foreign"}, {"chat_type": "group"}):
        _, generation, _ = await _seed_source_chain(
            db,
            scope="local",
            character_id="default",
            generation_status="running",
            source_context=context,
        )
        assert not await repo.register_recall(
            "local", "default", (photo.photo_id,), generation_id=UUID(generation)
        )
    _, desktop_gen, _ = await _seed_source_chain(
        db,
        scope="local",
        character_id="default",
        generation_status="running",
        source_context_null=True,
    )
    await repo.delete("local", "default", photo.photo_id)
    assert not await repo.register_recall(
        "local", "default", (photo.photo_id,), generation_id=UUID(desktop_gen)
    )
