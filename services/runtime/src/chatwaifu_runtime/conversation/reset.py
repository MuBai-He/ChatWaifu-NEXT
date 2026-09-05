"""Atomic persistence boundary for resetting one character experience scope."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.events import GenericCoreEvent

from chatwaifu_runtime.photo_memory.models import PhotoGenerationReference


@dataclass(frozen=True, slots=True)
class ExperienceResetRecord:
    audio_asset_ids: tuple[UUID, ...]
    memory_ids: tuple[UUID, ...]
    turns_deleted: int
    events_deleted: int
    reset_event: GenericCoreEvent
    photo_generations: tuple[PhotoGenerationReference, ...] = ()


class ExperienceResetRepository(Protocol):
    """Coordinates cross-domain truth deletion in one storage transaction."""

    async def audio_asset_ids(self, session_id: UUID) -> tuple[UUID, ...]: ...

    async def all_audio_asset_ids(self) -> tuple[UUID, ...]: ...

    async def reset(
        self,
        session_id: UUID,
        *,
        character_id: str,
        user_scope: str,
        memory_namespace: str,
        updated_at: datetime,
        reset_event: GenericCoreEvent,
    ) -> ExperienceResetRecord: ...
