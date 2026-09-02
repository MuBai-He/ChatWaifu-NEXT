"""Generation-scoped streaming conversation pipeline with hard cancellation."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

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

from chatwaifu_runtime.agent.tool_calling import AgentTurnOrchestrator
from chatwaifu_runtime.audio.store import AudioAssetStore
from chatwaifu_runtime.audio.streaming import AudioStreamHub
from chatwaifu_runtime.avatar.planner import SemanticAvatarCuePlanner
from chatwaifu_runtime.character_kernel.prompt import PromptCompiler
from chatwaifu_runtime.character_kernel.service import (
    USER_SCOPE,
    CharacterKernelService,
    TurnCharacterContext,
)
from chatwaifu_runtime.characters.service import CharacterProfile, CharacterService
from chatwaifu_runtime.conversation.models import (
    ConversationHistoryEntry,
    ConversationSourceContext,
    ConversationTurnOptions,
    GenerationAccepted,
    SessionDataReset,
)
from chatwaifu_runtime.conversation.repository import (
    ConversationRecoveryRecord,
    ConversationRepository,
)
from chatwaifu_runtime.conversation.reset import ExperienceResetRepository
from chatwaifu_runtime.conversation.speech import ConversationSpeechPipeline
from chatwaifu_runtime.conversation.text_segmenter import StreamingTextSegmenter
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.memory.service import MemoryService, UserTurnMemoryObservation
from chatwaifu_runtime.playback.service import PlaybackService
from chatwaifu_runtime.providers.contracts import LlmRequest
from chatwaifu_runtime.providers.factory import ProviderSet
from chatwaifu_runtime.sessions.service import SessionService

_PROACTIVE_PROMPT = (
    "[Runtime ambient event] The user has been quietly inactive for a while. "
    "Offer one brief, natural, low-pressure check-in as the character. "
    "Do not claim the user just spoke, do not mention timers or system policy, "
    "and do not demand a reply."
)
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ActiveGeneration:
    generation_id: UUID
    task: asyncio.Task[None]
    completing: bool = False


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        reset_repository: ExperienceResetRepository,
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
        agent: AgentTurnOrchestrator,
    ) -> None:
        self._repository = repository
        self._reset_repository = reset_repository
        self._publisher = publisher
        self._sessions = sessions
        self._providers = providers
        self._audio_assets = audio_assets
        self._characters = characters
        self._memory = memory
        self._speech = ConversationSpeechPipeline(providers, audio_assets, audio_streams, playback)
        self._character_kernel = character_kernel
        self._prompt_compiler = prompt_compiler
        self._agent = agent
        self._avatar_planner = SemanticAvatarCuePlanner()
        self._active: dict[UUID, _ActiveGeneration] = {}
        self._start_lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return sum(not item.task.done() for item in self._active.values())

    async def submit_text(
        self,
        session_id: UUID,
        text: str,
        *,
        options: ConversationTurnOptions | None = None,
        turn_id: UUID | None = None,
        generation_id: UUID | None = None,
    ) -> GenerationAccepted:
        return await self._submit(
            session_id,
            text,
            turn_id=turn_id or uuid4(),
            generation_id=generation_id or uuid4(),
            options=options or ConversationTurnOptions(),
        )

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
            options=ConversationTurnOptions(origin="voice"),
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
                    options=ConversationTurnOptions(
                        origin="proactive",
                        allow_tools=False,
                    ),
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
        options: ConversationTurnOptions,
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
                options=options,
            )
            try:
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
                        options=options,
                    ),
                    name=f"generation-{accepted.generation_id}",
                )
                self._active[session_id] = _ActiveGeneration(accepted.generation_id, task)
            except asyncio.CancelledError:
                await self._cancelled(
                    accepted,
                    "generation_admission_cancelled",
                    set_session_idle=True,
                )
                raise
            except Exception as error:
                await self._failed(
                    accepted,
                    error,
                    error_code="generation_admission_failed",
                    retryable=False,
                    set_session_idle=True,
                )
                raise
            return accepted

    async def cancel(self, session_id: UUID, reason: str = "user_interruption") -> bool:
        active = self._active.get(session_id)
        if active is None or active.task.done():
            return False
        if active.completing:
            # Completion is the generation's publication barrier. Once its
            # terminal sequence starts, interruption waits for that sequence
            # instead of appending cancellation facts after the completed event.
            await asyncio.shield(active.task)
            return False
        active.task.cancel(reason)
        try:
            await active.task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        return True

    def active_generation_id(self, session_id: UUID) -> UUID | None:
        active = self._active.get(session_id)
        if active is None or active.task.done():
            return None
        return active.generation_id

    async def reset(self, session_id: UUID) -> SessionDataReset:
        """Reset the current session and its character/user experience scope.

        Conversation events, turns, and generated audio are session-owned. Long-term
        memory plus Affect/Relationship state are owned by the current
        ``character_id + user_scope`` pair. No unrelated session, character, or user
        data is removed.
        """

        async with self._start_lock:
            session = await self._sessions.get_session(session_id)
            if session is None:
                raise KeyError(f"unknown session {session_id}")
            if session.state is not SessionState.READY:
                raise RuntimeError(f"session is not ready: {session.state}")
            await self.cancel(session_id, "session_data_reset")
            self._active.pop(session_id, None)
            now = datetime.now(UTC)
            audio_asset_ids = await self._reset_repository.audio_asset_ids(session_id)
            staged_audio = self._audio_assets.stage_remove(audio_asset_ids)
            try:
                memory_namespace = await self._memory.prepare_scope_reset(
                    session.character_id, USER_SCOPE
                )
                reset = await self._reset_repository.reset(
                    session_id,
                    character_id=session.character_id,
                    user_scope=USER_SCOPE,
                    memory_namespace=memory_namespace,
                    updated_at=now,
                    reset_event=GenericCoreEvent(
                        event_id=uuid4(),
                        event_type="session.data_reset",
                        session_id=session_id,
                        occurred_at=now,
                        source="runtime.conversation",
                        privacy=PrivacyLevel.PRIVATE,
                        payload={
                            "character_id": session.character_id,
                            "user_scope": USER_SCOPE,
                            "conversation": "current_session",
                            "audio": "current_session",
                            "memory": "current_character_user",
                            "character_state": "current_character_user",
                        },
                    ),
                )
            except BaseException as reset_error:
                try:
                    staged_audio.rollback()
                except BaseException as rollback_error:
                    raise BaseExceptionGroup(
                        "experience reset and audio rollback both failed",
                        [reset_error, rollback_error],
                    ) from None
                raise
            audio_commit = staged_audio.commit()
            if not audio_commit.cleanup_complete:
                logger.warning(
                    "experience reset committed with deferred audio quarantine cleanup",
                    extra={
                        "session_id": str(session_id),
                        "audio_assets_deleted": audio_commit.deleted_count,
                        "audio_assets_pending": audio_commit.pending_count,
                    },
                )
            await self._memory.finalize_scope_reset(reset.memory_ids)
            try:
                await self._publisher.publish_persisted(reset.reset_event)
            except Exception:
                # The reset event is already durable in the outbox. A later
                # Runtime start will republish it; committed deletion must not
                # be reported as failed merely because one live client vanished.
                logger.exception("failed to publish durable session reset event")
            return SessionDataReset(
                session_id=session_id,
                character_id=session.character_id,
                user_scope=USER_SCOPE,
                turns_deleted=reset.turns_deleted,
                events_deleted=reset.events_deleted,
                memories_deleted=len(reset.memory_ids),
                audio_assets_deleted=audio_commit.deleted_count,
                audio_assets_pending_cleanup=audio_commit.pending_count,
                audio_cleanup_complete=audio_commit.cleanup_complete,
            )

    async def stop(self) -> None:
        active = tuple(self._active.values())
        for generation in active:
            if not generation.completing:
                generation.task.cancel("runtime_stopping")
        if active:
            await asyncio.gather(*(item.task for item in active), return_exceptions=True)
        self._active.clear()

    async def list_messages(self, session_id: UUID, limit: int = 100) -> list[dict[str, object]]:
        return await self._repository.list_messages(session_id, limit=limit)

    async def recovery_state(self, session_id: UUID) -> ConversationRecoveryRecord:
        return await self._repository.recovery_state(session_id)

    async def _commit_user_turn(
        self,
        session_id: UUID,
        text: str,
        *,
        turn_id: UUID,
        generation_id: UUID,
        options: ConversationTurnOptions,
    ) -> tuple[GenerationAccepted, tuple[UserTurnCommittedEvent, AssistantGenerationStartedEvent]]:
        now = datetime.now(UTC)
        audio_stream_id = uuid4()
        user_event = UserTurnCommittedEvent(
            event_id=uuid4(),
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            occurred_at=now,
            source="runtime.conversation",
            privacy=PrivacyLevel.LOCAL,
            payload=UserTurnCommittedPayload(text=text),
        )
        generation_event = AssistantGenerationStartedEvent(
            event_id=uuid4(),
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            occurred_at=now,
            source="runtime.conversation",
            privacy=PrivacyLevel.LOCAL,
            payload=AssistantGenerationStartedPayload(backend_kind=self._providers.llm.kind),
        )
        events = await self._repository.commit_user_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            audio_stream_id=audio_stream_id,
            text=text,
            backend_kind=self._providers.llm.kind,
            source_context=options.source_context,
            occurred_at=now,
            user_event=user_event,
            generation_event=generation_event,
        )
        return (
            GenerationAccepted(
                session_id,
                turn_id,
                generation_id,
                audio_stream_id,
                GenerationState.RUNNING,
            ),
            events,
        )

    async def _commit_proactive_turn(
        self, session_id: UUID, reason: str
    ) -> tuple[GenerationAccepted, tuple[GenericCoreEvent, AssistantGenerationStartedEvent]]:
        now = datetime.now(UTC)
        turn_id = uuid4()
        generation_id = uuid4()
        audio_stream_id = uuid4()
        proactive_event = GenericCoreEvent.model_validate(
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
        )
        generation_event = AssistantGenerationStartedEvent(
            event_id=uuid4(),
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            occurred_at=now,
            source="runtime.conversation",
            privacy=PrivacyLevel.LOCAL,
            payload=AssistantGenerationStartedPayload(backend_kind=self._providers.llm.kind),
        )
        events = await self._repository.commit_proactive_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            audio_stream_id=audio_stream_id,
            prompt=_PROACTIVE_PROMPT,
            backend_kind=self._providers.llm.kind,
            occurred_at=now,
            proactive_event=proactive_event,
            generation_event=generation_event,
        )
        return (
            GenerationAccepted(
                session_id,
                turn_id,
                generation_id,
                audio_stream_id,
                GenerationState.RUNNING,
            ),
            events,
        )

    async def _run_generation(
        self,
        accepted: GenerationAccepted,
        user_text: str,
        character: CharacterProfile,
        character_context: TurnCharacterContext,
        memory_context: MemoryContextPacket,
        history: tuple[ConversationHistoryEntry, ...],
        *,
        trigger: Literal["user", "proactive"] = "user",
        memory_observation: UserTurnMemoryObservation | None = None,
        options: ConversationTurnOptions,
    ) -> None:
        output = ""
        segmenter = StreamingTextSegmenter() if options.emits("audio") else None
        segment_index = 0
        memory_projection_submitted = memory_observation is None

        async def deliver_segment(text: str) -> None:
            nonlocal memory_projection_submitted, segment_index
            if not options.emits("audio"):
                return
            await self._speech.synthesize_segment(
                accepted,
                text,
                segment_index,
                character.voice_profile,
                style=_voice_style_instruction(character_context.plan),
                ensure_current=self._ensure_current,
                emit_generic=self._emit_generic,
                emit_avatar=self._emit_avatar,
            )
            if not memory_projection_submitted and memory_observation is not None:
                await self._memory.enqueue_user_turn(memory_observation)
                memory_projection_submitted = True
            segment_index += 1

        try:
            if options.emits("audio"):
                await self._emit_generic(
                    accepted,
                    "assistant.audio_stream_started",
                    {"stream_id": str(accepted.audio_stream_id)},
                )
            if options.emits("avatar"):
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
                source_context=options.source_context,
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
                recalled_memory_texts=compilation.recalled_memory_texts,
                trigger=trigger,
            )
            async for delta in self._agent.stream(
                request,
                session_id=accepted.session_id,
                turn_id=accepted.turn_id,
                ensure_current=lambda: self._ensure_current(accepted),
                allow_tools=trigger == "user" and options.allow_tools,
            ):
                self._ensure_current(accepted)
                output += delta
                await self._emit_generic(
                    accepted, "assistant.text_delta", {"text": delta}, ephemeral=True
                )
                if segmenter is not None:
                    for segment in segmenter.feed(delta):
                        await deliver_segment(segment)
            if segmenter is not None:
                for segment in segmenter.flush():
                    await deliver_segment(segment)
            self._ensure_current(accepted)
            await self._complete(
                accepted,
                output,
                emit_avatar=options.emits("avatar"),
                source_context=options.source_context,
            )
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
    ) -> tuple[ConversationHistoryEntry, ...]:
        return await self._repository.recent_history(session_id, current_turn_id, limit=limit)

    async def _complete(
        self,
        accepted: GenerationAccepted,
        output: str,
        *,
        emit_avatar: bool,
        source_context: ConversationSourceContext | None,
    ) -> bool:
        self._begin_completion(accepted)
        now = datetime.now(UTC)
        assistant_turn_id = uuid4()
        completed = await self._repository.complete_generation(
            session_id=accepted.session_id,
            generation_id=accepted.generation_id,
            assistant_turn_id=assistant_turn_id,
            output=output,
            source_context=source_context,
            occurred_at=now,
            set_session_idle=self._is_current(accepted),
        )
        if not completed:
            logger.info(
                "generation completion skipped because state is no longer running",
                extra={
                    "session_id": str(accepted.session_id),
                    "generation_id": str(accepted.generation_id),
                },
            )
            return False
        try:
            if emit_avatar:
                await self._emit_avatar(accepted, "state", "idle", priority=90)
            await self._emit_generic(
                accepted,
                "assistant.generation_completed",
                {"text": output, "assistant_turn_id": str(assistant_turn_id)},
            )
        except Exception:
            logger.exception(
                "failed emitting completion events after generation committed",
                extra={
                    "session_id": str(accepted.session_id),
                    "generation_id": str(accepted.generation_id),
                },
            )
        return True

    async def _cancelled(
        self,
        accepted: GenerationAccepted,
        reason: str,
        *,
        set_session_idle: bool | None = None,
    ) -> None:
        now = datetime.now(UTC)
        cancelled = await self._repository.cancel_generation(
            session_id=accepted.session_id,
            generation_id=accepted.generation_id,
            occurred_at=now,
            set_session_idle=(
                self._is_current(accepted) if set_session_idle is None else set_session_idle
            ),
        )
        if not cancelled:
            logger.info(
                "generation cancellation skipped because state is no longer running",
                extra={
                    "session_id": str(accepted.session_id),
                    "generation_id": str(accepted.generation_id),
                },
            )
            return
        await self._emit_generic(accepted, "assistant.generation_cancelled", {"reason": reason})
        await self._emit_generic(accepted, "conversation.interrupted", {"reason": reason})

    async def _failed(
        self,
        accepted: GenerationAccepted,
        error: Exception,
        *,
        error_code: str = "provider_error",
        retryable: bool = True,
        set_session_idle: bool | None = None,
    ) -> None:
        now = datetime.now(UTC)
        failed = await self._repository.fail_generation(
            session_id=accepted.session_id,
            generation_id=accepted.generation_id,
            error_code=error_code,
            occurred_at=now,
            set_session_idle=(
                self._is_current(accepted) if set_session_idle is None else set_session_idle
            ),
        )
        if not failed:
            logger.info(
                "generation failure skipped because state is no longer running",
                extra={
                    "session_id": str(accepted.session_id),
                    "generation_id": str(accepted.generation_id),
                },
            )
            return
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
                        code=error_code,
                        message=str(error),
                        retryable=retryable,
                        component="conversation",
                    )
                ),
            )
        )

    async def _emit_generic(
        self,
        accepted: GenerationAccepted,
        event_type: str,
        payload: dict[str, object],
        *,
        ephemeral: bool = False,
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
        if ephemeral:
            await self._publisher.publish_ephemeral(event)
        else:
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

    def _begin_completion(self, accepted: GenerationAccepted) -> None:
        self._ensure_current(accepted)
        active = self._active[accepted.session_id]
        active.completing = True

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
