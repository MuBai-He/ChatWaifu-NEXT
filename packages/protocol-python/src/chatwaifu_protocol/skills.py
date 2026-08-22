"""Product Runtime Skill discovery and job contracts."""

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.avatar import AvatarCue
from chatwaifu_protocol.base import JsonObject, JsonValue, ProtocolModel, SideEffect


class SkillRunState(StrEnum):
    CREATED = "created"
    ACTIVATING = "activating"
    RUNNING = "running"
    WAITING_FOR_TOOL = "waiting_for_tool"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SkillCapability(ProtocolModel):
    name: str
    description: str
    input_schema: JsonObject
    output_schema: JsonObject
    side_effect: SideEffect = SideEffect.READ
    required_permissions: list[str] = Field(default_factory=list)
    confirmation_required: bool = False
    timeout_seconds: float = Field(default=30, gt=0)


class SkillDefinition(ProtocolModel):
    skill_id: str
    version: str
    name: str
    description: str
    capabilities: list[SkillCapability] = Field(default_factory=list[SkillCapability])
    interruptible: bool = True
    background_allowed: bool = False


class SkillRunSnapshot(ProtocolModel):
    skill_run_id: UUID
    skill_id: str
    session_id: UUID
    state: SkillRunState
    progress: float | None = Field(default=None, ge=0, le=1)
    started_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class SkillResult(ProtocolModel):
    status: str
    data: JsonValue = None
    spoken_summary: str | None = None
    ui_cards: list[JsonObject] = Field(default_factory=list[JsonObject])
    avatar_cues: list[AvatarCue] = Field(default_factory=list[AvatarCue])
    memory_proposal_ids: list[UUID] = Field(default_factory=list[UUID])
    prospective_task_ids: list[UUID] = Field(default_factory=list[UUID])
    provenance: list[str] = Field(default_factory=list)
