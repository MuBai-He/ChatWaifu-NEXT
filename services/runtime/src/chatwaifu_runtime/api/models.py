"""Runtime HTTP request and status models."""

from typing import Literal
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


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


class SessionRecoveryMessage(BaseModel):
    turn_id: UUID
    role: Literal["user", "assistant"]
    committed_text: str
    committed_at: AwareDatetime | None
    created_at: AwareDatetime


class SessionRecoveryResponse(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    messages: list[SessionRecoveryMessage]
    after_sequence: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
    active_generation_id: UUID | None


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


class TtsConfigurationUpdateRequest(BaseModel):
    """Provider-neutral top-level configuration patch.

    Provider fields are intentionally accepted here and then validated against
    the selected registration's strict Pydantic model. The current extension
    contract supports one write-only ``api_key`` credential; registrations
    cannot advertise arbitrary secret names that this request cannot store.
    """

    model_config = ConfigDict(extra="allow")

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


class McpConnectionConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    transport: Literal["stdio", "streamable_http", "sse"]
    command: list[str] = Field(default_factory=list, max_length=32)
    url: str | None = Field(default=None, min_length=1, max_length=4096)
    allow_remote: bool = False
    enabled: bool = True
    timeout_seconds: float = Field(default=30, gt=0, le=600)
    trust_level: Literal["trusted", "untrusted"] = "untrusted"
    sandbox_mode: Literal["required", "preferred", "disabled"] | None = None
    network_policy: Literal["deny", "loopback", "allow"] | None = None
    bearer_token: str | None = Field(default=None, min_length=1, max_length=16_384)
    clear_bearer_token: bool = False


class McpResourceReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str = Field(min_length=1, max_length=4096)


class McpPromptGetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    arguments: dict[str, str] = Field(default_factory=dict)


class McpToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    arguments: JsonObject = Field(default_factory=dict)


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
