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


class TtsProvider(Protocol):
    @property
    def kind(self) -> str: ...

    async def synthesize(self, text: str, destination: Path) -> SynthesisResult: ...
