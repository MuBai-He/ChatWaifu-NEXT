"""Runtime HTTP request and status models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: str = Field(default="default", min_length=1, max_length=128)


class RuntimeHealth(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ready", "error"]
    subscribers: int
    dropped_events: int
    providers: dict[str, str]
    resources: dict[str, object]


class SubmitTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000)


class InterruptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="user_interruption", min_length=1, max_length=200)


class ResetSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


class TtsProviderSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,127}$")


class AliyunTtsConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    model: str = Field(min_length=1, max_length=256)
    voice_id: str = Field(min_length=1, max_length=256)
    region: Literal["beijing", "singapore"] = "beijing"
    workspace_id: str = Field(default="", max_length=256)
    language_type: Literal[
        "Auto",
        "Chinese",
        "English",
        "German",
        "Italian",
        "Portuguese",
        "Spanish",
        "Japanese",
        "Korean",
        "French",
        "Russian",
    ] = "Auto"
    sample_rate: Literal[8000, 16000, 24000, 48000] = 24000
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: int = Field(default=50, ge=0, le=100)
    pitch_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    max_audio_bytes: int = Field(default=32_000_000, ge=1_000_000, le=128_000_000)
    api_key: str | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False


class WebRtcOfferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sdp: str = Field(min_length=1, max_length=1_000_000)
    type: Literal["offer"] = "offer"
    pc_id: str | None = Field(default=None, min_length=1, max_length=256)
    restart_pc: bool = False
    activation_mode: Literal["push_to_talk", "open_mic"] = "push_to_talk"


class WebRtcIceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: str = Field(max_length=16_384)
    sdp_mid: str = Field(min_length=1, max_length=64)
    sdp_mline_index: int = Field(ge=0, le=64)


class WebRtcPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pc_id: str = Field(min_length=1, max_length=256)
    candidates: list[WebRtcIceCandidate] = Field(
        default_factory=lambda: list[WebRtcIceCandidate](), max_length=32
    )


class InstallPluginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1, max_length=4096)


class ExamplePluginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    example_id: str = Field(default="local-echo", pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")


class PluginEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class SkillConfirmationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow_once", "allow_session", "allow_always", "deny"]


class MemoryProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject"]


class MemoryCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2_000)


class MemoryPinnedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pinned: bool


class ModelRoleConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["demo", "openai_compatible", "local_hash", "disabled"]
    model: str = Field(min_length=1, max_length=256)
    base_url: str = Field(default="", max_length=2048)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    context_window: int = Field(default=8192, ge=1024, le=2_000_000)
    enabled: bool = True
    api_key: str | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False


class CharacterInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["avatar_touch"]
    region: str = Field(default="body", min_length=1, max_length=64)
