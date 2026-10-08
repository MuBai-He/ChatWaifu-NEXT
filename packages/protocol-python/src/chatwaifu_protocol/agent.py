"""Versioned discovery and autonomous task contracts; never provider objects."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field

from chatwaifu_protocol.base import JsonObject, ProtocolModel, SideEffect


class AgentVersionedModel(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    AUTHORIZATION_REQUIRED = "authorization_required"
    DISABLED = "disabled"
    NOT_CONFIGURED = "not_configured"
    ADAPTER_REQUIRED = "adapter_required"
    UNSUPPORTED = "unsupported"


class CapabilityDescriptor(AgentVersionedModel):
    capability_id: str
    skill_id: str
    skill_version: str
    name: str
    description: str
    category: str
    source: Literal["builtin", "plugin", "mcp_connection"]
    status: CapabilityStatus
    availability_reason: str | None = Field(default=None, max_length=400)
    side_effect: SideEffect
    required_permissions: list[str] = Field(default_factory=list)
    confirmation_required: bool = False
    execution_location: Literal["runtime", "paired_device", "plugin"] = "runtime"
    fingerprint: str


class CapabilityPage(AgentVersionedModel):
    items: list[CapabilityDescriptor] = Field(
        default_factory=list[CapabilityDescriptor], max_length=32
    )
    next_cursor: str | None = None
    categories: list[str] = Field(default_factory=list, max_length=128)


class CapabilityDetail(AgentVersionedModel):
    descriptor: CapabilityDescriptor
    input_schema: JsonObject
    output_schema: JsonObject
    instructions: str


class AgentTaskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_AUTHORIZATION = "waiting_authorization"
    WAITING_EVENT = "waiting_event"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskAuthorization(AgentVersionedModel):
    allowed_skill_ids: list[str] = Field(default_factory=list, max_length=64)
    resource_roots: list[str] = Field(default_factory=list, max_length=16)
    calendar_ids: list[str] = Field(default_factory=list, max_length=32)
    source_ref: str = Field(min_length=1, max_length=256)
    allow_writes: bool = False
    capability_fingerprints: dict[str, str] = Field(default_factory=dict[str, str])
    expires_at: AwareDatetime


class ArtifactRef(AgentVersionedModel):
    artifact_id: UUID
    task_id: UUID | None = None
    session_id: UUID
    name: str
    media_type: str
    byte_length: int = Field(ge=0)
    sha256: str
    version: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    validation_status: Literal["pending", "structural", "rendered", "failed"] = "pending"


class AgentTaskCreate(AgentVersionedModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    goal: str = Field(min_length=1, max_length=8000)
    completion_criteria: list[str] = Field(default_factory=list, max_length=16)
    authorization: TaskAuthorization
    max_tool_calls: int = Field(default=100, ge=1, le=100)
    max_active_seconds: int = Field(default=1800, ge=1, le=1800)


class TaskChannelBinding(AgentVersionedModel):
    """Runtime-issued channel authority, never accepted from a model or task-create API."""

    connection_id: UUID
    account_key: str
    conversation_key: str
    sender_key: str
    source_ref: str
    route_id: UUID | None = None
    route_revision: int | None = None
    policy_revision: int | None = None
    scene_id: str | None = None
    audience_fingerprint: str | None = None
    link_id: UUID | None = None
    link_revision: int | None = None


class TaskDeliveryTarget(AgentVersionedModel):
    request_id: UUID
    task_id: UUID
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    binding: TaskChannelBinding
    purpose: Literal["file", "result"] = "file"


class AgentTask(AgentVersionedModel):
    task_id: UUID
    session_id: UUID
    scope: str
    goal: str
    completion_criteria: list[str]
    authorization: TaskAuthorization
    channel_binding: TaskChannelBinding | None = None
    state: AgentTaskState
    revision: int = Field(default=0, ge=0)
    max_tool_calls: int
    max_active_seconds: int
    tool_calls: int = 0
    active_seconds: float = 0
    result_text: str | None = None
    blocked_reason: str | None = None
    wake_at: AwareDatetime | None = None
    continuation: str | None = Field(default=None, max_length=8000)
    candidate_id: UUID | None = None
    delivery_id: UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AgentTaskPage(AgentVersionedModel):
    items: list[AgentTask] = Field(default_factory=list[AgentTask], max_length=50)
    next_cursor: str | None = None


class AgentTaskJournal(AgentVersionedModel):
    items: list[JsonObject] = Field(default_factory=list[JsonObject], max_length=100)


class AgentTaskAction(AgentVersionedModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    action: Literal["pause", "resume", "cancel", "defer"]
    input_text: str | None = Field(default=None, max_length=8000)
    wake_at: AwareDatetime | None = None


class TaskAuthorizationUpdate(AgentVersionedModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    authorization: TaskAuthorization


class TaskReconciliation(AgentVersionedModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    step_key: str = Field(min_length=1, max_length=256)
    outcome: Literal["accepted", "rejected"]
    evidence_ref: str = Field(min_length=1, max_length=1000)


class AgentEvent(AgentVersionedModel):
    event_id: UUID
    session_id: UUID
    scope: str
    task_id: UUID | None = None
    kind: Literal["input", "work", "wake"]
    source_refs: list[str] = Field(default_factory=list, max_length=96)
    occurred_at: AwareDatetime
    expires_at: AwareDatetime


class DecisionRecord(AgentVersionedModel):
    action: Literal["wait", "respond", "clarify", "task", "defer", "capability_gap"]
    reason: str = Field(max_length=400)
    source_refs: list[str] = Field(max_length=16)
    goal: str | None = Field(default=None, max_length=2000)
    wake_after_seconds: int | None = Field(default=None, ge=30, le=86400)
    quiet_seconds: int | None = Field(default=None, ge=0, le=86400)
    memory_source_refs: list[str] = Field(default_factory=list, max_length=8)


class GroupAutonomyPolicy(AgentVersionedModel):
    route_id: UUID
    route_revision: int = Field(ge=1)
    revision: int = Field(default=0, ge=0)
    mode: Literal["off", "shadow", "member"] = "off"
    merge_seconds: int = Field(default=3, ge=1, le=10)
    decision_interval_seconds: int = Field(default=10, ge=10, le=60)
    observations_per_hour: int = Field(default=240, ge=1, le=240)
    messages_per_hour: int = Field(default=20, ge=0, le=20)
    message_interval_seconds: int = Field(default=30, ge=30, le=3600)
    quiet_start: int = Field(default=23, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    memory_enabled: bool = False


class GroupAutonomyUpdate(AgentVersionedModel):
    expected_revision: int = Field(ge=0)
    policy: GroupAutonomyPolicy


class AgentDevelopmentPolicy(AgentVersionedModel):
    revision: int = Field(default=0, ge=0)
    enabled: bool = False


class CandidateCreate(AgentVersionedModel):
    session_id: UUID
    goal: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=256)


class CandidateFeature(AgentVersionedModel):
    candidate_id: UUID
    session_id: UUID
    goal: str
    source_ref: str
    state: Literal["queued", "developing", "tested", "blocked", "failed", "approved"]
    revision: int = 0
    plugin_id: str | None = None
    package_sha256: str | None = None
    artifact: ArtifactRef | None = None
    test_summary: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CandidateApproval(AgentVersionedModel):
    expected_revision: int = Field(ge=0)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
