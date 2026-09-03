"""Internal records at the External Channel Gateway persistence boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartPayload,
    ChannelDeliveryPartStatus,
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
    plan_version: int = 1
    part_count: int = 1
    delivered_part_count: int = 0
    cancel_requested_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ChannelDeliveryPartRecord:
    part_id: UUID
    delivery_id: UUID
    ordinal: int
    kind: ChannelDeliveryPartKind
    payload: ChannelDeliveryPartPayload
    required: bool
    status: ChannelDeliveryPartStatus
    delay_after_ms: int
    not_before_at: datetime | None
    attempt: int
    lease_id: UUID | None
    lease_expires_at: datetime | None
    provider_client_id: str
    provider_message_id: str | None
    last_error: StructuredError | None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None


@dataclass(frozen=True, slots=True)
class ChannelDeliveryPlanRecord:
    delivery: ChannelDeliveryRecord
    parts: tuple[ChannelDeliveryPartRecord, ...]

    @property
    def delivery_id(self) -> UUID:
        return self.delivery.delivery_id

    @property
    def channel_turn_id(self) -> UUID:
        return self.delivery.channel_turn_id

    @property
    def connection_id(self) -> UUID:
        return self.delivery.connection_id

    @property
    def status(self) -> ChannelDeliveryStatus:
        return self.delivery.status

    @property
    def plan_version(self) -> int:
        return self.delivery.plan_version

    @property
    def part_count(self) -> int:
        return len(self.parts)

    @property
    def delivered_part_count(self) -> int:
        return sum(1 for part in self.parts if part.status is ChannelDeliveryPartStatus.DELIVERED)

    @property
    def cancel_requested_at(self) -> datetime | None:
        return self.delivery.cancel_requested_at

    @property
    def next_pending_ordinal(self) -> int | None:
        for part in self.parts:
            if part.status in {
                ChannelDeliveryPartStatus.PENDING,
                ChannelDeliveryPartStatus.SENDING,
            }:
                return part.ordinal
        return None

    @property
    def created_at(self) -> datetime:
        return self.delivery.created_at

    @property
    def updated_at(self) -> datetime:
        return self.delivery.updated_at

    @property
    def delivered_at(self) -> datetime | None:
        return self.delivery.delivered_at
