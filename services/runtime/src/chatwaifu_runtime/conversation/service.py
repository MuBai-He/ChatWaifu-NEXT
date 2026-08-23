"""Generation-scoped streaming conversation pipeline with hard cancellation."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import aiosqlite
from chatwaifu_protocol.avatar import AvatarCue
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import (
    AssistantGenerationStartedEvent,
    AssistantGenerationStartedPayload,
    AvatarCueEmittedEvent,
    ErrorRaisedEvent,
    ErrorRaisedPayload,
    GenericCoreEvent,
    UserTurnCommittedEvent,
    UserTurnCommittedPayload,
)
from chatwaifu_protocol.session import GenerationState, SessionState

from chatwaifu_runtime.audio.store import AudioAssetStore
from chatwaifu_runtime.characters.service import CharacterProfile, CharacterService
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.memory.service import MemoryItem, MemoryService
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.providers.contracts import LlmRequest
from chatwaifu_runtime.providers.factory import ProviderSet
from chatwaifu_runtime.sessions.service import SessionService

_SEGMENT_ENDINGS = frozenset("。\uff01\uff1f!?\uff1b;\n")


@dataclass(frozen=True, slots=True)
class GenerationAccepted:
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    state: GenerationState


@dataclass(frozen=True, slots=True)
class SessionDataReset:
    session_id: UUID
    turns_deleted: int
    events_deleted: int
    memories_deleted: int
    audio_assets_deleted: int


@dataclass(slots=True)
class _ActiveGeneration:
    generation_id: UUID
    task: asyncio.Task[None]


class ConversationService:
    def __init__(
        self,
        database: Database,
        event_store: EventStore,
        publisher: EventPublisher,
        sessions: SessionService,
        providers: ProviderSet,
        audio_assets: AudioAssetStore,
        characters: CharacterService,
        memory: MemoryService,
    ) -> None:
        self._database = database
        self._event_store = event_store
        self._publisher = publisher
        self._sessions = sessions
        self._providers = providers
        self._audio_assets = audio_assets
        self._characters = characters
        self._memory = memory
        self._active: dict[UUID, _ActiveGeneration] = {}
        self._start_lock = asyncio.Lock()

    async def submit_text(self, session_id: UUID, text: str) -> GenerationAccepted:
        normalized = text.strip()
        if not normalized:
            raise ValueError("message text must not be blank")
        async with self._start_lock:
            await self.cancel(session_id, "superseded_by_new_turn")
            session = await self._sessions.get_session(session_id)
            if session is None:
                raise KeyError(f"unknown session {session_id}")
            if session.state is not SessionState.READY:
                raise RuntimeError(f"session is not ready: {session.state}")
            accepted, events = await self._commit_user_turn(session_id, normalized)
            for event in events:
                await self._publisher.publish_persisted(event)
            await self._memory.apply_explicit_command(session_id, accepted.turn_id, normalized)
            memories = await self._memory.recall(session_id, accepted.turn_id)
            character = self._characters.get(session.character_id)
            if character is None:
                raise RuntimeError(f"character is not installed: {session.character_id}")
            task = asyncio.create_task(
                self._run_generation(accepted, normalized, character, memories),
                name=f"generation-{accepted.generation_id}",
            )
            self._active[session_id] = _ActiveGeneration(accepted.generation_id, task)
            return accepted

    async def cancel(self, session_id: UUID, reason: str = "user_interruption") -> bool:
        active = self._active.get(session_id)
        if active is None or active.task.done():
            return False
        active.task.cancel(reason)
        try:
            await active.task
        except asyncio.CancelledError:
            pass
        return True

    async def reset(self, session_id: UUID) -> SessionDataReset:
        """Return a ready session to a clean local-demo state."""

        async with self._start_lock:
            session = await self._sessions.get_session(session_id)
            if session is None:
                raise KeyError(f"unknown session {session_id}")
            if session.state is not SessionState.READY:
                raise RuntimeError(f"session is not ready: {session.state}")
            await self.cancel(session_id, "session_data_reset")
            self._active.pop(session_id, None)
            now = datetime.now(UTC)
            async with self._database.transaction() as connection:
                memories_deleted = await self._memory.clear_all_in_transaction(connection)
                events_cursor = await connection.execute(
                    "DELETE FROM events WHERE session_id = ?", (str(session_id),)
                )
                events_deleted = max(events_cursor.rowcount, 0)
                await events_cursor.close()
                turns_cursor = await connection.execute(
                    "DELETE FROM turns WHERE session_id = ?", (str(session_id),)
                )
                turns_deleted = max(turns_cursor.rowcount, 0)
                await turns_cursor.close()
                await connection.execute(
                    """
                    UPDATE sessions
                    SET conversation_state = 'idle', revision = revision + 1,
                        next_sequence = 1, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (now.isoformat(), str(session_id)),
                )
            audio_assets_deleted = self._audio_assets.clear()
            return SessionDataReset(
                session_id=session_id,
                turns_deleted=turns_deleted,
                events_deleted=events_deleted,
                memories_deleted=memories_deleted,
                audio_assets_deleted=audio_assets_deleted,
            )

    async def stop(self) -> None:
        active = tuple(self._active.values())
        for generation in active:
            generation.task.cancel("runtime_stopping")
        if active:
            await asyncio.gather(*(item.task for item in active), return_exceptions=True)
        self._active.clear()

    async def list_messages(self, session_id: UUID, limit: int = 100) -> list[dict[str, object]]:
        rows = await self._database.fetchall(
            """
            SELECT turn_id, role, committed_text, committed_at, created_at
            FROM turns WHERE session_id = ? AND committed_text IS NOT NULL
            ORDER BY created_at ASC LIMIT ?
            """,
            (str(session_id), min(max(limit, 1), 500)),
        )
        return [dict(row) for row in rows]

    async def _commit_user_turn(
        self, session_id: UUID, text: str
    ) -> tuple[GenerationAccepted, tuple[UserTurnCommittedEvent, AssistantGenerationStartedEvent]]:
        now = datetime.now(UTC)
        turn_id = uuid4()
        generation_id = uuid4()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, role, committed_text, committed_at, created_at
                )
                VALUES (?, ?, 'user', ?, ?, ?)
                """,
                (str(turn_id), str(session_id), text, now.isoformat(), now.isoformat()),
            )
            await connection.execute(
                """
                INSERT INTO generations(
                    generation_id, session_id, turn_id, state, backend_kind, started_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(generation_id),
                    str(session_id),
                    str(turn_id),
                    GenerationState.RUNNING.value,
                    self._providers.llm.kind,
                    now.isoformat(),
                ),
            )
            await connection.execute(
                """
                UPDATE sessions SET conversation_state = 'generating', updated_at = ?
                WHERE session_id = ?
                """,
                (now.isoformat(), str(session_id)),
            )
            user_event = await self._event_store.append_in_transaction(
                connection,
                UserTurnCommittedEvent(
                    event_id=uuid4(),
                    session_id=session_id,
                    turn_id=turn_id,
                    generation_id=generation_id,
                    occurred_at=now,
                    source="runtime.conversation",
                    privacy=PrivacyLevel.LOCAL,
                    payload=UserTurnCommittedPayload(text=text),
                ),
            )
            generation_event = await self._event_store.append_in_transaction(
                connection,
                AssistantGenerationStartedEvent(
                    event_id=uuid4(),
                    session_id=session_id,
                    turn_id=turn_id,
                    generation_id=generation_id,
                    occurred_at=now,
                    source="runtime.conversation",
                    privacy=PrivacyLevel.LOCAL,
                    payload=AssistantGenerationStartedPayload(
                        backend_kind=self._providers.llm.kind
                    ),
                ),
            )
        return (
            GenerationAccepted(session_id, turn_id, generation_id, GenerationState.RUNNING),
            (user_event, generation_event),
        )

    async def _run_generation(
        self,
        accepted: GenerationAccepted,
        user_text: str,
        character: CharacterProfile,
        memories: list[MemoryItem],
    ) -> None:
        output = ""
        segment = ""
        try:
            await self._emit_avatar(accepted, "state", "thinking", priority=60)
            request = LlmRequest(
                generation_id=accepted.generation_id,
                user_text=user_text,
                system_prompt=character.system_prompt,
                character_name=character.display_name,
                context=_memory_context(memories),
            )
            async for delta in self._providers.llm.stream(request):
                self._ensure_current(accepted)
                output += delta
                segment += delta
                await self._emit_generic(accepted, "assistant.text_delta", {"text": delta})
                if _segment_ready(segment):
                    await self._synthesize_segment(accepted, segment)
                    segment = ""
            if segment.strip():
                await self._synthesize_segment(accepted, segment)
            self._ensure_current(accepted)
            await self._complete(accepted, output)
        except asyncio.CancelledError as error:
            reason = str(error.args[0]) if error.args else "interrupted"
            await self._cancelled(accepted, reason)
            raise
        except Exception as error:
            await self._failed(accepted, error)
        finally:
            current = self._active.get(accepted.session_id)
            if current and current.generation_id == accepted.generation_id:
                self._active.pop(accepted.session_id, None)

    async def _synthesize_segment(self, accepted: GenerationAccepted, text: str) -> None:
        self._ensure_current(accepted)
        await self._emit_generic(
            accepted, "assistant.text_segment_committed", {"text": text.strip()}
        )
        asset = self._audio_assets.allocate()
        try:
            result = await self._providers.tts.synthesize(text.strip(), asset.path)
        except BaseException:
            asset.path.unlink(missing_ok=True)
            raise
        self._ensure_current(accepted)
        await self._emit_avatar(accepted, "speech", "speaking", priority=70)
        await self._emit_generic(
            accepted,
            "assistant.audio_chunk_queued",
            {
                "asset_id": str(asset.asset_id),
                "url": asset.url,
                "text": text.strip(),
                "media_type": result.media_type,
                "sample_rate": result.sample_rate,
                "duration_ms": result.duration_ms,
                "tts_provider": self._providers.tts.kind,
            },
        )

    async def _complete(self, accepted: GenerationAccepted, output: str) -> None:
        now = datetime.now(UTC)
        assistant_turn_id = uuid4()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, role, committed_text, committed_at, created_at
                )
                VALUES (?, ?, 'assistant', ?, ?, ?)
                """,
                (
                    str(assistant_turn_id),
                    str(accepted.session_id),
                    output,
                    now.isoformat(),
                    now.isoformat(),
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
                    now.isoformat(),
                    str(accepted.generation_id),
                ),
            )
            await self._set_idle_if_current(connection, accepted, now)
        await self._emit_generic(
            accepted,
            "assistant.generation_completed",
            {"text": output, "assistant_turn_id": str(assistant_turn_id)},
        )
        await self._emit_avatar(accepted, "state", "idle", priority=90)

    async def _cancelled(self, accepted: GenerationAccepted, reason: str) -> None:
        now = datetime.now(UTC)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE generations SET state = ?, invalidated_at = ?
                WHERE generation_id = ? AND state = ?
                """,
                (
                    GenerationState.CANCELLED.value,
                    now.isoformat(),
                    str(accepted.generation_id),
                    GenerationState.RUNNING.value,
                ),
            )
            await self._set_idle_if_current(connection, accepted, now)
        await self._emit_generic(accepted, "assistant.generation_cancelled", {"reason": reason})
        await self._emit_generic(accepted, "conversation.interrupted", {"reason": reason})

    async def _failed(self, accepted: GenerationAccepted, error: Exception) -> None:
        now = datetime.now(UTC)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE generations SET state = ?, error_code = ?, completed_at = ?
                WHERE generation_id = ?
                """,
                (
                    GenerationState.FAILED.value,
                    "provider_error",
                    now.isoformat(),
                    str(accepted.generation_id),
                ),
            )
            await self._set_idle_if_current(connection, accepted, now)
        await self._publisher.emit(
            ErrorRaisedEvent(
                event_id=uuid4(),
                session_id=accepted.session_id,
                turn_id=accepted.turn_id,
                generation_id=accepted.generation_id,
                occurred_at=now,
                source="runtime.conversation",
                privacy=PrivacyLevel.LOCAL,
                payload=ErrorRaisedPayload(
                    error=StructuredError(
                        code="provider_error",
                        message=str(error),
                        retryable=True,
                        component="conversation",
                    )
                ),
            )
        )

    async def _set_idle_if_current(
        self,
        connection: aiosqlite.Connection,
        accepted: GenerationAccepted,
        now: datetime,
    ) -> None:
        if self._is_current(accepted):
            await connection.execute(
                """
                UPDATE sessions SET conversation_state = 'idle', updated_at = ?
                WHERE session_id = ?
                """,
                (now.isoformat(), str(accepted.session_id)),
            )

    async def _emit_generic(
        self, accepted: GenerationAccepted, event_type: str, payload: dict[str, object]
    ) -> None:
        event = GenericCoreEvent.model_validate(
            {
                "event_id": uuid4(),
                "event_type": event_type,
                "session_id": accepted.session_id,
                "turn_id": accepted.turn_id,
                "generation_id": accepted.generation_id,
                "occurred_at": datetime.now(UTC),
                "source": "runtime.conversation",
                "privacy": PrivacyLevel.LOCAL,
                "payload": payload,
            }
        )
        await self._publisher.emit(event)

    async def _emit_avatar(
        self,
        accepted: GenerationAccepted,
        kind: Literal["state", "expression", "motion", "gaze", "speech", "override"],
        name: str,
        *,
        priority: int,
    ) -> None:
        event = AvatarCueEmittedEvent.model_validate(
            {
                "event_id": uuid4(),
                "session_id": accepted.session_id,
                "turn_id": accepted.turn_id,
                "generation_id": accepted.generation_id,
                "occurred_at": datetime.now(UTC),
                "source": "runtime.conversation",
                "privacy": PrivacyLevel.LOCAL,
                "payload": {
                    "cue": AvatarCue(
                        cue_id=uuid4(),
                        generation_id=accepted.generation_id,
                        kind=kind,
                        name=name,
                        priority=priority,
                    )
                },
            }
        )
        await self._publisher.emit(event)

    def _ensure_current(self, accepted: GenerationAccepted) -> None:
        if not self._is_current(accepted):
            raise asyncio.CancelledError("generation is no longer active")

    def _is_current(self, accepted: GenerationAccepted) -> bool:
        active = self._active.get(accepted.session_id)
        return active is not None and active.generation_id == accepted.generation_id


def _segment_ready(text: str) -> bool:
    stripped = text.rstrip()
    return len(text) >= 90 or bool(stripped and stripped[-1] in _SEGMENT_ENDINGS)


def _memory_context(memories: list[MemoryItem]) -> tuple[tuple[str, str], ...]:
    if not memories:
        return ()
    content = "; ".join(item.content for item in memories)
    return (("system", f"记忆: 用户明确要求记住以下内容: {content}"),)
