"""Photo assets and observed descriptions are evidence, not inferred personal facts."""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from chatwaifu_protocol.photo_memory import PhotoMemoryDeleteResult


class PhotoMemoryRevisionConflict(ValueError):
    """Concurrent settings or deletion invalidated the requested write."""


@dataclass(frozen=True, slots=True)
class PhotoSaveCandidate:
    data: bytes = field(repr=False)
    mime_type: Literal["image/png", "image/jpeg"]
    width: int
    height: int
    title: str
    description: str
    confidence: float
    keywords: tuple[str, ...]
    source_connection_id: UUID
    generation_id: UUID
    captured_at: str | None = None
    captured_at_offset: str | None = None
    original_width: int | None = None
    original_height: int | None = None
    original_mime_type: Literal["image/png", "image/jpeg"] | None = None


@dataclass(frozen=True, slots=True)
class PhotoImage:
    data: bytes = field(repr=False)
    mime_type: Literal["image/png", "image/jpeg"]


@dataclass(frozen=True, slots=True)
class PhotoGenerationReference:
    session_id: UUID
    generation_id: UUID


@dataclass(frozen=True, slots=True)
class PhotoDeletion:
    result: PhotoMemoryDeleteResult
    affected_generations: tuple[PhotoGenerationReference, ...] = ()
