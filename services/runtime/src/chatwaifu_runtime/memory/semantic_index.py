"""SQLite-backed semantic projection using a separately routed embedding model."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast
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
        fingerprint = self._models.embedding_fingerprint()
        vectors = await self._models.embed([record.text])
        if len(vectors) != 1:
            return
        await self.upsert_active_record(record, vectors[0], expected_fingerprint=fingerprint)

    async def delete(self, memory_id: UUID) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                "DELETE FROM memory_embeddings WHERE memory_id = ?", (str(memory_id),)
            )

    async def upsert_active_record(
        self, record: MemoryRecord, vector: list[float], *, expected_fingerprint: str | None = None
    ) -> bool:
        fingerprint = expected_fingerprint or self._models.embedding_fingerprint()
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as connection:
            if self._models.embedding_fingerprint() != fingerprint:
                return False
            cursor = await connection.execute(
                """
                INSERT INTO memory_embeddings(memory_id, model_fingerprint, vector_json, updated_at)
                SELECT m.memory_id, ?, ?, ?
                FROM memory_records m
                WHERE m.memory_id = ? AND m.state = 'active' AND m.text = ?
                ON CONFLICT(memory_id, model_fingerprint) DO UPDATE SET
                    vector_json=excluded.vector_json, updated_at=excluded.updated_at
                """,
                (
                    fingerprint,
                    json.dumps(vector, separators=(",", ":")),
                    now,
                    str(record.memory_id),
                    record.text,
                ),
            )
            inserted = cursor.rowcount > 0
            await cursor.close()
            return inserted

    async def purge_stale_embeddings(self, active_fingerprint: str) -> int:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "DELETE FROM memory_embeddings WHERE model_fingerprint != ?",
                (active_fingerprint,),
            )
            count = cursor.rowcount
            await cursor.close()
            return count

    async def search(
        self, query: str, namespaces: Sequence[str], limit: int
    ) -> list[ScoredMemoryReference]:
        vectors = await self._models.embed([query])
        if not vectors or not namespaces:
            return []
        query_vector = vectors[0]
        q_dim = len(query_vector)
        if q_dim == 0:
            return []
        try:
            norm = math.hypot(*query_vector)
            if not math.isfinite(norm) or norm <= 1e-12:
                return []
        except OverflowError:
            return []

        placeholders = ",".join("?" for _ in namespaces)
        rows = await self._database.fetchall(
            f"""
            SELECT e.memory_id, e.model_fingerprint, e.vector_json
            FROM memory_embeddings AS e
            JOIN memory_records AS m ON m.memory_id = e.memory_id
            WHERE m.state = 'active'
              AND m.namespace IN ({placeholders})
            ORDER BY e.updated_at DESC
            """,
            (*namespaces,),
        )
        current_fp = self._models.embedding_fingerprint()
        seen: dict[str, tuple[str, list[float]]] = {}
        for row in rows:
            mem_id = str(row["memory_id"])
            fp = str(row["model_fingerprint"])
            if mem_id in seen and seen[mem_id][0] == current_fp:
                continue
            try:
                raw_vec: object = json.loads(str(row["vector_json"]))
                if not isinstance(raw_vec, list):
                    continue
                raw_list = cast(list[object], raw_vec)
                if len(raw_list) != q_dim:
                    continue
                parsed: list[float] = []
                valid = True
                for item in raw_list:
                    if isinstance(item, bool) or not isinstance(item, (int, float)):
                        valid = False
                        break
                    fval = float(item)
                    if not math.isfinite(fval):
                        valid = False
                        break
                    parsed.append(fval)
                if not valid or not parsed:
                    continue
                seen[mem_id] = (fp, parsed)
            except Exception:
                continue

        scored: list[ScoredMemoryReference] = []
        for mem_id, (_fp, vec) in seen.items():
            sim = _cosine(query_vector, vec)
            scored.append(
                ScoredMemoryReference(
                    memory_id=UUID(mem_id),
                    score=max(0.0, min(1.0, sim)),
                )
            )
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]

    async def rebuild(self, records: Sequence[MemoryRecord]) -> int:
        fingerprint = self._models.embedding_fingerprint()
        count = 0
        for record in records:
            if record.state == "active":
                await self.upsert(record)
                count += 1
        await self.purge_stale_embeddings(fingerprint)
        return count


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.hypot(*left)
    right_norm = math.hypot(*right)
    if (
        not math.isfinite(left_norm)
        or not math.isfinite(right_norm)
        or min(left_norm, right_norm) <= 1e-12
    ):
        return 0.0
    return math.fsum((a / left_norm) * (b / right_norm) for a, b in zip(left, right, strict=True))
