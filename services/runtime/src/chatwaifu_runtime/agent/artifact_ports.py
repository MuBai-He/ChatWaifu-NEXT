"""Scoped artifact metadata persistence; content paths never cross the protocol."""

from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.agent import ArtifactRef


class ArtifactRepository(Protocol):
    async def put(self, scope: str, artifact: ArtifactRef, relative_path: str) -> None: ...
    async def get(self, scope: str, artifact_id: UUID) -> tuple[ArtifactRef, str] | None: ...
    async def list(self, scope: str, session_id: UUID) -> list[ArtifactRef]: ...
