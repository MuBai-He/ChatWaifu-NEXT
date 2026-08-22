"""Permission and per-invocation confirmation contracts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from chatwaifu_protocol.base import JsonObject, ProtocolModel, SideEffect


class PermissionRequest(ProtocolModel):
    request_id: UUID
    principal: str
    capability: str
    permission: str
    side_effect: SideEffect
    reason: str
    context: JsonObject = Field(default_factory=dict)
    requested_at: AwareDatetime


class PermissionDecision(ProtocolModel):
    request_id: UUID
    decision: Literal["allow_once", "allow_session", "allow_always", "deny"]
    decided_by: str
    decided_at: AwareDatetime
    reason: str | None = None
