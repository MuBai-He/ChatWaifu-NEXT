"""Versioned contracts shared by mobile sync, knowledge UI and index adapters."""

from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chatwaifu_protocol.base import JsonObject
from pydantic import BaseModel, ConfigDict, Field, model_validator

Identity = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_:.-]+$")]
Kind = Literal[
    "source", "memory", "todo", "event", "person", "project", "organization", "reference"
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(Contract):
    record_id: Identity
    revision: int = Field(ge=1)


class RecordDraft(Contract):
    record_id: Identity
    kind: Kind
    room_id: Identity
    title: str = Field(default="", max_length=256)
    text: str = Field(default="", max_length=64000)
    state: Literal["active", "open", "waiting", "blocked", "completed", "cancelled"] = "active"
    confirmed: bool = False
    occurred_at: int = Field(ge=0)
    due_at: int | None = Field(default=None, ge=0)
    starts_at: int | None = Field(default=None, ge=0)
    ends_at: int | None = Field(default=None, ge=0)
    remind_at: int | None = Field(default=None, ge=0)
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    source_refs: list[Citation] = Field(default_factory=lambda: list[Citation](), max_length=30)
    sender_id: Identity | None = None
    sender_name: str = Field(default="", max_length=256)
    sent_by_owner: bool = False
    origin: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_record(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("invalid_timezone") from None
        if self.ends_at is not None and (self.starts_at is None or self.ends_at <= self.starts_at):
            raise ValueError("invalid_event_range")
        if self.kind == "source" and self.source_refs:
            raise ValueError("source_cannot_reference_itself")
        if self.kind != "source" and not self.title.strip():
            raise ValueError("title_required")
        if len(str(self.origin)) > 80000:
            raise ValueError("origin_too_large")
        return self


class Record(RecordDraft):
    revision: int
    deleted: bool = False
    updated_at: int


class Mutation(Contract):
    operation_id: UUID
    expected_revision: int = Field(ge=0)
    action: Literal["upsert", "forget"] = "upsert"
    record: RecordDraft


class PolicyMutation(Contract):
    operation_id: UUID
    room_id: Identity
    expected_revision: int = Field(ge=0)
    excluded: bool
    label: str = Field(default="", max_length=256)


class PushRequest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    session_id: UUID
    device_id: UUID
    policy_epoch: int = Field(ge=0)
    mutations: list[Mutation] = Field(default_factory=lambda: list[Mutation](), max_length=100)
    policies: list[PolicyMutation] = Field(
        default_factory=lambda: list[PolicyMutation](), max_length=100
    )

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(self.model_dump_json().encode()) > 1_000_000:
            raise ValueError("sync_batch_too_large")
        ids = [str(m.operation_id) for m in (*self.mutations, *self.policies)]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_operation")
        return self


class Node(Contract):
    node_id: Identity
    kind: Kind
    label: str = Field(min_length=1, max_length=256)
    room_id: Identity
    stable_identity: Identity | None = None


class Relation(Contract):
    relation_id: Identity
    source: Identity
    target: Identity
    predicate: Literal[
        "participates",
        "responsible",
        "belongs_to",
        "depends_on",
        "waiting_for",
        "occurs_at",
        "derived_from",
        "related_to",
    ]
    description: str = Field(default="", max_length=1000)
    valid_from: int | None = None
    valid_to: int | None = None
    status: Literal["proposed", "confirmed", "rejected", "superseded", "forgotten"] = "proposed"
    source_refs: list[Citation] = Field(min_length=1, max_length=30)


class Invalidation(Contract):
    index_id: str = Field(min_length=1, max_length=200)
    valid_to: int


class Projection(Contract):
    invalidations: list[Invalidation] = Field(default_factory=lambda: list[Invalidation]())
    index_refs: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    nodes: list[Node] = Field(default_factory=lambda: list[Node](), max_length=100)
    relations: list[Relation] = Field(default_factory=lambda: list[Relation](), max_length=150)


class QueryRequest(Contract):
    session_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    hops: int = Field(default=2, ge=1, le=3)
    at: int | None = None
    answer: bool = True


class ReviewRequest(Contract):
    session_id: UUID
    expected_revision: int = Field(ge=1)
    decision: Literal["confirm", "reject", "forget"]


class MergeRequest(Contract):
    session_id: UUID
    operation_id: UUID
    source_ids: list[Identity] = Field(min_length=2, max_length=20)
    target_id: Identity
    expected_graph_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_selection(self) -> Self:
        if (
            len(set(self.source_ids)) != len(self.source_ids)
            or self.target_id not in self.source_ids
        ):
            raise ValueError("invalid_merge_selection")
        return self


class RelationCorrection(Contract):
    session_id: UUID
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    source: Identity
    target: Identity
    predicate: Literal[
        "participates",
        "responsible",
        "belongs_to",
        "depends_on",
        "waiting_for",
        "occurs_at",
        "derived_from",
        "related_to",
    ]
    description: str = Field(min_length=1, max_length=1000)
    valid_from: int | None = None
    valid_to: int | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> Self:
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("invalid_relation_interval")
        return self
