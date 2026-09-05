"""SQLite implementation of the conversation persistence port."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid5

import aiosqlite
from chatwaifu_protocol.character import ResponsePlan
from chatwaifu_protocol.events import (
    AssistantGenerationStartedEvent,
    AvatarCueEmittedEvent,
    ErrorRaisedEvent,
    GenericCoreEvent,
    UserTurnCommittedEvent,
)
from chatwaifu_protocol.session import GenerationState
from pydantic import BaseModel, ConfigDict

from chatwaifu_runtime.conversation.models import (
    ConversationHistoryEntry,
    ConversationSourceContext,
    ConversationUserInputContext,
)
from chatwaifu_runtime.conversation.repository import (
    ConversationGenerationRecord,
    ConversationRecoveryRecord,
    ConversationRepository,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore

_LOCAL_OWNER_SCOPE = "local"


class _EventPayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    plan: ResponsePlan | None = None


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
    ) -> tuple[ConversationHistoryEntry, ...]:
        local_rows = await self._database.fetchall(
            """
            SELECT role, committed_text, source_context_json, committed_at, generation_id
            FROM turns
            WHERE session_id = ? AND turn_id != ? AND committed_text IS NOT NULL
                AND role IN ('user', 'assistant')
                AND (role != 'assistant' OR NOT EXISTS (
                    SELECT 1 FROM photo_context_redactions AS redaction
                    WHERE redaction.generation_id = turns.generation_id
                ))
            ORDER BY created_at DESC LIMIT ?
            """,
            (str(session_id), str(current_turn_id), limit),
        )
        # Keep a small, durable cross-surface ledger beside the current session
        # history. This lets a later desktop turn understand that a recent
        # exchange happened through WeChat (and, in future, another channel)
        # without merging independent provider sessions or trusting display
        # labels as identity. V1 has one owner principal_scope and joins only the
        # same character.
        sourced_rows = await self._database.fetchall(
            """
            SELECT turn.role, turn.committed_text, turn.source_context_json,
                   turn.committed_at, turn.generation_id
            FROM turns AS turn
            JOIN sessions AS source_session
              ON source_session.session_id = turn.session_id
            JOIN sessions AS current_session
              ON current_session.session_id = ?
            WHERE turn.session_id != ?
              AND source_session.character_id = current_session.character_id
              AND turn.source_context_json IS NOT NULL
              AND COALESCE(
                    json_extract(turn.source_context_json, '$.principal_scope'),
                    'local'
                  ) = ?
              AND turn.committed_text IS NOT NULL
              AND turn.role IN ('user', 'assistant')
              AND (turn.role != 'assistant' OR NOT EXISTS (
                  SELECT 1 FROM photo_context_redactions AS redaction
                  WHERE redaction.generation_id = turn.generation_id
              ))
            ORDER BY turn.committed_at DESC, turn.created_at DESC
            LIMIT ?
            """,
            (str(session_id), str(session_id), _LOCAL_OWNER_SCOPE, min(12, limit)),
        )
        rows = sorted(
            (*local_rows, *sourced_rows),
            key=lambda row: str(row["committed_at"]),
        )
        entries: list[ConversationHistoryEntry] = []
        for row in rows:
            raw_source = row["source_context_json"]
            source = (
                ConversationSourceContext.from_json(str(raw_source))
                if raw_source is not None
                else None
            )
            entries.append(
                ConversationHistoryEntry(
                    role=str(row["role"]),
                    text=str(row["committed_text"]),
                    source_context=source,
                    generation_id=(
                        UUID(str(row["generation_id"])) if row["generation_id"] else None
                    ),
                )
            )
        return tuple(entries)

    async def prepare_history(
        self, generation_id: UUID, history: tuple[ConversationHistoryEntry, ...]
    ) -> tuple[ConversationHistoryEntry, ...]:
        # History may have been read before a concurrent deletion. Check it again
        # at the prompt boundary and persist indirect dependencies in the same
        # transaction, including before a background photo observation finishes.
        kept: list[ConversationHistoryEntry] = []
        async with self._database.transaction() as connection:
            for entry in history:
                if entry.role != "assistant" or entry.generation_id is None:
                    kept.append(entry)
                    continue
                cursor = await connection.execute(
                    "SELECT 1 FROM photo_context_redactions WHERE generation_id = ?",
                    (str(entry.generation_id),),
                )
                redacted = await cursor.fetchone()
                await cursor.close()
                if redacted is not None:
                    continue
                await connection.execute(
                    """
                    INSERT OR IGNORE INTO conversation_history_dependencies(
                        source_generation_id, derived_generation_id
                    ) VALUES (?, ?)
                    """,
                    (str(entry.generation_id), str(generation_id)),
                )
                kept.append(entry)
        return tuple(kept)

    async def generation_result(self, generation_id: UUID) -> ConversationGenerationRecord | None:
        row = await self._database.fetchone(
            """
            SELECT generation_id, session_id, turn_id, state, output_text, error_code,
                   audio_stream_id
            FROM generations WHERE generation_id = ?
            """,
            (str(generation_id),),
        )
        if row is None:
            return None
        raw_audio_stream_id = row["audio_stream_id"] if "audio_stream_id" in row.keys() else None
        return ConversationGenerationRecord(
            generation_id=UUID(str(row["generation_id"])),
            session_id=UUID(str(row["session_id"])),
            turn_id=UUID(str(row["turn_id"])),
            state=GenerationState(str(row["state"])),
            output_text=str(row["output_text"]) if row["output_text"] is not None else None,
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            audio_stream_id=UUID(str(raw_audio_stream_id))
            if raw_audio_stream_id is not None
            else None,
        )

    async def generation_user_input_context(
        self, generation_id: UUID
    ) -> ConversationUserInputContext | None:
        # Anchor the current input to its durable channel generation. Read only its
        # immediate predecessor, so another route or topic acts as a barrier.
        row = await self._database.fetchone(
            """
            SELECT substr(current.committed_text, 1, 2000) AS user_text,
                   length(current.committed_text) AS text_length,
                   CASE WHEN previous_channel.binding_id = channel.binding_id
                        AND previous_channel.connection_id = channel.connection_id
                        AND previous_channel.principal_scope = channel.principal_scope
                        AND previous_channel.sender_key = channel.sender_key
                        AND julianday(current.committed_at) - julianday(previous.committed_at)
                            BETWEEN 0 AND (5.0 / 1440)
                        AND length(previous.committed_text) <= 2000
                   THEN previous.committed_text END AS previous_user_text
            FROM generations AS generation
            JOIN turns AS current ON current.turn_id = generation.turn_id
                AND current.session_id = generation.session_id AND current.role = 'user'
            JOIN channel_turns AS channel ON channel.generation_id = generation.generation_id
                AND channel.turn_id = current.turn_id AND channel.session_id = current.session_id
            LEFT JOIN turns AS previous ON previous.rowid = (
                SELECT candidate.rowid FROM turns AS candidate
                WHERE candidate.session_id = current.session_id AND candidate.role = 'user'
                    AND candidate.rowid < current.rowid
                ORDER BY candidate.rowid DESC LIMIT 1
            )
            LEFT JOIN channel_turns AS previous_channel
                ON previous_channel.turn_id = previous.turn_id
                AND previous_channel.session_id = current.session_id
            WHERE generation.generation_id = ?
            """,
            (str(generation_id),),
        )
        if row is None or row["user_text"] is None or int(row["text_length"]) > 2000:
            return None
        return ConversationUserInputContext(
            user_text=str(row["user_text"]),
            previous_user_text=(
                str(row["previous_user_text"]) if row["previous_user_text"] is not None else None
            ),
        )

    async def generation_response_plan(self, generation_id: UUID) -> ResponsePlan | None:
        async with self._database.transaction() as connection:
            gen_cursor = await connection.execute(
                """
                SELECT session_id, turn_id
                FROM generations
                WHERE generation_id = ?
                """,
                (str(generation_id),),
            )
            gen_row = await gen_cursor.fetchone()
            await gen_cursor.close()
            if gen_row is None:
                return None
            session_id = str(gen_row["session_id"])
            turn_id = str(gen_row["turn_id"])

            event_cursor = await connection.execute(
                """
                SELECT payload_json
                FROM events
                WHERE session_id = ?
                  AND event_type = 'character.response_planned'
                  AND json_extract(envelope_json, '$.generation_id') = ?
                  AND json_extract(envelope_json, '$.turn_id') = ?
                ORDER BY sequence DESC LIMIT 1
                """,
                (session_id, str(generation_id), turn_id),
            )
            event_row = await event_cursor.fetchone()
            await event_cursor.close()
            if event_row is None:
                return None

            try:
                model = _EventPayloadModel.model_validate_json(str(event_row["payload_json"]))
                return model.plan
            except Exception:
                return None

    async def commit_user_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        audio_stream_id: UUID,
        text: str,
        backend_kind: str,
        source_context: ConversationSourceContext | None,
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
                source_context=source_context,
                occurred_at=occurred_at,
            )
            persisted_user = await self._event_store.append_in_transaction(connection, user_event)
            persisted_generation = await self._event_store.append_in_transaction(
                connection, generation_event
            )
        return persisted_user, persisted_generation

    async def begin_realtime_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        audio_stream_id: UUID,
        backend_kind: str,
        occurred_at: datetime,
        generation_event: AssistantGenerationStartedEvent,
    ) -> AssistantGenerationStartedEvent:
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, role, committed_text, committed_at, created_at,
                    source_context_json
                ) VALUES (?, ?, 'user', NULL, NULL, ?, NULL)
                """,
                (str(turn_id), str(session_id), occurred_at.isoformat()),
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
            persisted_generation = await self._event_store.append_in_transaction(
                connection, generation_event
            )
        return persisted_generation

    async def commit_realtime_user_transcript(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        occurred_at: datetime,
        user_event: UserTurnCommittedEvent,
    ) -> UserTurnCommittedEvent | None:
        async with self._database.transaction() as connection:
            # Defense in depth: the generation must provably own this
            # session/turn pair, so a misrouted caller cannot commit text to a
            # turn that belongs to a different generation.
            cursor = await connection.execute(
                """
                UPDATE turns SET committed_text = ?, committed_at = ?
                WHERE turn_id = ? AND session_id = ? AND committed_text IS NULL
                AND EXISTS (
                    SELECT 1 FROM generations
                    WHERE generation_id = ? AND session_id = ? AND turn_id = ?
                )
                """,
                (
                    text,
                    occurred_at.isoformat(),
                    str(turn_id),
                    str(session_id),
                    str(generation_id),
                    str(session_id),
                    str(turn_id),
                ),
            )
            if cursor.rowcount == 0:
                return None
            persisted_user = await self._event_store.append_in_transaction(connection, user_event)
        return persisted_user

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
                source_context=None,
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
        turn_id: UUID,
        generation_id: UUID,
        assistant_turn_id: UUID,
        output: str,
        source_context: ConversationSourceContext | None,
        occurred_at: datetime,
        set_session_idle: bool,
        complete_event: GenericCoreEvent,
        pre_events: tuple[AvatarCueEmittedEvent | GenericCoreEvent, ...] = (),
    ) -> tuple[tuple[AvatarCueEmittedEvent | GenericCoreEvent, ...], GenericCoreEvent] | None:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE generations SET state = ?, output_text = ?, completed_at = ?
                WHERE generation_id = ? AND session_id = ? AND turn_id = ? AND state = ?
                """,
                (
                    GenerationState.COMPLETED.value,
                    output,
                    occurred_at.isoformat(),
                    str(generation_id),
                    str(session_id),
                    str(turn_id),
                    GenerationState.RUNNING.value,
                ),
            )
            updated = cursor.rowcount > 0
            await cursor.close()
            if not updated:
                return None
            await connection.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, role, committed_text, committed_at, created_at,
                    source_context_json, generation_id
                ) VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?)
                """,
                (
                    str(assistant_turn_id),
                    str(session_id),
                    output,
                    occurred_at.isoformat(),
                    occurred_at.isoformat(),
                    source_context.to_json() if source_context is not None else None,
                    str(generation_id),
                ),
            )
            await self._set_idle(connection, session_id, occurred_at, enabled=set_session_idle)
            persisted_pre_events: list[AvatarCueEmittedEvent | GenericCoreEvent] = []
            for event in pre_events:
                persisted = await self._event_store.append_in_transaction(connection, event)
                persisted_pre_events.append(persisted)
            persisted_event = await self._event_store.append_in_transaction(
                connection, complete_event
            )
            return tuple(persisted_pre_events), persisted_event

    async def cancel_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        occurred_at: datetime,
        set_session_idle: bool,
        cancel_events: tuple[GenericCoreEvent, ...] = (),
    ) -> tuple[GenericCoreEvent, ...] | None:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE generations SET state = ?, invalidated_at = ?
                WHERE generation_id = ? AND session_id = ? AND turn_id = ? AND state = ?
                """,
                (
                    GenerationState.CANCELLED.value,
                    occurred_at.isoformat(),
                    str(generation_id),
                    str(session_id),
                    str(turn_id),
                    GenerationState.RUNNING.value,
                ),
            )
            updated = cursor.rowcount > 0
            await cursor.close()
            if not updated:
                return None
            await self._set_idle(connection, session_id, occurred_at, enabled=set_session_idle)
            persisted_events: list[GenericCoreEvent] = []
            for event in cancel_events:
                persisted = await self._event_store.append_in_transaction(connection, event)
                persisted_events.append(persisted)
            return tuple(persisted_events)

    async def fail_generation(
        self,
        *,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        error_code: str,
        occurred_at: datetime,
        set_session_idle: bool,
        fail_event: ErrorRaisedEvent,
        recovery_text: str | None = None,
        source_context: ConversationSourceContext | None = None,
    ) -> ErrorRaisedEvent | None:
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE generations SET state = ?, error_code = ?, completed_at = ?
                WHERE generation_id = ? AND session_id = ? AND turn_id = ? AND state = ?
                """,
                (
                    GenerationState.FAILED.value,
                    error_code,
                    occurred_at.isoformat(),
                    str(generation_id),
                    str(session_id),
                    str(turn_id),
                    GenerationState.RUNNING.value,
                ),
            )
            updated = cursor.rowcount > 0
            await cursor.close()
            if not updated:
                return None
            if recovery_text and recovery_text.strip():
                recovery_turn_id = uuid5(generation_id, "provider-failure-recovery")
                await connection.execute(
                    """
                    INSERT INTO turns(
                        turn_id, session_id, role, committed_text, committed_at, created_at,
                        source_context_json, generation_id
                    ) VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?)
                    """,
                    (
                        str(recovery_turn_id),
                        str(session_id),
                        recovery_text,
                        occurred_at.isoformat(),
                        occurred_at.isoformat(),
                        source_context.to_json() if source_context is not None else None,
                        str(generation_id),
                    ),
                )
            await self._set_idle(connection, session_id, occurred_at, enabled=set_session_idle)
            persisted_event = await self._event_store.append_in_transaction(connection, fail_event)
            return persisted_event

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
        source_context: ConversationSourceContext | None,
        occurred_at: datetime,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO turns(
                turn_id, session_id, role, committed_text, committed_at, created_at,
                source_context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(turn_id),
                str(session_id),
                role,
                text,
                occurred_at.isoformat(),
                occurred_at.isoformat(),
                source_context.to_json() if source_context is not None else None,
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
