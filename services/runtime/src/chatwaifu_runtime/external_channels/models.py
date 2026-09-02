"""Internal records at the External Channel Gateway persistence boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
    ChannelDeliveryStatus,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError


@dataclass(frozen=True, slots=True)
class ChannelConnectionRecord:
    configuration: ChannelConnectionConfiguration
    status: ChannelConnectionStatus
    access_token_hash: str
    last_error: StructuredError | None
    last_seen_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ChannelBindingRecord:
    binding_id: UUID
    connection_id: UUID
    conversation_key: str
    sender_key: str
    session_id: UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChannelTurnRecord:
    channel_turn_id: UUID
    connection_id: UUID
    binding_id: UUID
    external_message_id: str
    content_sha256: str
    account_key: str | None
    conversation_key: str
    chat_type: ChannelChatType
    conversation_label: str | None
    sender_key: str
    sender_display_name: str | None
    principal_scope: str
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    status: ChannelTurnStatus
    reply_text: str | None
    error: StructuredError | None
    delivery_id: UUID | None
    delivery_status: ChannelDeliveryStatus | None
    revision: int
    accepted_at: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ChannelDeliveryRecord:
    delivery_id: UUID
    channel_turn_id: UUID
    connection_id: UUID
    status: ChannelDeliveryStatus
    attempt: int
    lease_id: UUID | None
    lease_expires_at: datetime | None
    provider_message_id: str | None
    last_error: StructuredError | None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None
