"""Scoped, atomic photo assets, lexical recall and deletion provenance."""

import hashlib
import io
import json
import math
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import aiosqlite
from chatwaifu_protocol.photo_memory import (
    PhotoMemoryDeleteResult,
    PhotoMemorySettings,
    PhotoMemorySnapshot,
    PhotoUserAnnotation,
    SavedPhoto,
)
from PIL import Image

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.photo_memory.annotations import (
    PhotoAnnotationCandidate,
    PhotoAnnotationContext,
)
from chatwaifu_runtime.photo_memory.models import (
    PhotoDeletion,
    PhotoGenerationReference,
    PhotoImage,
    PhotoMemoryRevisionConflict,
    PhotoSaveCandidate,
)

MAX_PHOTO_SIZE = 5 * 1024 * 1024
MAX_CAPACITY = 200
MAX_TOTAL_BYTES = 500 * 1024 * 1024


def _tokenize_cjk(text: str) -> str:
    text = text[:2000]
    tokens: list[str] = []
    for match in re.finditer(r"[a-zA-Z0-9]+", text):
        tokens.append(match.group(0))
    cjk_blocks = re.findall(r"[\u4e00-\u9fff]+", text)
    for block in cjk_blocks:
        if len(block) == 1:
            tokens.append(block)
        else:
            for i in range(len(block) - 1):
                tokens.append(block[i : i + 2])
    return " ".join(tokens)


def _tokenize_query(query: str) -> str:
    query = query[:200]
    filler = {
        "之前",
        "刚才",
        "那张",
        "照片",
        "图片",
        "截图",
        "里",
        "有",
        "什么",
        "一张",
        "给我",
        "给你",
        "看看",
        "看下",
        "看一下",
        "发给",
        "发你",
        "发我",
        "发来",
        "发过",
        "的",
        "呢",
        "吗",
        "帮我",
        "找找",
        "找一下",
        "找下",
    }
    for f in filler:
        query = query.replace(f, " ")

    tokens: list[str] = []
    for match in re.finditer(r"[a-zA-Z0-9]{2,}", query):
        tokens.append(f'"{match.group(0)}"')

    cjk_blocks = re.findall(r"[\u4e00-\u9fff]+", query)
    for block in cjk_blocks:
        if len(block) == 1:
            if block not in {
                "张",
                "个",
                "片",
                "图",
                "里",
                "有",
                "看",
                "发",
                "找",
                "给",
                "的",
                "那",
                "这",
                "你",
                "我",
            }:
                tokens.append(f'"{block}"')
        else:
            for i in range(len(block) - 1):
                tokens.append(f'"{block[i : i + 2]}"')

    return " OR ".join(tokens)


class SQLitePhotoMemoryRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def _ensure_settings(
        self, connection: aiosqlite.Connection, scope: str, character_id: str
    ) -> aiosqlite.Row:
        row = await self._fetch_settings_row(connection, scope, character_id)
        if row is not None:
            return row
        now = datetime.now(UTC).isoformat()
        cursor = await connection.execute(
            """
            INSERT INTO photo_memory_settings (
                principal_scope, character_id, retention_enabled, revision,
                created_at, updated_at
            ) VALUES (?, ?, 0, 0, ?, ?)
            ON CONFLICT(principal_scope, character_id) DO NOTHING
            """,
            (scope, character_id, now, now),
        )
        await cursor.close()
        row = await self._fetch_settings_row(connection, scope, character_id)
        if row is None:
            raise RuntimeError("Failed to ensure photo memory settings")
        return row

    async def _fetch_settings_row(
        self, connection: aiosqlite.Connection, scope: str, character_id: str
    ) -> aiosqlite.Row | None:
        cursor = await connection.execute(
            """
            SELECT principal_scope, character_id, retention_enabled, revision, created_at,
                updated_at
            FROM photo_memory_settings
            WHERE principal_scope = ? AND character_id = ?
            """,
            (scope, character_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def get_settings(self, scope: str, character_id: str) -> PhotoMemorySettings:
        async with self._database.transaction() as conn:
            row = await self._ensure_settings(conn, scope, character_id)
            return PhotoMemorySettings(
                retention_enabled=bool(row["retention_enabled"]),
                revision=int(row["revision"]),
            )

    async def update_settings(
        self, scope: str, character_id: str, *, retention_enabled: bool, expected_revision: int
    ) -> PhotoMemorySettings:
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as conn:
            row = await self._ensure_settings(conn, scope, character_id)
            if int(row["revision"]) != expected_revision:
                raise PhotoMemoryRevisionConflict("Settings revision conflict")

            new_rev = expected_revision + 1
            await conn.execute(
                """
                UPDATE photo_memory_settings
                SET retention_enabled = ?, revision = ?, updated_at = ?
                WHERE principal_scope = ? AND character_id = ?
                """,
                (1 if retention_enabled else 0, new_rev, now, scope, character_id),
            )
            return PhotoMemorySettings(
                retention_enabled=retention_enabled,
                revision=new_rev,
            )

    def _row_to_saved_photo(self, row: aiosqlite.Row) -> SavedPhoto:
        keys = row.keys()
        return SavedPhoto(
            user_annotations=json.loads(row["user_annotations_json"])
            if "user_annotations_json" in row.keys()
            else [],
            photo_id=UUID(row["photo_id"]),
            sha256=row["sha256"],
            mime_type=row["mime_type"],
            byte_size=row["byte_size"],
            width=row["width"],
            height=row["height"],
            title=row["title"],
            description=row["description"],
            confidence=row["confidence"],
            keywords=json.loads(row["keywords"]),
            caption=row["caption"],
            received_at=datetime.fromisoformat(row["received_at"]),
            saved_at=datetime.fromisoformat(row["saved_at"]),
            source_connection_id=UUID(row["source_connection_id"]),
            source_session_id=UUID(row["source_session_id"]),
            source_turn_id=UUID(row["source_turn_id"]),
            source_generation_id=UUID(row["source_generation_id"]),
            captured_at=row["captured_at"] if "captured_at" in keys else None,
            captured_at_offset=row["captured_at_offset"] if "captured_at_offset" in keys else None,
            original_width=row["original_width"] if "original_width" in keys else None,
            original_height=row["original_height"] if "original_height" in keys else None,
            original_mime_type=row["original_mime_type"] if "original_mime_type" in keys else None,
        )

    async def snapshot(self, scope: str, character_id: str) -> PhotoMemorySnapshot:
        async with self._database.transaction() as conn:
            row = await self._ensure_settings(conn, scope, character_id)
            settings = PhotoMemorySettings(
                retention_enabled=bool(row["retention_enabled"]),
                revision=int(row["revision"]),
            )

            cursor = await conn.execute(
                """
                SELECT photo_id, sha256, mime_type, byte_size, width, height,
                       title, description, confidence, keywords, caption,
                       received_at, saved_at, source_connection_id,
                       source_session_id, source_turn_id, source_generation_id,
                       captured_at, captured_at_offset, original_width,
                       original_height, original_mime_type, user_annotations_json
                FROM photo_assets
                WHERE principal_scope = ? AND character_id = ?
                ORDER BY saved_at DESC
                """,
                (scope, character_id),
            )
            rows = await cursor.fetchall()
            await cursor.close()

            items = [self._row_to_saved_photo(r) for r in rows]
            total_bytes = sum(r["byte_size"] for r in rows)

            return PhotoMemorySnapshot(
                settings=settings,
                items=items,
                total_bytes=total_bytes,
                capacity=200,
            )

    async def save(
        self,
        scope: str,
        character_id: str,
        candidate: PhotoSaveCandidate,
        *,
        expected_revision: int,
    ) -> SavedPhoto | None:
        data = candidate.data
        byte_size = len(data)
        if byte_size == 0 or byte_size > MAX_PHOTO_SIZE:
            return None

        try:
            with Image.open(io.BytesIO(data)) as img:
                if (
                    img.width > 2048
                    or img.height > 2048
                    or getattr(img, "n_frames", 1) != 1
                    or (candidate.width, candidate.height) != img.size
                ):
                    return None
                img.verify()
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                width, height = img.size
                fmt = img.format
                is_animated = getattr(img, "is_animated", False)
        except Exception:
            return None

        if fmt not in ("PNG", "JPEG") or is_animated:
            return None
        mime = "image/png" if fmt == "PNG" else "image/jpeg"
        if candidate.mime_type != mime:
            return None

        if width > 2048 or height > 2048:
            return None

        if not math.isfinite(candidate.confidence) or not 0.9 <= candidate.confidence <= 1:
            return None
        if not candidate.title.strip() or len(candidate.title) > 80:
            return None
        if not candidate.description.strip() or len(candidate.description) > 600:
            return None
        if len(candidate.keywords) > 12 or any(
            not kw.strip() or len(kw) > 40 for kw in candidate.keywords
        ):
            return None

        sha256 = hashlib.sha256(data).hexdigest()

        async with self._database.transaction() as conn:
            settings_row = await self._ensure_settings(conn, scope, character_id)
            current_rev = int(settings_row["revision"])
            if current_rev != expected_revision:
                raise PhotoMemoryRevisionConflict("Settings revision conflict")
            if not bool(settings_row["retention_enabled"]):
                return None

            cursor = await conn.execute(
                """
                SELECT t.session_id, t.turn_id, IFNULL(u.committed_text, '') as caption,
                       COALESCE(json_extract(u.source_context_json, '$.received_at'),
                                t.accepted_at, u.created_at) as received_at
                FROM channel_connections c
                JOIN channel_turns t ON t.connection_id = c.connection_id
                JOIN turns u ON u.turn_id = t.turn_id
                JOIN generations g ON g.generation_id = t.generation_id
                JOIN sessions s ON s.session_id = t.session_id
                WHERE c.connection_id = ?
                  AND c.enabled = 1
                  AND c.deleted_at IS NULL
                  AND c.provider_id = 'weixin_ilink'
                  AND c.principal_scope = ?
                  AND c.character_id = ?
                  AND t.generation_id = ?
                  AND t.status = 'completed'
                  AND t.principal_scope = c.principal_scope
                  AND g.state = 'completed'
                  AND NOT EXISTS (
                      SELECT 1 FROM photo_context_redactions r
                      WHERE r.generation_id = g.generation_id
                  )
                  AND g.session_id = t.session_id
                  AND g.turn_id = t.turn_id
                  AND s.character_id = c.character_id
                  AND u.role = 'user'
                  AND u.session_id = t.session_id
                  AND (
                      u.source_context_json IS NOT NULL AND (
                          json_extract(u.source_context_json, '$.connection_id') = c.connection_id
                          AND json_extract(u.source_context_json, '$.principal_scope')
                              = c.principal_scope
                          AND json_extract(u.source_context_json, '$.chat_type') = 'direct'
                      )
                  )
                LIMIT 1
                """,
                (
                    str(candidate.source_connection_id),
                    scope,
                    character_id,
                    str(candidate.generation_id),
                ),
            )
            source_valid = await cursor.fetchone()
            await cursor.close()
            if source_valid is None:
                return None

            session_id = source_valid["session_id"]
            turn_id = source_valid["turn_id"]
            caption = source_valid["caption"][:1000]
            if caption.strip() in {"[图片]", "[Image]"}:
                caption = ""
            received_at = source_valid["received_at"]
            now_dt = datetime.now(UTC)
            now = now_dt.isoformat()

            # Check deduplication
            cursor = await conn.execute(
                """
                SELECT photo_id, sha256, mime_type, byte_size, width, height,
                       title, description, confidence, keywords, caption,
                       received_at, saved_at, source_connection_id,
                       source_session_id, source_turn_id, source_generation_id,
                       captured_at, captured_at_offset, original_width,
                       original_height, original_mime_type, user_annotations_json
                FROM photo_assets
                WHERE principal_scope = ? AND character_id = ? AND sha256 = ?
                """,
                (scope, character_id, sha256),
            )
            dup_row = await cursor.fetchone()
            await cursor.close()

            if dup_row is not None:
                photo_id_str = dup_row["photo_id"]
                # Add source ref if not exists
                await conn.execute(
                    """
                    INSERT INTO photo_references (
                        photo_id, generation_id, session_id, reference_type, created_at
                    ) VALUES (?, ?, ?, 'source', ?)
                    ON CONFLICT(photo_id, generation_id) DO NOTHING
                    """,
                    (photo_id_str, str(candidate.generation_id), session_id, now),
                )
                return self._row_to_saved_photo(dup_row)

            # Check capacity
            cursor = await conn.execute(
                """
                SELECT COUNT(*) as cnt, IFNULL(SUM(byte_size), 0) as total
                FROM photo_assets
                WHERE principal_scope = ? AND character_id = ?
                """,
                (scope, character_id),
            )
            cap_row = await cursor.fetchone()
            await cursor.close()
            if (
                cap_row is None
                or cap_row["cnt"] >= MAX_CAPACITY
                or cap_row["total"] + byte_size > MAX_TOTAL_BYTES
            ):
                return None

            photo_id = uuid4()
            photo_id_str = str(photo_id)
            keywords_json = json.dumps(list(candidate.keywords[:12]))

            await conn.execute(
                """
                INSERT INTO photo_assets (
                    photo_id, principal_scope, character_id, sha256, mime_type,
                    byte_size, width, height, title, description, confidence,
                    keywords, caption, received_at, saved_at, source_connection_id,
                    source_session_id, source_turn_id, source_generation_id,
                    captured_at, captured_at_offset, original_width, original_height,
                    original_mime_type, data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    photo_id_str,
                    scope,
                    character_id,
                    sha256,
                    mime,
                    byte_size,
                    width,
                    height,
                    candidate.title,
                    candidate.description,
                    candidate.confidence,
                    keywords_json,
                    caption,
                    received_at,
                    now,
                    str(candidate.source_connection_id),
                    session_id,
                    turn_id,
                    str(candidate.generation_id),
                    candidate.captured_at,
                    candidate.captured_at_offset,
                    candidate.original_width,
                    candidate.original_height,
                    candidate.original_mime_type,
                    data,
                ),
            )

            await conn.execute(
                """
                INSERT INTO photo_references (
                    photo_id, generation_id, session_id, reference_type, created_at
                ) VALUES (?, ?, ?, 'source', ?)
                """,
                (photo_id_str, str(candidate.generation_id), session_id, now),
            )

            fts_content = _tokenize_cjk(
                f"{candidate.title} {candidate.description} "
                f"{' '.join(candidate.keywords)} {caption}"
            )
            await conn.execute(
                """
                INSERT INTO photo_assets_fts (photo_id, content)
                VALUES (?, ?)
                """,
                (photo_id_str, fts_content),
            )

            cursor = await conn.execute(
                "SELECT photo_id, sha256, mime_type, byte_size, width, "
                "height, title, description, confidence, keywords, caption, "
                "received_at, saved_at, source_connection_id, "
                "source_session_id, source_turn_id, source_generation_id, "
                "captured_at, captured_at_offset, original_width, "
                "original_height, original_mime_type, user_annotations_json "
                "FROM photo_assets WHERE photo_id = ?",
                (photo_id_str,),
            )
            saved_row = await cursor.fetchone()
            await cursor.close()
            assert saved_row is not None
            return self._row_to_saved_photo(saved_row)

    async def get_image(
        self, scope: str, character_id: str, photo_id: UUID, *, expected_sha256: str | None = None
    ) -> PhotoImage | None:
        async with self._database.transaction() as conn:
            query = """
                SELECT data, mime_type, sha256
                FROM photo_assets
                WHERE principal_scope = ? AND character_id = ? AND photo_id = ?
            """
            cursor = await conn.execute(query, (scope, character_id, str(photo_id)))
            row = await cursor.fetchone()
            await cursor.close()

            if row is None:
                return None
            if expected_sha256 is not None and row["sha256"] != expected_sha256:
                return None
            return PhotoImage(data=row["data"], mime_type=row["mime_type"])

    async def search(
        self, scope: str, character_id: str, query: str, *, limit: int = 8
    ) -> list[SavedPhoto]:
        limit = max(1, min(8, limit))
        if not query.strip():
            return []

        safe_query = _tokenize_query(query)
        if not safe_query.strip():
            return []

        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT p.photo_id, p.sha256, p.mime_type, p.byte_size, p.width, p.height, p.title,
                    p.description, p.confidence, p.keywords, p.caption, p.received_at, p.saved_at,
                    p.source_connection_id, p.source_session_id, p.source_turn_id,
                    p.source_generation_id,
                    p.captured_at, p.captured_at_offset, p.original_width, p.original_height,
                    p.original_mime_type, p.user_annotations_json
                FROM photo_assets_fts f
                JOIN photo_assets p ON p.photo_id = f.photo_id
                WHERE f.photo_assets_fts MATCH ?
                  AND p.principal_scope = ?
                  AND p.character_id = ?
                ORDER BY rank
                LIMIT ?
                """,
                (safe_query, scope, character_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [self._row_to_saved_photo(r) for r in rows]

    async def list_recent(
        self, scope: str, character_id: str, *, limit: int = 3
    ) -> list[SavedPhoto]:
        limit = max(1, min(3, limit))
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT photo_id, sha256, mime_type, byte_size, width, height, title, description,
                    confidence, keywords, caption, received_at, saved_at, source_connection_id,
                    source_session_id, source_turn_id, source_generation_id,
                    captured_at, captured_at_offset, original_width, original_height,
                    original_mime_type, user_annotations_json
                FROM photo_assets
                WHERE principal_scope = ? AND character_id = ?
                ORDER BY saved_at DESC
                LIMIT ?
                """,
                (scope, character_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [self._row_to_saved_photo(r) for r in rows]

    async def get_photos(
        self, scope: str, character_id: str, photo_ids: Sequence[UUID]
    ) -> list[SavedPhoto]:
        if not photo_ids:
            return []
        async with self._database.transaction() as conn:
            photos: list[SavedPhoto] = []
            for pid in photo_ids:
                cursor = await conn.execute(
                    """
                    SELECT photo_id, sha256, mime_type, byte_size, width, height, title,
                        description, confidence, keywords, caption, received_at, saved_at,
                        source_connection_id, source_session_id, source_turn_id,
                        source_generation_id,
                        captured_at, captured_at_offset, original_width, original_height,
                        original_mime_type, user_annotations_json
                    FROM photo_assets
                    WHERE principal_scope = ? AND character_id = ? AND photo_id = ?
                    """,
                    (scope, character_id, str(pid)),
                )
                row = await cursor.fetchone()
                await cursor.close()
                if row is not None:
                    photos.append(self._row_to_saved_photo(row))
            return photos

    async def register_recall(
        self, scope: str, character_id: str, photo_ids: tuple[UUID, ...], *, generation_id: UUID
    ) -> list[SavedPhoto]:
        photo_ids = photo_ids[:12]
        if not photo_ids:
            return []

        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as conn:
            # register_recall accepts only an existing ACTIVE/RUNNING generation whose session
            # matches scope+character
            cursor = await conn.execute(
                """
                SELECT g.session_id
                FROM generations g
                JOIN turns u ON u.turn_id = g.turn_id
                JOIN sessions s ON s.session_id = g.session_id
                LEFT JOIN photo_context_redactions r ON r.generation_id = g.generation_id
                WHERE g.generation_id = ?
                  AND g.state = 'running'
                  AND u.role = 'user' AND u.session_id = g.session_id
                  AND (u.source_context_json IS NULL OR
                       json_extract(u.source_context_json, '$.chat_type') = 'direct')
                  AND s.character_id = ?
                  AND r.generation_id IS NULL
                  AND COALESCE(json_extract(u.source_context_json, '$.principal_scope'), 'local') =
                      ?
                """,
                (str(generation_id), character_id, scope),
            )
            gen_row = await cursor.fetchone()
            await cursor.close()
            if gen_row is None:
                return []
            session_id = gen_row["session_id"]

            valid_photos: list[SavedPhoto] = []
            for pid in photo_ids:
                pid_str = str(pid)
                cursor = await conn.execute(
                    "SELECT photo_id, sha256, mime_type, byte_size, width, "
                    "height, title, description, confidence, keywords, "
                    "caption, received_at, saved_at, source_connection_id, "
                    "source_session_id, source_turn_id, source_generation_id, "
                    "captured_at, captured_at_offset, original_width, "
                    "original_height, original_mime_type, user_annotations_json "
                    "FROM photo_assets WHERE principal_scope = ? AND "
                    "character_id = ? AND photo_id = ?",
                    (scope, character_id, pid_str),
                )
                p_row = await cursor.fetchone()
                await cursor.close()
                if p_row is not None:
                    await conn.execute(
                        """
                        INSERT INTO photo_references (
                            photo_id, generation_id, session_id, reference_type, created_at
                        ) VALUES (?, ?, ?, 'recall', ?)
                        ON CONFLICT(photo_id, generation_id) DO NOTHING
                        """,
                        (pid_str, str(generation_id), session_id, now),
                    )
                    valid_photos.append(self._row_to_saved_photo(p_row))
            return valid_photos

    async def delete(self, scope: str, character_id: str, photo_id: UUID) -> PhotoDeletion:
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as conn:
            # Check if photo exists in scope
            cursor = await conn.execute(
                "SELECT photo_id FROM photo_assets WHERE principal_scope = ? "
                "AND character_id = ? AND photo_id = ?",
                (scope, character_id, str(photo_id)),
            )
            exists = await cursor.fetchone()
            await cursor.close()

            settings_row = await self._ensure_settings(conn, scope, character_id)
            current_rev = int(settings_row["revision"])

            if exists is None:
                return PhotoDeletion(
                    result=PhotoMemoryDeleteResult(deleted=False, revision=current_rev),
                    affected_generations=(),
                )

            # Get affected generations via recursive CTE
            cursor = await conn.execute(
                """
                WITH RECURSIVE descendants AS (
                    SELECT generation_id, session_id
                    FROM photo_references
                    WHERE photo_id = ?

                    UNION

                    SELECT d.derived_generation_id, g.session_id
                    FROM descendants p
                    JOIN conversation_history_dependencies d ON d.source_generation_id =
                        p.generation_id
                    JOIN generations g ON g.generation_id = d.derived_generation_id
                )
                SELECT DISTINCT generation_id, session_id FROM descendants
                """,
                (str(photo_id),),
            )
            ref_rows = await cursor.fetchall()
            await cursor.close()

            affected: list[PhotoGenerationReference] = []
            for row in ref_rows:
                gen_id = row["generation_id"]
                sess_id = row["session_id"]
                affected.append(
                    PhotoGenerationReference(
                        session_id=UUID(sess_id),
                        generation_id=UUID(gen_id),
                    )
                )
                await conn.execute(
                    """
                    INSERT INTO photo_context_redactions (
                        generation_id, session_id, principal_scope, character_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(generation_id) DO NOTHING
                    """,
                    (gen_id, sess_id, scope, character_id, now),
                )

            await conn.execute("DELETE FROM photo_embeddings WHERE photo_id = ?", (str(photo_id),))
            await conn.execute("DELETE FROM photo_assets_fts WHERE photo_id = ?", (str(photo_id),))
            await conn.execute("DELETE FROM photo_assets WHERE photo_id = ?", (str(photo_id),))

            new_rev = current_rev + 1
            await conn.execute(
                """
                UPDATE photo_memory_settings
                SET revision = ?, updated_at = ?
                WHERE principal_scope = ? AND character_id = ?
                """,
                (new_rev, now, scope, character_id),
            )

            return PhotoDeletion(
                result=PhotoMemoryDeleteResult(deleted=True, revision=new_rev),
                affected_generations=tuple(affected),
            )

    async def annotation_context(self, generation_id: UUID) -> PhotoAnnotationContext | None:
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """SELECT g.session_id, g.turn_id, t.committed_text, t.created_at,
                          t.source_context_json, s.character_id
                   FROM generations g JOIN turns t ON t.turn_id=g.turn_id
                   JOIN sessions s ON s.session_id=g.session_id
                   WHERE g.generation_id=? AND g.state='completed' AND t.role='user'
                     AND t.session_id=g.session_id
                     AND NOT EXISTS (SELECT 1 FROM photo_context_redactions r
                                     WHERE r.generation_id=g.generation_id)""",
                (str(generation_id),),
            )
            row = await cursor.fetchone()
            if row is None or not row["committed_text"]:
                return None
            source = json.loads(row["source_context_json"]) if row["source_context_json"] else None
            if source and source.get("chat_type") != "direct":
                return None
            scope = source.get("principal_scope", "local") if source else "local"
            settings = await self._ensure_settings(conn, scope, row["character_id"])
            if not settings["retention_enabled"]:
                return None
            # Only current referenced photos or the immediately preceding user turn.
            # A new unsaved image turn has no reference and therefore cannot bind to an older photo.
            cursor = await conn.execute(
                """SELECT photo_id FROM photo_references WHERE generation_id=?""",
                (str(generation_id),),
            )
            refs = await cursor.fetchall()
            if not refs:
                cursor = await conn.execute(
                    """SELECT g.generation_id FROM turns t JOIN generations g ON g.turn_id=t.turn_id
                       WHERE t.session_id=? AND t.role='user' AND t.created_at<?
                         AND julianday(?) - julianday(t.created_at) BETWEEN 0 AND 0.0208333333
                       ORDER BY t.created_at DESC LIMIT 1""",
                    (row["session_id"], row["created_at"], row["created_at"]),
                )
                previous = await cursor.fetchone()
                if previous is None:
                    return None
                cursor = await conn.execute(
                    "SELECT photo_id FROM photo_references WHERE generation_id=?",
                    (previous["generation_id"],),
                )
                refs = await cursor.fetchall()
            ids = [r["photo_id"] for r in refs]
            if not ids or len(ids) > 3:
                return None
            placeholders = ",".join("?" for _ in ids)
            cursor = await conn.execute(
                "SELECT * FROM photo_assets WHERE principal_scope=? AND character_id=? "
                f"AND photo_id IN ({placeholders})",
                (scope, row["character_id"], *ids),
            )
            photos = tuple(self._row_to_saved_photo(p) for p in await cursor.fetchall())
            return PhotoAnnotationContext(
                scope,
                row["character_id"],
                generation_id,
                int(settings["revision"]),
                row["committed_text"],
                row["created_at"],
                photos,
            )

    async def save_annotation(
        self, context: PhotoAnnotationContext, candidate: PhotoAnnotationCandidate
    ) -> bool:
        if candidate.confidence < 0.9 or candidate.quote not in context.text:
            return False
        if candidate.photo_id not in {p.photo_id for p in context.photos}:
            return False
        async with self._database.transaction() as conn:
            settings = await self._ensure_settings(conn, context.scope, context.character_id)
            if not settings["retention_enabled"] or settings["revision"] != context.revision:
                return False
            cursor = await conn.execute(
                """SELECT g.session_id, t.committed_text FROM generations g
                   JOIN turns t ON t.turn_id=g.turn_id AND t.session_id=g.session_id
                   JOIN sessions s ON s.session_id=g.session_id
                   WHERE g.generation_id=? AND g.state='completed' AND t.role='user'
                     AND s.character_id=? AND NOT EXISTS
                       (SELECT 1 FROM photo_context_redactions
                        WHERE generation_id=g.generation_id)""",
                (str(context.generation_id), context.character_id),
            )
            source = await cursor.fetchone()
            if source is None or source["committed_text"] != context.text:
                return False
            cursor = await conn.execute(
                "SELECT user_annotations_json, title, description, caption, keywords "
                "FROM photo_assets "
                "WHERE photo_id=? AND principal_scope=? AND character_id=?",
                (str(candidate.photo_id), context.scope, context.character_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            annotations = [PhotoUserAnnotation.model_validate(a) for a in json.loads(row[0])]
            if len(annotations) >= 32 or any(
                a.source_generation_id == context.generation_id
                or (not a.superseded and a.quote == candidate.quote)
                for a in annotations
            ):
                return False
            if candidate.replaces_id is not None:
                target = next(
                    (
                        a
                        for a in annotations
                        if a.annotation_id == candidate.replaces_id
                        and not a.superseded
                        and a.kind == candidate.kind
                    ),
                    None,
                )
                if target is None:
                    return False
                annotations = [
                    a.model_copy(update={"superseded": True}) if a is target else a
                    for a in annotations
                ]
            annotations.append(
                PhotoUserAnnotation(
                    annotation_id=uuid4(),
                    quote=candidate.quote,
                    kind=candidate.kind,
                    source_generation_id=context.generation_id,
                    observed_at=datetime.fromisoformat(context.observed_at),
                )
            )
            await conn.execute(
                "UPDATE photo_assets SET user_annotations_json=? WHERE photo_id=?",
                (
                    json.dumps(
                        [a.model_dump(mode="json") for a in annotations], ensure_ascii=False
                    ),
                    str(candidate.photo_id),
                ),
            )
            content = " ".join(
                [
                    row["title"],
                    row["description"],
                    row["caption"],
                    *json.loads(row["keywords"]),
                    *[a.quote for a in annotations if not a.superseded],
                ]
            )
            await conn.execute(
                "DELETE FROM photo_assets_fts WHERE photo_id=?", (str(candidate.photo_id),)
            )
            await conn.execute(
                "INSERT INTO photo_assets_fts(photo_id,content) VALUES (?,?)",
                (str(candidate.photo_id), _tokenize_cjk(content)),
            )
            await conn.execute(
                "DELETE FROM photo_embeddings WHERE photo_id=?", (str(candidate.photo_id),)
            )
            await conn.execute(
                """INSERT INTO photo_references
                   (photo_id,generation_id,session_id,reference_type,created_at)
                   VALUES (?,?,?,'recall',?) ON CONFLICT(photo_id,generation_id) DO NOTHING""",
                (
                    str(candidate.photo_id),
                    str(context.generation_id),
                    source["session_id"],
                    datetime.now(UTC).isoformat(),
                ),
            )
            return True
