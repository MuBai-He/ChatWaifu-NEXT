"""Owner-scoped evidence, continuous delegation and canonical work contracts."""

from datetime import date
from typing import Literal, Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, PositiveInt, model_validator

from chatwaifu_protocol.base import JsonObject, ProtocolModel


class EvidenceRef(ProtocolModel):
    kind: Literal["event", "knowledge"]
    reference: str = Field(min_length=1, max_length=256)
    revision: int = Field(default=1, ge=1)


class KnowledgeExcerpt(ProtocolModel):
    evidence: EvidenceRef
    text: str = Field(max_length=2400)
    confirmed: bool
    subject: str | None = None
    occurred_at: int


class WaitCondition(ProtocolModel):
    kind: Literal["input", "source", "time", "authorization", "device", "outcome", "task"]
    room_ids: list[str] = Field(default_factory=list[str], max_length=32)
    sender_id: str | None = None
    reference: str | None = None


class ExecutionBudget(ProtocolModel):
    max_tool_calls: int = Field(ge=0)
    remaining_tool_calls: int = Field(ge=0)
    max_active_seconds: float = Field(ge=0)
    remaining_active_seconds: float = Field(ge=0)


class ExecutionContext(ProtocolModel):
    audience: Literal["owner", "shared"] = "owner"
    budget: ExecutionBudget | None = None
    authority_revision: int | None = None
    execution_id: UUID = Field(default_factory=uuid4)
    owner: str
    character_id: str
    mode: Literal["interactive", "observe", "act", "consolidate"]
    session_id: UUID
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    task_id: UUID | None = None
    intent_id: UUID | None = None
    intent_revision: int | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list[EvidenceRef], max_length=30)


class IntentSchedule(ProtocolModel):
    first_wake_at: AwareDatetime
    repeat: Literal["none", "daily", "weekly"] = "none"
    timezone: str = "Asia/Shanghai"
    kind: Literal["run", "remind"] = "run"
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def valid_schedule(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("invalid_schedule_timezone") from None
        if self.kind == "remind" and self.title is None:
            raise ValueError("reminder_title_required")
        return self


class ReminderDefaults(ProtocolModel):
    """Owner-selected rules for future work in this intent's scope."""

    event_offset_seconds: int | None = Field(default=None, ge=0, le=2678400)
    todo_offset_seconds: int | None = Field(default=None, ge=0, le=2678400)
    primary_device_id: UUID

    @model_validator(mode="after")
    def has_rule(self) -> Self:
        if self.event_offset_seconds is None and self.todo_offset_seconds is None:
            raise ValueError("empty_reminder_defaults")
        return self


class StandingIntentInput(ProtocolModel):
    reminder_defaults: ReminderDefaults | None = None
    session_id: UUID
    goal: str = Field(min_length=1, max_length=2000)
    room_ids: list[str] = Field(default_factory=list[str], max_length=32)
    schedule: IntentSchedule | None = None
    observe_calendar: bool = False
    allowed_skill_ids: list[str] = Field(min_length=1, max_length=32)
    resource_roots: list[str] = Field(default_factory=lambda: ["."], max_length=16)
    calendar_ids: list[str] = Field(default_factory=list, max_length=32)
    tasklist_ids: list[str] = Field(default_factory=list[str], max_length=32)
    allowed_operations: list[Literal["create", "update", "complete", "cancel", "delete"]] = Field(
        default_factory=lambda: ["create", "update", "complete", "cancel"]
    )
    allow_writes: bool = False
    shadow: bool = True
    expires_at: AwareDatetime | None = None
    max_tool_calls: int = Field(default=20, ge=1, le=100)
    max_active_seconds: int = Field(default=300, ge=30, le=1800)


class StandingIntentUpdate(StandingIntentInput):
    expected_revision: int = Field(ge=1)


class StandingIntent(StandingIntentInput):
    intent_id: UUID
    owner: str
    character_id: str
    revision: int = Field(ge=1)
    state: Literal["active", "paused", "revoked"] = "active"
    capability_fingerprints: dict[str, str] = Field(default_factory=dict)
    created_at: AwareDatetime


class IntentAction(ProtocolModel):
    operation_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    expected_revision: int = Field(ge=1)
    action: Literal["pause", "resume", "revoke", "enable"]


class Observation(ProtocolModel):
    revision: int = Field(default=1, ge=1)
    error_code: str | None = Field(default=None, max_length=64)
    last_review_id: UUID | None = None
    observation_id: UUID
    owner: str
    room_id: str
    evidence: EvidenceRef
    ingestion_mode: Literal["live", "historical", "delayed"]
    occurred_at: int
    received_at: int
    policy_epoch: int
    state: Literal["pending", "running", "done", "failed", "superseded"] = "pending"
    attempts: int = 0


class OwnerWorkDecision(ProtocolModel):
    operation_id: UUID
    request_hash: str = Field(pattern="^[0-9a-f]{64}$")
    received_at: AwareDatetime
    pending: bool = True


class ConfirmedWorkReview(ProtocolModel):
    session_id: UUID
    operation_id: UUID = Field(default_factory=uuid4)
    record_id: str = Field(min_length=1, max_length=200)
    expected_record_revision: int = Field(ge=1)


class WorkItem(ProtocolModel):
    owner_decision: OwnerWorkDecision | None = None
    requested_fields: list[str] = Field(default_factory=list[str], max_length=16)
    migration_target: JsonObject = Field(default_factory=dict)
    work_item_id: str = Field(min_length=1, max_length=200)
    memory_identity: JsonObject = Field(default_factory=dict)
    legacy_record_id: str | None = Field(default=None, max_length=200)
    owner: str = "local"
    room_id: str
    source_room_ids: list[str] = Field(default_factory=list[str], max_length=100)
    ingestion_mode: Literal["live", "historical", "delayed"] = "historical"
    kind: Literal["todo", "event", "memory", "reference", "decision"]
    title: str = Field(min_length=1, max_length=256)
    details: str = Field(default="", max_length=64000)
    action_scope: Literal["single", "series", "following"] = "single"
    provider_updated_at: AwareDatetime | None = None
    operation: Literal["create", "update", "complete", "cancel"] = "create"
    target_id: str | None = None
    expected_revision: int = 0
    target_applied_revision: int | None = None
    legacy_review: bool = False
    state: Literal["proposed", "open", "waiting", "blocked", "completed", "cancelled", "rejected"]
    revision: int = Field(default=1, ge=1)
    acceptance: Literal["pending", "human", "policy"] = "pending"
    temporal_status: Literal["current", "historical", "uncertain"] = "current"
    temporal_reason: str = Field(default="", max_length=200)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=30)
    due_date: date | None = None
    clear_due: bool = False
    primary_device_id: UUID | None = None
    reminder_rule: Literal["absolute", "relative"] = "absolute"
    reminder_policy: Literal["inherit", "none"] = "inherit"
    reminder_offset_seconds: int | None = Field(default=None, ge=0, le=2678400)
    due_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    previous_starts_at: AwareDatetime | None = None
    previous_ends_at: AwareDatetime | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    remind_at: AwareDatetime | None = None
    timezone: str = "Asia/Shanghai"
    involvement: Literal["self", "other", "unclear"] = "unclear"
    task_id: UUID | None = None
    reconciled_task_revision: int | None = None
    waiting_reason: str | None = Field(default=None, max_length=1200)
    bindings: list[JsonObject] = Field(default_factory=list[JsonObject], max_length=16)
    updated_at: AwareDatetime


class WorkItemEdit(ProtocolModel):
    start_date: date | None = None
    end_date: date | None = None
    involvement: Literal["self", "other", "unclear"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=256)
    details: str | None = Field(default=None, max_length=2400)
    due_date: date | None = None
    primary_device_id: UUID | None = None
    due_at: AwareDatetime | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    remind_at: AwareDatetime | None = None


class WorkItemAction(ProtocolModel):
    operation_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    expected_revision: int = Field(ge=1)
    action: Literal["confirm", "reject", "complete", "cancel", "snooze", "edit", "link"]
    target_id: str | None = None
    expected_target_revision: int | None = Field(default=None, ge=1)
    changes: WorkItemEdit | None = None
    remind_at: AwareDatetime | None = None


class ProcessingRequest(ProtocolModel):
    session_id: UUID
    device_id: UUID
    room_ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal["prepare", "activate"]
    expected_epoch: int = Field(default=0, ge=0)
    drained: bool = False
    watermarks: dict[str, int] = Field(default_factory=dict)
    completed_sources: list[EvidenceRef] = Field(
        default_factory=list[EvidenceRef], max_length=10000
    )
    pending_reviews: list[JsonObject] = Field(default_factory=list[JsonObject], max_length=100)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(self.model_dump_json().encode()) > 1_000_000:
            raise ValueError("processing_snapshot_too_large")
        if self.action == "activate" and not self.drained:
            raise ValueError("native_leases_must_be_drained")
        return self


class ActivityReceipt(ProtocolModel):
    session_id: UUID
    device_id: UUID
    state: Literal["accepted", "presented", "blocked", "opened", "unknown", "withdrawn"]


class ActivityValidationRequest(ProtocolModel):
    session_id: UUID
    sequences: list[PositiveInt] = Field(min_length=1, max_length=200)


class ActivityEntry(ProtocolModel):
    redacted: bool = False
    delivery_history: list[JsonObject] = Field(default_factory=list[JsonObject])
    details: JsonObject = Field(default_factory=dict)
    delivery: dict[str, str] = Field(default_factory=dict[str, str])
    sequence: int
    kind: str
    reference: str
    state: str
    summary: str
    created_at: AwareDatetime
    evidence: list[EvidenceRef] = Field(default_factory=list[EvidenceRef])


class ReminderOccurrence(ProtocolModel):
    content_fingerprint: str = ""
    accepted_work_revision: int = 0
    accepted_at: AwareDatetime | None = None
    occurrence_id: UUID
    owner: str
    work_item_id: str
    work_revision: int
    scheduled_at: AwareDatetime
    primary_device_id: UUID | None = None
    state: Literal[
        "scheduled",
        "waiting_device",
        "accepted",
        "presented",
        "dismissed",
        "snoozed",
        "completed",
        "cancelled",
        "blocked",
        "outcome_unknown",
    ]
    presented_at: AwareDatetime | None = None
    acknowledged_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class ActionProposal(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    action_id: UUID
    task_id: UUID
    work_item_id: str
    work_revision: int = Field(ge=1)
    target_id: str | None = None
    expected_target_revision: int = Field(ge=0)
    intent_id: UUID | None = None
    intent_revision: int | None = None
    evidence: list[EvidenceRef] = Field(max_length=30)
    requested_changes: JsonObject
    target: JsonObject
    verification_requirements: list[str]
