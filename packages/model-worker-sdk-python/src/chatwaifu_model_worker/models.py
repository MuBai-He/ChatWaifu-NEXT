"""Typed v1 DTOs shared by Runtime clients and isolated workers."""

import base64
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

WORKER_SCHEMA_VERSION = "1.0"


class WorkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkerRequest(WorkerModel):
    schema_version: Literal["1.0"] = WORKER_SCHEMA_VERSION
    request_id: UUID
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    job_id: UUID


class SttTranscriptionRequest(WorkerRequest):
    audio_base64: str = Field(min_length=1, max_length=32_000_000)
    sample_rate: int = Field(ge=8_000, le=48_000)
    channels: Literal[1, 2]
    language: str | None = Field(default=None, min_length=2, max_length=32)

    @model_validator(mode="after")
    def validate_pcm16_alignment(self) -> "SttTranscriptionRequest":
        audio = self.audio_bytes()
        if len(audio) % (self.channels * 2) != 0:
            raise ValueError("audio must contain aligned PCM16 frames")
        return self

    def audio_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.audio_base64, validate=True)
        except ValueError as error:
            raise ValueError("audio_base64 is not valid base64") from error


class SttTranscriptionResult(WorkerModel):
    schema_version: Literal["1.0"] = WORKER_SCHEMA_VERSION
    request_id: UUID
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    job_id: UUID
    text: str = Field(max_length=20_000)
    language: str | None = Field(default=None, min_length=2, max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)
    duration_ms: int = Field(ge=0)
    provider: str = Field(min_length=1, max_length=128)


class TtsSynthesisRequest(WorkerRequest):
    text: str = Field(min_length=1, max_length=5000)
    language: str = Field(min_length=2, max_length=32)
    voice_id: str = Field(min_length=1, max_length=128)
    speaker_id: int = Field(ge=0, le=1024)
    speed: float = Field(ge=0.5, le=2.0)
    style: str | None = Field(default=None, max_length=500)
    pitch: float | None = Field(default=None, ge=0.5, le=2.0)
    output_format: Literal["wav"] = "wav"


class TtsSynthesisResult(WorkerModel):
    schema_version: Literal["1.0"] = WORKER_SCHEMA_VERSION
    request_id: UUID
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    job_id: UUID
    audio_base64: str = Field(min_length=16, max_length=64_000_000)
    media_type: Literal["audio/wav"] = "audio/wav"
    sample_rate: int = Field(ge=8_000, le=48_000)
    duration_ms: int = Field(ge=0)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    speaker_id: int = Field(ge=0, le=1024)

    @model_validator(mode="after")
    def validate_wave_audio(self) -> "TtsSynthesisResult":
        audio = self.audio_bytes()
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ValueError("audio_base64 must contain a RIFF/WAVE asset")
        return self

    def audio_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.audio_base64, validate=True)
        except ValueError as error:
            raise ValueError("audio_base64 is not valid base64") from error


class TtsWorkerCapabilities(WorkerModel):
    """Provider-neutral discovery metadata exposed by every TTS worker."""

    schema_version: Literal["1.0"] = WORKER_SCHEMA_VERSION
    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,127}$")
    display_name: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    languages: list[str] = Field(min_length=1, max_length=32)
    supports_voice_cloning: bool = False
    supports_style: bool = False
    supports_speed: bool = True
    supports_pitch: bool = False
    native_streaming: bool = False
    output_formats: list[Literal["wav"]] = Field(default_factory=lambda: ["wav"])
    local_only: bool = True


class WorkerHealth(WorkerModel):
    schema_version: Literal["1.0"] = WORKER_SCHEMA_VERSION
    status: Literal["starting", "ready", "busy", "degraded"]
    worker_id: str = Field(min_length=1, max_length=128)
    model_loaded: bool
    model: str = Field(min_length=1, max_length=256)
    queue_depth: int = Field(ge=0)
    device: str = Field(min_length=1, max_length=64)
    capabilities: list[str] = Field(default_factory=list)
