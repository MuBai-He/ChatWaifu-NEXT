"""Command envelopes represent requested actions, not facts that occurred."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.base import JsonObject, ProtocolModel
from chatwaifu_protocol.version import SCHEMA_VERSION


class CommandEnvelope[CommandTypeT: str, PayloadT: ProtocolModel | JsonObject](ProtocolModel):
    command_id: UUID
    schema_version: str = SCHEMA_VERSION
    command_type: CommandTypeT
    issued_at: AwareDatetime
    issuer: str = Field(min_length=1)
    session_id: UUID | None = None
    turn_id: UUID | None = None
    generation_id: UUID | None = None
    correlation_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    payload: PayloadT


class SessionStartPayload(ProtocolModel):
    character_id: str = Field(min_length=1)


class TextSendPayload(ProtocolModel):
    text: str = Field(min_length=1, max_length=20_000)


class ConversationInterruptPayload(ProtocolModel):
    reason: str = Field(default="user_interruption", min_length=1)


class SessionStartCommand(CommandEnvelope[Literal["cmd.session.start"], SessionStartPayload]):
    command_type: Literal["cmd.session.start"] = "cmd.session.start"


class TextSendCommand(CommandEnvelope[Literal["cmd.text.send"], TextSendPayload]):
    command_type: Literal["cmd.text.send"] = "cmd.text.send"


class ConversationInterruptCommand(
    CommandEnvelope[Literal["cmd.conversation.interrupt"], ConversationInterruptPayload]
):
    command_type: Literal["cmd.conversation.interrupt"] = "cmd.conversation.interrupt"


CommandModel = SessionStartCommand | TextSendCommand | ConversationInterruptCommand
