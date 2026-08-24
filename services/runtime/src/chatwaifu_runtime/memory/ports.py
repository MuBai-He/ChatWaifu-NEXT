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
