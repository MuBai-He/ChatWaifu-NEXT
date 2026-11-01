"""Application boundary for owner-private knowledge; persistence remains in its adapter."""

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.knowledge.contracts import Citation, Mutation, PushRequest, Record
from chatwaifu_runtime.knowledge.indexer import GraphIndex
from chatwaifu_runtime.knowledge.retrieval import KnowledgeRetrieval


class KnowledgeRepository(Protocol):
    async def private_owner(self, session_id: str) -> str: ...
    async def push(self, owner: str, body: PushRequest) -> JsonObject: ...
    async def pull(self, owner: str, device: str, after: int, limit: int = 100) -> JsonObject: ...
    async def acknowledge(
        self, owner: str, device: str, cursor: int, applied: list[Citation] | None = None
    ) -> None: ...
    async def record_ack(self, owner: str, record_id: str, revision: int) -> JsonObject: ...
    async def exact_memories(self, owner: str, text: str) -> list[Record]: ...
    async def memory_identity(self, owner: str, predicate: str) -> list[Record]: ...
    async def resolve_conflict(
        self,
        owner: str,
        conflict_id: str,
        keep_local: bool,
        operation_id: str,
        expected_revision: int,
    ) -> JsonObject: ...
    async def source_window(self, owner: str, room_id: str, at: int) -> list[Record]: ...
    async def apply_mutation(self, owner: str, mutation: "Mutation") -> JsonObject: ...
    async def get(
        self, owner: str, record_id: str, revision: int | None = None
    ) -> Record | None: ...
    async def status(self, owner: str) -> JsonObject: ...
    async def conflicts(self, owner: str) -> list[JsonObject]: ...
    async def epoch(self, owner: str) -> int: ...
    async def eligible(
        self, owner: str, citation: Citation, *, historical: bool = False
    ) -> bool: ...
    async def records(
        self, owner: str, *, query: str = "", kind: str | None = None, limit: int = 100
    ) -> list[Record]: ...
    async def records_page(
        self,
        owner: str,
        *,
        query: str,
        kind: str | None,
        limit: int,
        collection: bool,
        cursor: str | None,
    ) -> tuple[list[Record], str | None, int]: ...
    async def allowed_rooms(self, owner: str) -> list[str]: ...
    async def source_rooms(self, owner: str) -> list[JsonObject]: ...
    async def create_viewer(self, owner: str, session: str, device: str, ttl: int) -> str: ...
    async def validate_viewer(self, token: str) -> str | None: ...


class KnowledgeService:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository
        self.retrieval: KnowledgeRetrieval | None = None
        self.index: GraphIndex | None = None
        self.enabled = False
        self.on_change: Callable[[], None] | None = None
        self.on_invalidate: Callable[[], None] | None = None

    async def owner(self, session_id: UUID | str) -> str:
        return await self.repository.private_owner(str(session_id))

    async def push(self, body: PushRequest) -> JsonObject:
        result = await self.repository.push(await self.owner(body.session_id), body)
        receipts = result.get("receipts", [])
        changed = (
            [
                receipt
                for receipt in receipts
                if isinstance(receipt, dict)
                and receipt.get("status") == "accepted"
                and receipt.get("content_changed") is not False
            ]
            if isinstance(receipts, list)
            else []
        )
        if self.on_invalidate is not None and (
            body.policies
            or (
                changed
                and any(m.action == "forget" or m.expected_revision > 0 for m in body.mutations)
            )
        ):
            self.on_invalidate()
        if self.on_change is not None:
            self.on_change()
        return result

    async def close(self) -> None:
        if self.index is not None:
            await self.index.close()
