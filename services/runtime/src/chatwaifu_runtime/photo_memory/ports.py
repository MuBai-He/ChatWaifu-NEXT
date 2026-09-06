from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
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

    async def get_photos(
        self, scope: str, character_id: str, photo_ids: Sequence[UUID]
    ) -> list[SavedPhoto]: ...

    async def register_recall(
        self, scope: str, character_id: str, photo_ids: tuple[UUID, ...], *, generation_id: UUID
    ) -> list[SavedPhoto]: ...

    async def delete(self, scope: str, character_id: str, photo_id: UUID) -> PhotoDeletion: ...


class EmbeddingModality(StrEnum):
    TEXT = "text"
    IMAGE = "image"


class DocumentRepresentationKind(StrEnum):
    PHOTO_DESCRIPTION_V1 = "photo_description_v1"
    PHOTO_VISUAL_V1 = "photo_visual_v1"


class EmbeddingQueryMode(StrEnum):
    TEXT = "text"
    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class PhotoEmbeddingInput:
    modality: EmbeddingModality
    text: str | None = None
    image_bytes: bytes | None = None
    mime_type: str | None = None

    @classmethod
    def from_text(cls, text: str) -> "PhotoEmbeddingInput":
        return cls(modality=EmbeddingModality.TEXT, text=text)

    @classmethod
    def from_image(cls, image_bytes: bytes, mime_type: str) -> "PhotoEmbeddingInput":
        return cls(modality=EmbeddingModality.IMAGE, image_bytes=image_bytes, mime_type=mime_type)


@dataclass(frozen=True, slots=True)
class EmbeddingDescriptor:
    supported_modalities: frozenset[EmbeddingModality]
    vector_space_id: str
    semantic_capability: bool
    enabled: bool
    opaque_fingerprint: str


class PhotoEmbeddingPort(Protocol):
    """Narrow provider-neutral embedding port with multimodal reservation."""

    def describe(self) -> EmbeddingDescriptor: ...

    async def embed(self, inputs: Sequence[PhotoEmbeddingInput]) -> list[list[float]]: ...


class PhotoSemanticPersistencePort(Protocol):
    """Scoped index reads and transactional writes only; no provider calls or ranking."""

    async def upsert_embedding(
        self,
        scope: str,
        character_id: str,
        photo_id: UUID,
        sha256: str,
        representation: str,
        vector_space_id: str,
        model_fingerprint: str,
        route_generation: int,
        vector: list[float],
        *,
        guard: Callable[[], bool] | None = None,
    ) -> bool: ...

    async def get_max_route_generation(self) -> int: ...

    async def list_embeddings(
        self,
        scope: str,
        character_id: str,
        representation: str,
        vector_space_id: str | None = None,
    ) -> list[tuple[UUID, list[float]]]: ...

    async def count_embeddings(
        self,
        scope: str,
        character_id: str,
        representation: str,
    ) -> int: ...

    async def delete_embedding(self, photo_id: UUID) -> None: ...

    async def purge_stale_spaces(
        self,
        representation: str,
        active_vector_space_id: str,
    ) -> int: ...

    async def purge_stale_generations(
        self,
        representation: str,
        active_route_generation: int,
    ) -> int: ...

    async def list_unindexed_photos(
        self,
        scope: str,
        character_id: str,
        representation: str,
        vector_space_id: str,
        *,
        limit: int = 50,
    ) -> list[SavedPhoto]: ...

    async def list_all_unindexed_photos(
        self,
        representation: str,
        vector_space_id: str,
        *,
        limit: int = 50,
    ) -> list[tuple[str, str, SavedPhoto]]: ...

    async def list_all_photos(
        self,
    ) -> list[tuple[str, str, SavedPhoto]]: ...
