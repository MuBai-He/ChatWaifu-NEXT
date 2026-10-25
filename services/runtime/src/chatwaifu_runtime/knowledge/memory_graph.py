"""TemporalMemoryGraph bridge indexes formal memory without creating fake chat events."""

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.memory import MemoryRecord

from chatwaifu_runtime.knowledge.contracts import Citation, Record
from chatwaifu_runtime.knowledge.indexer import GraphIndex
from chatwaifu_runtime.knowledge.retrieval import GraphReader
from chatwaifu_runtime.memory.ports import ScoredMemoryReference


class FormalMemoryStore(Protocol):
    async def index_formal_memory(self, record: MemoryRecord) -> None: ...
    async def formal_graph_ready(self, namespaces: Sequence[str]) -> bool: ...
    async def remove_formal_memory(self, memory_id: UUID) -> None: ...
    async def get(
        self, owner: str, record_id: str, revision: int | None = None
    ) -> Record | None: ...
    async def eligible(
        self, owner: str, citation: Citation, *, historical: bool = False
    ) -> bool: ...
    async def allowed_rooms(self, owner: str) -> list[str]: ...


class KnowledgeMemoryGraph:
    def __init__(self, store: FormalMemoryStore, graph: GraphReader, index: GraphIndex | None):
        self.store, self.graph, self.index = store, graph, index

    async def upsert(self, record: MemoryRecord) -> None:
        if record.namespace == "user/local/global" or record.namespace.endswith("/user/local"):
            await self.store.index_formal_memory(record)

    async def delete(self, memory_id: UUID) -> None:
        await self.store.remove_formal_memory(memory_id)

    async def search(
        self, query: str, namespaces: Sequence[str], observed_at: datetime, limit: int
    ) -> list[ScoredMemoryReference]:
        async with asyncio.timeout(2):
            return await self._search(query, namespaces, limit)

    async def _search(
        self, query: str, namespaces: Sequence[str], limit: int
    ) -> list[ScoredMemoryReference]:
        allowed = {n for n in namespaces if n == "user/local/global" or n.endswith("/user/local")}
        if not allowed or not await self.store.formal_graph_ready(tuple(allowed)):
            return []
        ids = (
            await self.index.search("local", query, await self.store.allowed_rooms("local"))
            if self.index
            else []
        )
        graph = await self.graph.graph("local", query=query, index_ids=ids, hops=2, limit=80)
        result: dict[UUID, ScoredMemoryReference] = {}
        for edge in cast(list[JsonObject], graph["edges"]):
            # Unverified relations never add a fact to formal-memory context.
            if edge["status"] != "confirmed":
                continue
            for raw in cast(list[JsonObject], edge["source_refs"]):
                ref = Citation.model_validate(raw)
                item = await self.store.get("local", ref.record_id, ref.revision)
                if (
                    not item
                    or not item.confirmed
                    or item.kind != "memory"
                    or not await self.store.eligible("local", ref)
                ):
                    continue
                if (
                    item.origin.get("source_type") != "formal_memory"
                    or item.origin.get("namespace") not in allowed
                ):
                    continue
                identifier = UUID(str(item.origin["memory_id"]))
                result[identifier] = ScoredMemoryReference(identifier, 0.8)
                if len(result) >= limit:
                    return list(result.values())
        return list(result.values())
