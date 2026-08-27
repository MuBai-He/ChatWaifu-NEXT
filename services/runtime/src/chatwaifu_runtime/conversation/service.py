"""Generation-scoped streaming conversation pipeline with hard cancellation."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import aiosqlite
from chatwaifu_protocol.avatar import AvatarCue
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.character import ResponsePlan
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
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_protocol.session import GenerationState, SessionState

from chatwaifu_runtime.audio.store import AudioAssetStore
from chatwaifu_runtime.audio.streaming import AudioStreamHub, AudioStreamPacket
from chatwaifu_runtime.avatar.planner import SemanticAvatarCuePlanner
from chatwaifu_runtime.character_kernel.prompt import PromptCompiler
from chatwaifu_runtime.character_kernel.service import (
    CharacterKernelService,
    TurnCharacterContext,
)
from chatwaifu_runtime.characters.service import (
    CharacterProfile,
    CharacterService,
    CharacterVoiceProfile,
)
from chatwaifu_runtime.conversation.text_segmenter import StreamingTextSegmenter
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.memory.service import MemoryService, UserTurnMemoryObservation
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.playback.service import PlaybackService
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    SynthesisRequest,
    SynthesisResult,
    TtsPcmChunk,
)
from chatwaifu_runtime.providers.factory import ProviderSet
from chatwaifu_runtime.sessions.service import SessionService

_PROACTIVE_PROMPT = (
    "[Runtime ambient event] The user has been quietly inactive for a while. "
    "Offer one brief, natural, low-pressure check-in as the character. "
    "Do not claim the user just spoke, do not mention timers or system policy, "
    "and do not demand a reply."
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GenerationAccepted:
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    audio_stream_id: UUID
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
        audio_streams: AudioStreamHub,
        characters: CharacterService,
        memory: MemoryService,
        playback: PlaybackService,
        character_kernel: CharacterKernelService,
        prompt_compiler: PromptCompiler,
    ) -> None:
        self._database = database
        self._event_store = event_store
        self._publisher = publisher
        self._sessions = sessions
        self._providers = providers
        self._audio_assets = audio_assets
        self._audio_streams = audio_streams
        self._characters = characters
        self._memory = memory
        self._playback = playback
        self._character_kernel = character_kernel
        self._prompt_compiler = prompt_compiler
        self._avatar_planner = SemanticAvatarCuePlanner()
        self._active: dict[UUID, _ActiveGeneration] = {}
        self._start_lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return sum(not item.task.done() for item in self._active.values())

    async def submit_text(self, session_id: UUID, text: str) -> GenerationAccepted:
        return await self._submit(session_id, text, turn_id=uuid4(), generation_id=uuid4())

    async def submit_voice_transcript(
        self,
        session_id: UUID,
        text: str,
        *,
        turn_id: UUID,
        generation_id: UUID,
    ) -> GenerationAccepted:
        """Commit a VAD/STT turn using the identity allocated at speech start."""

        return await self._submit(
            session_id,
            text,
            turn_id=turn_id,
            generation_id=generation_id,
        )

    async def submit_proactive(
        self, session_id: UUID, *, reason: str = "idle_check_in"
    ) -> GenerationAccepted:
        """Start a policy-approved character turn without fabricating a user message."""

        async with self._start_lock:
            if self.active_generation_id(session_id) is not None:
                raise RuntimeError("session already has an active generation")
            session = await self._sessions.get_session(session_id)
            if session is None:
                raise KeyError(f"unknown session {session_id}")
            if session.state is not SessionState.READY:
                raise RuntimeError(f"session is not ready: {session.state}")
            accepted, events = await self._commit_proactive_turn(session_id, reason)
            for event in events:
                await self._publisher.publish_persisted(event)
            character = self._characters.get(session.character_id)
            if character is None:
                raise RuntimeError(f"character is not installed: {session.character_id}")
            memory_context = await self._memory.retrieve_context(
                session_id,
                accepted.turn_id,
                character.character_id,
                "轻声主动关心用户",
            )
            history = await self._recent_history(session_id, accepted.turn_id)
            character_context = await self._character_kernel.plan_proactive_turn(
                session_id=session_id,
                turn_id=accepted.turn_id,
                generation_id=accepted.generation_id,
                character_id=character.character_id,
            )
            task = asyncio.create_task(
                self._run_generation(
                    accepted,
                    _PROACTIVE_PROMPT,
                    character,
                    character_context,
                    memory_context,
                    history,
                    trigger="proactive",
                ),
                name=f"proactive-generation-{accepted.generation_id}",
            )
            self._active[session_id] = _ActiveGeneration(accepted.generation_id, task)
            return accepted

    async def _submit(
        self,
        session_id: UUID,
        text: str,
        *,
        turn_id: UUID,
        generation_id: UUID,
    ) -> GenerationAccepted:
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
            accepted, events = await self._commit_user_turn(
                session_id,
                normalized,
                turn_id=turn_id,
                generation_id=generation_id,
            )
            for event in events:
                await self._publisher.publish_persisted(event)
            character = self._characters.get(session.character_id)
            if character is None:
                raise RuntimeError(f"character is not installed: {session.character_id}")
            memory_observation: UserTurnMemoryObservation | None = None
            if self._memory.parse_explicit_command(normalized) is not None:
                await self._memory.observe_user_turn(
                    session_id,
                    accepted.turn_id,
                    events[0].event_id,
                    character.character_id,
                    normalized,
                )
            else:
                memory_observation = UserTurnMemoryObservation(
                    session_id=session_id,
                    turn_id=accepted.turn_id,
                    source_event_id=events[0].event_id,
                    character_id=character.character_id,
                    text=normalized,
                )
            memory_context = await self._memory.retrieve_context(
                session_id,
                accepted.turn_id,
                character.character_id,
                normalized,
            )
            history = await self._recent_history(session_id, accepted.turn_id)
            character_context = await self._character_kernel.observe_user_turn(
                session_id=session_id,
                turn_id=accepted.turn_id,
                generation_id=accepted.generation_id,
                character_id=character.character_id,
                text=normalized,
            )
            task = asyncio.create_task(
                self._run_generation(
                    accepted,
                    normalized,
                    character,
                    character_context,
                    memory_context,
                    history,
                    memory_observation=memory_observation,
                ),
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

    def active_generation_id(self, session_id: UUID) -> UUID | None:
        active = self._active.get(session_id)
        if active is None or active.task.done():
            return None
        return active.generation_id

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
            memories_deleted = await self._memory.clear_all()
            await self._character_kernel.clear_all()
            async with self._database.transaction() as connection:
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
                    "DELETE FROM ambient_actions WHERE session_id = ?", (str(session_id),)
                )
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
            FROM turns
            WHERE session_id = ? AND committed_text IS NOT NULL
                AND role IN ('user', 'assistant')
            ORDER BY created_at ASC LIMIT ?
            """,
            (str(session_id), min(max(limit, 1), 500)),
        )
        return [dict(row) for row in rows]

    async def _commit_user_turn(
        self,
        session_id: UUID,
        text: str,
        *,
        turn_id: UUID,
        generation_id: UUID,
    ) -> tuple[GenerationAccepted, tuple[UserTurnCommittedEvent, AssistantGenerationStartedEvent]]:
        now = datetime.now(UTC)
        audio_stream_id = uuid4()
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
                    generation_id, session_id, turn_id, state, backend_kind,
                    audio_stream_id, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(generation_id),
                    str(session_id),
                    str(turn_id),
                    GenerationState.RUNNING.value,
                    self._providers.llm.kind,
                    str(audio_stream_id),
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
            GenerationAccepted(
                session_id,
                turn_id,
                generation_id,
                audio_stream_id,
                GenerationState.RUNNING,
            ),
            (user_event, generation_event),
        )

    async def _commit_proactive_turn(
        self, session_id: UUID, reason: str
    ) -> tuple[GenerationAccepted, tuple[GenericCoreEvent, AssistantGenerationStartedEvent]]:
        now = datetime.now(UTC)
        turn_id = uuid4()
        generation_id = uuid4()
        audio_stream_id = uuid4()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO turns(
                    turn_id, session_id, role, committed_text, committed_at, created_at
                ) VALUES (?, ?, 'system', ?, ?, ?)
                """,
                (
                    str(turn_id),
                    str(session_id),
                    _PROACTIVE_PROMPT,
                    now.isoformat(),
                    now.isoformat(),
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
                    self._providers.llm.kind,
                    str(audio_stream_id),
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
            proactive_event = await self._event_store.append_in_transaction(
                connection,
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "companion.proactive_triggered",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "generation_id": generation_id,
                        "occurred_at": now,
                        "source": "runtime.companion",
                        "privacy": PrivacyLevel.LOCAL,
                        "payload": {"reason": reason},
                    }
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
            GenerationAccepted(
                session_id,
                turn_id,
                generation_id,
                audio_stream_id,
                GenerationState.RUNNING,
            ),
            (proactive_event, generation_event),
        )

    async def _run_generation(
        self,
        accepted: GenerationAccepted,
        user_text: str,
        character: CharacterProfile,
        character_context: TurnCharacterContext,
        memory_context: MemoryContextPacket,
        history: tuple[tuple[str, str], ...],
        *,
        trigger: Literal["user", "proactive"] = "user",
        memory_observation: UserTurnMemoryObservation | None = None,
    ) -> None:
        output = ""
        segmenter = StreamingTextSegmenter()
        segment_index = 0
        memory_projection_submitted = memory_observation is None

        async def deliver_segment(text: str) -> None:
            nonlocal memory_projection_submitted, segment_index
            await self._synthesize_segment(
                accepted,
                text,
                segment_index,
                character.voice_profile,
                _voice_style_instruction(character_context.plan),
            )
            if not memory_projection_submitted and memory_observation is not None:
                await self._memory.enqueue_user_turn(memory_observation)
                memory_projection_submitted = True
            segment_index += 1

        try:
            await self._emit_generic(
                accepted,
                "assistant.audio_stream_started",
                {"stream_id": str(accepted.audio_stream_id)},
            )
            for planned in self._avatar_planner.plan_response(
                accepted.session_id,
                character_context.plan,
                character.avatar_capabilities,
            ):
                await self._emit_avatar(
                    accepted,
                    planned.kind,
                    planned.name,
                    priority=planned.priority,
                    duration_ms=planned.duration_ms,
                )
            await self._emit_avatar(accepted, "state", "thinking", priority=60)
            compilation = await self._prompt_compiler.compile(
                character=character,
                kernel=character_context.snapshot,
                plan=character_context.plan,
                memory=memory_context,
                history=history,
                user_text=user_text,
            )
            await self._emit_generic(
                accepted,
                "character.prompt_compiled",
                {"report": compilation.report.model_dump(mode="json")},
            )
            request = LlmRequest(
                generation_id=accepted.generation_id,
                user_text=user_text,
                system_prompt=compilation.system_prompt,
                character_name=character.display_name,
                context=compilation.context,
                history=compilation.history,
                trigger=trigger,
            )
            async for delta in self._providers.llm.stream(request):
                self._ensure_current(accepted)
                output += delta
                await self._emit_generic(accepted, "assistant.text_delta", {"text": delta})
                for segment in segmenter.feed(delta):
                    await deliver_segment(segment)
            for segment in segmenter.flush():
                await deliver_segment(segment)
            self._ensure_current(accepted)
            await self._complete(accepted, output)
        except asyncio.CancelledError as error:
            reason = str(error.args[0]) if error.args else "interrupted"
            await self._cancelled(accepted, reason)
            raise
        except Exception as error:
            await self._failed(accepted, error)
        finally:
            if not memory_projection_submitted and memory_observation is not None:
                try:
                    await self._memory.enqueue_user_turn(memory_observation)
                except Exception:
                    logger.exception(
                        "could not enqueue committed user turn for memory projection",
                        extra={
                            "session_id": str(memory_observation.session_id),
                            "turn_id": str(memory_observation.turn_id),
                        },
                    )
            current = self._active.get(accepted.session_id)
            if current and current.generation_id == accepted.generation_id:
                self._active.pop(accepted.session_id, None)

    async def _recent_history(
        self, session_id: UUID, current_turn_id: UUID, limit: int = 16
    ) -> tuple[tuple[str, str], ...]:
        rows = await self._database.fetchall(
            """
            SELECT role, committed_text
            FROM turns
            WHERE session_id = ? AND turn_id != ? AND committed_text IS NOT NULL
                AND role IN ('user', 'assistant')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (str(session_id), str(current_turn_id), limit),
        )
        return tuple((str(row["role"]), str(row["committed_text"])) for row in reversed(rows))

    async def _synthesize_segment(
        self,
        accepted: GenerationAccepted,
        text: str,
        segment_index: int,
        voice: CharacterVoiceProfile,
        style: str | None = None,
    ) -> None:
        self._ensure_current(accepted)
        await self._emit_generic(
            accepted, "assistant.text_segment_committed", {"text": text.strip()}
        )
        asset = self._audio_assets.allocate()
        await self._playback.register_segment(
            session_id=accepted.session_id,
            generation_id=accepted.generation_id,
            stream_id=accepted.audio_stream_id,
            segment_id=asset.asset_id,
            segment_index=segment_index,
            text=text.strip(),
            duration_ms=0,
            duration_finalized=False,
        )
        result: SynthesisResult | None = None
        stream_started = False
        native_streaming = False
        streamed_audio_bytes = 0
        stream_sample_rate = 24_000
        stream_channels = 1
        try:
            stream = self._providers.tts.stream(
                SynthesisRequest(
                    session_id=accepted.session_id,
                    turn_id=accepted.turn_id,
                    generation_id=accepted.generation_id,
                    segment_id=asset.asset_id,
                    text=text.strip(),
                    destination=asset.path,
                    language=voice.language,
                    voice_id=voice.voice_id,
                    speaker_id=voice.speaker_id,
                    speed=voice.speed,
                    style=style,
                )
            )
            async for event in stream:
                self._ensure_current(accepted)
                if isinstance(event, TtsPcmChunk):
                    native_streaming = native_streaming or event.native_streaming
                    streamed_audio_bytes += len(event.pcm16)
                    stream_sample_rate = event.sample_rate
                    stream_channels = event.channels
                    if not stream_started:
                        stream_started = True
                        await self._audio_streams.publish(
                            AudioStreamPacket(
                                phase="started",
                                session_id=accepted.session_id,
                                turn_id=accepted.turn_id,
                                generation_id=accepted.generation_id,
                                stream_id=accepted.audio_stream_id,
                                segment_id=asset.asset_id,
                                segment_index=segment_index,
                                text=text.strip(),
                                sample_rate=event.sample_rate,
                                channels=event.channels,
                                native_streaming=event.native_streaming,
                                provider_id=self._providers.tts.provider_for(accepted.session_id),
                            )
                        )
                    await self._audio_streams.publish(
                        AudioStreamPacket(
                            phase="chunk",
                            session_id=accepted.session_id,
                            turn_id=accepted.turn_id,
                            generation_id=accepted.generation_id,
                            stream_id=accepted.audio_stream_id,
                            segment_id=asset.asset_id,
                            segment_index=segment_index,
                            text=text.strip(),
                            sequence=event.sequence,
                            sample_rate=event.sample_rate,
                            channels=event.channels,
                            native_streaming=event.native_streaming,
                            pcm16=event.pcm16,
                            provider_id=self._providers.tts.provider_for(accepted.session_id),
                        )
                    )
                else:
                    result = event.result
            if result is None:
                raise RuntimeError("TTS provider ended without a completed result")
        except BaseException:
            asset.path.unlink(missing_ok=True)
            if stream_started:
                partial_duration_ms = (
                    streamed_audio_bytes * 1000 // max(1, stream_sample_rate * stream_channels * 2)
                )
                await self._playback.finalize_segment(asset.asset_id, partial_duration_ms)
                await self._audio_streams.publish(
                    AudioStreamPacket(
                        phase="cancelled",
                        session_id=accepted.session_id,
                        turn_id=accepted.turn_id,
                        generation_id=accepted.generation_id,
                        stream_id=accepted.audio_stream_id,
                        segment_id=asset.asset_id,
                        segment_index=segment_index,
                        text=text.strip(),
                        native_streaming=native_streaming,
                        duration_ms=partial_duration_ms,
                        reason="generation_cancelled",
                    )
                )
            else:
                await self._playback.discard_segment(asset.asset_id)
            raise
        self._ensure_current(accepted)
        await self._playback.finalize_segment(asset.asset_id, result.duration_ms)
        live_completion_consumers = await self._audio_streams.publish(
            AudioStreamPacket(
                phase="completed",
                session_id=accepted.session_id,
                turn_id=accepted.turn_id,
                generation_id=accepted.generation_id,
                stream_id=accepted.audio_stream_id,
                segment_id=asset.asset_id,
                segment_index=segment_index,
                text=text.strip(),
                sample_rate=result.sample_rate,
                duration_ms=result.duration_ms,
                native_streaming=native_streaming,
                provider_id=result.provider_id,
                model=result.model,
            )
        )
        await self._emit_avatar(accepted, "speech", "speaking", priority=70)
        await self._emit_generic(
            accepted,
            "assistant.audio_chunk_queued",
            {
                "asset_id": str(asset.asset_id),
                "stream_id": str(accepted.audio_stream_id),
                "segment_id": str(asset.asset_id),
                "segment_index": segment_index,
                "url": asset.url,
                "text": text.strip(),
                "media_type": result.media_type,
                "sample_rate": result.sample_rate,
                "duration_ms": result.duration_ms,
                "tts_provider": result.provider_id,
                "tts_model": result.model,
                "streamed_live": stream_started
                and native_streaming
                and live_completion_consumers > 0,
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
        duration_ms: int | None = None,
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
                        duration_ms=duration_ms,
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


def _voice_style_instruction(plan: ResponsePlan) -> str:
    tone = {
        "gentle": "轻柔温和",
        "bright": "轻快明亮",
        "shy": "有些害羞",
        "serious": "认真克制",
        "playful": "俏皮亲近",
        "concerned": "关心而柔和",
    }[plan.tone]
    expression = {
        "neutral": "自然平静",
        "happy": "带着开心的笑意",
        "sad": "略带难过但不要夸张",
        "angry": "略带生气但保持克制",
        "surprised": "带一点惊讶",
        "shy": "带一点羞涩",
        "curious": "带着好奇",
    }[plan.expression]
    return f"{tone}，{expression}，像面对面聊天一样自然。"
