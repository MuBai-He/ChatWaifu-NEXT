"""Validate client playout receipts and derive the text a user actually heard."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import aiosqlite
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.commands import PlaybackAckCommand
from chatwaifu_protocol.events import (
    AssistantPlaybackPayload,
    AssistantPlaybackProgressEvent,
    AssistantPlaybackStartedEvent,
    AssistantPlaybackStoppedEvent,
    AssistantPlaybackStoppedPayload,
    AssistantSpokenTextCommittedEvent,
    AssistantSpokenTextCommittedPayload,
    EventModel,
)

from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore


@dataclass(frozen=True, slots=True)
class PlaybackAckResult:
    command_id: UUID
    segment_id: UUID
    state: str
    played_pts_ms: int
    completed: bool
    spoken_text: str
    turn_id: UUID | None = None
    committed_event_id: UUID | None = None
    all_segments_completed: bool = False
    duplicate: bool = False


class PlaybackService:
    """Owns segment receipts; providers and frontend never write playback state directly."""

    def __init__(
        self,
        database: Database,
        event_store: EventStore,
        publisher: EventPublisher,
    ) -> None:
        self._database = database
        self._event_store = event_store
        self._publisher = publisher

    async def register_segment(
        self,
        *,
        session_id: UUID,
        generation_id: UUID,
        stream_id: UUID,
        segment_id: UUID,
        segment_index: int,
        text: str,
        duration_ms: int,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO playback_segments(
                    segment_id, stream_id, session_id, generation_id, segment_index,
                    text, duration_ms, state, queued_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                """,
                (
                    str(segment_id),
                    str(stream_id),
                    str(session_id),
                    str(generation_id),
                    segment_index,
                    text,
                    duration_ms,
                    now,
                ),
            )

    async def acknowledge(self, command: PlaybackAckCommand) -> PlaybackAckResult:
        if command.session_id is None or command.generation_id is None:
            raise ValueError("playback acknowledgement requires session_id and generation_id")
        payload = command.payload
        if payload.phase in {"stopped", "queue_cleared"} and payload.reason is None:
            raise ValueError("stopped playback acknowledgement requires a reason")
        if payload.phase == "queue_cleared" and payload.reason != "queue_cleared":
            raise ValueError("queue_cleared acknowledgement requires queue_cleared reason")

        now = datetime.now(UTC)
        persisted: list[EventModel] = []
        async with self._database.transaction() as connection:
            duplicate_cursor = await connection.execute(
                "SELECT 1 FROM playback_ack_commands WHERE command_id = ?",
                (str(command.command_id),),
            )
            duplicate = await duplicate_cursor.fetchone()
            await duplicate_cursor.close()

            row_cursor = await connection.execute(
                """
                SELECT segment.segment_id, segment.stream_id, segment.session_id,
                       segment.generation_id, segment.text, segment.duration_ms,
                       segment.state, segment.played_pts_ms, generation.turn_id
                FROM playback_segments AS segment
                JOIN generations AS generation
                  ON generation.generation_id = segment.generation_id
                WHERE segment.segment_id = ?
                """,
                (str(payload.segment_id),),
            )
            row = await row_cursor.fetchone()
            await row_cursor.close()
            if row is None:
                raise KeyError("playback segment not found")
            if (
                str(row["session_id"]) != str(command.session_id)
                or str(row["generation_id"]) != str(command.generation_id)
                or str(row["stream_id"]) != str(payload.stream_id)
            ):
                raise ValueError("playback acknowledgement identity does not match segment")

            if duplicate is not None:
                spoken_text = await _spoken_text(connection, command.generation_id)
                return PlaybackAckResult(
                    command_id=command.command_id,
                    segment_id=payload.segment_id,
                    state=str(row["state"]),
                    played_pts_ms=int(row["played_pts_ms"]),
                    completed=str(row["state"]) == "completed",
                    spoken_text=spoken_text,
                    duplicate=True,
                )

            previous_state = str(row["state"])
            if previous_state in {"completed", "stopped"}:
                await connection.execute(
                    """
                    INSERT INTO playback_ack_commands(command_id, segment_id, phase, received_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(command.command_id),
                        str(payload.segment_id),
                        payload.phase,
                        now.isoformat(),
                    ),
                )
                spoken_text = await _spoken_text(connection, command.generation_id)
                return PlaybackAckResult(
                    command_id=command.command_id,
                    segment_id=payload.segment_id,
                    state=previous_state,
                    played_pts_ms=int(row["played_pts_ms"]),
                    completed=previous_state == "completed",
                    spoken_text=spoken_text,
                    duplicate=True,
                )

            duration_ms = int(row["duration_ms"])
            if payload.played_pts_ms > duration_ms + 1_000:
                raise ValueError("played_pts_ms exceeds registered segment duration")
            played_pts_ms = max(int(row["played_pts_ms"]), min(payload.played_pts_ms, duration_ms))
            completed = (
                payload.phase == "stopped"
                and payload.reason == "ended"
                and played_pts_ms >= max(0, duration_ms - 100)
            )
            if payload.phase in {"started", "progress"}:
                state = "playing"
            elif completed:
                state = "completed"
            else:
                state = "stopped"

            started_at = (
                now.isoformat() if state == "playing" and previous_state == "queued" else None
            )
            stopped_at = now.isoformat() if state in {"completed", "stopped"} else None
            await connection.execute(
                """
                UPDATE playback_segments
                SET state = ?, played_pts_ms = ?, buffered_ms = ?, client_clock_ms = ?,
                    transport = ?, stop_reason = COALESCE(?, stop_reason),
                    started_at = COALESCE(started_at, ?), stopped_at = COALESCE(stopped_at, ?)
                WHERE segment_id = ?
                """,
                (
                    state,
                    played_pts_ms,
                    payload.buffered_ms,
                    payload.client_clock_ms,
                    payload.transport,
                    payload.reason,
                    started_at,
                    stopped_at,
                    str(payload.segment_id),
                ),
            )

            event = _playback_event(
                command,
                played_pts_ms=played_pts_ms,
                completed=completed,
                occurred_at=now,
            )
            persisted.append(await self._event_store.append_in_transaction(connection, event))

            newly_completed = completed and previous_state != "completed"
            committed_event_id: UUID | None = None
            spoken_text = await _spoken_text(connection, command.generation_id)
            if newly_completed:
                await connection.execute(
                    "UPDATE generations SET spoken_text = ? WHERE generation_id = ?",
                    (spoken_text, str(command.generation_id)),
                )
                committed = AssistantSpokenTextCommittedEvent(
                    event_id=uuid4(),
                    session_id=command.session_id,
                    turn_id=UUID(str(row["turn_id"])),
                    generation_id=command.generation_id,
                    occurred_at=now,
                    source="runtime.playback",
                    causation_id=command.command_id,
                    privacy=PrivacyLevel.LOCAL,
                    payload=AssistantSpokenTextCommittedPayload(
                        stream_id=payload.stream_id,
                        segment_id=payload.segment_id,
                        text=str(row["text"]),
                        spoken_text=spoken_text,
                    ),
                )
                persisted.append(
                    await self._event_store.append_in_transaction(connection, committed)
                )
                committed_event_id = committed.event_id
            incomplete_cursor = await connection.execute(
                """
                SELECT 1 FROM playback_segments
                WHERE generation_id = ? AND state != 'completed' LIMIT 1
                """,
                (str(command.generation_id),),
            )
            all_segments_completed = await incomplete_cursor.fetchone() is None
            await incomplete_cursor.close()
            await connection.execute(
                """
                INSERT INTO playback_ack_commands(command_id, segment_id, phase, received_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(command.command_id),
                    str(payload.segment_id),
                    payload.phase,
                    now.isoformat(),
                ),
            )

        for event in persisted:
            await self._publisher.publish_persisted(event)
        return PlaybackAckResult(
            command_id=command.command_id,
            segment_id=payload.segment_id,
            state=state,
            played_pts_ms=played_pts_ms,
            completed=completed,
            spoken_text=spoken_text,
            turn_id=UUID(str(row["turn_id"])),
            committed_event_id=committed_event_id,
            all_segments_completed=all_segments_completed,
        )

    async def status(self, session_id: UUID, generation_id: UUID) -> dict[str, object]:
        generation = await self._database.fetchone(
            """
            SELECT spoken_text, audio_stream_id FROM generations
            WHERE session_id = ? AND generation_id = ?
            """,
            (str(session_id), str(generation_id)),
        )
        if generation is None:
            raise KeyError("generation not found")
        rows = await self._database.fetchall(
            """
            SELECT segment_id, stream_id, segment_index, text, duration_ms, state,
                   played_pts_ms, buffered_ms, transport, stop_reason
            FROM playback_segments WHERE generation_id = ? ORDER BY segment_index
            """,
            (str(generation_id),),
        )
        return {
            "session_id": str(session_id),
            "generation_id": str(generation_id),
            "stream_id": generation["audio_stream_id"],
            "spoken_text": str(generation["spoken_text"]),
            "segments": [dict(row) for row in rows],
        }


def _playback_event(
    command: PlaybackAckCommand,
    *,
    played_pts_ms: int,
    completed: bool,
    occurred_at: datetime,
) -> EventModel:
    payload = command.payload
    progress = AssistantPlaybackPayload(
        stream_id=payload.stream_id,
        segment_id=payload.segment_id,
        played_pts_ms=played_pts_ms,
        buffered_ms=payload.buffered_ms,
        client_clock_ms=payload.client_clock_ms,
        transport=payload.transport,
    )
    if payload.phase == "started":
        return AssistantPlaybackStartedEvent(
            event_id=uuid4(),
            session_id=command.session_id,
            generation_id=command.generation_id,
            occurred_at=occurred_at,
            source="runtime.playback",
            causation_id=command.command_id,
            privacy=PrivacyLevel.LOCAL,
            payload=progress,
        )
    if payload.phase == "progress":
        return AssistantPlaybackProgressEvent(
            event_id=uuid4(),
            session_id=command.session_id,
            generation_id=command.generation_id,
            occurred_at=occurred_at,
            source="runtime.playback",
            causation_id=command.command_id,
            privacy=PrivacyLevel.LOCAL,
            payload=progress,
        )
    return AssistantPlaybackStoppedEvent(
        event_id=uuid4(),
        session_id=command.session_id,
        generation_id=command.generation_id,
        occurred_at=occurred_at,
        source="runtime.playback",
        causation_id=command.command_id,
        privacy=PrivacyLevel.LOCAL,
        payload=AssistantPlaybackStoppedPayload(
            stream_id=payload.stream_id,
            segment_id=payload.segment_id,
            played_pts_ms=played_pts_ms,
            buffered_ms=payload.buffered_ms,
            client_clock_ms=payload.client_clock_ms,
            transport=payload.transport,
            reason=payload.reason or "interrupted",
            completed=completed,
        ),
    )


async def _spoken_text(connection: aiosqlite.Connection, generation_id: UUID) -> str:
    cursor = await connection.execute(
        """
        SELECT text FROM playback_segments
        WHERE generation_id = ? AND state = 'completed'
        ORDER BY segment_index
        """,
        (str(generation_id),),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return "".join(str(row["text"]) for row in rows)
