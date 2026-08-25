"""SQLite-backed semantic projection using a separately routed embedding model."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from chatwaifu_protocol.memory import MemoryRecord

from chatwaifu_runtime.memory.ports import ScoredMemoryReference, SemanticMemoryIndex
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.providers.model_config import ModelConfigurationService


class SQLiteSemanticMemoryIndex(SemanticMemoryIndex):
    def __init__(self, database: Database, models: ModelConfigurationService) -> None:
        self._database = database
        self._models = models

    async def upsert(self, record: MemoryRecord) -> None:
        vectors = await self._models.embed([record.text])
        if not vectors:
            return
        fingerprint = self._models.embedding_fingerprint()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO memory_embeddings(memory_id, model_fingerprint, vector_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(memory_id, model_fingerprint) DO UPDATE SET
                    vector_json=excluded.vector_json, updated_at=excluded.updated_at
                """,
                (
                    str(record.memory_id),
                    fingerprint,
                    json.dumps(vectors[0], separators=(",", ":")),
                    datetime.now(UTC).isoformat(),
                ),
            )

    async def delete(self, memory_id: UUID) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                "DELETE FROM memory_embeddings WHERE memory_id = ?", (str(memory_id),)
            )

    async def search(
        self, query: str, namespaces: Sequence[str], limit: int
    ) -> list[ScoredMemoryReference]:
        vectors = await self._models.embed([query])
        if not vectors or not namespaces:
            return []
        fingerprint = self._models.embedding_fingerprint()
        placeholders = ",".join("?" for _ in namespaces)
        rows = await self._database.fetchall(
            f"""
            SELECT e.memory_id, e.vector_json
            FROM memory_embeddings AS e
            JOIN memory_records AS m ON m.memory_id = e.memory_id
            WHERE e.model_fingerprint = ? AND m.state = 'active'
              AND m.namespace IN ({placeholders})
            """,
            (fingerprint, *namespaces),
        )
        query_vector = vectors[0]
        scored = [
            ScoredMemoryReference(
                memory_id=UUID(str(row["memory_id"])),
                score=max(0.0, min(1.0, _cosine(query_vector, json.loads(row["vector_json"])))),
            )
            for row in rows
        ]
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]

    async def rebuild(self, records: Sequence[MemoryRecord]) -> int:
        fingerprint = self._models.embedding_fingerprint()
        async with self._database.transaction() as connection:
            await connection.execute(
                "DELETE FROM memory_embeddings WHERE model_fingerprint != ?",
                (fingerprint,),
            )
        count = 0
        for record in records:
            if record.state == "active":
                await self.upsert(record)
                count += 1
        return count


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
