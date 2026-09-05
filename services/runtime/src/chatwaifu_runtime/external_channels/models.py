"""Internal records at the External Channel Gateway persistence boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Self, cast
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
from chatwaifu_protocol.events import GenericCoreEvent

from chatwaifu_runtime.providers.contracts import LlmInputImage


@dataclass(frozen=True, slots=True)
class ChannelInboundImageInput:
    source_fingerprint: str
    load: Callable[[], Awaitable[LlmInputImage]] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        raw_fp = cast(object, self.source_fingerprint)
        if (
            not isinstance(raw_fp, str)
            or len(self.source_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in self.source_fingerprint)
        ):
            raise ValueError("source_fingerprint must be a 64-character lowercase hex string")


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


@dataclass(frozen=True, slots=True)
class ChannelDeliveryPartDeferRequest:
    delivery_id: UUID
    part_id: UUID
    lease_id: UUID
    not_before_at: datetime
    error: StructuredError | None = None


@dataclass(frozen=True, slots=True)
class DeliveryTransitionResult:
    plan: ChannelDeliveryPlanRecord
    part: ChannelDeliveryPartRecord | None
    applied: bool
    persisted_events: tuple[GenericCoreEvent, ...] = ()

    def __iter__(self):
        return iter((self.plan, self.part))

    def __getitem__(self, index: int) -> object:
        return (self.plan, self.part)[index]

    def __getattr__(self, name: str) -> object:
        if self.part is not None and hasattr(self.part, name):
            return getattr(self.part, name)
        if hasattr(self.plan, name):
            return getattr(self.plan, name)
        raise AttributeError(f"'DeliveryTransitionResult' object has no attribute '{name}'")


@dataclass(frozen=True, slots=True)
class CompleteTurnResult:
    turn: ChannelTurnRecord
    persisted_events: tuple[GenericCoreEvent, ...] = ()

    def __iter__(self):
        return iter((self.turn, self.persisted_events))

    def __getitem__(self, index: int) -> object:
        return (self.turn, self.persisted_events)[index]

    def __getattr__(self, name: str) -> object:
        if hasattr(self.turn, name):
            return getattr(self.turn, name)
        raise AttributeError(f"'CompleteTurnResult' object has no attribute '{name}'")


class LeaseRecoveryResult(int):
    """Subclass of int for backward compatibility with `recovered_count: int` callers."""

    terminal_plans: tuple[ChannelDeliveryPlanRecord, ...]
    persisted_events: tuple[GenericCoreEvent, ...]

    def __new__(
        cls,
        count: int,
        terminal_plans: tuple[ChannelDeliveryPlanRecord, ...] = (),
        persisted_events: tuple[GenericCoreEvent, ...] = (),
    ) -> Self:
        obj = super().__new__(cls, count)
        obj.terminal_plans = tuple(terminal_plans)
        obj.persisted_events = tuple(persisted_events)
        return obj
