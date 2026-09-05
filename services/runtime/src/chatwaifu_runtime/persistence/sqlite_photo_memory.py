"""Scoped, atomic photo assets, lexical recall and deletion provenance."""

import hashlib
import io
import json
import math
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import aiosqlite
from chatwaifu_protocol.photo_memory import (
    PhotoMemoryDeleteResult,
    PhotoMemorySettings,
    PhotoMemorySnapshot,
    SavedPhoto,
)
from PIL import Image

from chatwaifu_runtime.persistence.database import Database
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
        "看看",
        "发给",
        "的",
        "呢",
        "吗",
        "帮我",
        "找找",
        "找一下",
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
        return SavedPhoto(
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
                       source_session_id, source_turn_id, source_generation_id
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
                       source_session_id, source_turn_id, source_generation_id
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
                    source_session_id, source_turn_id, source_generation_id, data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "source_session_id, source_turn_id, source_generation_id "
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
                    p.source_generation_id
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
                    source_session_id, source_turn_id, source_generation_id
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
                    "source_session_id, source_turn_id, source_generation_id "
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
