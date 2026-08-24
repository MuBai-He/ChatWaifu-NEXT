"""Provider-neutral streaming and synthesis contracts."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
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


class TtsProvider(Protocol):
    @property
    def kind(self) -> str: ...

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult: ...

    async def close(self) -> None: ...
