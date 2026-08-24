"""Provider-neutral streaming and synthesis contracts."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LlmRequest:
    generation_id: UUID
    user_text: str
    system_prompt: str
    character_name: str = "ChatWaifu"
    context: tuple[tuple[str, str], ...] = ()
    history: tuple[tuple[str, str], ...] = ()


class LlmProvider(Protocol):
    @property
    def kind(self) -> str: ...

    def stream(self, request: LlmRequest) -> AsyncIterator[str]: ...


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    path: Path
    media_type: str
    sample_rate: int
    duration_ms: int
    provider_id: str
    model: str


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
