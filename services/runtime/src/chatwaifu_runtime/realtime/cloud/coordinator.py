"""Cloud realtime coordinator and normalized provider event router.

Bridges CloudRealtimeSession events into ChatWaifu-owned domain and media sinks,
enforcing deduplication, lineage resolution, generation tombstones, and error
normalization without leaking provider-specific payloads or SDK types into domain code.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.errors import StructuredError

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

_LOGGER = logging.getLogger(__name__)


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
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, text: str
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
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, text: str
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

    def admit_turn(
        self, turn_id: UUID, generation_id: UUID, utterance_id: UUID | None = None
    ) -> None:
        """Register turn and generation in mirror when admitted by runtime."""
        self._mirror.register_generation(generation_id, turn_id, utterance_id=utterance_id)

    def start(self) -> None:
        """Start background event pump task."""
        if self._is_running:
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

    async def stop(self) -> None:
        """Stop background event pump and close underlying session."""
        await self.terminate_active_generation(reason="coordinator_stopped", terminal="cancelled")
        self._is_running = False
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            self._pump_task = None
        try:
            await self._session.close()
        except Exception:
            _LOGGER.warning("Error closing session in coordinator.stop()", exc_info=True)
        await self._domain_sink.session_closed(self.session_id, "coordinator_stopped")

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

        Each provided identity must independently point at a registered
        binding; conflicting identities are dropped with a lineage_mismatch
        instead of letting priority order silently reroute the event. A
        brand-new item id is learned only when an explicit Runtime generation
        anchors it. Never guesses the active or last generation.
        """
        resolved: dict[str, UUID] = {}
        if generation_id is not None:
            if not self._mirror.has_binding(generation_id):
                await self._emit_unmapped_event(
                    event_name=event_name,
                    details={"generation_id": str(generation_id)},
                )
                return None
            resolved["generation_id"] = generation_id
        if provider_response_id is not None:
            mapped_response = self._mirror.lookup_response_generation(provider_response_id)
            if mapped_response is None:
                anchor = resolved.get("generation_id")
                if anchor is None:
                    await self._emit_unmapped_event(
                        event_name=event_name,
                        details={"provider_response_id": str(provider_response_id)},
                    )
                    return None
                # First sighting anchored by Runtime identity: learn it, unless
                # the id is already owned elsewhere (immutable bindings).
                if self._mirror.bind_provider_response(provider_response_id, anchor) is None:
                    await self._emit_diagnostic(
                        code="lineage_mismatch",
                        message="Provider response id already bound elsewhere",
                        details={
                            "event": event_name,
                            "provider_response_id": str(provider_response_id),
                        },
                    )
                    return None
                mapped_response = anchor
            resolved["provider_response_id"] = mapped_response
        if provider_item_id is not None:
            mapped_item = self._mirror.lookup_item_generation(provider_item_id)
            if mapped_item is None:
                mapped_item = self._mirror.lookup_response_generation(provider_item_id)
            if mapped_item is None:
                anchor = resolved.get("generation_id")
                if anchor is None:
                    await self._emit_unmapped_event(
                        event_name=event_name,
                        details={"provider_item_id": str(provider_item_id)},
                    )
                    return None
                if self._mirror.bind_provider_item(provider_item_id, anchor) is None:
                    await self._emit_diagnostic(
                        code="lineage_mismatch",
                        message="Provider item id already bound elsewhere",
                        details={
                            "event": event_name,
                            "provider_item_id": str(provider_item_id),
                        },
                    )
                    return None
                mapped_item = anchor
            resolved["provider_item_id"] = mapped_item
        if not resolved:
            await self._emit_unmapped_event(event_name=event_name, details={})
            return None
        distinct = set(resolved.values())
        if len(distinct) > 1:
            await self._emit_diagnostic(
                code="lineage_mismatch",
                message="Conflicting provider identities for one event",
                details={
                    "event": event_name,
                    **{name: str(gen) for name, gen in resolved.items()},
                },
            )
            return None
        return next(iter(distinct))

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

    async def cancel_generation(self, generation_id: UUID, reason: str = "cancelled") -> None:
        """Cancel a generation, invalidating it in the mirror and interrupting the provider."""
        turn_id = self._mirror.get_turn_id(generation_id)
        self._mirror.cancel_generation(generation_id)
        if turn_id is not None:
            await self._domain_sink.response_cancelled(
                self.session_id, turn_id, generation_id, reason
            )
        await self._session.interrupt(generation_id, reason)

    async def _pump_loop(self) -> None:
        try:
            async for event in self._session.events():
                await self.dispatch_event(event)
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
            await self._domain_sink.session_closed(self.session_id, "pump_failed")
            try:
                await self._session.close()
            except Exception:
                pass

    async def dispatch_event(self, event: RealtimeProviderEvent) -> None:
        """Dispatch a single provider event through the mirror and normalizer."""
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
                self._is_running = False
                await self.terminate_active_generation(
                    reason=f"session_closed: {event.reason}",
                    terminal="cancelled",
                )
                await self._domain_sink.session_closed(self.session_id, event.reason)

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
                ):
                    _LOGGER.debug(
                        "Dropping late OutputAudioEvent for inactive or tombstoned generation %s",
                        gen_id,
                    )
                    return

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

                if self._mirror.is_tombstoned(gen_id) or not self._mirror.is_active(gen_id):
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
                self._mirror.complete_generation(gen_id)
                await self._domain_sink.response_completed(self.session_id, turn_id, gen_id, text)
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
