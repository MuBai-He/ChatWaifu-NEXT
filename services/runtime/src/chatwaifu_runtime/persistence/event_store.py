"""Append-only session event store with transactional outbox."""

import json
from datetime import UTC, datetime
from uuid import UUID

import aiosqlite
from chatwaifu_protocol.events import EventModel
from pydantic import BaseModel

from chatwaifu_runtime.persistence.database import Database


class EventStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def append[EventT: EventModel](self, event: EventT) -> EventT:
        async with self._database.transaction() as connection:
            return await self.append_in_transaction(connection, event)

    async def append_in_transaction[EventT: EventModel](
        self, connection: aiosqlite.Connection, event: EventT
    ) -> EventT:
        if event.session_id is None:
            raise ValueError("persisted Runtime events require a session_id")
        cursor = await connection.execute(
            """
            UPDATE sessions
            SET next_sequence = next_sequence + 1, updated_at = ?
            WHERE session_id = ?
            RETURNING next_sequence - 1
            """,
            (event.occurred_at.isoformat(), str(event.session_id)),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise KeyError(f"unknown session {event.session_id}")
        persisted = event.model_copy(update={"sequence": int(row[0])})
        envelope_json = persisted.model_dump_json()
        payload = persisted.payload
        payload_json = (
            payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        )
        await connection.execute(
            """
            INSERT INTO events(
                event_id, session_id, sequence, event_type, schema_version,
                occurred_at, source, correlation_id, causation_id, payload_json, envelope_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(persisted.event_id),
                str(persisted.session_id),
                persisted.sequence,
                persisted.event_type,
                persisted.schema_version,
                persisted.occurred_at.isoformat(),
                persisted.source,
                str(persisted.correlation_id) if persisted.correlation_id else None,
                str(persisted.causation_id) if persisted.causation_id else None,
                json.dumps(payload_json, ensure_ascii=False),
                envelope_json,
            ),
        )
        await connection.execute(
            "INSERT INTO outbox(event_id, envelope_json, created_at) VALUES (?, ?, ?)",
            (str(persisted.event_id), envelope_json, datetime.now(UTC).isoformat()),
        )
        return persisted

    async def read_stream(
        self, session_id: UUID, *, after_sequence: int = 0, limit: int = 100
    ) -> list[dict[str, object]]:
        rows = await self._database.fetchall(
            """
            SELECT envelope_json FROM events
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence ASC LIMIT ?
            """,
            (str(session_id), after_sequence, min(max(limit, 1), 500)),
        )
        return [json.loads(str(row["envelope_json"])) for row in rows]

    async def pending_outbox(self, limit: int = 100) -> list[dict[str, object]]:
        rows = await self._database.fetchall(
            """
            SELECT envelope_json FROM outbox
            WHERE published_at IS NULL ORDER BY created_at ASC LIMIT ?
            """,
            (min(max(limit, 1), 500),),
        )
        return [json.loads(str(row["envelope_json"])) for row in rows]

    async def mark_published(self, event_id: UUID | str) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                "UPDATE outbox SET published_at = ? WHERE event_id = ?",
                (datetime.now(UTC).isoformat(), str(event_id)),
            )
