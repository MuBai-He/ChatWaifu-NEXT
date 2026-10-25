"""Rebuild only derived indexes from canonical reviewed facts, with a final revision fence."""

from collections.abc import AsyncIterator
from typing import Protocol

from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.knowledge.contracts import Node, Record, Relation


class RebuildStore(Protocol):
    async def rebuild_revision(self) -> str: ...
    async def rebuild_groups(self) -> dict[str, list[str]]: ...
    def rebuild_pages(
        self,
    ) -> AsyncIterator[tuple[str, list[Node], list[Relation], list[Record]]]: ...
    async def restore_aliases(self, owner: str, aliases: dict[str, str]) -> None: ...


class RestorableIndex(Protocol):
    async def begin_rebuild(self, groups: dict[str, list[str]]) -> None: ...
    async def restore(
        self, owner: str, nodes: list[Node], edges: list[Relation], records: list[Record]
    ) -> dict[str, str]: ...
    async def finish_rebuild(self) -> None: ...


async def rebuild(store: RebuildStore, index: RestorableIndex) -> JsonObject:
    revision = await store.rebuild_revision()
    await index.begin_rebuild(await store.rebuild_groups())
    restored = 0
    async for owner, nodes, edges, records in store.rebuild_pages():
        if revision != await store.rebuild_revision():
            raise ValueError("knowledge_changed_during_rebuild_retry")
        aliases = await index.restore(owner, nodes, edges, records)
        await store.restore_aliases(owner, aliases)
        restored += len(edges)
    if revision != await store.rebuild_revision():
        raise ValueError("knowledge_changed_during_rebuild_retry")
    await index.finish_rebuild()
    return {"relations_restored": restored, "status": "complete"}
