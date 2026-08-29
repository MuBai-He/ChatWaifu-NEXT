"""SQLite implementation of the conversation persistence port."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import aiosqlite
from chatwaifu_protocol.events import (
    AssistantGenerationStartedEvent,
    GenericCoreEvent,
    UserTurnCommittedEvent,
)
from chatwaifu_protocol.session import GenerationState

from chatwaifu_runtime.conversation.repository import (
    ConversationRecoveryRecord,
    ConversationRepository,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore


class SQLiteConversationRepository(ConversationRepository):
    def __init__(self, database: Database, event_store: EventStore) -> None:
        self._database = database
        self._event_store = event_store

    async def recovery_state(self, session_id: UUID) -> ConversationRecoveryRecord:
        """Read history and its replay cursor from one SQLite snapshot."""

        async with self._database.transaction() as connection:
            messages_cursor = await connection.execute(
                """
                SELECT turn_id, role, committed_text, committed_at, created_at
                FROM turns
                WHERE session_id = ? AND committed_text IS NOT NULL
                    AND role IN ('user', 'assistant')
                ORDER BY created_at ASC LIMIT 500
                """,
                (str(session_id),),
            )
            messages = tuple(dict(row) for row in await messages_cursor.fetchall())
            await messages_cursor.close()
            sequence_cursor = await connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE session_id = ?",
                (str(session_id),),
            )
            sequence_row = await sequence_cursor.fetchone()
            await sequence_cursor.close()
            last_sequence = int(sequence_row[0]) if sequence_row is not None else 0
            active_cursor = await connection.execute(
                """
                SELECT generation_id
                FROM generations
                WHERE session_id = ? AND state IN ('created', 'running', 'cancelling')
                ORDER BY started_at DESC LIMIT 1
                """,
                (str(session_id),),
            )
            active_row = await active_cursor.fetchone()
            await active_cursor.close()
            active_generation_id = (
                UUID(str(active_row["generation_id"])) if active_row is not None else None
            )
            after_sequence = last_sequence
            if active_generation_id is not None:
                started_cursor = await connection.execute(
                    """
                    SELECT sequence FROM events
                    WHERE session_id = ?
                      AND event_type = 'assistant.generation_started'
                      AND json_extract(envelope_json, '$.generation_id') = ?
                    ORDER BY sequence ASC LIMIT 1
                    """,
                    (str(session_id), str(active_generation_id)),
                )
                started_row = await started_cursor.fetchone()
                await started_cursor.close()
                if started_row is not None:
                    after_sequence = max(0, int(started_row["sequence"]) - 1)
        return ConversationRecoveryRecord(
            messages=messages,
            after_sequence=after_sequence,
            last_sequence=last_sequence,
            active_generation_id=active_generation_id,
        )

    async def list_messages(self, session_id: UUID, *, limit: int) -> list[dict[str, object]]:
        rows = await self._database.fetchall(
            """
            SELECT turn_id, role, committed_text, committed_at, created_at
            FROM turns
            WHERE session_id = ? AND committed_text IS NOT NULL
                AND role IN ('user', 'assistant')
            ORDER BY created_at ASC LIMIT ?
            """,
            (str(session_id), min(max(limit, 1), 500)),
        )
        return [dict(row) for row in rows]

    async def recent_history(
        self, session_id: UUID, current_turn_id: UUID, *, limit: int
    ) -> tuple[tuple[str, str], ...]:
        rows = await self._database.fetchall(
            """
            SELECT role, committed_text
            FROM turns
            WHERE session_id = ? AND turn_id != ? AND committed_text IS NOT NULL
                AND role IN ('user', 'assistant')
            ORDER BY created_at DESC LIMIT ?
            """,
            (str(session_id), str(current_turn_id), limit),
        )
        return tuple((str(row["role"]), str(row["committed_text"])) for row in reversed(rows))

    async def commit_user_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        audio_stream_id: UUID,
        text: str,
        backend_kind: str,
        occurred_at: datetime,
        user_event: UserTurnCommittedEvent,
        generation_event: AssistantGenerationStartedEvent,
    ) -> tuple[UserTurnCommittedEvent, AssistantGenerationStartedEvent]:
        async with self._database.transaction() as connection:
            await self._insert_generation(
                connection,
                session_id=session_id,
                turn_id=turn_id,
                generation_id=generation_id,
                audio_stream_id=audio_stream_id,
                role="user",
                text=text,
                backend_kind=backend_kind,
                occurred_at=occurred_at,
            )
            persisted_user = await self._event_store.append_in_transaction(connection, user_event)
            persisted_generation = await self._event_store.append_in_transaction(
                connection, generation_event
            )
        return persisted_user, persisted_generation

    async def commit_proactive_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        audio_stream_id: UUID,
        prompt: str,
        backend_kind: str,
        occurred_at: datetime,
        proactive_event: GenericCoreEvent,
        generation_event: AssistantGenerationStartedEvent,
    ) -> tuple[GenericCoreEvent, AssistantGenerationStartedEvent]:
        async with self._database.transaction() as connection:
            await self._insert_generation(
                connection,
                session_id=session_id,
                turn_id=turn_id,
                generation_id=generation_id,
                audio_stream_id=audio_stream_id,
                role="system",
                text=prompt,
                backend_kind=backend_kind,
                occurred_at=occurred_at,
            )
            persisted_proactive = await self._event_store.append_in_transaction(
                connection, proactive_event
            )
            persisted_generation = await self._event_store.append_in_transaction(
                connection, generation_event
            )
        return persisted_proactive, persisted_generation

    async def complete_generation(
        self,
        *,
        session_id: UUID,
        generation_id: UUID,
        assistant_turn_id: UUID,
        output: str,
        occurred_at: datetime,
        set_session_idle: bool,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, role, committed_text, committed_at, created_at
                ) VALUES (?, ?, 'assistant', ?, ?, ?)
                """,
                (
                    str(assistant_turn_id),
                    str(session_id),
                    output,
                    occurred_at.isoformat(),
                    occurred_at.isoformat(),
                ),
            )
            await connection.execute(
                """
                UPDATE generations SET state = ?, output_text = ?, completed_at = ?
                WHERE generation_id = ?
                """,
                (
                    GenerationState.COMPLETED.value,
                    output,
                    occurred_at.isoformat(),
                    str(generation_id),
                ),
            )
            await self._set_idle(connection, session_id, occurred_at, enabled=set_session_idle)

    async def cancel_generation(
        self,
        *,
        session_id: UUID,
        generation_id: UUID,
        occurred_at: datetime,
        set_session_idle: bool,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE generations SET state = ?, invalidated_at = ?
                WHERE generation_id = ? AND state = ?
                """,
                (
                    GenerationState.CANCELLED.value,
                    occurred_at.isoformat(),
                    str(generation_id),
                    GenerationState.RUNNING.value,
                ),
            )
            await self._set_idle(connection, session_id, occurred_at, enabled=set_session_idle)

    async def fail_generation(
        self,
        *,
        session_id: UUID,
        generation_id: UUID,
        error_code: str,
        occurred_at: datetime,
        set_session_idle: bool,
    ) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE generations SET state = ?, error_code = ?, completed_at = ?
                WHERE generation_id = ?
                """,
                (
                    GenerationState.FAILED.value,
                    error_code,
                    occurred_at.isoformat(),
                    str(generation_id),
                ),
            )
            await self._set_idle(connection, session_id, occurred_at, enabled=set_session_idle)

    @staticmethod
    async def _insert_generation(
        connection: aiosqlite.Connection,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        audio_stream_id: UUID,
        role: str,
        text: str,
        backend_kind: str,
        occurred_at: datetime,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO turns(turn_id, session_id, role, committed_text, committed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(turn_id),
                str(session_id),
                role,
                text,
                occurred_at.isoformat(),
                occurred_at.isoformat(),
            ),
        )
        await connection.execute(
            """
            INSERT INTO generations(
                generation_id, session_id, turn_id, state, backend_kind,
                audio_stream_id, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(generation_id),
                str(session_id),
                str(turn_id),
                GenerationState.RUNNING.value,
                backend_kind,
                str(audio_stream_id),
                occurred_at.isoformat(),
            ),
        )
        await connection.execute(
            """
            UPDATE sessions SET conversation_state = 'generating', updated_at = ?
            WHERE session_id = ?
            """,
            (occurred_at.isoformat(), str(session_id)),
        )

    @staticmethod
    async def _set_idle(
        connection: aiosqlite.Connection,
        session_id: UUID,
        occurred_at: datetime,
        *,
        enabled: bool,
    ) -> None:
        if enabled:
            await connection.execute(
                """
                UPDATE sessions SET conversation_state = 'idle', updated_at = ?
                WHERE session_id = ?
                """,
                (occurred_at.isoformat(), str(session_id)),
            )
