"""Optional retrieval ports reserved for Memory schemes B and C."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.memory import MemoryRecord


@dataclass(frozen=True, slots=True)
class ScoredMemoryReference:
    memory_id: UUID
    score: float


class SemanticMemoryIndex(Protocol):
    """Scheme B port for a local embedding index; not a truth source."""

    async def upsert(self, record: MemoryRecord) -> None: ...

    async def delete(self, memory_id: UUID) -> None: ...

    async def search(
        self, query: str, namespaces: Sequence[str], limit: int
    ) -> list[ScoredMemoryReference]: ...


class TemporalMemoryGraph(Protocol):
    """Scheme C port for entity/time expansion; not a truth source."""

    async def upsert(self, record: MemoryRecord) -> None: ...

    async def delete(self, memory_id: UUID) -> None: ...

    async def search(
        self,
        query: str,
        namespaces: Sequence[str],
        observed_at: datetime,
        limit: int,
    ) -> list[ScoredMemoryReference]: ...


class NullSemanticMemoryIndex(SemanticMemoryIndex):
    async def upsert(self, record: MemoryRecord) -> None:
        del record

    async def delete(self, memory_id: UUID) -> None:
        del memory_id

    async def search(
        self, query: str, namespaces: Sequence[str], limit: int
    ) -> list[ScoredMemoryReference]:
        del query, namespaces, limit
        return []


class NullTemporalMemoryGraph(TemporalMemoryGraph):
    async def upsert(self, record: MemoryRecord) -> None:
        del record

    async def delete(self, memory_id: UUID) -> None:
        del memory_id

    async def search(
        self,
        query: str,
        namespaces: Sequence[str],
        observed_at: datetime,
        limit: int,
    ) -> list[ScoredMemoryReference]:
        del query, namespaces, observed_at, limit
        return []


@dataclass(frozen=True, slots=True)
class SpokenMemoryFact:
    source_event_id: UUID
    session_id: UUID
    turn_id: UUID
    spoken_text: str
    state: str
    created_at: datetime
    completed_at: datetime | None = None
    staged_candidates_json: str | None = None
    checkpoint_index: int = 0
    retry_count: int = 0
    next_retry_at: datetime | None = None
    last_error: str | None = None


class SpokenMemoryRepository(Protocol):
    """Port for persistent per-consumer spoken memory processing state."""

    async def record_fact_if_new(
        self,
        *,
        source_event_id: UUID,
        session_id: UUID,
        turn_id: UUID,
        spoken_text: str,
        created_at: datetime | None = None,
    ) -> bool: ...

    async def get_fact(self, source_event_id: UUID) -> SpokenMemoryFact | None: ...

    async def mark_completed(
        self,
        source_event_id: UUID,
        completed_at: datetime,
    ) -> None: ...

    async def is_completed(
        self,
        source_event_id: UUID,
    ) -> bool: ...

    async def stage_candidates(
        self,
        source_event_id: UUID,
        candidates_json: str,
    ) -> None: ...

    async def update_checkpoint(
        self,
        source_event_id: UUID,
        checkpoint_index: int,
    ) -> None: ...

    async def record_retry_failure(
        self,
        source_event_id: UUID,
        error_message: str,
        retry_count: int,
        next_retry_at: datetime,
    ) -> None: ...

    async def record_dead_letter(
        self,
        source_event_id: UUID,
        error_message: str,
        retry_count: int,
    ) -> None: ...

    async def list_pending(
        self,
        *,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[SpokenMemoryFact]: ...

    async def get_earliest_retry_at(self) -> datetime | None: ...

    async def backfill_unprocessed_spoken_events(self, limit: int = 100) -> int: ...

    async def record_scope_reset(self, character_id: str, reset_at: datetime) -> None: ...

    async def clear_scope_facts(self, character_id: str) -> int: ...

    async def clear_all_facts(self) -> int: ...

    async def is_scope_reset(self, character_id: str, occurred_at: datetime) -> bool: ...
