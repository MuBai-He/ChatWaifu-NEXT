"""Bounded, versioned mobile information-processing contract."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chatwaifu_protocol.harness import EvidenceRef
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

MessageRef = Annotated[str, Field(pattern=r"^wechat:[0-9a-f]{64}(?::[0-9a-f]{64}:[0-9]+)?$")]
RoomRef = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class InformationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: MessageRef
    room_ref: RoomRef
    conversation: str = Field(default="", max_length=256)
    sent_by_owner: bool
    sender_ref: str = Field(default="", max_length=64)
    sender_name: str = Field(default="", max_length=256)
    context_only: bool = False
    message_time_ms: int = Field(ge=0)
    text: str = Field(max_length=3000)
    segment: int = Field(default=1, ge=1, le=10000)
    previous_segment_context: str = Field(default="", max_length=200)
    text_truncated: bool = False


class PriorMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room_ref: RoomRef
    conversation: str = Field(default="", max_length=256)
    sent_by_owner: bool
    message_time_ms: int = Field(ge=0)
    text: str = Field(max_length=1000)


class WorkSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    room_ref: RoomRef
    revision: int = Field(ge=1)
    kind: Literal["todo", "event"]
    title: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=1200)
    state: Literal["open", "waiting", "blocked"]
    involvement: Literal["self", "other", "unclear"]
    due_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None


class PriorKnowledge(BaseModel):
    """Earlier same-room background, never authority to write or cite a new source."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    room_ref: RoomRef
    kind: Literal["memory", "decision", "reference", "todo", "event"]
    summary: str = Field(min_length=1, max_length=1200)
    state: Literal["confirmed", "pending_review"]
    source_time_ms: int = Field(ge=0)


class AnalyzeInformationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.1", "1.2"] = "1.1"
    session_id: UUID
    request_id: UUID
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    records: list[InformationMessage] = Field(min_length=1, max_length=30)
    known_items: list[WorkSnapshot] = Field(
        default_factory=lambda: list[WorkSnapshot](), max_length=20
    )
    prior_context: list[PriorMessage] = Field(
        default_factory=lambda: list[PriorMessage](), max_length=8
    )
    prior_knowledge: list[PriorKnowledge] = Field(
        default_factory=lambda: list[PriorKnowledge](), max_length=10
    )
    context_incomplete: bool = False

    @model_validator(mode="after")
    def validate_scope_and_budget(self) -> "AnalyzeInformationRequest":
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError("unknown_timezone") from None
        if len({record.ref for record in self.records}) != len(self.records):
            raise ValueError("duplicate_source_reference")
        if self.schema_version == "1.1" and any(record.context_only for record in self.records):
            raise ValueError("context_evidence_requires_1_2")
        if self.schema_version == "1.2":
            if len({record.room_ref for record in self.records}) != 1:
                raise ValueError("mixed_conversation_window")
            if not any(not record.context_only for record in self.records):
                raise ValueError("missing_current_source")
        if sum(len(record.text.encode("utf-16-le")) // 2 for record in self.records) > 16000:
            raise ValueError("source_budget_exceeded")
        if sum(len(record.text) for record in self.prior_context) > 6000:
            raise ValueError("context_budget_exceeded")
        rooms = {record.room_ref for record in self.records}
        if any(item.room_ref not in rooms for item in (*self.known_items, *self.prior_context)):
            raise ValueError("context_outside_batch_rooms")
        fresh_time = min(
            record.message_time_ms for record in self.records if not record.context_only
        )
        if any(
            item.room_ref not in rooms or item.source_time_ms >= fresh_time
            for item in self.prior_knowledge
        ):
            raise ValueError("knowledge_outside_batch_history")
        if len({item.id for item in self.prior_knowledge}) != len(self.prior_knowledge):
            raise ValueError("duplicate_background_knowledge")
        if sum(len(item.summary.encode("utf-16-le")) // 2 for item in self.prior_knowledge) > 6000:
            raise ValueError("knowledge_budget_exceeded")
        if len({item.id for item in self.known_items}) != len(self.known_items):
            raise ValueError("duplicate_target_id")
        return self


class InformationSuggestion(BaseModel):
    requested_fields: list[
        Literal[
            "title",
            "details",
            "starts_at",
            "ends_at",
            "start_date",
            "end_date",
            "due_date",
            "due_at",
        ]
    ] = Field(
        default_factory=lambda: list[
            Literal[
                "title",
                "details",
                "starts_at",
                "ends_at",
                "start_date",
                "end_date",
                "due_date",
                "due_at",
            ]
        ](),
        max_length=8,
    )
    model_config = ConfigDict(extra="forbid")
    confidence: float = Field(default=0, ge=0, le=1)
    sensitivity: Literal["private", "sensitive"] = "private"
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["todo", "event", "memory", "decision", "reference"]
    importance: Literal["high", "medium", "low"]
    reason: str = Field(max_length=1200)
    source_refs: list[MessageRef] = Field(min_length=1, max_length=10)
    involvement: Literal["self", "other", "unclear"]
    action_scope: Literal["single", "series", "following"] = "single"
    operation: Literal["create", "update", "complete", "cancel"] = "create"
    target_id: str = Field(default="", max_length=100)
    expected_revision: int = Field(default=0, ge=0)
    state: Literal["open", "waiting", "blocked"] = "open"
    temporal_status: Literal["current", "historical", "uncertain"] = "current"
    temporal_reason: str = Field(default="", max_length=200)
    due_date: date | None = None
    clear_due: bool = False
    reminder_offset_seconds: int | None = Field(default=None, ge=0, le=2678400)
    remind_at: AwareDatetime | None = None
    due_at: AwareDatetime | None = None
    previous_starts_at: AwareDatetime | None = None
    previous_ends_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    due_text: str | None = Field(default=None, max_length=500)
    location: str = Field(default="", max_length=200)
    details: str = Field(default="", max_length=1200)

    @field_validator("location", mode="before")
    @classmethod
    def unspecified_location(cls, value: object) -> object:
        # An omitted optional location and explicit JSON null both mean unknown.
        return "" if value is None else value

    @model_validator(mode="after")
    def validate_times(self) -> "InformationSuggestion":
        if (self.start_date is None) != (self.end_date is None) or (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date <= self.start_date
        ):
            raise ValueError("invalid_all_day_range")
        if self.start_date is not None and (self.starts_at is not None or self.ends_at is not None):
            raise ValueError("mixed_event_window")
        for value in (self.due_at, self.starts_at, self.ends_at):
            if value is not None and not 2000 <= value.year <= 2100:
                raise ValueError("date_out_of_range")
        if self.starts_at is not None and self.ends_at is not None:
            duration = (self.ends_at - self.starts_at).total_seconds()
            if not 0 < duration <= 31 * 86400:
                raise ValueError("invalid_event_range")
        if self.operation in ("complete", "cancel") and any(
            value is not None for value in (self.due_at, self.starts_at, self.ends_at)
        ):
            raise ValueError("terminal_operation_changes_time")
        return self


class InformationModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[InformationSuggestion] = Field(
        default_factory=lambda: list[InformationSuggestion](), max_length=12
    )


class AnalyzeInformationResponse(BaseModel):
    schema_version: Literal["1.1", "1.2"] = "1.1"
    request_id: UUID
    items: list[InformationSuggestion]
    processed_refs: list[MessageRef]
    model_owner: Literal["chatwaifu_runtime"] = "chatwaifu_runtime"
    model_role: Literal["memory_extraction"] = "memory_extraction"
    requires_confirmation: Literal[True] = True


class UnderstandingQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)


class RuntimeInformationSuggestion(InformationSuggestion):
    reminder_policy: Literal["inherit", "none"] = "inherit"
    context_refs: list[EvidenceRef] = Field(
        default_factory=lambda: list[EvidenceRef](), max_length=10
    )

    @model_validator(mode="after")
    def validate_reminder_choice(self) -> "RuntimeInformationSuggestion":
        if self.reminder_policy == "none" and (
            self.remind_at is not None or self.reminder_offset_seconds is not None
        ):
            raise ValueError("declined_reminder_has_time")
        return self


class RuntimeUnderstandingResult(BaseModel):
    """Internal Runtime profile; legacy 1.1/1.2 suggestion responses stay unchanged."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    items: list[RuntimeInformationSuggestion] = Field(
        default_factory=lambda: list[RuntimeInformationSuggestion](), max_length=12
    )
    queries: list[UnderstandingQuery] = Field(
        default_factory=lambda: list[UnderstandingQuery](), max_length=3
    )
