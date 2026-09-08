"""Cloud realtime coordinator and normalized provider event router.

Bridges CloudRealtimeSession events into ChatWaifu-owned domain and media sinks,
enforcing deduplication, lineage resolution, generation tombstones, and error
normalization without leaking provider-specific payloads or SDK types into domain code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.realtime.admission import RealtimeTurnAdmissionPort
from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    CloudRealtimeSession,
    InputAudioCommittedEvent,
    OutputAudioEvent,
    ProviderErrorEvent,
    RealtimeOutputAudioFrame,
    RealtimeProviderEvent,
    RealtimeTranscriptCandidate,
    RealtimeUsage,
    ResponseCancelledEvent,
    ResponseCompletedEvent,
    ResponseStartedEvent,
    SessionClosedEvent,
    SessionDegradedEvent,
    SessionReadyEvent,
    UsageRecordedEvent,
    UserTranscriptEvent,
)
from chatwaifu_runtime.realtime.cloud.mirror import RealtimeSessionMirror
from chatwaifu_runtime.realtime.contracts import VoiceTurnIdentity

_LOGGER = logging.getLogger(__name__)
SESSION_CLOSE_TIMEOUT_SECONDS = 2.0


class RealtimeDomainSink(Protocol):
    """Sink for normalized ChatWaifu domain events."""

    async def response_started(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID
    ) -> None: ...

    async def transcript_delta(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        role: Literal["user", "assistant"] = "assistant",
        utterance_id: UUID | None = None,
    ) -> None: ...

    async def transcript_final(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        role: Literal["user", "assistant"],
        utterance_id: UUID | None = None,
    ) -> None: ...

    async def response_completed(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        *,
        has_audio: bool = False,
        playback_confirmed: bool = False,
    ) -> None: ...

    async def response_cancelled(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, reason: str
    ) -> None: ...

    async def response_failed(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, reason: str
    ) -> None: ...

    async def usage_recorded(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, usage: RealtimeUsage
    ) -> None: ...

    async def session_ready(self, session_id: UUID, provider_session_id: str | None) -> None: ...

    async def session_degraded(self, session_id: UUID, reason: str) -> None: ...

    async def session_closed(self, session_id: UUID, reason: str) -> None: ...

    async def input_audio_committed(self, session_id: UUID, turn_id: UUID | None) -> None: ...

    async def provider_error(self, session_id: UUID, error: StructuredError) -> None: ...


class RealtimeMediaSink(Protocol):
    """Sink for high-frequency normalized audio frames."""

    async def handle_audio_frame(self, frame: RealtimeOutputAudioFrame) -> None: ...

    async def finalize_response_audio(self, generation_id: UUID) -> int | None: ...

    def clear_generation(self, generation_id: UUID) -> None: ...

    async def session_terminated(self) -> None: ...


@dataclass(slots=True)
class InMemoryDomainSink(RealtimeDomainSink):
    """Deterministic in-memory domain sink for testing and telemetry inspection."""

    responses_started: list[tuple[UUID, UUID, UUID]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID]]()
    )
    transcript_deltas: list[tuple[UUID, UUID, UUID, str]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, str]]()
    )
    type DeltaWithRole = tuple[UUID, UUID, UUID, str, Literal["user", "assistant"]]
    transcript_deltas_with_role: list[DeltaWithRole] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, str, Literal["user", "assistant"]]]()
    )
    transcript_finals: list[tuple[UUID, UUID, UUID, str, Literal["user", "assistant"]]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, str, Literal["user", "assistant"]]]()
    )
    type UtteredDelta = tuple[UUID, UUID, UUID, str, Literal["user", "assistant"], UUID | None]
    transcript_delta_utterances: list[UtteredDelta] = field(
        default_factory=lambda: list[
            tuple[UUID, UUID, UUID, str, Literal["user", "assistant"], UUID | None]
        ]()
    )
    transcript_final_utterances: list[UtteredDelta] = field(
        default_factory=lambda: list[
            tuple[UUID, UUID, UUID, str, Literal["user", "assistant"], UUID | None]
        ]()
    )
    responses_completed: list[tuple[UUID, UUID, UUID, str]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, str]]()
    )
    responses_cancelled: list[tuple[UUID, UUID, UUID, str]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, str]]()
    )
    responses_failed: list[tuple[UUID, UUID, UUID, str]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, str]]()
    )
    usages_recorded: list[tuple[UUID, UUID, UUID, RealtimeUsage]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, RealtimeUsage]]()
    )
    session_readies: list[tuple[UUID, str | None]] = field(
        default_factory=lambda: list[tuple[UUID, str | None]]()
    )
    session_degradations: list[tuple[UUID, str]] = field(
        default_factory=lambda: list[tuple[UUID, str]]()
    )
    session_closures: list[tuple[UUID, str]] = field(
        default_factory=lambda: list[tuple[UUID, str]]()
    )
    input_audio_commits: list[tuple[UUID, UUID | None]] = field(
        default_factory=lambda: list[tuple[UUID, UUID | None]]()
    )
    provider_errors: list[tuple[UUID, StructuredError]] = field(
        default_factory=lambda: list[tuple[UUID, StructuredError]]()
    )

    async def response_started(self, session_id: UUID, turn_id: UUID, generation_id: UUID) -> None:
        self.responses_started.append((session_id, turn_id, generation_id))

    async def transcript_delta(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        role: Literal["user", "assistant"] = "assistant",
        utterance_id: UUID | None = None,
    ) -> None:
        self.transcript_deltas.append((session_id, turn_id, generation_id, text))
        self.transcript_deltas_with_role.append((session_id, turn_id, generation_id, text, role))
        self.transcript_delta_utterances.append(
            (session_id, turn_id, generation_id, text, role, utterance_id)
        )

    async def transcript_final(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        role: Literal["user", "assistant"],
        utterance_id: UUID | None = None,
    ) -> None:
        self.transcript_finals.append((session_id, turn_id, generation_id, text, role))
        self.transcript_final_utterances.append(
            (session_id, turn_id, generation_id, text, role, utterance_id)
        )

    async def response_completed(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        *,
        has_audio: bool = False,
        playback_confirmed: bool = False,
    ) -> None:
        self.responses_completed.append((session_id, turn_id, generation_id, text))

    async def response_cancelled(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, reason: str
    ) -> None:
        self.responses_cancelled.append((session_id, turn_id, generation_id, reason))

    async def response_failed(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, reason: str
    ) -> None:
        self.responses_failed.append((session_id, turn_id, generation_id, reason))

    async def usage_recorded(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, usage: RealtimeUsage
    ) -> None:
        self.usages_recorded.append((session_id, turn_id, generation_id, usage))

    async def session_ready(self, session_id: UUID, provider_session_id: str | None) -> None:
        self.session_readies.append((session_id, provider_session_id))

    async def session_degraded(self, session_id: UUID, reason: str) -> None:
        self.session_degradations.append((session_id, reason))

    async def session_closed(self, session_id: UUID, reason: str) -> None:
        self.session_closures.append((session_id, reason))

    async def input_audio_committed(self, session_id: UUID, turn_id: UUID | None) -> None:
        self.input_audio_commits.append((session_id, turn_id))

    async def provider_error(self, session_id: UUID, error: StructuredError) -> None:
        self.provider_errors.append((session_id, error))


@dataclass(slots=True)
class InMemoryMediaSink(RealtimeMediaSink):
    """Deterministic in-memory media sink for testing audio output delivery."""

    received_frames: list[RealtimeOutputAudioFrame] = field(
        default_factory=lambda: list[RealtimeOutputAudioFrame]()
    )

    async def handle_audio_frame(self, frame: RealtimeOutputAudioFrame) -> None:
        self.received_frames.append(frame)

    async def finalize_response_audio(self, generation_id: UUID) -> int | None:
        return None

    def clear_generation(self, generation_id: UUID) -> None:
        pass

    async def session_terminated(self) -> None:
        pass


class CloudRealtimeCoordinator:
    """Coordinates lifecycle, event pumping, and normalization for a cloud realtime session."""

    def __init__(
        self,
        session_id: UUID,
        *,
        session: CloudRealtimeSession,
        mirror: RealtimeSessionMirror,
        domain_sink: RealtimeDomainSink,
        media_sink: RealtimeMediaSink | None = None,
    ) -> None:
        self.session_id: UUID = session_id
        self._session = session
        self._mirror = mirror
        self._domain_sink = domain_sink
        self._media_sink = media_sink
        self._pump_task: asyncio.Task[None] | None = None
        self._is_running: bool = False
        self._end_task: asyncio.Task[None] | None = None
        self._closing = False
        self._admission_lock = asyncio.Lock()
        self._admission_task: asyncio.Task[VoiceTurnIdentity | None] | None = None
        self._missing_ack_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._ack_timeout_seconds: float = 5.0
        self._injected_ack_timeout_event: asyncio.Event | None = None
        self._injected_ack_deadline_waiter: Callable[[float], Awaitable[None]] | None = None

    def set_ack_timeout(self, seconds: float) -> None:
        self._ack_timeout_seconds = seconds

    def inject_ack_timeout_event(self, event: asyncio.Event | None) -> None:
        self._injected_ack_timeout_event = event

    def inject_ack_deadline_waiter(self, waiter: Callable[[float], Awaitable[None]] | None) -> None:
        self._injected_ack_deadline_waiter = waiter

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def mirror(self) -> RealtimeSessionMirror:
        return self._mirror

    @property
    def session(self) -> CloudRealtimeSession:
        return self._session

    @property
    def domain_sink(self) -> RealtimeDomainSink:
        return self._domain_sink

    @property
    def media_sink(self) -> RealtimeMediaSink | None:
        return self._media_sink

    def set_media_sink(self, media_sink: RealtimeMediaSink | None) -> None:
        self._media_sink = media_sink

    @property
    def is_closing(self) -> bool:
        return self._closing

    async def admit_utterance(
        self, admission: RealtimeTurnAdmissionPort
    ) -> VoiceTurnIdentity | None:
        """Join in-flight admission before closing, including its durable cancellation."""
        async with self._admission_lock:
            if self._closing:
                return None
            self._admission_task = asyncio.create_task(
                self._perform_admission(admission), name=f"cloud-admission-{self.session_id}"
            )
            try:
                return await asyncio.shield(self._admission_task)
            except asyncio.CancelledError:
                # The transaction may already have committed. Preserve its returned
                # identity and join cancellation through the same session cleanup.
                await self._end_session("admission_cancelled")
                raise

    async def _perform_admission(
        self, admission: RealtimeTurnAdmissionPort
    ) -> VoiceTurnIdentity | None:
        if self._closing:
            return None
        identity = await admission.begin_utterance(self.session_id)
        if self._closing:
            await admission.cancel_utterance(identity, "cloud_session_closed_during_admission")
            return None
        self.admit_turn(
            identity.turn_id,
            identity.generation_id,
            identity.utterance_id,
            audio_stream_id=identity.audio_stream_id,
        )
        return identity

    def admit_turn(
        self,
        turn_id: UUID,
        generation_id: UUID,
        utterance_id: UUID | None = None,
        audio_stream_id: UUID | None = None,
    ) -> None:
        """Register turn and generation in mirror when admitted by runtime."""
        if self._closing:
            raise RuntimeError("Cannot admit a turn on a closing cloud session")
        self._mirror.register_generation(
            generation_id,
            turn_id,
            utterance_id=utterance_id,
            audio_stream_id=audio_stream_id,
        )

    def start(self) -> None:
        """Start background event pump task."""
        if self._is_running or self._closing:
            return
        self._is_running = True
        self._pump_task = asyncio.create_task(
            self._pump_loop(),
            name=f"cloud-realtime-pump-{str(self.session_id)[:8]}",
        )

    async def terminate_active_generation(
        self,
        reason: str = "terminated",
        terminal: Literal["cancelled", "failed"] = "cancelled",
        error: StructuredError | None = None,
    ) -> None:
        """Idempotently terminate active generation in mirror and domain sink."""
        active_gen = self._mirror.active_generation_id
        if active_gen is not None:
            self._cancel_missing_ack_timer(active_gen)
            if self._media_sink is not None:
                self._media_sink.clear_generation(active_gen)
            turn_id = self._mirror.get_turn_id(active_gen)
            was_tombstoned = self._mirror.is_tombstoned(active_gen)
            self._mirror.cancel_generation(active_gen)
            if not was_tombstoned and turn_id is not None:
                if terminal == "failed":
                    await self._domain_sink.response_failed(
                        self.session_id,
                        turn_id,
                        active_gen,
                        error.message if error else reason,
                    )
                else:
                    await self._domain_sink.response_cancelled(
                        self.session_id,
                        turn_id,
                        active_gen,
                        reason,
                    )

    def complete_generation(self, generation_id: UUID) -> None:
        """Mark generation completed in mirror after playback confirmation."""
        self._cancel_missing_ack_timer(generation_id)
        self._mirror.complete_generation(generation_id)
        if self._media_sink is not None:
            self._media_sink.clear_generation(generation_id)

    async def playback_completed(
        self, generation_id: UUID, turn_id: UUID | None, spoken_text: str
    ) -> None:
        """Handle durable playback confirmation for all segments of a generation."""
        if (
            self._closing
            or self._mirror.is_tombstoned(generation_id)
            or not self._mirror.is_active(generation_id)
            or not self._mirror.is_provider_response_done(generation_id)
        ):
            _LOGGER.debug(
                "Dropping playback_completed for generation %s "
                "(closing=%s, tombstoned=%s, active=%s, done=%s)",
                generation_id,
                self._closing,
                self._mirror.is_tombstoned(generation_id),
                self._mirror.is_active(generation_id),
                self._mirror.is_provider_response_done(generation_id),
            )
            return

        self._cancel_missing_ack_timer(generation_id)
        self._mirror.complete_generation(generation_id)
        if self._media_sink is not None:
            self._media_sink.clear_generation(generation_id)
        resolved_turn = turn_id or self._mirror.get_turn_id(generation_id)
        if resolved_turn is not None:
            await self._domain_sink.response_completed(
                self.session_id,
                resolved_turn,
                generation_id,
                spoken_text,
                has_audio=True,
                playback_confirmed=True,
            )

    def _start_missing_ack_timer(
        self, generation_id: UUID, turn_id: UUID, duration_ms: int | None = None
    ) -> None:
        self._cancel_missing_ack_timer(generation_id)
        task = asyncio.create_task(
            self._missing_ack_timeout_runner(generation_id, turn_id, duration_ms=duration_ms),
            name=f"cloud-missing-ack-{str(generation_id)[:8]}",
        )
        self._missing_ack_tasks[generation_id] = task

    def _cancel_missing_ack_timer(self, generation_id: UUID) -> None:
        task = self._missing_ack_tasks.pop(generation_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _missing_ack_timeout_runner(
        self, generation_id: UUID, turn_id: UUID, duration_ms: int | None = None
    ) -> None:
        audio_seconds = (
            (duration_ms / 1000.0) if (duration_ms is not None and duration_ms > 0) else 0.0
        )
        total_timeout = audio_seconds + self._ack_timeout_seconds
        try:
            if self._injected_ack_deadline_waiter is not None:
                await self._injected_ack_deadline_waiter(total_timeout)
            elif self._injected_ack_timeout_event is not None:
                await self._injected_ack_timeout_event.wait()
            else:
                await asyncio.sleep(total_timeout)
        except asyncio.CancelledError:
            return
        finally:
            self._missing_ack_tasks.pop(generation_id, None)

        if self._mirror.is_active(generation_id):
            _LOGGER.warning(
                "Playback ACK timed out for generation %s (session %s)",
                generation_id,
                self.session_id,
            )
            self._mirror.cancel_generation(generation_id)
            if self._media_sink is not None:
                self._media_sink.clear_generation(generation_id)
            await self._domain_sink.response_cancelled(
                self.session_id,
                turn_id,
                generation_id,
                "playback_ack_timeout",
            )

    async def stop(self) -> None:
        """Join one terminal cleanup; a closed provider session cannot be restarted."""
        await self._end_session("coordinator_stopped")

    async def _end_session(self, reason: str) -> None:
        self._closing = True
        self._is_running = False
        if self._end_task is None:
            self._end_task = asyncio.create_task(
                self._finish_session(reason, asyncio.current_task()),
                name=f"cloud-session-close-{self.session_id}",
            )
        await asyncio.shield(self._end_task)

    async def _finish_session(self, reason: str, initiator: asyncio.Task[object] | None) -> None:
        # The closing flag fences dispatch/admission before any cleanup awaits.
        ack_tasks = [
            task
            for task in self._missing_ack_tasks.values()
            if not task.done() and task is not initiator
        ]
        for task in ack_tasks:
            task.cancel()
        if ack_tasks:
            await asyncio.gather(*ack_tasks, return_exceptions=True)
        self._missing_ack_tasks.clear()
        pump = self._pump_task
        if pump is not None and pump is not initiator:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        try:
            if self._media_sink is not None:
                await self._media_sink.session_terminated()
        finally:
            try:
                try:
                    if self._admission_task is not None:
                        await asyncio.shield(self._admission_task)
                finally:
                    await self.terminate_active_generation(reason=reason, terminal="cancelled")
            finally:
                try:
                    async with asyncio.timeout(SESSION_CLOSE_TIMEOUT_SECONDS):
                        await self._session.close()
                except Exception:
                    _LOGGER.warning(
                        "Cloud session close failed or timed out session_id=%s", self.session_id
                    )
                await self._domain_sink.session_closed(self.session_id, reason)

    async def _emit_diagnostic(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, str] | None = None,
    ) -> None:
        """Report a dropped event as a structured diagnostic error."""
        merged: JsonObject = {"event": code}
        if details:
            merged.update(details)
        await self._domain_sink.provider_error(
            self.session_id,
            StructuredError(
                code=code,
                message=message,
                retryable=False,
                component="realtime.cloud",
                details=merged,
            ),
        )

    async def _emit_unmapped_event(
        self, *, event_name: str, details: dict[str, str] | None = None
    ) -> None:
        """Report a dropped identity-less event as a structured unmapped error."""
        await self._emit_diagnostic(
            code="unmapped_realtime_event",
            message=f"Cannot resolve generation_id for {event_name}",
            details={"event": event_name, **(details or {})},
        )

    def _event_session_id(self, event: RealtimeProviderEvent) -> UUID | None:
        """Extract the session identity claimed by a provider event."""
        match event:
            case SessionReadyEvent() | SessionClosedEvent() | SessionDegradedEvent():
                return event.session_id
            case InputAudioCommittedEvent():
                return event.session_id
            case UserTranscriptEvent() | AssistantTranscriptEvent():
                return event.candidate.session_id
            case ResponseStartedEvent() | ResponseCompletedEvent() | ResponseCancelledEvent():
                return event.session_id
            case OutputAudioEvent():
                return event.frame.session_id
            case UsageRecordedEvent():
                return event.usage.session_id
            case ProviderErrorEvent():
                return event.error.session_id

    async def _resolve_consistent_identities(
        self,
        *,
        event_name: str,
        generation_id: UUID | None = None,
        provider_response_id: str | None = None,
        provider_item_id: str | None = None,
    ) -> UUID | None:
        """Resolve only when every supplied provider identity agrees.

        Two phases: first purely read and validate every identity against
        registered bindings, then — only after a single anchor is settled —
        commit any brand-new provider mappings. A rejected conflict therefore
        leaves no residual mapping behind for future events to resolve
        through. Never guesses the active or last generation.
        """
        # Phase 1: read-only validation, no mapping writes.
        anchor: UUID | None = None
        if generation_id is not None:
            if not self._mirror.has_binding(generation_id):
                await self._emit_unmapped_event(
                    event_name=event_name,
                    details={"generation_id": str(generation_id)},
                )
                return None
            anchor = generation_id

        response_mapped: UUID | None = None
        if provider_response_id is not None:
            response_mapped = self._mirror.lookup_response_generation(provider_response_id)

        item_mapped: UUID | None = None
        item_alias_mapped: UUID | None = None
        if provider_item_id is not None:
            item_mapped = self._mirror.lookup_item_generation(provider_item_id)
            if item_mapped is None:
                item_alias_mapped = self._mirror.lookup_response_generation(provider_item_id)

        known: dict[str, UUID] = {}
        if anchor is not None:
            known["generation_id"] = anchor
        if response_mapped is not None:
            known["provider_response_id"] = response_mapped
        effective_item = item_mapped if item_mapped is not None else item_alias_mapped
        if effective_item is not None:
            known["provider_item_id"] = effective_item

        provided_but_unknown = (
            (provider_response_id is not None and response_mapped is None and anchor is None)
            or (provider_item_id is not None and effective_item is None and anchor is None)
            or (generation_id is None and not known)
        )
        if not known or provided_but_unknown:
            # An unbound provider id can only be learned with an explicit
            # Runtime generation anchoring it (handled in phase 2); without
            # any anchor the event is unmapped.
            if anchor is None:
                await self._emit_unmapped_event(
                    event_name=event_name,
                    details={
                        "generation_id": str(generation_id),
                        "provider_item_id": str(provider_item_id),
                        "provider_response_id": str(provider_response_id),
                    },
                )
                return None
        distinct = set(known.values())
        if len(distinct) > 1:
            await self._emit_diagnostic(
                code="lineage_mismatch",
                message="Conflicting provider identities for one event",
                details={
                    "event": event_name,
                    **{name: str(gen) for name, gen in known.items()},
                },
            )
            return None

        # Phase 2: single settled anchor — commit brand-new mappings only.
        settled = anchor if anchor is not None else next(iter(distinct))
        if provider_response_id is not None and response_mapped is None:
            if self._mirror.bind_provider_response(provider_response_id, settled) is None:
                await self._emit_diagnostic(
                    code="lineage_mismatch",
                    message="Provider response id already bound elsewhere",
                    details={
                        "event": event_name,
                        "provider_response_id": str(provider_response_id),
                    },
                )
                return None
        if provider_item_id is not None and effective_item is None:
            if self._mirror.bind_provider_item(provider_item_id, settled) is None:
                await self._emit_diagnostic(
                    code="lineage_mismatch",
                    message="Provider item id already bound elsewhere",
                    details={
                        "event": event_name,
                        "provider_item_id": str(provider_item_id),
                    },
                )
                return None
        return settled

    async def _resolve_candidate_generation(
        self,
        *,
        event_name: str,
        candidate: RealtimeTranscriptCandidate,
        expected_role: str,
    ) -> UUID | None:
        """Strictly resolve a transcript candidate to a registered generation.

        The candidate role must match the wrapping event; identity resolution
        requires every supplied provider identity to agree.
        """
        if candidate.role != expected_role:
            await self._emit_diagnostic(
                code="lineage_mismatch",
                message="Transcript candidate role does not match event",
                details={
                    "event": event_name,
                    "candidate_role": str(candidate.role),
                    "expected_role": expected_role,
                },
            )
            return None
        return await self._resolve_consistent_identities(
            event_name=event_name,
            generation_id=candidate.generation_id,
            provider_response_id=candidate.provider_response_id,
            provider_item_id=candidate.provider_item_id,
        )

    async def _resolve_candidate_turn(self, *, event_name: str, gen_id: UUID) -> UUID | None:
        turn_id = self._mirror.get_turn_id(gen_id)
        if turn_id is None:
            await self._emit_unmapped_event(
                event_name=event_name,
                details={"generation_id": str(gen_id)},
            )
            return None
        return turn_id

    async def _resolve_admitted_utterance(
        self, *, gen_id: UUID, candidate: RealtimeTranscriptCandidate
    ) -> UUID | None:
        """Return the Runtime-admitted utterance id, validating any provider echo.

        The provider-echoed id may only confirm the admitted identity, never
        override it. Mismatches and missing admissions drop the candidate.
        """
        admitted = self._mirror.get_utterance_id(gen_id)
        if admitted is None:
            await self._emit_diagnostic(
                code="lineage_mismatch",
                message="No admitted utterance_id for generation",
                details={"generation_id": str(gen_id)},
            )
            return None
        if candidate.utterance_id is not None and candidate.utterance_id != admitted:
            await self._emit_diagnostic(
                code="lineage_mismatch",
                message="Provider utterance_id does not match admitted utterance_id",
                details={
                    "generation_id": str(gen_id),
                    "admitted_utterance_id": str(admitted),
                },
            )
            return None
        return admitted

    async def report_media_failure(self, code: str, error: Exception) -> None:
        """Fail the active generation for a media-plane operation failure.

        A failed send_audio/commit_input means the provider will never produce
        the matching completion: the generation must leave RUNNING here rather
        than stall the session. Retry/reconnect policy belongs to a later phase.
        """
        structured = StructuredError(
            code=code,
            message=f"Cloud realtime media operation failed: {error}",
            retryable=False,
            component="realtime.cloud",
            details={"session_id": str(self.session_id), "error": str(error)},
        )
        await self.terminate_active_generation(
            reason=f"{code}: {error}",
            terminal="failed",
            error=structured,
        )
        await self._domain_sink.provider_error(self.session_id, structured)
        await self._domain_sink.session_degraded(self.session_id, code)

    async def cancel_generation(
        self, generation_id: UUID, reason: str = "cancelled", *, interrupt_provider: bool = True
    ) -> None:
        """Cancel a generation, invalidating it in the mirror and interrupting the provider."""
        self._cancel_missing_ack_timer(generation_id)
        if self._media_sink is not None:
            self._media_sink.clear_generation(generation_id)
        if self._mirror.is_tombstoned(generation_id):
            return
        turn_id = self._mirror.get_turn_id(generation_id)
        self._mirror.cancel_generation(generation_id)
        if turn_id is not None:
            await self._domain_sink.response_cancelled(
                self.session_id, turn_id, generation_id, reason
            )
        if interrupt_provider:
            await self._session.interrupt(generation_id, reason)

    async def _pump_loop(self) -> None:
        try:
            async for event in self._session.events():
                await self.dispatch_event(event)
                if not self._is_running:
                    break
            await self._end_session("provider_eof")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _LOGGER.exception("Error pumping realtime events for %s: %s", self.session_id, e)
            self._is_running = False
            structured_error = StructuredError(
                code="realtime_pump_failed",
                message=f"Event pump failed: {e}",
                retryable=False,
                component="realtime.cloud",
                details={"session_id": str(self.session_id), "error": str(e)},
            )
            await self.terminate_active_generation(
                reason=f"pump_failed: {e}",
                terminal="cancelled",
                error=structured_error,
            )
            await self._domain_sink.provider_error(self.session_id, structured_error)
            await self._end_session("pump_failed")

    async def dispatch_event(self, event: RealtimeProviderEvent) -> None:
        """Dispatch a single provider event through the mirror and normalizer."""
        if self._closing:
            return
        # 0. Session fence: every event must belong to this coordinator session.
        claimed_session = self._event_session_id(event)
        if claimed_session != self.session_id:
            if isinstance(event, OutputAudioEvent):
                _LOGGER.debug(
                    "Dropping output audio frame for foreign session %s",
                    claimed_session,
                )
                return
            await self._emit_diagnostic(
                code="session_mismatch",
                message="Provider event session_id does not match coordinator session",
                details={
                    "claimed_session_id": str(claimed_session),
                    "coordinator_session_id": str(self.session_id),
                },
            )
            return

        # 1. Event Deduplication. A None key means the event carries no
        # replay identity and must always be processed (never content-deduped).
        event_key = self._compute_event_key(event)
        if event_key is not None and self._mirror.is_duplicate(event_key):
            _LOGGER.debug("Dropping duplicate realtime event: %s", event_key)
            return

        # 2. Event routing and normalization
        match event:
            case SessionReadyEvent():
                self._mirror.set_provider_session_id(event.provider_session_id)
                await self._domain_sink.session_ready(self.session_id, event.provider_session_id)

            case SessionDegradedEvent():
                await self._domain_sink.session_degraded(self.session_id, event.reason)

            case SessionClosedEvent():
                await self._end_session(event.reason)

            case InputAudioCommittedEvent():
                await self._domain_sink.input_audio_committed(self.session_id, event.turn_id)

            case ResponseStartedEvent():
                gen_id = await self._resolve_consistent_identities(
                    event_name="ResponseStartedEvent",
                    generation_id=event.generation_id,
                    provider_response_id=event.provider_response_id,
                )
                if gen_id is None:
                    return

                if event.provider_response_id:
                    bound = self._mirror.bind_provider_response(event.provider_response_id, gen_id)
                    if bound is None:
                        await self._emit_diagnostic(
                            code="lineage_mismatch",
                            message="Provider response id already bound elsewhere",
                            details={
                                "event": "ResponseStartedEvent",
                                "provider_response_id": str(event.provider_response_id),
                            },
                        )
                        return

                if self._mirror.is_tombstoned(gen_id):
                    _LOGGER.debug(
                        "Dropping late ResponseStartedEvent for tombstoned generation %s", gen_id
                    )
                    return

                turn_id = await self._resolve_candidate_turn(
                    event_name="ResponseStartedEvent", gen_id=gen_id
                )
                if turn_id is None:
                    return
                await self._domain_sink.response_started(self.session_id, turn_id, gen_id)

            case OutputAudioEvent():
                frame = event.frame
                gen_id = await self._resolve_consistent_identities(
                    event_name="OutputAudioEvent", generation_id=frame.generation_id
                )
                if (
                    gen_id is None
                    or self._mirror.is_tombstoned(gen_id)
                    or not self._mirror.is_active(gen_id)
                    or self._mirror.is_provider_response_done(gen_id)
                ):
                    _LOGGER.debug(
                        "Dropping late OutputAudioEvent for inactive, done, "
                        "or tombstoned generation %s",
                        gen_id,
                    )
                    return

                if frame.audio:
                    self._mirror.mark_has_audio(gen_id)
                if self._media_sink is not None:
                    if frame.generation_id != gen_id:
                        frame = RealtimeOutputAudioFrame(
                            session_id=frame.session_id,
                            generation_id=gen_id,
                            sequence=frame.sequence,
                            pts_ms=frame.pts_ms,
                            sample_rate=frame.sample_rate,
                            channels=frame.channels,
                            audio=frame.audio,
                            is_final=frame.is_final,
                        )
                    await self._media_sink.handle_audio_frame(frame)

            case AssistantTranscriptEvent():
                candidate = event.candidate
                gen_id = await self._resolve_candidate_generation(
                    event_name="AssistantTranscriptEvent",
                    candidate=candidate,
                    expected_role="assistant",
                )
                if gen_id is None:
                    return

                if (
                    self._mirror.is_tombstoned(gen_id)
                    or not self._mirror.is_active(gen_id)
                    or self._mirror.is_provider_response_done(gen_id)
                ):
                    _LOGGER.debug(
                        "Dropping late AssistantTranscriptEvent for generation %s",
                        gen_id,
                    )
                    return

                turn_id = await self._resolve_candidate_turn(
                    event_name="AssistantTranscriptEvent", gen_id=gen_id
                )
                if turn_id is None:
                    return

                if candidate.phase == "delta":
                    self._mirror.append_text(gen_id, candidate.text)
                    await self._domain_sink.transcript_delta(
                        self.session_id,
                        turn_id,
                        gen_id,
                        candidate.text,
                        role="assistant",
                        utterance_id=candidate.utterance_id,
                    )
                elif candidate.phase == "final":
                    self._mirror.set_authoritative_final_text(gen_id, candidate.text)
                    await self._domain_sink.transcript_final(
                        self.session_id,
                        turn_id,
                        gen_id,
                        candidate.text,
                        "assistant",
                        utterance_id=candidate.utterance_id,
                    )

            case UserTranscriptEvent():
                candidate = event.candidate
                gen_id = await self._resolve_candidate_generation(
                    event_name="UserTranscriptEvent",
                    candidate=candidate,
                    expected_role="user",
                )
                if gen_id is None:
                    return
                turn_id = await self._resolve_candidate_turn(
                    event_name="UserTranscriptEvent", gen_id=gen_id
                )
                if turn_id is None:
                    return
                # The Runtime-admitted utterance is authoritative; a provider
                # echo may only confirm it, never override it.
                utterance_id = await self._resolve_admitted_utterance(
                    gen_id=gen_id, candidate=candidate
                )
                if utterance_id is None:
                    return

                if candidate.phase == "delta":
                    await self._domain_sink.transcript_delta(
                        self.session_id,
                        turn_id,
                        gen_id,
                        candidate.text,
                        role="user",
                        utterance_id=utterance_id,
                    )
                elif candidate.phase == "final":
                    await self._domain_sink.transcript_final(
                        self.session_id,
                        turn_id,
                        gen_id,
                        candidate.text,
                        "user",
                        utterance_id=utterance_id,
                    )

            case ResponseCompletedEvent():
                gen_id = await self._resolve_consistent_identities(
                    event_name="ResponseCompletedEvent",
                    generation_id=event.generation_id,
                    provider_response_id=event.provider_response_id,
                )
                if (
                    gen_id is None
                    or self._mirror.is_tombstoned(gen_id)
                    or not self._mirror.is_active(gen_id)
                ):
                    if gen_id is not None:
                        _LOGGER.debug(
                            "Dropping late ResponseCompletedEvent for generation %s",
                            gen_id,
                        )
                    return

                turn_id = await self._resolve_candidate_turn(
                    event_name="ResponseCompletedEvent", gen_id=gen_id
                )
                if turn_id is None:
                    return

                text = self._mirror.get_completed_text(gen_id, event.final_text)
                has_audio = self._mirror.has_audio(gen_id)
                if not has_audio:
                    self._mirror.complete_generation(gen_id)
                    await self._domain_sink.response_completed(
                        self.session_id, turn_id, gen_id, text, has_audio=False
                    )
                else:
                    self._mirror.mark_provider_response_done(gen_id)
                    duration_ms: int | None = None
                    if self._media_sink is not None:
                        duration_ms = await self._media_sink.finalize_response_audio(gen_id)
                    await self._domain_sink.response_completed(
                        self.session_id, turn_id, gen_id, text, has_audio=True
                    )
                    if (
                        not self._closing
                        and not self._mirror.is_tombstoned(gen_id)
                        and self._mirror.is_active(gen_id)
                    ):
                        self._start_missing_ack_timer(gen_id, turn_id, duration_ms=duration_ms)
                if event.usage is not None:
                    await self._domain_sink.usage_recorded(
                        self.session_id, turn_id, gen_id, event.usage
                    )

            case ResponseCancelledEvent():
                gen_id = await self._resolve_consistent_identities(
                    event_name="ResponseCancelledEvent",
                    generation_id=event.generation_id,
                    provider_response_id=event.provider_response_id,
                )
                if gen_id is None:
                    return
                was_tombstoned = self._mirror.is_tombstoned(gen_id)
                turn_id = await self._resolve_candidate_turn(
                    event_name="ResponseCancelledEvent", gen_id=gen_id
                )
                self._mirror.cancel_generation(gen_id)
                if not was_tombstoned and turn_id is not None:
                    await self._domain_sink.response_cancelled(
                        self.session_id, turn_id, gen_id, event.reason
                    )

            case UsageRecordedEvent():
                if event.usage.generation_id is None:
                    # Session-level usage carries no generation identity and
                    # must never be attributed to the last generation.
                    return
                gen_id = await self._resolve_consistent_identities(
                    event_name="UsageRecordedEvent",
                    generation_id=event.usage.generation_id,
                )
                if gen_id is None:
                    return
                turn_id = await self._resolve_candidate_turn(
                    event_name="UsageRecordedEvent", gen_id=gen_id
                )
                if turn_id is None:
                    return
                await self._domain_sink.usage_recorded(
                    self.session_id, turn_id, gen_id, event.usage
                )

            case ProviderErrorEvent():
                error = event.error
                if error.generation_id is not None:
                    # Generation-scoped errors fail only their own registered
                    # generation. Late, tombstoned, or unknown errors must
                    # never fall through to the current active generation.
                    gen_id = await self._resolve_consistent_identities(
                        event_name="ProviderErrorEvent",
                        generation_id=error.generation_id,
                    )
                    if gen_id is None or self._mirror.is_tombstoned(gen_id):
                        if gen_id is not None:
                            await self._emit_unmapped_event(
                                event_name="ProviderErrorEvent",
                                details={
                                    "generation_id": str(error.generation_id),
                                    "provider_code": error.code,
                                    "reason": "tombstoned",
                                },
                            )
                        return
                    turn_id = await self._resolve_candidate_turn(
                        event_name="ProviderErrorEvent", gen_id=gen_id
                    )
                    if turn_id is None:
                        return
                    self._mirror.cancel_generation(gen_id)
                    await self._domain_sink.response_failed(
                        self.session_id,
                        turn_id,
                        gen_id,
                        f"provider_error.{error.code}: {error.message}",
                    )
                    return
                structured_error = StructuredError(
                    code=f"provider_error.{error.code}",
                    message=error.message,
                    retryable=error.retryable,
                    component="realtime.cloud",
                    details={
                        "provider_code": error.code,
                        "backend_id": error.backend_id,
                        **error.details,
                    },
                )
                await self._domain_sink.provider_error(self.session_id, structured_error)
                await self._domain_sink.session_degraded(
                    self.session_id, f"provider_error.{error.code}"
                )

    def _compute_event_key(self, event: RealtimeProviderEvent) -> str | None:
        if event.event_id:
            return f"id:{event.event_id}"
        match event:
            case SessionReadyEvent():
                return f"ready:{event.session_id}:{event.provider_session_id}"
            case SessionClosedEvent():
                return f"closed:{event.session_id}:{event.reason}"
            case SessionDegradedEvent():
                return f"degraded:{event.session_id}:{event.reason}"
            case InputAudioCommittedEvent():
                return f"commit:{event.session_id}:{event.turn_id}"
            case UserTranscriptEvent():
                c = event.candidate
                if c.phase == "final":
                    return (
                        f"usr_fin:{c.session_id}:{c.generation_id}:{c.provider_item_id}:"
                        f"{c.provider_response_id}:{c.revision}:{c.text}"
                    )
                if c.provider_sequence is not None:
                    return (
                        f"usr_dseq:{c.session_id}:{c.generation_id}:{c.provider_item_id}:"
                        f"{c.provider_response_id}:{c.provider_sequence}"
                    )
                return (
                    f"usr_dlt:{c.session_id}:{c.generation_id}:{c.provider_item_id}:"
                    f"{c.provider_response_id}:{c.revision}:{c.text}"
                )
            case ResponseStartedEvent():
                return f"resp_start:{event.generation_id}:{event.provider_response_id}"
            case OutputAudioEvent():
                f = event.frame
                return f"audio:{f.session_id}:{f.generation_id}:{f.sequence}"
            case AssistantTranscriptEvent():
                c = event.candidate
                if c.phase == "final":
                    return (
                        f"ast_fin:{c.session_id}:{c.generation_id}:{c.provider_item_id}:"
                        f"{c.provider_response_id}:{c.revision}:{c.text}"
                    )
                if c.provider_sequence is not None:
                    return (
                        f"ast_dseq:{c.session_id}:{c.generation_id}:{c.provider_item_id}:"
                        f"{c.provider_response_id}:{c.provider_sequence}"
                    )
                # Streaming deltas without replay identity must never be
                # content-deduped: identical consecutive fragments ("哈","哈")
                # are legitimate stream output, not replays.
                return None
            case ResponseCompletedEvent():
                return f"resp_comp:{event.generation_id}:{event.provider_response_id}"
            case ResponseCancelledEvent():
                return f"resp_canc:{event.session_id}:{event.generation_id}:{event.reason}"
            case UsageRecordedEvent():
                u = event.usage
                return (
                    f"usage:{u.session_id}:{u.generation_id}:"
                    f"{u.total_tokens}:{u.input_tokens}:{u.output_tokens}"
                )
            case ProviderErrorEvent():
                e = event.error
                return f"error:{e.session_id}:{e.backend_id}:{e.generation_id}:{e.code}:{e.message}"
