"""Atomic scoped persistence boundary for photo observations and recall provenance."""

from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.photo_memory import PhotoMemorySettings, PhotoMemorySnapshot, SavedPhoto

from chatwaifu_runtime.photo_memory.models import PhotoDeletion, PhotoImage, PhotoSaveCandidate


class PhotoMemoryRepository(Protocol):
    async def get_settings(self, scope: str, character_id: str) -> PhotoMemorySettings: ...

    async def update_settings(
        self, scope: str, character_id: str, *, retention_enabled: bool, expected_revision: int
    ) -> PhotoMemorySettings: ...

    async def snapshot(self, scope: str, character_id: str) -> PhotoMemorySnapshot: ...

    async def save(
        self,
        scope: str,
        character_id: str,
        candidate: PhotoSaveCandidate,
        *,
        expected_revision: int,
    ) -> SavedPhoto | None: ...

    async def get_image(
        self, scope: str, character_id: str, photo_id: UUID, *, expected_sha256: str | None = None
    ) -> PhotoImage | None: ...

    async def search(
        self, scope: str, character_id: str, query: str, *, limit: int = 8
    ) -> list[SavedPhoto]: ...

    async def list_recent(
        self, scope: str, character_id: str, *, limit: int = 3
    ) -> list[SavedPhoto]: ...

    async def register_recall(
        self, scope: str, character_id: str, photo_ids: tuple[UUID, ...], *, generation_id: UUID
    ) -> list[SavedPhoto]: ...

    async def delete(self, scope: str, character_id: str, photo_id: UUID) -> PhotoDeletion: ...
