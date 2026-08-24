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


class SubmitTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20_000)


class InterruptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="user_interruption", min_length=1, max_length=200)


class ResetSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


class WebRtcOfferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sdp: str = Field(min_length=1, max_length=1_000_000)
    type: Literal["offer"] = "offer"
    pc_id: str | None = Field(default=None, min_length=1, max_length=256)
    restart_pc: bool = False


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
