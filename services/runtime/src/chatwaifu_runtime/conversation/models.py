"""Conversation coordination values shared by domain collaborators."""

from dataclasses import dataclass
from uuid import UUID

from chatwaifu_protocol.session import GenerationState


@dataclass(frozen=True, slots=True)
class GenerationAccepted:
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    audio_stream_id: UUID
    state: GenerationState


@dataclass(frozen=True, slots=True)
class SessionDataReset:
    session_id: UUID
    character_id: str
    user_scope: str
    turns_deleted: int
    events_deleted: int
    memories_deleted: int
    audio_assets_deleted: int
