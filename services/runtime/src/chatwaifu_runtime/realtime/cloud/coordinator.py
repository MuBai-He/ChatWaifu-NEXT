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
from uuid import UUID, uuid4

from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    CloudRealtimeSession,
    InputAudioCommittedEvent,
    OutputAudioEvent,
    ProviderErrorEvent,
    RealtimeOutputAudioFrame,
    RealtimeProviderEvent,
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
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, text: str
    ) -> None: ...

    async def transcript_final(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        role: Literal["user", "assistant"],
    ) -> None: ...

    async def response_completed(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, text: str
    ) -> None: ...

    async def response_cancelled(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, reason: str
    ) -> None: ...

    async def usage_recorded(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, usage: RealtimeUsage
    ) -> None: ...

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
    transcript_finals: list[tuple[UUID, UUID, UUID, str, Literal["user", "assistant"]]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, str, Literal["user", "assistant"]]]()
    )
    responses_completed: list[tuple[UUID, UUID, UUID, str]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, str]]()
    )
    responses_cancelled: list[tuple[UUID, UUID, UUID, str]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, str]]()
    )
    usages_recorded: list[tuple[UUID, UUID, UUID, RealtimeUsage]] = field(
        default_factory=lambda: list[tuple[UUID, UUID, UUID, RealtimeUsage]]()
    )
    provider_errors: list[tuple[UUID, StructuredError]] = field(
        default_factory=lambda: list[tuple[UUID, StructuredError]]()
    )

    async def response_started(self, session_id: UUID, turn_id: UUID, generation_id: UUID) -> None:
        self.responses_started.append((session_id, turn_id, generation_id))

    async def transcript_delta(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, text: str
    ) -> None:
        self.transcript_deltas.append((session_id, turn_id, generation_id, text))

    async def transcript_final(
        self,
        session_id: UUID,
        turn_id: UUID,
        generation_id: UUID,
        text: str,
        role: Literal["user", "assistant"],
    ) -> None:
        self.transcript_finals.append((session_id, turn_id, generation_id, text, role))

    async def response_completed(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, text: str
    ) -> None:
        self.responses_completed.append((session_id, turn_id, generation_id, text))

    async def response_cancelled(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, reason: str
    ) -> None:
        self.responses_cancelled.append((session_id, turn_id, generation_id, reason))

    async def usage_recorded(
        self, session_id: UUID, turn_id: UUID, generation_id: UUID, usage: RealtimeUsage
    ) -> None:
        self.usages_recorded.append((session_id, turn_id, generation_id, usage))

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
    def mirror(self) -> RealtimeSessionMirror:
        return self._mirror

    @property
    def session(self) -> CloudRealtimeSession:
        return self._session

    def start(self) -> None:
        """Start background event pump task."""
        if self._is_running:
            return
        self._is_running = True
        self._pump_task = asyncio.create_task(
            self._pump_loop(),
            name=f"cloud-realtime-pump-{str(self.session_id)[:8]}",
        )

    async def stop(self) -> None:
        """Stop background event pump and close underlying session."""
        self._is_running = False
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            self._pump_task = None
        await self._session.close()

    async def cancel_generation(self, generation_id: UUID, reason: str = "cancelled") -> None:
        """Cancel a generation, invalidating it in the mirror and interrupting the provider."""
        turn_id = self._mirror.get_turn_id(generation_id) or uuid4()
        self._mirror.cancel_generation(generation_id)
        await self._domain_sink.response_cancelled(self.session_id, turn_id, generation_id, reason)
        await self._session.interrupt(generation_id, reason)

    async def _pump_loop(self) -> None:
        try:
            async for event in self._session.events():
                await self.dispatch_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Error while pumping cloud realtime events for %s", self.session_id)

    async def dispatch_event(self, event: RealtimeProviderEvent) -> None:
        """Dispatch a single provider event through the mirror and normalizer."""
        # 1. Event Deduplication
        event_key = self._compute_event_key(event)
        if self._mirror.is_duplicate(event_key):
            _LOGGER.debug("Dropping duplicate realtime event: %s", event_key)
            return

        # 2. Event routing and normalization
        match event:
            case SessionReadyEvent():
                self._mirror.set_provider_session_id(event.provider_session_id)

            case SessionClosedEvent() | SessionDegradedEvent():
                pass

            case InputAudioCommittedEvent():
                pass

            case ResponseStartedEvent():
                gen_id = self._mirror.resolve_generation_id(
                    generation_id=event.generation_id,
                    provider_response_id=event.provider_response_id,
                )
                if gen_id is None:
                    gen_id = event.generation_id

                if self._mirror.is_tombstoned(gen_id):
                    _LOGGER.debug(
                        "Dropping late ResponseStartedEvent for tombstoned generation %s", gen_id
                    )
                    return

                self._mirror.bind_provider_response(event.provider_response_id, gen_id)
                turn_id = self._mirror.get_turn_id(gen_id) or uuid4()
                await self._domain_sink.response_started(self.session_id, turn_id, gen_id)

            case OutputAudioEvent():
                frame = event.frame
                gen_id = self._mirror.resolve_generation_id(generation_id=frame.generation_id)
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
                    await self._media_sink.handle_audio_frame(frame)

            case AssistantTranscriptEvent():
                candidate = event.candidate
                gen_id = self._mirror.resolve_generation_id(
                    generation_id=candidate.generation_id,
                    provider_response_id=candidate.provider_item_id,
                )
                if (
                    gen_id is None
                    or self._mirror.is_tombstoned(gen_id)
                    or not self._mirror.is_active(gen_id)
                ):
                    _LOGGER.debug(
                        "Dropping late AssistantTranscriptEvent for generation %s",
                        gen_id,
                    )
                    return

                turn_id = self._mirror.get_turn_id(gen_id) or uuid4()
                if candidate.phase == "delta":
                    self._mirror.append_text(gen_id, candidate.text)
                    await self._domain_sink.transcript_delta(
                        self.session_id, turn_id, gen_id, candidate.text
                    )
                elif candidate.phase == "final":
                    await self._domain_sink.transcript_final(
                        self.session_id, turn_id, gen_id, candidate.text, "assistant"
                    )

            case UserTranscriptEvent():
                candidate = event.candidate
                turn_id = self._mirror.active_turn_id or uuid4()
                gen_id = candidate.generation_id or self._mirror.active_generation_id or uuid4()
                if candidate.phase == "delta":
                    await self._domain_sink.transcript_delta(
                        self.session_id, turn_id, gen_id, candidate.text
                    )
                elif candidate.phase == "final":
                    await self._domain_sink.transcript_final(
                        self.session_id, turn_id, gen_id, candidate.text, "user"
                    )

            case ResponseCompletedEvent():
                gen_id = self._mirror.resolve_generation_id(
                    generation_id=event.generation_id,
                    provider_response_id=event.provider_response_id,
                )
                if (
                    gen_id is None
                    or self._mirror.is_tombstoned(gen_id)
                    or not self._mirror.is_active(gen_id)
                ):
                    _LOGGER.debug(
                        "Dropping late ResponseCompletedEvent for generation %s",
                        gen_id,
                    )
                    return

                text = self._mirror.get_accumulated_text(gen_id)
                turn_id = self._mirror.get_turn_id(gen_id) or uuid4()
                self._mirror.complete_generation(gen_id)
                await self._domain_sink.response_completed(self.session_id, turn_id, gen_id, text)
                if event.usage is not None:
                    await self._domain_sink.usage_recorded(
                        self.session_id, turn_id, gen_id, event.usage
                    )

            case ResponseCancelledEvent():
                gen_id = self._mirror.resolve_generation_id(
                    generation_id=event.generation_id,
                    provider_response_id=event.provider_response_id,
                )
                if gen_id is not None:
                    was_tombstoned = self._mirror.is_tombstoned(gen_id)
                    turn_id = self._mirror.get_turn_id(gen_id) or uuid4()
                    self._mirror.cancel_generation(gen_id)
                    if not was_tombstoned:
                        await self._domain_sink.response_cancelled(
                            self.session_id, turn_id, gen_id, event.reason
                        )

            case UsageRecordedEvent():
                gen_id = self._mirror.active_generation_id or uuid4()
                turn_id = self._mirror.get_turn_id(gen_id) or uuid4()
                await self._domain_sink.usage_recorded(
                    self.session_id, turn_id, gen_id, event.usage
                )

            case ProviderErrorEvent():
                structured_error = StructuredError(
                    code=f"provider_error.{event.error.code}",
                    message=event.error.message,
                    retryable=event.error.retryable,
                    component="realtime.cloud",
                    details={
                        "provider_code": event.error.code,
                        "backend_id": event.error.backend_id,
                        **event.error.details,
                    },
                )
                await self._domain_sink.provider_error(self.session_id, structured_error)

    def _compute_event_key(self, event: RealtimeProviderEvent) -> str:
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
                return f"usr_tx:{c.session_id}:{c.phase}:{c.revision}:{c.text}"
            case ResponseStartedEvent():
                return f"resp_start:{event.generation_id}:{event.provider_response_id}"
            case OutputAudioEvent():
                f = event.frame
                return f"audio:{f.session_id}:{f.generation_id}:{f.sequence}"
            case AssistantTranscriptEvent():
                c = event.candidate
                return f"ast_tx:{c.generation_id}:{c.phase}:{c.revision}:{c.text}"
            case ResponseCompletedEvent():
                return f"resp_comp:{event.generation_id}:{event.provider_response_id}"
            case ResponseCancelledEvent():
                return f"resp_canc:{event.session_id}:{event.generation_id}:{event.reason}"
            case UsageRecordedEvent():
                u = event.usage
                return f"usage:{u.total_tokens}:{u.input_tokens}:{u.output_tokens}"
            case ProviderErrorEvent():
                e = event.error
                return f"error:{e.code}:{e.message}"
