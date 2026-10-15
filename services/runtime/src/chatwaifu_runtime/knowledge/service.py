"""Application boundary for owner-private knowledge; persistence remains in its adapter."""

from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.base import JsonObject

from chatwaifu_runtime.knowledge.contracts import Citation, PushRequest, Record
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
    async def allowed_rooms(self, owner: str) -> list[str]: ...
    async def create_viewer(self, owner: str, session: str, device: str, ttl: int) -> str: ...
    async def validate_viewer(self, token: str) -> str | None: ...


class KnowledgeService:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository
        self.retrieval: KnowledgeRetrieval | None = None
        self.index: GraphIndex | None = None
        self.enabled = False

    async def owner(self, session_id: UUID | str) -> str:
        return await self.repository.private_owner(str(session_id))

    async def push(self, body: PushRequest) -> JsonObject:
        return await self.repository.push(await self.owner(body.session_id), body)

    async def close(self) -> None:
        if self.index is not None:
            await self.index.close()
