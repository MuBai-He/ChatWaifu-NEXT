"""Tests for bounded photo metadata extraction, EXIF stripping, migration 27, and recall."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import base64
import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.migrations import MIGRATIONS
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from chatwaifu_runtime.photo_memory.metadata import (
    PhotoSourceMetadata,
    extract_photo_metadata,
    strip_image_exif,
)
from chatwaifu_runtime.photo_memory.models import PhotoSaveCandidate
from chatwaifu_runtime.photo_memory.recall import PhotoRecallService, _evidence
from chatwaifu_runtime.providers.contracts import LlmInputImage
from PIL import ExifTags, Image

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _create_test_image(
    size: tuple[int, int] = (100, 200),
    format: str = "JPEG",
    *,
    date_original: str | None = None,
    offset_original: str | None = None,
    orientation: int | None = None,
    gps: bool = False,
    serial: str | None = None,
) -> bytes:
    img = Image.new("RGB", size, color="blue")
    exif = img.getexif()

    if orientation is not None:
        exif[ExifTags.Base.Orientation] = orientation

    if serial is not None:
        # BodySerialNumber tag 0xA431 = 42033
        exif[42033] = serial

    if gps:
        # GPS IFD tag 0x8825 = 34853
        gps_ifd = exif.get_ifd(34853)
        gps_ifd[1] = "N"
        gps_ifd[2] = (37.5, 46.2, 29.7)

    if date_original is not None or offset_original is not None:
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        if date_original is not None:
            exif_ifd[ExifTags.Base.DateTimeOriginal] = date_original
        if offset_original is not None:
            exif_ifd[ExifTags.Base.OffsetTimeOriginal] = offset_original

    buf = io.BytesIO()
    if orientation or gps or serial or date_original or offset_original:
        img.save(buf, format=format, exif=exif)
    else:
        img.save(buf, format=format)
    return buf.getvalue()


def test_extract_photo_metadata_with_datetime_and_offset() -> None:
    raw = _create_test_image(
        size=(100, 200),
        format="JPEG",
        date_original="2023:10:15 14:30:00",
        offset_original="+08:00",
    )
    meta = extract_photo_metadata(raw)
    assert meta.captured_at == "2023-10-15T14:30:00+08:00"
    assert meta.captured_at_offset == "+08:00"
    assert meta.original_width == 100
    assert meta.original_height == 200
    assert meta.original_mime_type == "image/jpeg"


def test_extract_photo_metadata_naive_datetime_preserves_unknown_timezone() -> None:
    raw = _create_test_image(
        size=(300, 400),
        format="JPEG",
        date_original="2023:10:15 14:30:00",
        offset_original=None,
    )
    meta = extract_photo_metadata(raw)
    # Naive ISO string: no trailing Z, no timezone offset
    assert meta.captured_at == "2023-10-15T14:30:00"
    assert meta.captured_at_offset is None
    assert meta.original_width == 300
    assert meta.original_height == 400
    assert meta.original_mime_type == "image/jpeg"


def test_extract_photo_metadata_orientation_rotation() -> None:
    # Orientation 6 (90 deg CCW / 270 deg CW) transposes dimensions
    raw_rot6 = _create_test_image(size=(100, 200), orientation=6)
    meta_rot6 = extract_photo_metadata(raw_rot6)
    assert meta_rot6.original_width == 200
    assert meta_rot6.original_height == 100

    # Orientation 8 (90 deg CW) transposes dimensions
    raw_rot8 = _create_test_image(size=(100, 200), orientation=8)
    meta_rot8 = extract_photo_metadata(raw_rot8)
    assert meta_rot8.original_width == 200
    assert meta_rot8.original_height == 100

    # Orientation 1 (normal) does not transpose
    raw_rot1 = _create_test_image(size=(100, 200), orientation=1)
    meta_rot1 = extract_photo_metadata(raw_rot1)
    assert meta_rot1.original_width == 100
    assert meta_rot1.original_height == 200


def test_extract_photo_metadata_png_without_exif() -> None:
    raw = _create_test_image(size=(80, 60), format="PNG")
    meta = extract_photo_metadata(raw)
    assert meta.captured_at is None
    assert meta.captured_at_offset is None
    assert meta.original_width == 80
    assert meta.original_height == 60
    assert meta.original_mime_type == "image/png"


def test_extract_photo_metadata_strict_allowlist_excludes_gps_and_serials() -> None:
    raw = _create_test_image(
        size=(150, 150),
        format="JPEG",
        date_original="2024:06:01 12:00:00",
        gps=True,
        serial="SN-123456789-SECRET",
    )
    meta = extract_photo_metadata(raw)
    # Allowlist: only captured_at, captured_at_offset, original_width,
    # original_height, original_mime_type
    assert meta.captured_at == "2024-06-01T12:00:00"
    assert not hasattr(meta, "gps")
    assert not hasattr(meta, "serial")
    assert not hasattr(meta, "location")
    assert PhotoSourceMetadata.__slots__ == (
        "captured_at",
        "captured_at_offset",
        "original_width",
        "original_height",
        "original_mime_type",
    )


@pytest.mark.parametrize(
    "invalid_date",
    [
        "2023:10:15 14:30:00garbage",
        "2023:02:30 12:00:00",  # invalid calendar day (Feb 30)
        "not-a-datetime",  # non-numeric garbage
        "0000:00:00 00:00:00",  # zeroed out EXIF
    ],
)
def test_extract_photo_metadata_invalid_date_returns_none(invalid_date: str) -> None:
    raw = _create_test_image(date_original=invalid_date)
    meta = extract_photo_metadata(raw)
    assert meta.captured_at is None
    assert meta.captured_at_offset is None
    # Dimensions and mime type should still be parsed safely
    assert meta.original_width == 100
    assert meta.original_height == 200
    assert meta.original_mime_type == "image/jpeg"


def test_extract_photo_metadata_bogus_bytes_safe_unknown() -> None:
    meta = extract_photo_metadata(b"not-an-image-at-all-just-random-bytes")
    assert meta.captured_at is None
    assert meta.captured_at_offset is None
    assert meta.original_width is None
    assert meta.original_height is None
    assert meta.original_mime_type is None


def test_strip_image_exif() -> None:
    raw = _create_test_image(
        date_original="2023:10:15 14:30:00",
        offset_original="+08:00",
        gps=True,
    )
    llm_image = LlmInputImage(data=raw, mime_type="image/jpeg")
    stripped = strip_image_exif(llm_image)

    # Stripped image should have no capture date or EXIF metadata
    meta = extract_photo_metadata(stripped.data)
    assert meta.captured_at is None
    assert meta.captured_at_offset is None
    assert meta.original_width == 100
    assert meta.original_height == 200

    # Unreadable images cannot bypass metadata stripping.
    non_img = LlmInputImage(data=b"raw-test-stub", mime_type="image/jpeg")
    with pytest.raises(ValueError, match="cannot sanitize"):
        strip_image_exif(non_img)


@pytest.fixture
async def repo_db(tmp_path: Path) -> AsyncIterator[tuple[Database, SQLitePhotoMemoryRepository]]:
    db_path = tmp_path / "metadata_test.db"
    database = Database(
        path=db_path,
        config=StorageConfig(database_path=db_path, busy_timeout_ms=5000),
    )
    await database.open()
    repo = SQLitePhotoMemoryRepository(database)
    yield database, repo
    await database.close()


async def _seed_source_chain(
    db: Database,
    *,
    scope: str = "user-1",
    character_id: str = "char-1",
    generation_status: str = "completed",
) -> tuple[str, str, str]:
    conn_id = str(uuid4())
    gen_id = str(uuid4())
    session_id = str(uuid4())
    turn_id = str(uuid4())
    binding_id = str(uuid4())
    channel_turn_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    sc_json = json.dumps(
        {"connection_id": conn_id, "principal_scope": scope, "chat_type": "direct"}
    )

    async with db.transaction() as conn:
        await conn.execute(
            """
            INSERT OR IGNORE INTO channel_connections (
                connection_id, provider_id, name, character_id, principal_scope,
                enabled, access_token_hash, created_at, updated_at
            ) VALUES (?, 'weixin_ilink', 'test-conn', ?, ?, 1, 'hash', ?, ?)
            """,
            (conn_id, character_id, scope, now, now),
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
                turn_id, session_id, role, committed_text, created_at, source_context_json
            ) VALUES (?, ?, 'user', 'caption', ?, ?)
            """,
            (turn_id, session_id, now, sc_json),
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
            ) VALUES (
                ?, ?, ?, ?, 'hash', 'conv-key', 'sender-key', ?, ?, ?, ?, 'completed', ?, ?, ?
            )
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
                now,
                now,
                now,
            ),
        )
    return conn_id, gen_id, session_id


@pytest.mark.asyncio
async def test_migration_27_preserves_legacy_photos_with_null_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_v26.db"
    storage = StorageConfig(database_path=db_path)

    # 1. Initialize up to Migration 26
    legacy_db = Database(
        db_path,
        storage,
        migrations=tuple(m for m in MIGRATIONS if m[0] <= 26),
    )
    await legacy_db.open()

    scope = "scope-legacy"
    char_id = "char-legacy"
    conn_id, gen_id, session_id = await _seed_source_chain(
        legacy_db, scope=scope, character_id=char_id
    )

    # Insert legacy photo directly via v26 schema
    sha256 = "a" * 64
    received_at = "2023-01-01T10:00:00Z"
    saved_at = "2023-01-01T10:05:00Z"
    ref_id = str(uuid4())

    async with legacy_db.transaction() as conn:
        await conn.execute(
            """
            INSERT OR IGNORE INTO photo_memory_settings (
                principal_scope, character_id, retention_enabled, revision, created_at, updated_at
            ) VALUES (?, ?, 1, 1, ?, ?)
            """,
            (scope, char_id, saved_at, saved_at),
        )
        await conn.execute(
            """
            INSERT INTO photo_assets (
                photo_id, principal_scope, character_id, sha256, mime_type, byte_size,
                width, height, title, description, confidence, keywords, caption,
                received_at, saved_at, source_connection_id, source_session_id,
                source_turn_id, source_generation_id, data
            ) VALUES (
                ?, ?, ?, ?, 'image/png', 68, 1, 1, 'legacy photo', 'desc', 0.9, '[]',
                'caption', ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                ref_id,
                scope,
                char_id,
                sha256,
                received_at,
                saved_at,
                conn_id,
                session_id,
                gen_id,
                gen_id,
                PNG_1X1,
            ),
        )
        await conn.execute(
            """
            INSERT INTO photo_references (
                photo_id, generation_id, session_id, reference_type, created_at
            ) VALUES (?, ?, ?, 'source', ?)
            """,
            (ref_id, gen_id, session_id, saved_at),
        )

    await legacy_db.close()

    # 2. Reopen with all migrations including Migration 27
    upgraded_db = Database(db_path, storage)
    await upgraded_db.open()
    try:
        repo = SQLitePhotoMemoryRepository(upgraded_db)
        photos = await repo.get_photos(scope, char_id, [UUID(ref_id)])
        assert len(photos) == 1
        photo = photos[0]
        # Legacy record must have None for new metadata columns;
        # Crucially: captured_at must NOT be fabricated from received_at!
        assert photo.captured_at is None
        assert photo.captured_at_offset is None
        assert photo.original_width is None
        assert photo.original_height is None
        assert photo.original_mime_type is None
        assert photo.received_at.isoformat().replace("+00:00", "Z") == received_at
    finally:
        await upgraded_db.close()


@pytest.mark.asyncio
async def test_save_and_retrieve_photo_with_metadata(
    repo_db: tuple[Database, SQLitePhotoMemoryRepository],
) -> None:
    db, repo = repo_db
    scope = "user-1"
    char_id = "char-1"
    await repo.update_settings(scope, char_id, retention_enabled=True, expected_revision=0)
    conn_id, gen_id, _ = await _seed_source_chain(db, scope=scope, character_id=char_id)

    candidate = PhotoSaveCandidate(
        data=PNG_1X1,
        mime_type="image/png",
        width=1,
        height=1,
        title="mountain view",
        description="a scenic mountain view with snowy peaks",
        confidence=0.95,
        keywords=("mountain", "scenic"),
        source_connection_id=UUID(conn_id),
        generation_id=UUID(gen_id),
        captured_at="2023-08-20T16:45:00+08:00",
        captured_at_offset="+08:00",
        original_width=4032,
        original_height=3024,
        original_mime_type="image/jpeg",
    )

    saved = await repo.save(scope, char_id, candidate, expected_revision=1)
    assert saved is not None
    assert saved.captured_at == "2023-08-20T16:45:00+08:00"
    assert saved.captured_at_offset == "+08:00"
    assert saved.original_width == 4032
    assert saved.original_height == 3024
    assert saved.original_mime_type == "image/jpeg"

    # Verify retrieval through get_photos
    retrieved = await repo.get_photos(scope, char_id, [saved.photo_id])
    assert len(retrieved) == 1
    p = retrieved[0]
    assert p.captured_at == "2023-08-20T16:45:00+08:00"
    assert p.captured_at_offset == "+08:00"
    assert p.original_width == 4032
    assert p.original_height == 3024
    assert p.original_mime_type == "image/jpeg"

    # Verify retrieval through list_recent
    recent = await repo.list_recent(scope, char_id, limit=5)
    assert len(recent) == 1
    assert recent[0].captured_at == "2023-08-20T16:45:00+08:00"

    # Verify retrieval through search
    search_results = await repo.search(scope, char_id, "mountain", limit=5)
    assert len(search_results) == 1
    assert search_results[0].captured_at == "2023-08-20T16:45:00+08:00"


@pytest.mark.asyncio
async def test_deduplication_preserves_existing_asset_metadata(
    repo_db: tuple[Database, SQLitePhotoMemoryRepository],
) -> None:
    db, repo = repo_db
    scope = "user-1"
    char_id = "char-1"
    await repo.update_settings(scope, char_id, retention_enabled=True, expected_revision=0)

    # First save with authoritative metadata
    c1, g1, _ = await _seed_source_chain(db, scope=scope, character_id=char_id)
    first_candidate = PhotoSaveCandidate(
        data=PNG_1X1,
        mime_type="image/png",
        width=1,
        height=1,
        title="first title",
        description="first desc",
        confidence=0.9,
        keywords=("first",),
        source_connection_id=UUID(c1),
        generation_id=UUID(g1),
        captured_at="2023-08-20T16:45:00+08:00",
        captured_at_offset="+08:00",
        original_width=4000,
        original_height=3000,
        original_mime_type="image/jpeg",
    )
    first_saved = await repo.save(scope, char_id, first_candidate, expected_revision=1)
    assert first_saved is not None

    # Second save: duplicate image bytes (identical sha256) with missing metadata
    c2, g2, _ = await _seed_source_chain(db, scope=scope, character_id=char_id)
    second_candidate = PhotoSaveCandidate(
        data=PNG_1X1,
        mime_type="image/png",
        width=1,
        height=1,
        title="second title",
        description="second desc",
        confidence=0.9,
        keywords=("second",),
        source_connection_id=UUID(c2),
        generation_id=UUID(g2),
        captured_at=None,  # missing metadata on duplicate upload
        captured_at_offset=None,
        original_width=None,
        original_height=None,
        original_mime_type=None,
    )
    second_saved = await repo.save(scope, char_id, second_candidate, expected_revision=1)
    assert second_saved is not None
    assert second_saved.photo_id == first_saved.photo_id

    # The existing photo_asset's authoritative metadata should NOT be overwritten!
    assert second_saved.captured_at == "2023-08-20T16:45:00+08:00"
    assert second_saved.captured_at_offset == "+08:00"
    assert second_saved.original_width == 4000
    assert second_saved.original_height == 3000


@pytest.mark.asyncio
async def test_recall_evidence_and_preamble_date_distinction(
    repo_db: tuple[Database, SQLitePhotoMemoryRepository],
) -> None:
    db, repo = repo_db
    scope = "user-1"
    char_id = "char-1"
    await repo.update_settings(scope, char_id, retention_enabled=True, expected_revision=0)
    conn_id, gen_id, _ = await _seed_source_chain(db, scope=scope, character_id=char_id)

    candidate = PhotoSaveCandidate(
        data=PNG_1X1,
        mime_type="image/png",
        width=1,
        height=1,
        title="小猫",
        description="绿草地上一只小猫",
        confidence=0.95,
        keywords=("cat", "ginger"),
        source_connection_id=UUID(conn_id),
        generation_id=UUID(gen_id),
        captured_at="2022-04-12T09:15:00+02:00",
        captured_at_offset="+02:00",
        original_width=3000,
        original_height=2000,
        original_mime_type="image/jpeg",
    )
    saved = await repo.save(scope, char_id, candidate, expected_revision=1)
    assert saved is not None

    # Seed an active/running generation for the turn invoking recall
    _, recall_gen_id, _ = await _seed_source_chain(
        db, scope=scope, character_id=char_id, generation_status="running"
    )

    recall_service = PhotoRecallService(repo)
    result = await recall_service.recall(
        scope,
        char_id,
        "之前那张小猫照片",
        generation_id=UUID(recall_gen_id),
    )

    # 1. Preamble guidance check: strictly guides distinction between received_at and captured_at
    assert "received_at is the authoritative channel receipt timestamp" in result.evidence
    assert "NOT the date the photo was taken" in result.evidence
    assert (
        "captured_at (if present) is from EXIF metadata and is not guaranteed true"
        in result.evidence
    )

    # 2. Serialized evidence check: includes both dates
    ev = _evidence(saved)
    assert ev["captured_at"] == "2022-04-12T09:15:00+02:00"
    assert "received_at" in ev
    assert ev["user_caption"] == "caption"


def test_historical_date_and_unrelated_offset() -> None:
    raw = _create_test_image(date_original="1969:12:31 23:59:59")
    assert extract_photo_metadata(raw).captured_at == "1969-12-31T23:59:59"
    img = Image.open(io.BytesIO(raw))
    exif = img.getexif()
    exif[ExifTags.Base.OffsetTime] = "+08:00"
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)
    assert extract_photo_metadata(out.getvalue()).captured_at_offset is None
