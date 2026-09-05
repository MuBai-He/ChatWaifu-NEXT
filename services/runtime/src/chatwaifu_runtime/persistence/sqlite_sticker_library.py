"""SQLite implementation of owner-scoped learned sticker repository."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import aiosqlite
from chatwaifu_protocol.sticker_library import (
    LearnedSticker,
    StickerLibraryDeleteResult,
    StickerLibrarySettings,
    StickerLibrarySnapshot,
)
from pydantic import ValidationError

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.sticker_library.models import (
    StickerLibraryRevisionConflict,
    StickerSaveCandidate,
)

PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
MAX_STICKER_SIZE = 5 * 1024 * 1024  # 5 MiB
MAX_CAPACITY = 100
MAX_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MiB


class SqliteStickerLibraryRepository:
    """Thread-safe, transaction-atomic SQLite adapter for learned stickers."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def _ensure_settings(
        self,
        connection: aiosqlite.Connection,
        scope: str,
        character_id: str,
    ) -> aiosqlite.Row:
        row = await self._fetch_settings_row(connection, scope, character_id)
        if row is not None:
            return row
        now = datetime.now(UTC).isoformat()
        cursor = await connection.execute(
            """
            INSERT INTO sticker_library_settings (
                principal_scope, character_id, learning_enabled, revision,
                created_at, updated_at
            ) VALUES (?, ?, 0, 0, ?, ?)
            ON CONFLICT(principal_scope, character_id) DO NOTHING
            """,
            (scope, character_id, now, now),
        )
        await cursor.close()
        row = await self._fetch_settings_row(connection, scope, character_id)
        if row is None:
            raise RuntimeError("Failed to ensure sticker library settings")
        return row

    async def _fetch_settings_row(
        self,
        connection: aiosqlite.Connection,
        scope: str,
        character_id: str,
    ) -> aiosqlite.Row | None:
        cursor = await connection.execute(
            """
            SELECT principal_scope, character_id, learning_enabled, revision, created_at, updated_at
            FROM sticker_library_settings
            WHERE principal_scope = ? AND character_id = ?
            """,
            (scope, character_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def get_settings(
        self,
        scope: str,
        character_id: str,
    ) -> StickerLibrarySettings:
        async with self._database.transaction() as conn:
            row = await self._ensure_settings(conn, scope, character_id)
            return StickerLibrarySettings(
                learning_enabled=bool(row["learning_enabled"]),
                revision=int(row["revision"]),
            )

    async def update_settings(
        self,
        scope: str,
        character_id: str,
        *,
        learning_enabled: bool,
        expected_revision: int,
    ) -> StickerLibrarySettings:
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as conn:
            row = await self._ensure_settings(conn, scope, character_id)
            current_rev = int(row["revision"])
            if current_rev != expected_revision:
                raise StickerLibraryRevisionConflict(
                    f"Settings revision conflict: expected {expected_revision}, "
                    f"current {current_rev}"
                )
            new_rev = current_rev + 1
            cursor = await conn.execute(
                """
                UPDATE sticker_library_settings
                SET learning_enabled = ?, revision = ?, updated_at = ?
                WHERE principal_scope = ? AND character_id = ? AND revision = ?
                """,
                (1 if learning_enabled else 0, new_rev, now, scope, character_id, current_rev),
            )
            affected = cursor.rowcount
            await cursor.close()
            if affected != 1:
                raise StickerLibraryRevisionConflict(
                    f"Concurrent update conflict on settings for {scope}:{character_id}"
                )
            return StickerLibrarySettings(
                learning_enabled=learning_enabled,
                revision=new_rev,
            )

    async def snapshot(
        self,
        scope: str,
        character_id: str,
    ) -> StickerLibrarySnapshot:
        async with self._database.transaction() as conn:
            settings_row = await self._ensure_settings(conn, scope, character_id)
            settings = StickerLibrarySettings(
                learning_enabled=bool(settings_row["learning_enabled"]),
                revision=int(settings_row["revision"]),
            )
            cursor = await conn.execute(
                """
                SELECT sticker_id, sha256, mime_type, label, description,
                       expression, byte_size, source_connection_id, generation_id,
                       learned_at
                FROM learned_stickers
                WHERE principal_scope = ? AND character_id = ?
                ORDER BY learned_at ASC
                """,
                (scope, character_id),
            )
            rows = await cursor.fetchall()
            await cursor.close()

            items: list[LearnedSticker] = []
            total_bytes = 0
            for r in rows:
                items.append(
                    LearnedSticker(
                        sticker_id=r["sticker_id"],
                        sha256=r["sha256"],
                        mime_type="image/png",
                        label=r["label"],
                        description=r["description"],
                        expression=r["expression"],
                        byte_size=r["byte_size"],
                        learned_at=datetime.fromisoformat(r["learned_at"]),
                        source_connection_id=UUID(r["source_connection_id"]),
                    )
                )
                total_bytes += int(r["byte_size"])

            return StickerLibrarySnapshot(
                settings=settings,
                items=items,
                total_bytes=total_bytes,
                capacity=MAX_CAPACITY,
            )

    async def save(
        self,
        scope: str,
        character_id: str,
        candidate: StickerSaveCandidate,
        *,
        expected_revision: int,
    ) -> LearnedSticker | None:
        data = candidate.data
        byte_size = len(data)
        if byte_size == 0 or byte_size > MAX_STICKER_SIZE:
            return None
        if not data.startswith(PNG_MAGIC):
            return None
        sha256 = hashlib.sha256(data).hexdigest()

        # Pre-validate fields via pydantic contract rules
        dummy_id = "learned_" + "0" * 32
        now_dt = datetime.now(UTC)
        try:
            LearnedSticker(
                sticker_id=dummy_id,
                sha256=sha256,
                mime_type="image/png",
                label=candidate.label,
                description=candidate.description,
                expression=candidate.expression,
                byte_size=byte_size,
                learned_at=now_dt,
                source_connection_id=candidate.source_connection_id,
            )
        except ValidationError:
            return None

        async with self._database.transaction() as conn:
            settings_row = await self._ensure_settings(conn, scope, character_id)
            current_rev = int(settings_row["revision"])
            if current_rev != expected_revision:
                raise StickerLibraryRevisionConflict(
                    f"Settings revision conflict: expected {expected_revision}, "
                    f"current {current_rev}"
                )
            if not bool(settings_row["learning_enabled"]):
                return None

            # Atomically verify source connection and source generation
            cursor = await conn.execute(
                """
                SELECT 1
                FROM channel_connections c
                JOIN channel_turns t ON t.connection_id = c.connection_id
                WHERE c.connection_id = ?
                  AND c.enabled = 1
                  AND c.deleted_at IS NULL
                  AND c.principal_scope = ?
                  AND c.character_id = ?
                  AND t.generation_id = ?
                  AND t.status = 'completed'
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
                # Reject unavailable source
                return None

            # Check deduplication before capacity
            cursor = await conn.execute(
                """
                SELECT sticker_id, sha256, mime_type, label, description,
                       expression, byte_size, source_connection_id, generation_id,
                       learned_at
                FROM learned_stickers
                WHERE principal_scope = ? AND character_id = ? AND sha256 = ?
                """,
                (scope, character_id, sha256),
            )
            dup_row = await cursor.fetchone()
            await cursor.close()
            if dup_row is not None:
                return LearnedSticker(
                    sticker_id=dup_row["sticker_id"],
                    sha256=dup_row["sha256"],
                    mime_type="image/png",
                    label=dup_row["label"],
                    description=dup_row["description"],
                    expression=dup_row["expression"],
                    byte_size=dup_row["byte_size"],
                    learned_at=datetime.fromisoformat(dup_row["learned_at"]),
                    source_connection_id=UUID(dup_row["source_connection_id"]),
                )

            # Capacity bounds check: count and total_bytes
            cursor = await conn.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(byte_size), 0)
                FROM learned_stickers
                WHERE principal_scope = ? AND character_id = ?
                """,
                (scope, character_id),
            )
            cap_row = await cursor.fetchone()
            await cursor.close()
            current_count = int(cap_row[0]) if cap_row else 0
            current_bytes = int(cap_row[1]) if cap_row else 0

            if current_count >= MAX_CAPACITY or (current_bytes + byte_size) > MAX_TOTAL_BYTES:
                return None

            sticker_id = f"learned_{uuid4().hex}"
            learned_at_str = now_dt.isoformat()
            cursor = await conn.execute(
                """
                INSERT INTO learned_stickers (
                    sticker_id, principal_scope, character_id, sha256,
                    mime_type, label, description, expression, byte_size,
                    data, source_connection_id, generation_id, learned_at
                ) VALUES (?, ?, ?, ?, 'image/png', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sticker_id,
                    scope,
                    character_id,
                    sha256,
                    candidate.label,
                    candidate.description,
                    candidate.expression,
                    byte_size,
                    data,
                    str(candidate.source_connection_id),
                    str(candidate.generation_id),
                    learned_at_str,
                ),
            )
            await cursor.close()

            return LearnedSticker(
                sticker_id=sticker_id,
                sha256=sha256,
                mime_type="image/png",
                label=candidate.label,
                description=candidate.description,
                expression=candidate.expression,
                byte_size=byte_size,
                learned_at=now_dt,
                source_connection_id=candidate.source_connection_id,
            )

    async def get_image(
        self,
        scope: str,
        character_id: str,
        sticker_id: str,
        *,
        expected_sha256: str | None = None,
    ) -> bytes | None:
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT sha256, byte_size, data
                FROM learned_stickers
                WHERE principal_scope = ? AND character_id = ? AND sticker_id = ?
                """,
                (scope, character_id, sticker_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None

            data: bytes = row["data"]
            stored_sha256: str = row["sha256"]
            byte_size: int = row["byte_size"]

            # Fail closed on corruption or mismatch
            if len(data) != byte_size:
                return None
            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != stored_sha256:
                return None
            if expected_sha256 is not None and stored_sha256 != expected_sha256:
                return None
            return data

    async def delete(
        self,
        scope: str,
        character_id: str,
        sticker_id: str,
    ) -> StickerLibraryDeleteResult:
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as conn:
            settings_row = await self._ensure_settings(conn, scope, character_id)
            current_rev = int(settings_row["revision"])
            new_rev = current_rev + 1

            # Physically remove row/BLOB
            cursor = await conn.execute(
                """
                DELETE FROM learned_stickers
                WHERE principal_scope = ? AND character_id = ? AND sticker_id = ?
                """,
                (scope, character_id, sticker_id),
            )
            deleted = cursor.rowcount > 0
            await cursor.close()

            # Atomically bump revision so in-flight learners cannot resurrect deleted asset
            cursor = await conn.execute(
                """
                UPDATE sticker_library_settings
                SET revision = ?, updated_at = ?
                WHERE principal_scope = ? AND character_id = ?
                """,
                (new_rev, now, scope, character_id),
            )
            await cursor.close()

            return StickerLibraryDeleteResult(
                deleted=deleted,
                revision=new_rev,
            )
