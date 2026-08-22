"""Structured cross-boundary errors safe for user-facing transport."""

from uuid import UUID

from pydantic import Field

from chatwaifu_protocol.base import JsonObject, ProtocolModel


class StructuredError(ProtocolModel):
    code: str = Field(min_length=1)
    message: str
    retryable: bool
    component: str = Field(min_length=1)
    details: JsonObject = Field(default_factory=dict)
    correlation_id: UUID | None = None
