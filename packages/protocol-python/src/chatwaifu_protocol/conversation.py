"""Conversation interruption contracts shared before the runtime state machine exists."""

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.base import ProtocolModel


class InterruptionInitiator(StrEnum):
    USER = "user"
    SYSTEM = "system"
    SKILL = "skill"


class ConversationInterruption(ProtocolModel):
    interruption_id: UUID
    session_id: UUID
    generation_id: UUID
    initiated_by: InterruptionInitiator
    reason: str = Field(min_length=1)
    requested_at: AwareDatetime
