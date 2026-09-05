"""Failure-atomic SQLite implementation of the experience reset boundary."""

from datetime import datetime
from uuid import UUID

from chatwaifu_protocol.events import GenericCoreEvent

from chatwaifu_runtime.conversation.reset import (
    ExperienceResetRecord,
    ExperienceResetRepository,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_photo_redaction import redact_scope_photos


class SQLiteExperienceResetRepository(ExperienceResetRepository):
    def __init__(self, database: Database, event_store: EventStore) -> None:
        self._database = database
        self._event_store = event_store

    async def audio_asset_ids(self, session_id: UUID) -> tuple[UUID, ...]:
        rows = await self._database.fetchall(
            "SELECT segment_id FROM playback_segments WHERE session_id = ?",
            (str(session_id),),
        )
        return tuple(UUID(str(row["segment_id"])) for row in rows)

    async def all_audio_asset_ids(self) -> tuple[UUID, ...]:
        rows = await self._database.fetchall("SELECT segment_id FROM playback_segments")
        return tuple(UUID(str(row["segment_id"])) for row in rows)

    async def reset(
        self,
        session_id: UUID,
        *,
        character_id: str,
        user_scope: str,
        memory_namespace: str,
        updated_at: datetime,
        reset_event: GenericCoreEvent,
    ) -> ExperienceResetRecord:
        async with self._database.transaction() as connection:
            photo_generations = await redact_scope_photos(connection, user_scope, character_id)
            audio_cursor = await connection.execute(
                "SELECT segment_id FROM playback_segments WHERE session_id = ?",
                (str(session_id),),
            )
            audio_asset_ids = tuple(
                UUID(str(row["segment_id"])) for row in await audio_cursor.fetchall()
            )
            await audio_cursor.close()

            memory_cursor = await connection.execute(
                "SELECT memory_id FROM memory_records WHERE namespace = ?",
                (memory_namespace,),
            )
            memory_ids = tuple(
                UUID(str(row["memory_id"])) for row in await memory_cursor.fetchall()
            )
            await memory_cursor.close()
            await connection.execute(
                """
                DELETE FROM memory_proposals
                WHERE target_memory_id IN (
                    SELECT memory_id FROM memory_records WHERE namespace = ?
                ) OR json_extract(candidate_json, '$.namespace') = ?
                """,
                (memory_namespace, memory_namespace),
            )
            await connection.execute(
                "DELETE FROM memory_records WHERE namespace = ?",
                (memory_namespace,),
            )
            await connection.execute(
                "DELETE FROM character_states WHERE character_id = ? AND user_scope = ?",
                (character_id, user_scope),
            )
            await connection.execute(
                "DELETE FROM relationship_states WHERE character_id = ? AND user_scope = ?",
                (character_id, user_scope),
            )

            events_cursor = await connection.execute(
                """
                DELETE FROM events
                WHERE session_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM memory_sources AS source
                      WHERE source.source_event_id = events.event_id
                  )
                """,
                (str(session_id),),
            )
            events_deleted = max(events_cursor.rowcount, 0)
            await events_cursor.close()
            turns_cursor = await connection.execute(
                "DELETE FROM turns WHERE session_id = ?", (str(session_id),)
            )
            turns_deleted = max(turns_cursor.rowcount, 0)
            await turns_cursor.close()
            await connection.execute(
                "DELETE FROM ambient_actions WHERE session_id = ?", (str(session_id),)
            )
            await connection.execute(
                """
                UPDATE sessions
                SET conversation_state = 'idle', revision = revision + 1,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (updated_at.isoformat(), str(session_id)),
            )
            persisted_reset_event = await self._event_store.append_in_transaction(
                connection, reset_event
            )
        return ExperienceResetRecord(
            audio_asset_ids=audio_asset_ids,
            memory_ids=memory_ids,
            turns_deleted=turns_deleted,
            events_deleted=events_deleted,
            reset_event=persisted_reset_event,
            photo_generations=photo_generations,
        )
