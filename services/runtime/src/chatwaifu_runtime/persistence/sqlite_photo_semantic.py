"""Scoped SQLite persistence adapter for rebuildable photo embedding projections."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import aiosqlite
from chatwaifu_protocol.photo_memory import SavedPhoto

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.photo_memory.ports import PhotoSemanticPersistencePort

logger = logging.getLogger(__name__)


class SQLitePhotoSemanticAdapter(PhotoSemanticPersistencePort):
    """Scoped index reads and transactional writes only; no provider calls or ranking."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert_embedding(
        self,
        scope: str,
        character_id: str,
        photo_id: UUID,
        sha256: str,
        representation: str,
        vector_space_id: str,
        model_fingerprint: str,
        route_generation: int,
        vector: list[float],
        *,
        guard: Callable[[], bool] | None = None,
        expected_annotation_count: int | None = None,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        vector_json = json.dumps(vector)
        async with self._database.transaction() as conn:
            if guard is not None and not guard():
                return False
            cursor = await conn.execute(
                """
                INSERT INTO photo_embeddings (
                    photo_id, representation, principal_scope, character_id,
                    vector_space_id, model_fingerprint, route_generation,
                    vector_json, updated_at
                )
                SELECT
                    p.photo_id, ?, p.principal_scope, p.character_id,
                    ?, ?, ?, ?, ?
                FROM photo_assets p
                WHERE p.photo_id = ?
                  AND p.principal_scope = ?
                  AND p.character_id = ?
                  AND p.sha256 = ?
                  AND (? IS NULL OR json_array_length(p.user_annotations_json) = ?)
                ON CONFLICT(photo_id, representation) DO UPDATE SET
                    vector_space_id = excluded.vector_space_id,
                    model_fingerprint = excluded.model_fingerprint,
                    route_generation = excluded.route_generation,
                    vector_json = excluded.vector_json,
                    updated_at = excluded.updated_at
                WHERE excluded.route_generation >= photo_embeddings.route_generation
                """,
                (
                    representation,
                    vector_space_id,
                    model_fingerprint,
                    route_generation,
                    vector_json,
                    now,
                    str(photo_id),
                    scope,
                    character_id,
                    sha256,
                    expected_annotation_count,
                    expected_annotation_count,
                ),
            )
            inserted = cursor.rowcount > 0
            await cursor.close()
            return inserted

    async def list_embeddings(
        self,
        scope: str,
        character_id: str,
        representation: str,
        vector_space_id: str | None = None,
    ) -> list[tuple[UUID, list[float]]]:
        async with self._database.transaction() as conn:
            if vector_space_id is not None:
                cursor = await conn.execute(
                    """
                    SELECT photo_id, vector_json
                    FROM photo_embeddings
                    WHERE principal_scope = ?
                      AND character_id = ?
                      AND representation = ?
                      AND vector_space_id = ?
                    """,
                    (scope, character_id, representation, vector_space_id),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT photo_id, vector_json
                    FROM photo_embeddings
                    WHERE principal_scope = ?
                      AND character_id = ?
                      AND representation = ?
                    """,
                    (scope, character_id, representation),
                )
            rows = await cursor.fetchall()
            await cursor.close()

            results: list[tuple[UUID, list[float]]] = []
            for row in rows:
                try:
                    raw_vec = cast(object, json.loads(str(row["vector_json"])))
                    if isinstance(raw_vec, list):
                        parsed: list[float] = []
                        valid = True
                        for item in cast(list[object], raw_vec):
                            if isinstance(item, bool) or not isinstance(item, (int, float)):
                                valid = False
                                break
                            fval = float(item)
                            if not math.isfinite(fval):
                                valid = False
                                break
                            parsed.append(fval)
                        if valid and parsed:
                            results.append((UUID(str(row["photo_id"])), parsed))
                except Exception as err:
                    logger.warning("corrupt vector_json for photo %s: %s", row["photo_id"], err)
            return results

    async def count_embeddings(
        self,
        scope: str,
        character_id: str,
        representation: str,
    ) -> int:
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM photo_embeddings
                WHERE principal_scope = ?
                  AND character_id = ?
                  AND representation = ?
                """,
                (scope, character_id, representation),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return int(row["cnt"]) if row is not None else 0

    async def get_max_route_generation(self) -> int:
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(route_generation), 0) AS max_gen FROM photo_embeddings"
            )
            row = await cursor.fetchone()
            await cursor.close()
            return int(row["max_gen"]) if row is not None else 0

    async def list_all_photos(
        self,
    ) -> list[tuple[str, str, SavedPhoto]]:
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT DISTINCT principal_scope, character_id
                FROM photo_assets
                ORDER BY principal_scope, character_id
                """
            )
            principals = await cursor.fetchall()
            await cursor.close()

            results: list[tuple[str, str, SavedPhoto]] = []
            for p in principals:
                scope = str(p["principal_scope"])
                char_id = str(p["character_id"])
                c2 = await conn.execute(
                    """
                    SELECT p.principal_scope, p.character_id, p.photo_id, p.sha256, p.mime_type,
                        p.byte_size, p.width, p.height, p.title, p.description, p.confidence,
                        p.keywords, p.caption, p.received_at, p.saved_at, p.source_connection_id,
                        p.source_session_id, p.source_turn_id, p.source_generation_id,
                        p.captured_at, p.captured_at_offset, p.original_width, p.original_height,
                        p.original_mime_type, p.user_annotations_json
                    FROM photo_assets p
                    WHERE p.principal_scope = ? AND p.character_id = ?
                    ORDER BY p.saved_at ASC
                    LIMIT 200
                    """,
                    (scope, char_id),
                )
                rows = await c2.fetchall()
                await c2.close()
                results.extend((scope, char_id, _row_to_saved_photo(r)) for r in rows)
            return results

    async def delete_embedding(self, photo_id: UUID) -> None:
        async with self._database.transaction() as conn:
            await conn.execute(
                "DELETE FROM photo_embeddings WHERE photo_id = ?",
                (str(photo_id),),
            )

    async def purge_stale_spaces(
        self,
        representation: str,
        active_vector_space_id: str,
    ) -> int:
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM photo_embeddings
                WHERE representation = ? AND vector_space_id != ?
                """,
                (representation, active_vector_space_id),
            )
            deleted = cursor.rowcount
            await cursor.close()
            return deleted

    async def purge_stale_generations(
        self,
        representation: str,
        active_route_generation: int,
    ) -> int:
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM photo_embeddings
                WHERE representation = ? AND route_generation < ?
                """,
                (representation, active_route_generation),
            )
            deleted = cursor.rowcount
            await cursor.close()
            return deleted

    async def list_unindexed_photos(
        self,
        scope: str,
        character_id: str,
        representation: str,
        vector_space_id: str,
        *,
        limit: int = 50,
    ) -> list[SavedPhoto]:
        limit = max(1, min(200, limit))
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT p.photo_id, p.sha256, p.mime_type, p.byte_size, p.width, p.height, p.title,
                    p.description, p.confidence, p.keywords, p.caption, p.received_at, p.saved_at,
                    p.source_connection_id, p.source_session_id, p.source_turn_id,
                    p.source_generation_id,
                    p.captured_at, p.captured_at_offset, p.original_width, p.original_height,
                    p.original_mime_type, p.user_annotations_json
                FROM photo_assets p
                LEFT JOIN photo_embeddings e
                    ON e.photo_id = p.photo_id
                   AND e.representation = ?
                   AND e.vector_space_id = ?
                WHERE p.principal_scope = ? AND p.character_id = ? AND e.photo_id IS NULL
                ORDER BY p.saved_at ASC
                LIMIT ?
                """,
                (representation, vector_space_id, scope, character_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [_row_to_saved_photo(r) for r in rows]

    async def list_all_unindexed_photos(
        self,
        representation: str,
        vector_space_id: str,
        *,
        limit: int = 50,
    ) -> list[tuple[str, str, SavedPhoto]]:
        limit = max(1, min(200, limit))
        async with self._database.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT p.principal_scope, p.character_id, p.photo_id, p.sha256, p.mime_type,
                    p.byte_size, p.width, p.height, p.title, p.description, p.confidence,
                    p.keywords, p.caption, p.received_at, p.saved_at, p.source_connection_id,
                    p.source_session_id, p.source_turn_id, p.source_generation_id,
                    p.captured_at, p.captured_at_offset, p.original_width, p.original_height,
                    p.original_mime_type, p.user_annotations_json
                FROM photo_assets p
                LEFT JOIN photo_embeddings e
                    ON e.photo_id = p.photo_id
                   AND e.representation = ?
                   AND e.vector_space_id = ?
                WHERE e.photo_id IS NULL
                ORDER BY p.saved_at ASC
                LIMIT ?
                """,
                (representation, vector_space_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return [(r["principal_scope"], r["character_id"], _row_to_saved_photo(r)) for r in rows]


def _row_to_saved_photo(row: aiosqlite.Row) -> SavedPhoto:
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
