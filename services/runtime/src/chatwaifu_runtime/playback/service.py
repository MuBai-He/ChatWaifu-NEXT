"""Validate client playout receipts and derive the text a user actually heard."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
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


@dataclass(frozen=True, slots=True)
class PlaybackCommitResult:
    segment_id: UUID
    generation_id: UUID
    session_id: UUID
    turn_id: UUID | None
    state: str
    played_pts_ms: int
    completed: bool
    spoken_text: str
    committed_event_id: UUID | None = None
    all_segments_completed: bool = False
    duplicate: bool = False


_LOGGER = logging.getLogger(__name__)


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
        self._completion_listeners: dict[
            UUID, tuple[UUID, Callable[[UUID, UUID | None, str], Awaitable[None]]]
        ] = {}

    def register_completion_listener(
        self,
        session_id: UUID,
        listener: Callable[[UUID, UUID | None, str], Awaitable[None]],
        token: UUID | None = None,
    ) -> UUID:
        """Register a session-scoped listener notified when all segments finish playing."""
        reg_token = token or uuid4()
        self._completion_listeners[session_id] = (reg_token, listener)
        return reg_token

    def unregister_completion_listener(self, session_id: UUID, token: UUID | None = None) -> bool:
        """Unregister a session-scoped completion listener if token matches."""
        entry = self._completion_listeners.get(session_id)
        if entry is not None:
            existing_token, _ = entry
            if token is None or token == existing_token:
                self._completion_listeners.pop(session_id, None)
                return True
        return False

    def register_spoken_listener(
        self, listener: Callable[[UUID, UUID | None, UUID, str], Awaitable[None]]
    ) -> None:
        """Deprecated: Spoken memory observer is decoupled via EventHub and durable queue."""
        pass

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
        duration_finalized: bool = True,
        transcript_finalized: bool = True,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO playback_segments(
                    segment_id, stream_id, session_id, generation_id, segment_index,
                    text, duration_ms, duration_finalized, transcript_finalized,
                    spoken_committed, state, queued_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'queued', ?)
                """,
                (
                    str(segment_id),
                    str(stream_id),
                    str(session_id),
                    str(generation_id),
                    segment_index,
                    text,
                    duration_ms,
                    int(duration_finalized),
                    int(transcript_finalized),
                    now,
                ),
            )

    async def has_active_segments(self, generation_id: UUID) -> bool:
        row = await self._database.fetchone(
            "SELECT 1 FROM playback_segments WHERE generation_id = ? LIMIT 1",
            (str(generation_id),),
        )
        return row is not None

    async def get_generation_segments(self, generation_id: UUID) -> list[dict[str, object]]:
        rows = await self._database.fetchall(
            """
            SELECT segment_id, stream_id, segment_index, text, duration_ms,
                   duration_finalized, transcript_finalized, spoken_committed,
                   state, played_pts_ms, buffered_ms, transport, stop_reason
            FROM playback_segments WHERE generation_id = ? ORDER BY segment_index
            """,
            (str(generation_id),),
        )
        return [dict(r) for r in rows]

    async def _try_commit_segment(
        self,
        connection: aiosqlite.Connection,
        segment_id: UUID,
        now: datetime,
        causation_id: UUID | None = None,
    ) -> tuple[bool, EventModel | None, UUID | None, str, bool]:
        """Join duration, transcript, and ended playout ACK into an at-most-once spoken commit."""
        row_cursor = await connection.execute(
            """
            SELECT segment.segment_id, segment.stream_id, segment.session_id,
                   segment.generation_id, segment.text, segment.duration_ms,
                   segment.duration_finalized, segment.transcript_finalized,
                   segment.spoken_committed, segment.state,
                   segment.played_pts_ms, segment.stop_reason,
                   generation.turn_id, generation.state AS generation_state
            FROM playback_segments AS segment
            JOIN generations AS generation
              ON generation.generation_id = segment.generation_id
            WHERE segment.segment_id = ?
            """,
            (str(segment_id),),
        )
        row = await row_cursor.fetchone()
        await row_cursor.close()
        if row is None:
            return False, None, None, "", False

        generation_id = UUID(str(row["generation_id"]))
        session_id = UUID(str(row["session_id"]))
        raw_turn = row["turn_id"]
        turn_id = (
            UUID(str(raw_turn))
            if raw_turn is not None and str(raw_turn).strip() and str(raw_turn) != "None"
            else None
        )
        stream_id = UUID(str(row["stream_id"]))
        duration_ms = int(row["duration_ms"])
        duration_finalized = bool(row["duration_finalized"])
        transcript_finalized = bool(row["transcript_finalized"])
        spoken_committed = bool(row["spoken_committed"])
        played_pts_ms = int(row["played_pts_ms"])
        stop_reason = str(row["stop_reason"] or "")
        generation_state = str(row["generation_state"])
        segment_state = str(row["state"])

        # Cancelled or failed generations, or discarded segments, can never commit.
        if generation_state in {"cancelled", "failed"} or segment_state == "discarded":
            spoken_text = await _spoken_text(connection, generation_id)
            return False, None, turn_id, spoken_text, False

        can_complete = (
            generation_state not in {"cancelled", "failed"}
            and duration_finalized
            and transcript_finalized
            and stop_reason == "ended"
            and played_pts_ms >= max(0, duration_ms - 100)
        )

        if not can_complete or spoken_committed:
            spoken_text = await _spoken_text(connection, generation_id)
            incomplete_cursor = await connection.execute(
                """
                SELECT 1 FROM playback_segments
                WHERE generation_id = ? AND state != 'completed' LIMIT 1
                """,
                (str(generation_id),),
            )
            all_completed = await incomplete_cursor.fetchone() is None
            await incomplete_cursor.close()
            return False, None, turn_id, spoken_text, all_completed

        update_cursor = await connection.execute(
            """
            UPDATE playback_segments
            SET state = 'completed', spoken_committed = 1, stopped_at = COALESCE(stopped_at, ?)
            WHERE segment_id = ? AND spoken_committed = 0
            """,
            (now.isoformat(), str(segment_id)),
        )
        updated = update_cursor.rowcount > 0
        await update_cursor.close()

        if not updated:
            spoken_text = await _spoken_text(connection, generation_id)
            return False, None, turn_id, spoken_text, False

        spoken_text = await _spoken_text(connection, generation_id)
        await connection.execute(
            "UPDATE generations SET spoken_text = ? WHERE generation_id = ?",
            (spoken_text, str(generation_id)),
        )
        persisted = None
        if str(row["text"]).strip() and spoken_text.strip():
            committed_event = AssistantSpokenTextCommittedEvent(
                event_id=uuid4(),
                session_id=session_id,
                turn_id=turn_id,
                generation_id=generation_id,
                occurred_at=now,
                source="runtime.playback",
                causation_id=causation_id,
                privacy=PrivacyLevel.LOCAL,
                payload=AssistantSpokenTextCommittedPayload(
                    stream_id=stream_id,
                    segment_id=segment_id,
                    text=str(row["text"]),
                    spoken_text=spoken_text,
                ),
            )
            persisted = await self._event_store.append_in_transaction(connection, committed_event)
            await connection.execute(
                """
                INSERT OR IGNORE INTO spoken_memory_facts(
                    source_event_id, session_id, turn_id, spoken_text, state, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (
                    str(persisted.event_id),
                    str(session_id),
                    str(turn_id),
                    spoken_text,
                    now.isoformat(),
                ),
            )

        incomplete_cursor = await connection.execute(
            """
            SELECT 1 FROM playback_segments
            WHERE generation_id = ? AND state != 'completed' LIMIT 1
            """,
            (str(generation_id),),
        )
        all_completed = await incomplete_cursor.fetchone() is None
        await incomplete_cursor.close()

        return True, persisted, turn_id, spoken_text, all_completed

    async def finalize_segment(
        self, segment_id: UUID, duration_ms: int
    ) -> PlaybackCommitResult | None:
        if duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        now = datetime.now(UTC)
        persisted: list[EventModel] = []
        commit_result: PlaybackCommitResult | None = None
        async with self._database.transaction() as connection:
            row_cursor = await connection.execute(
                """
                SELECT duration_ms, duration_finalized, spoken_committed
                FROM playback_segments WHERE segment_id = ?
                """,
                (str(segment_id),),
            )
            row = await row_cursor.fetchone()
            await row_cursor.close()
            if row is None:
                return None

            is_finalized = bool(row["duration_finalized"])
            is_spoken_committed = bool(row["spoken_committed"])
            existing_duration = int(row["duration_ms"])

            if is_finalized or is_spoken_committed:
                # First finalization immutable, identical replay idempotent;
                # reject/ignore conflict consistently
                if duration_ms != existing_duration:
                    _LOGGER.warning(
                        "Ignoring conflicting duration finalization for segment %s: "
                        "existing %d != new %d",
                        segment_id,
                        existing_duration,
                        duration_ms,
                    )
                return None

            await connection.execute(
                """
                UPDATE playback_segments
                SET duration_ms = ?, duration_finalized = 1
                WHERE segment_id = ? AND duration_finalized = 0
                """,
                (duration_ms, str(segment_id)),
            )
            committed, event, turn_id, spoken_text, all_completed = await self._try_commit_segment(
                connection, segment_id, now
            )
            if event is not None:
                persisted.append(event)
            if committed:
                row_cursor = await connection.execute(
                    """
                    SELECT session_id, generation_id, played_pts_ms
                    FROM playback_segments WHERE segment_id = ?
                    """,
                    (str(segment_id),),
                )
                seg_row = await row_cursor.fetchone()
                await row_cursor.close()
                if seg_row is not None:
                    commit_result = PlaybackCommitResult(
                        segment_id=segment_id,
                        generation_id=UUID(str(seg_row["generation_id"])),
                        session_id=UUID(str(seg_row["session_id"])),
                        turn_id=turn_id,
                        state="completed",
                        played_pts_ms=int(seg_row["played_pts_ms"]),
                        completed=True,
                        spoken_text=spoken_text,
                        committed_event_id=event.event_id if event else None,
                        all_segments_completed=all_completed,
                    )
        for ev in persisted:
            await self._publisher.publish_persisted(ev)
        if commit_result is not None:
            if commit_result.all_segments_completed:
                entry = self._completion_listeners.get(commit_result.session_id)
                if entry is not None:
                    _, listener = entry
                    try:
                        await listener(
                            commit_result.generation_id,
                            commit_result.turn_id,
                            commit_result.spoken_text,
                        )
                    except Exception:
                        _LOGGER.exception("Error in playback completion listener")
        return commit_result

    async def attach_transcript(self, segment_id: UUID, text: str) -> PlaybackCommitResult | None:
        now = datetime.now(UTC)
        persisted: list[EventModel] = []
        commit_result: PlaybackCommitResult | None = None
        async with self._database.transaction() as connection:
            row_cursor = await connection.execute(
                """
                SELECT text, transcript_finalized, spoken_committed
                FROM playback_segments WHERE segment_id = ?
                """,
                (str(segment_id),),
            )
            row = await row_cursor.fetchone()
            await row_cursor.close()
            if row is None:
                return None

            is_finalized = bool(row["transcript_finalized"])
            is_spoken_committed = bool(row["spoken_committed"])
            existing_text = str(row["text"])

            if is_finalized or is_spoken_committed:
                # First finalization immutable, identical replay idempotent;
                # reject/ignore conflict consistently
                if text != existing_text:
                    _LOGGER.warning(
                        "Ignoring conflicting transcript attachment for segment %s: "
                        "existing %s != new %s",
                        segment_id,
                        existing_text,
                        text,
                    )
                return None

            await connection.execute(
                """
                UPDATE playback_segments
                SET text = ?, transcript_finalized = 1
                WHERE segment_id = ? AND transcript_finalized = 0
                """,
                (text, str(segment_id)),
            )
            committed, event, turn_id, spoken_text, all_completed = await self._try_commit_segment(
                connection, segment_id, now
            )
            if event is not None:
                persisted.append(event)
            if committed:
                row_cursor = await connection.execute(
                    """
                    SELECT session_id, generation_id, played_pts_ms
                    FROM playback_segments WHERE segment_id = ?
                    """,
                    (str(segment_id),),
                )
                seg_row = await row_cursor.fetchone()
                await row_cursor.close()
                if seg_row is not None:
                    commit_result = PlaybackCommitResult(
                        segment_id=segment_id,
                        generation_id=UUID(str(seg_row["generation_id"])),
                        session_id=UUID(str(seg_row["session_id"])),
                        turn_id=turn_id,
                        state="completed",
                        played_pts_ms=int(seg_row["played_pts_ms"]),
                        completed=True,
                        spoken_text=spoken_text,
                        committed_event_id=event.event_id if event else None,
                        all_segments_completed=all_completed,
                    )
        for ev in persisted:
            await self._publisher.publish_persisted(ev)
        if commit_result is not None:
            if commit_result.all_segments_completed:
                entry = self._completion_listeners.get(commit_result.session_id)
                if entry is not None:
                    _, listener = entry
                    try:
                        await listener(
                            commit_result.generation_id,
                            commit_result.turn_id,
                            commit_result.spoken_text,
                        )
                    except Exception:
                        _LOGGER.exception("Error in playback completion listener")
        return commit_result

    async def attach_generation_transcript(
        self, generation_id: UUID, text: str
    ) -> PlaybackCommitResult | None:
        """Attach final transcript to all segments of a generation and join."""
        rows = await self._database.fetchall(
            """
            SELECT segment_id FROM playback_segments
            WHERE generation_id = ? ORDER BY segment_index
            """,
            (str(generation_id),),
        )
        if not rows:
            return None
        last_result: PlaybackCommitResult | None = None
        for i, row in enumerate(rows):
            # Whole-response segment is index 0. If multiple segments exist,
            # attach full text only to segment 0 to prevent duplicate spoken/memory commits.
            seg_text = text if i == 0 else ""
            res = await self.attach_transcript(UUID(str(row["segment_id"])), seg_text)
            if res is not None:
                last_result = res
        return last_result

    async def discard_segment(self, segment_id: UUID) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                "DELETE FROM playback_segments WHERE segment_id = ?",
                (str(segment_id),),
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
                       segment.duration_finalized, segment.transcript_finalized,
                       segment.spoken_committed, segment.state,
                       segment.played_pts_ms, generation.turn_id
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
            duration_finalized = bool(row["duration_finalized"])
            if duration_finalized and payload.played_pts_ms > duration_ms + 1_000:
                raise ValueError("played_pts_ms exceeds registered segment duration")
            played_pts_ms = max(
                int(row["played_pts_ms"]),
                min(payload.played_pts_ms, duration_ms)
                if duration_finalized
                else payload.played_pts_ms,
            )
            if payload.phase in {"started", "progress"}:
                state = "playing"
            else:
                state = "stopped"

            started_at = (
                now.isoformat() if state == "playing" and previous_state == "queued" else None
            )
            stopped_at = now.isoformat() if state == "stopped" else None
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

            (
                committed,
                commit_event,
                turn_id,
                spoken_text,
                all_segments_completed,
            ) = await self._try_commit_segment(
                connection, payload.segment_id, now, causation_id=command.command_id
            )
            if commit_event is not None:
                persisted.append(commit_event)

            if committed:
                completed = True
                state = "completed"
            elif bool(row["spoken_committed"]):
                completed = True
                state = "completed"
            else:
                completed = False

            event = _playback_event(
                command,
                played_pts_ms=played_pts_ms,
                completed=completed,
                occurred_at=now,
            )
            persisted.append(await self._event_store.append_in_transaction(connection, event))

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

        fallback_turn = UUID(str(row["turn_id"])) if row["turn_id"] is not None else None
        resolved_turn_id = turn_id or fallback_turn

        if all_segments_completed:
            entry = self._completion_listeners.get(command.session_id)
            if entry is not None:
                _, listener = entry
                try:
                    await listener(
                        command.generation_id,
                        resolved_turn_id,
                        spoken_text,
                    )
                except Exception:
                    _LOGGER.exception("Error in playback completion listener")

        return PlaybackAckResult(
            command_id=command.command_id,
            segment_id=payload.segment_id,
            state=state,
            played_pts_ms=played_pts_ms,
            completed=completed,
            spoken_text=spoken_text,
            turn_id=resolved_turn_id,
            committed_event_id=commit_event.event_id if commit_event is not None else None,
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
