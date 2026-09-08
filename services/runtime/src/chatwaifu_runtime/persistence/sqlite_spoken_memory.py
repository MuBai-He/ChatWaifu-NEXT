"""SQLite persistence adapter for durable spoken memory consumer facts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from chatwaifu_runtime.memory.ports import SpokenMemoryFact, SpokenMemoryRepository
from chatwaifu_runtime.persistence.database import Database


def _row_to_fact(r: dict[str, object]) -> SpokenMemoryFact:
    completed_at = datetime.fromisoformat(str(r["completed_at"])) if r.get("completed_at") else None
    next_retry_at = (
        datetime.fromisoformat(str(r["next_retry_at"])) if r.get("next_retry_at") else None
    )
    raw_staged = r.get("staged_candidates_json")
    staged_json = str(raw_staged) if raw_staged is not None else None
    raw_error = r.get("last_error")
    last_error = str(raw_error) if raw_error is not None else None

    return SpokenMemoryFact(
        source_event_id=UUID(str(r["source_event_id"])),
        session_id=UUID(str(r["session_id"])),
        turn_id=UUID(str(r["turn_id"])),
        spoken_text=str(r["spoken_text"]),
        state=str(r["state"]),
        created_at=datetime.fromisoformat(str(r["created_at"])),
        completed_at=completed_at,
        staged_candidates_json=staged_json,
        checkpoint_index=int(str(r.get("checkpoint_index") or "0")),
        retry_count=int(str(r.get("retry_count") or "0")),
        next_retry_at=next_retry_at,
        last_error=last_error,
    )


class SQLiteSpokenMemoryRepository(SpokenMemoryRepository):
    """Stores per-consumer pending/completed facts for spoken memory observation."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def record_fact_if_new(
        self,
        *,
        source_event_id: UUID,
        session_id: UUID,
        turn_id: UUID,
        spoken_text: str,
        created_at: datetime | None = None,
    ) -> bool:
        ts = (created_at or datetime.now(UTC)).isoformat()
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO spoken_memory_facts(
                    source_event_id, session_id, turn_id, spoken_text, state, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (str(source_event_id), str(session_id), str(turn_id), spoken_text, ts),
            )
            inserted = cursor.rowcount > 0
            await cursor.close()
            return inserted

    async def get_fact(self, source_event_id: UUID) -> SpokenMemoryFact | None:
        row = await self._database.fetchone(
            """
            SELECT
                source_event_id, session_id, turn_id, spoken_text, state,
                staged_candidates_json, checkpoint_index, retry_count,
                next_retry_at, last_error, created_at, completed_at
            FROM spoken_memory_facts
            WHERE source_event_id = ?
            """,
            (str(source_event_id),),
        )
        return _row_to_fact(dict(row)) if row is not None else None

    async def mark_completed(
        self,
        source_event_id: UUID,
        completed_at: datetime,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE spoken_memory_facts
                SET state = 'completed', completed_at = ?
                WHERE source_event_id = ?
                """,
                (completed_at.isoformat(), str(source_event_id)),
            )

    async def is_completed(
        self,
        source_event_id: UUID,
    ) -> bool:
        row = await self._database.fetchone(
            "SELECT 1 FROM spoken_memory_facts WHERE source_event_id = ? AND state = 'completed'",
            (str(source_event_id),),
        )
        return row is not None

    async def stage_candidates(
        self,
        source_event_id: UUID,
        candidates_json: str,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE spoken_memory_facts
                SET staged_candidates_json = ?
                WHERE source_event_id = ?
                """,
                (candidates_json, str(source_event_id)),
            )

    async def update_checkpoint(
        self,
        source_event_id: UUID,
        checkpoint_index: int,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE spoken_memory_facts
                SET checkpoint_index = ?
                WHERE source_event_id = ?
                """,
                (checkpoint_index, str(source_event_id)),
            )

    async def record_retry_failure(
        self,
        source_event_id: UUID,
        error_message: str,
        retry_count: int,
        next_retry_at: datetime,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE spoken_memory_facts
                SET retry_count = ?, next_retry_at = ?, last_error = ?
                WHERE source_event_id = ?
                """,
                (retry_count, next_retry_at.isoformat(), error_message, str(source_event_id)),
            )

    async def record_dead_letter(
        self,
        source_event_id: UUID,
        error_message: str,
        retry_count: int,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE spoken_memory_facts
                SET state = 'failed', retry_count = ?, last_error = ?
                WHERE source_event_id = ?
                """,
                (retry_count, error_message, str(source_event_id)),
            )

    async def list_pending(
        self,
        *,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[SpokenMemoryFact]:
        as_of_str = as_of.isoformat() if as_of else None
        rows = await self._database.fetchall(
            """
            SELECT
                source_event_id, session_id, turn_id, spoken_text, state,
                staged_candidates_json, checkpoint_index, retry_count,
                next_retry_at, last_error, created_at, completed_at
            FROM spoken_memory_facts
            WHERE state = 'pending'
              AND (? IS NULL OR next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (as_of_str, as_of_str, max(1, limit)),
        )
        return [_row_to_fact(dict(r)) for r in rows]

    async def get_earliest_retry_at(self) -> datetime | None:
        row = await self._database.fetchone(
            """
            SELECT MIN(next_retry_at) as earliest
            FROM spoken_memory_facts
            WHERE state = 'pending' AND next_retry_at IS NOT NULL
            """
        )
        if row and row["earliest"]:
            return datetime.fromisoformat(str(row["earliest"]))
        return None

    async def backfill_unprocessed_spoken_events(self, limit: int = 100) -> int:
        """Backfill assistant.spoken_text_committed events that are not yet marked completed.

        Bounded batch read directly from events table respecting memory scope reset fences.
        """
        rows = await self._database.fetchall(
            """
            SELECT e.event_id, e.session_id, e.occurred_at, e.envelope_json, e.payload_json
            FROM events e
            JOIN sessions s ON s.session_id = e.session_id
            LEFT JOIN memory_scope_resets r ON r.character_id = s.character_id
            LEFT JOIN memory_scope_resets r_all ON r_all.character_id = '__all__'
            WHERE e.event_type = 'assistant.spoken_text_committed'
              AND (r.reset_at IS NULL OR e.occurred_at > r.reset_at)
              AND (r_all.reset_at IS NULL OR e.occurred_at > r_all.reset_at)
              AND NOT EXISTS (
                  SELECT 1 FROM spoken_memory_facts f
                  WHERE f.source_event_id = e.event_id
              )
            ORDER BY e.sequence ASC
            LIMIT ?
            """,
            (max(1, limit),),
        )
        count = 0
        async with self._database.transaction() as connection:
            for r in rows:
                event_id = UUID(str(r["event_id"]))
                session_id = UUID(str(r["session_id"]))
                occurred_at = str(r["occurred_at"])
                payload_raw = r["payload_json"]
                envelope_raw = r["envelope_json"]
                payload: dict[str, object] = (
                    cast(dict[str, object], json.loads(str(payload_raw))) if payload_raw else {}
                )
                envelope: dict[str, object] = (
                    cast(dict[str, object], json.loads(str(envelope_raw))) if envelope_raw else {}
                )
                raw_spoken = payload.get("spoken_text")
                spoken_text = str(raw_spoken).strip() if isinstance(raw_spoken, str) else ""
                turn_id_raw = envelope.get("turn_id") or payload.get("turn_id")
                if not spoken_text or not isinstance(turn_id_raw, (str, UUID)):
                    continue
                turn_id = UUID(str(turn_id_raw))
                cursor = await connection.execute(
                    """
                    INSERT OR IGNORE INTO spoken_memory_facts(
                        source_event_id, session_id, turn_id, spoken_text, state, created_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?)
                    """,
                    (str(event_id), str(session_id), str(turn_id), spoken_text, occurred_at),
                )
                if cursor.rowcount > 0:
                    count += 1
                await cursor.close()
        return count

    async def record_scope_reset(self, character_id: str, reset_at: datetime) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO memory_scope_resets(character_id, reset_at)
                VALUES (?, ?)
                ON CONFLICT(character_id) DO UPDATE SET reset_at = excluded.reset_at
                """,
                (character_id, reset_at.isoformat()),
            )

    async def clear_scope_facts(self, character_id: str) -> int:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM spoken_memory_facts
                WHERE session_id IN (SELECT session_id FROM sessions WHERE character_id = ?)
                  AND state != 'completed'
                """,
                (character_id,),
            )
            deleted = cursor.rowcount
            await cursor.close()
            return max(0, deleted)

    async def clear_all_facts(self) -> int:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                "DELETE FROM spoken_memory_facts WHERE state != 'completed'"
            )
            deleted = cursor.rowcount
            await cursor.close()
            return max(0, deleted)

    async def is_scope_reset(self, character_id: str, occurred_at: datetime) -> bool:
        ts = occurred_at.isoformat()
        row = await self._database.fetchone(
            """
            SELECT 1 FROM memory_scope_resets
            WHERE (character_id = ? OR character_id = '__all__')
              AND reset_at >= ?
            LIMIT 1
            """,
            (character_id, ts),
        )
        return row is not None
