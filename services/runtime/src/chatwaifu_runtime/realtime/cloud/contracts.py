"""Provider-neutral cloud realtime contracts and interfaces.

This module defines typed abstractions for cloud speech-to-speech providers
(such as OpenAI Realtime or Gemini Live) without leaking any provider SDK
types, proprietary event formats, or network implementations into domain code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RealtimeCapabilities:
    """Capabilities exposed by a cloud realtime backend."""

    backend_id: str
    supported_input_modalities: tuple[str, ...] = ("audio", "text")
    supported_output_modalities: tuple[str, ...] = ("audio", "text")
    input_sample_rate: int = 16_000
    output_sample_rate: int = 24_000
    input_channels: int = 1
    output_channels: int = 1
    supported_codecs: tuple[str, ...] = ("pcm16",)
    supports_user_transcript: bool = True
    supports_assistant_transcript: bool = True
    supports_server_vad: bool = True
    supports_interrupt: bool = True
    supports_context_update: bool = True
    supports_tool_call: bool = False
    max_session_duration_seconds: int | None = None
    version: str = "1.0"


@dataclass(frozen=True, slots=True)
class RealtimeContextComponent:
    """A granular budgeted segment of context sent to a cloud session."""

    kind: str  # "persona", "relationship", "affect", "memory", "skills", "safety"
    text: str
    byte_count: int
    estimated_tokens: int
    priority: int = 0  # Lower number = higher priority for retention
    metadata: dict[str, str | int | float | bool] = field(
        default_factory=dict[str, str | int | float | bool]
    )


@dataclass(frozen=True, slots=True)
class RealtimeContextPatch:
    """An immutable, budgeted context patch applied to a cloud realtime session."""

    patch_id: UUID
    components: tuple[RealtimeContextComponent, ...]
    content_hash: str
    total_bytes: int
    estimated_tokens: int
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class RealtimeSessionOpenRequest:
    """Runtime-owned request to initiate a provider realtime session."""

    session_id: UUID
    character_id: str
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    initial_context: RealtimeContextPatch | None = None
    voice_id: str | None = None
    model: str | None = None
    sample_rate: int = 24_000
    channels: int = 1


@dataclass(frozen=True, slots=True)
class RealtimeSessionLineage:
    """Mapping between Runtime domain identity and opaque provider identity."""

    session_id: UUID
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    backend_id: str = ""
    provider_session_id: str = ""
    provider_response_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    revision: int = 0


@dataclass(frozen=True, slots=True)
class RealtimeInputAudioFrame:
    """Audio frame routed from local media plane to cloud provider."""

    session_id: UUID
    generation_id: UUID | None
    sequence: int
    pts_ms: int
    sample_rate: int
    channels: int
    audio: bytes
    is_final: bool = False


@dataclass(frozen=True, slots=True)
class RealtimeOutputAudioFrame:
    """Audio frame emitted by cloud provider destined for media playback."""

    session_id: UUID
    generation_id: UUID
    sequence: int
    pts_ms: int
    sample_rate: int
    channels: int
    audio: bytes
    is_final: bool = False


@dataclass(frozen=True, slots=True)
class RealtimeTranscriptCandidate:
    """Incremental or committed speech transcript from a realtime session."""

    session_id: UUID
    generation_id: UUID | None
    role: Literal["user", "assistant"]
    phase: Literal["delta", "final"]
    text: str
    source: str = "provider"
    provider_item_id: str | None = None
    revision: int = 0
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class RealtimeUsage:
    """Resource consumption report for a cloud realtime generation/session."""

    session_id: UUID
    backend_id: str
    generation_id: UUID | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    input_audio_seconds: float = 0.0
    output_audio_seconds: float = 0.0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class RealtimeProviderError:
    """Normalized provider-side error."""

    session_id: UUID
    backend_id: str
    code: str
    message: str
    generation_id: UUID | None = None
    retryable: bool = False
    details: dict[str, str] = field(default_factory=dict[str, str])


# --- Normalized Provider Events ---


@dataclass(frozen=True, slots=True)
class SessionReadyEvent:
    session_id: UUID
    provider_session_id: str
    backend_id: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class SessionClosedEvent:
    session_id: UUID
    backend_id: str
    reason: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class SessionDegradedEvent:
    session_id: UUID
    backend_id: str
    reason: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class InputAudioCommittedEvent:
    session_id: UUID
    turn_id: UUID | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class UserTranscriptEvent:
    candidate: RealtimeTranscriptCandidate
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResponseStartedEvent:
    session_id: UUID
    generation_id: UUID
    provider_response_id: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class OutputAudioEvent:
    frame: RealtimeOutputAudioFrame
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class AssistantTranscriptEvent:
    candidate: RealtimeTranscriptCandidate
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResponseCompletedEvent:
    session_id: UUID
    generation_id: UUID
    provider_response_id: str
    usage: RealtimeUsage | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResponseCancelledEvent:
    session_id: UUID
    generation_id: UUID
    provider_response_id: str | None = None
    reason: str = "cancelled"
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class UsageRecordedEvent:
    usage: RealtimeUsage
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderErrorEvent:
    error: RealtimeProviderError
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None


type RealtimeProviderEvent = (
    SessionReadyEvent
    | SessionClosedEvent
    | SessionDegradedEvent
    | InputAudioCommittedEvent
    | UserTranscriptEvent
    | ResponseStartedEvent
    | OutputAudioEvent
    | AssistantTranscriptEvent
    | ResponseCompletedEvent
    | ResponseCancelledEvent
    | UsageRecordedEvent
    | ProviderErrorEvent
)


class CloudRealtimeSession(Protocol):
    """Active speech-to-speech session with a cloud provider."""

    session_id: UUID
    lineage: RealtimeSessionLineage

    async def send_audio(self, frame: RealtimeInputAudioFrame) -> None: ...

    async def commit_input(self) -> None: ...

    async def update_context(self, patch: RealtimeContextPatch) -> None: ...

    async def interrupt(self, generation_id: UUID, reason: str = "user_barge_in") -> None: ...

    async def receive(self) -> RealtimeProviderEvent: ...

    def events(self) -> AsyncIterator[RealtimeProviderEvent]: ...

    async def submit_tool_result(self, call_id: str, output: str) -> None: ...

    async def close(self) -> None: ...


class CloudRealtimeBackend(Protocol):
    """Factory and lifecycle manager for cloud realtime sessions."""

    backend_id: str

    async def capabilities(self) -> RealtimeCapabilities: ...

    async def open_session(
        self,
        request: RealtimeSessionOpenRequest,
    ) -> CloudRealtimeSession: ...

    async def close(self) -> None: ...
