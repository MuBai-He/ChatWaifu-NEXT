"""ChatWaifu-owned realtime and speech backend contracts."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class VoiceTurnIdentity:
    session_id: UUID
    utterance_id: UUID
    audio_stream_id: UUID
    turn_id: UUID
    generation_id: UUID


@dataclass(frozen=True, slots=True)
class SttRequest:
    identity: VoiceTurnIdentity
    audio: bytes
    sample_rate: int
    channels: int
    language: str | None = None


@dataclass(frozen=True, slots=True)
class SttResult:
    text: str
    language: str | None
    provider: str


class SttBackend(Protocol):
    kind: str

    async def transcribe(self, request: SttRequest) -> SttResult | None: ...

    async def cancel(self, generation_id: UUID) -> None: ...

    async def close(self) -> None: ...
