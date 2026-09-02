"""Persistence port owned by the memory domain."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.memory import (
    MemoryChannelAttribution,
    MemoryProposal,
    MemoryRecord,
    MemorySource,
)


class MemorySearchHit:
    __slots__ = ("lexical_score", "record")

    def __init__(self, record: MemoryRecord, lexical_score: float) -> None:
        self.record = record
        self.lexical_score = lexical_score


class MemoryEventEvidence:
    __slots__ = (
        "channel_attribution",
        "event_id",
        "event_type",
        "occurred_at",
        "session_id",
        "turn_id",
    )

    def __init__(
        self,
        event_id: UUID,
        session_id: UUID,
        turn_id: UUID | None,
        occurred_at: datetime,
        event_type: str = "user.turn_committed",
        channel_attribution: MemoryChannelAttribution | None = None,
    ) -> None:
        self.event_id = event_id
        self.session_id = session_id
        self.turn_id = turn_id
        self.occurred_at = occurred_at
        self.event_type = event_type
        self.channel_attribution = channel_attribution


class MemoryRepository(Protocol):
    async def event_exists(self, event_id: UUID) -> bool: ...

    async def event_evidence(self, event_id: UUID) -> MemoryEventEvidence | None: ...

    async def find_exact(self, namespace: str, normalized_text: str) -> MemoryRecord | None: ...

    async def find_identity(
        self, namespace: str, subject_id: str, predicate: str
    ) -> list[MemoryRecord]: ...

    async def get(self, memory_id: UUID) -> MemoryRecord | None: ...

    async def get_many(self, memory_ids: Sequence[UUID]) -> list[MemoryRecord]: ...

    async def create_record(
        self,
        record: MemoryRecord,
        sources: Sequence[MemorySource],
        *,
        supersede_target: UUID | None = None,
    ) -> None: ...

    async def tombstone(self, memory_id: UUID, changed_at: datetime) -> bool: ...

    async def set_pinned(
        self, memory_id: UUID, pinned: bool, changed_at: datetime
    ) -> MemoryRecord | None: ...

    async def save_proposal(self, proposal: MemoryProposal) -> None: ...

    async def get_proposal(self, proposal_id: UUID) -> MemoryProposal | None: ...

    async def decide_proposal(
        self, proposal_id: UUID, status: str, decided_at: datetime
    ) -> bool: ...

    async def accept_proposal_atomically(
        self,
        *,
        proposal_id: UUID,
        record: MemoryRecord,
        sources: Sequence[MemorySource],
        supersede_target: UUID | None = None,
        decided_at: datetime,
    ) -> bool: ...

    async def list_proposals(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[MemoryProposal]: ...

    async def list_records(
        self,
        *,
        include_tombstoned: bool = False,
        namespace: str | None = None,
        kind: str | None = None,
        sensitivity: str | None = None,
        limit: int = 200,
    ) -> list[MemoryRecord]: ...

    async def list_sources(self, memory_id: UUID) -> list[MemorySource]: ...

    async def list_sources_many(
        self, memory_ids: Sequence[UUID]
    ) -> dict[UUID, list[MemorySource]]: ...

    async def search_fts(
        self, query: str, namespaces: Sequence[str], limit: int
    ) -> list[MemorySearchHit]: ...

    async def list_pinned(self, namespaces: Sequence[str], limit: int) -> list[MemoryRecord]: ...

    async def list_recent(self, namespaces: Sequence[str], limit: int) -> list[MemoryRecord]: ...

    async def clear_scope(self, namespace: str) -> list[UUID]: ...

    async def clear_all(self) -> int: ...
