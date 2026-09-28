"""Read-only, text-free interaction diagnostics for the local owner."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.base import ProtocolModel
from chatwaifu_protocol.character import PromptBudgetReport, PromptContextIdentity


class InteractionTraceSummary(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    interaction_id: UUID
    session_id: UUID
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    occurred_at: AwareDatetime
    trigger: Literal["user", "proactive", "ignored_voice", "proactive_deferred"]
    reason: str | None = None
    generation_state: str | None = None


class InteractionTracePage(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    items: list[InteractionTraceSummary] = Field(
        default_factory=list[InteractionTraceSummary], max_length=50
    )
    has_more: bool = False
    next_cursor: str | None = None


class DiagnosticResponsePlan(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    intent: str | None = None
    tone: str | None = None
    expression: str | None = None
    motion: str | None = None
    response_length: str | None = None


class DiagnosticMemoryReference(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    memory_id: UUID
    score: float | None = None
    selected_for_prompt: bool = False
    currently_visible: bool | None = None


class DiagnosticToolCall(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    tool_call_id: UUID | None = None
    status: str
    duration_ms: int | None = None
    error_code: str | None = None


class DiagnosticDeliveryPart(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    part_id: UUID
    ordinal: int
    kind: str
    required: bool
    status: str
    attempt: int
    delivered_at: AwareDatetime | None = None


class DiagnosticPlaybackSegment(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    segment_id: UUID
    segment_index: int
    state: str
    played_pts_ms: int
    transport: str | None = None


class DiagnosticTimelineItem(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    sequence: int
    event_type: str
    occurred_at: AwareDatetime
    reason: str | None = None
    status: str | None = None


class InteractionTraceDetail(ProtocolModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: InteractionTraceSummary
    prompt_identity: PromptContextIdentity | None = None
    response_plan: DiagnosticResponsePlan | None = None
    prompt_budget: PromptBudgetReport | None = None
    memory_candidates: list[DiagnosticMemoryReference] = Field(
        default_factory=list[DiagnosticMemoryReference]
    )
    selected_memory_ids: list[UUID] | None = None
    tool_calls: list[DiagnosticToolCall] = Field(default_factory=list[DiagnosticToolCall])
    delivery_status: str | None = None
    delivery_parts: list[DiagnosticDeliveryPart] = Field(
        default_factory=list[DiagnosticDeliveryPart]
    )
    playback_segments: list[DiagnosticPlaybackSegment] = Field(
        default_factory=list[DiagnosticPlaybackSegment]
    )
    timeline: list[DiagnosticTimelineItem] = Field(
        default_factory=list[DiagnosticTimelineItem], max_length=200
    )
    truncated: bool = False
    next_cursor: int | None = None
