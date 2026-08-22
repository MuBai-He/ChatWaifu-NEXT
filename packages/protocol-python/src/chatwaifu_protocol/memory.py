"""Provenance-preserving memory proposal and retrieval contracts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from chatwaifu_protocol.base import JsonValue, PrivacyLevel, ProtocolModel


class MemoryRecordDraft(ProtocolModel):
    namespace: str
    kind: str
    subject_id: str | None = None
    predicate: str | None = None
    value: JsonValue = None
    text: str
    observed_at: AwareDatetime
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    sensitivity: PrivacyLevel = PrivacyLevel.PRIVATE


class MemoryRecord(MemoryRecordDraft):
    memory_id: UUID
    source_event_ids: list[UUID] = Field(min_length=1)
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    state: Literal["active", "superseded", "contradicted", "tombstoned"] = "active"
    supersedes: UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MemoryProposal(ProtocolModel):
    proposal_id: UUID
    operation: Literal["add", "update", "supersede", "contradict", "forget", "ignore"]
    candidate: MemoryRecordDraft | None = None
    target_memory_id: UUID | None = None
    evidence_event_ids: list[UUID] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str

    @model_validator(mode="after")
    def require_operation_target(self) -> "MemoryProposal":
        if self.operation == "add" and self.candidate is None:
            raise ValueError("add proposals require a candidate")
        if self.operation in {"update", "supersede", "contradict", "forget"} and (
            self.target_memory_id is None
        ):
            raise ValueError(f"{self.operation} proposals require target_memory_id")
        return self


class MemoryExcerpt(ProtocolModel):
    memory_id: UUID
    text: str
    source_event_ids: list[UUID] = Field(min_length=1)
    relevance: float = Field(ge=0, le=1)


class MemoryContextPacket(ProtocolModel):
    pinned_facts: list[MemoryExcerpt] = Field(default_factory=list[MemoryExcerpt])
    recent_episodes: list[MemoryExcerpt] = Field(default_factory=list[MemoryExcerpt])
    relevant_memories: list[MemoryExcerpt] = Field(default_factory=list[MemoryExcerpt])
    open_commitments: list[MemoryExcerpt] = Field(default_factory=list[MemoryExcerpt])
    relationship_context: list[MemoryExcerpt] = Field(default_factory=list[MemoryExcerpt])
    provenance_ids: list[UUID] = Field(default_factory=list[UUID])
    token_budget_used: int = Field(ge=0)
