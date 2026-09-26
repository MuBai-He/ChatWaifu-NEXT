"""Session, turn, and assistant generation state snapshots."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.base import ProtocolModel


class SessionState(StrEnum):
    CREATED = "created"
    CONNECTING = "connecting"
    READY = "ready"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    CLOSING = "closing"
    CLOSED = "closed"


class ConversationState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    COMMITTING_USER_TURN = "committing_user_turn"
    PLANNING = "planning"
    GENERATING = "generating"
    SPEAKING = "speaking"
    INTERRUPTING = "interrupting"
    RECOVERING = "recovering"


class GenerationState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionSnapshot(ProtocolModel):
    session_id: UUID
    character_id: str
    participant_id: str = "local"
    scene_id: str | None = None
    scene_kind: Literal["private", "shared"] = "private"
    audience_ids: list[str] = Field(default_factory=lambda: ["local"])
    user_scope: str = "local"
    state: SessionState
    conversation_state: ConversationState
    revision: int = Field(ge=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TurnSnapshot(ProtocolModel):
    turn_id: UUID
    session_id: UUID
    committed_text: str | None = None
    committed_at: AwareDatetime | None = None
    active_skill_ids: list[str] = Field(default_factory=list)
    scene_snapshot_id: UUID | None = None


class GenerationSnapshot(ProtocolModel):
    generation_id: UUID
    session_id: UUID
    turn_id: UUID
    state: GenerationState
    backend_kind: str
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    invalidated_at: AwareDatetime | None = None


class ParticipantSnapshot(ProtocolModel):
    participant_id: str
    display_name: str = Field(min_length=1, max_length=80)
    created_at: AwareDatetime


class SceneSnapshot(ProtocolModel):
    scene_id: str
    display_name: str = Field(min_length=1, max_length=120)
    participant_ids: list[str] = Field(min_length=2, max_length=32)
    created_at: AwareDatetime
