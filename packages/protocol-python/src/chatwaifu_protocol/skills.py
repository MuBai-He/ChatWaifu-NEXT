"""Product Runtime Skill discovery, plugin, and job contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.avatar import AvatarCue
from chatwaifu_protocol.base import JsonObject, JsonValue, ProtocolModel, SideEffect
from chatwaifu_protocol.errors import StructuredError


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
    adapter_tool: str | None = Field(default=None, min_length=1, max_length=128)


class SkillDefinition(ProtocolModel):
    skill_id: str
    version: str
    name: str
    description: str
    capabilities: list[SkillCapability] = Field(default_factory=list[SkillCapability])
    interruptible: bool = True
    background_allowed: bool = False
    source: Literal["builtin", "plugin"] = "builtin"
    plugin_id: str | None = None
    enabled: bool = True


class PluginTransport(ProtocolModel):
    kind: Literal["stdio"] = "stdio"
    command: list[str] = Field(min_length=1, max_length=8)


class PluginManifest(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    plugin_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2_000)
    transport: PluginTransport
    skills: list[str] = Field(min_length=1, max_length=32)


class PluginSnapshot(ProtocolModel):
    plugin_id: str
    version: str
    name: str
    description: str
    enabled: bool
    install_path: str
    installed_at: AwareDatetime
    updated_at: AwareDatetime


class SkillInvocation(ProtocolModel):
    skill_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=128)
    arguments: JsonObject = Field(default_factory=dict)


class SkillRunSnapshot(ProtocolModel):
    skill_run_id: UUID
    skill_id: str
    skill_version: str
    capability: str
    plugin_id: str | None = None
    session_id: UUID
    state: SkillRunState
    progress: float | None = Field(default=None, ge=0, le=1)
    confirmation_request_id: UUID | None = None
    result: SkillResult | None = None
    error: StructuredError | None = None
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None


class SkillResult(ProtocolModel):
    status: str
    data: JsonValue = None
    spoken_summary: str | None = None
    ui_cards: list[JsonObject] = Field(default_factory=list[JsonObject])
    avatar_cues: list[AvatarCue] = Field(default_factory=list[AvatarCue])
    memory_proposal_ids: list[UUID] = Field(default_factory=list[UUID])
    prospective_task_ids: list[UUID] = Field(default_factory=list[UUID])
    provenance: list[str] = Field(default_factory=list)
