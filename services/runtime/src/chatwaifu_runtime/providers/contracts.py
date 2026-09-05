"""Provider-neutral streaming and synthesis contracts."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from chatwaifu_protocol.base import JsonObject, JsonValue

MAX_LLM_IMAGE_BYTES = 5 * 1024 * 1024


class LlmToolCallingUnavailableError(RuntimeError):
    """The selected provider cannot honor a requested structured tool round."""


class LlmImageInputUnavailableError(RuntimeError):
    """The selected provider cannot honor an image input."""


@dataclass(frozen=True, slots=True)
class LlmInputImage:
    """One provider-neutral raster image input attached to a turn."""

    data: bytes = field(repr=False)
    mime_type: Literal["image/png", "image/jpeg"]

    def __post_init__(self) -> None:
        if self.mime_type not in ("image/png", "image/jpeg"):
            raise ValueError(f"unsupported image mime type: {self.mime_type}")
        if type(self.data) is not bytes or not self.data:
            raise ValueError("image data must be non-empty bytes")
        if len(self.data) > MAX_LLM_IMAGE_BYTES:
            raise ValueError(
                f"image size {len(self.data)} exceeds maximum allowed of "
                f"{MAX_LLM_IMAGE_BYTES} bytes"
            )


@dataclass(frozen=True, slots=True)
class LlmToolDefinition:
    """One provider-neutral function exposed to a reasoning backend."""

    name: str
    description: str
    input_schema: JsonObject


@dataclass(frozen=True, slots=True)
class LlmToolCall:
    """A complete, validated tool request assembled by a provider adapter."""

    call_id: str
    name: str
    arguments: JsonObject


@dataclass(frozen=True, slots=True)
class LlmToolResult:
    """Bounded Runtime Skill result returned to the reasoning backend."""

    call_id: str
    name: str
    content: JsonValue
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class LlmToolExchange:
    """One assistant tool-request message and all of its tool results."""

    assistant_text: str
    calls: tuple[LlmToolCall, ...]
    results: tuple[LlmToolResult, ...]


@dataclass(frozen=True, slots=True)
class LlmTextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class LlmToolCallRequested:
    call: LlmToolCall


type LlmFinishReason = Literal["stop", "tool_calls", "length", "content_filter", "other"]


@dataclass(frozen=True, slots=True)
class LlmResponseCompleted:
    finish_reason: LlmFinishReason


type LlmStreamEvent = LlmTextDelta | LlmToolCallRequested | LlmResponseCompleted


@dataclass(frozen=True, slots=True)
class LlmRequest:
    generation_id: UUID
    user_text: str
    system_prompt: str
    character_name: str = "ChatWaifu"
    context: tuple[tuple[str, str], ...] = ()
    history: tuple[tuple[str, str], ...] = ()
    recalled_memory_texts: tuple[str, ...] = ()
    trigger: Literal["user", "proactive"] = "user"
    tools: tuple[LlmToolDefinition, ...] = ()
    tool_exchanges: tuple[LlmToolExchange, ...] = ()
    images: tuple[LlmInputImage, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if len(self.images) > 1:
            raise ValueError("at most one image is currently supported")
        for image in self.images:
            if not isinstance(image, LlmInputImage):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise TypeError("images must contain only LlmInputImage instances")


class LlmProvider(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def supports_tool_calling(self) -> bool: ...

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]: ...


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    path: Path
    media_type: str
    sample_rate: int
    duration_ms: int
    provider_id: str
    model: str


@dataclass(frozen=True, slots=True)
class TtsPcmChunk:
    """One ordered provider-neutral PCM16 fragment."""

    sequence: int
    pcm16: bytes
    sample_rate: int
    channels: int = 1
    native_streaming: bool = False


@dataclass(frozen=True, slots=True)
class TtsStreamCompleted:
    result: SynthesisResult


type TtsStreamEvent = TtsPcmChunk | TtsStreamCompleted


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    segment_id: UUID
    text: str
    destination: Path
    language: str
    voice_id: str
    speaker_id: int
    speed: float
    style: str | None = None
    pitch: float | None = None


@dataclass(frozen=True, slots=True)
class TtsProviderDescriptor:
    provider_id: str
    display_name: str
    model: str
    languages: tuple[str, ...]
    supports_voice_cloning: bool
    supports_style: bool
    supports_speed: bool
    supports_pitch: bool
    native_streaming: bool
    local_only: bool = True


@dataclass(frozen=True, slots=True)
class TtsProviderHealth:
    status: Literal["ready", "busy", "starting", "degraded", "unavailable"]
    model_loaded: bool
    queue_depth: int = 0
    device: str | None = None
    detail: str | None = None


class TtsProvider(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def descriptor(self) -> TtsProviderDescriptor: ...

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult: ...

    async def health(self) -> TtsProviderHealth: ...

    async def deactivate(self) -> None: ...

    async def close(self) -> None: ...
