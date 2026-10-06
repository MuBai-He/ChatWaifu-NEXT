"""Typed, provider-neutral values for fixed-owner proactive persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from chatwaifu_protocol.channel_proactive import (
    ChannelOutboundIntentStatus,
    ChannelProactivePolicy,
    ChannelProactivePreview,
    ChannelProactiveReason,
)
from chatwaifu_protocol.channels import ChannelConnectionStatus, ChannelDeliveryStatus
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import GenericCoreEvent

from chatwaifu_runtime.external_channels.models import (
    ChannelBindingRecord,
    ChannelConnectionRecord,
    ChannelTurnRecord,
)


@dataclass(frozen=True, slots=True)
class ChannelProactivePolicyRecord:
    connection_id: UUID
    policy: ChannelProactivePolicy
    revision: int = 0
    binding_id: UUID | None = None
    authorized_route_revision: int | None = None
    updated_at: datetime | None = None
    persisted_events: tuple[GenericCoreEvent, ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ChannelOutboundIntentRecord:
    request_id: UUID
    connection_id: UUID
    binding_id: UUID
    source_event_key: str
    anchor_channel_turn_id: UUID
    account_key: str
    sender_key: str
    conversation_key: str
    character_id: str
    principal_scope: str
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    audio_stream_id: UUID
    policy_revision: int
    route_revision: int
    revision: int
    status: ChannelOutboundIntentStatus
    budget_day: str
    not_before_at: datetime
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    settled_at: datetime | None = None
    settled_reason: str | None = None
    cancel_requested_at: datetime | None = None
    cancel_reason: str | None = None
    reply_text: str | None = None
    reply_sha256: str | None = None
    delivery_id: UUID | None = None
    delivery_status: ChannelDeliveryStatus | None = None
    provider_receipt_present: bool = False
    error: StructuredError | None = None
    persisted_events: tuple[GenericCoreEvent, ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ChannelProactiveContext:
    policy: ChannelProactivePolicyRecord
    connection: ChannelConnectionRecord | None
    binding: ChannelBindingRecord | None
    last_owner_turn: ChannelTurnRecord | None
    reserved_today: int
    last_reserved_at: datetime | None
    pending_request_id: UUID | None
    global_active_count: int
    conversation_busy: bool
    episode_not_before_at: datetime | None = None
    episode_expires_at: datetime | None = None
    episode_reserved: bool = False
    episode_revoked: bool = False


@dataclass(frozen=True, slots=True)
class ChannelOutboundReservationResult:
    intent: ChannelOutboundIntentRecord | None
    created: bool
    reason: ChannelProactiveReason


@dataclass(frozen=True, slots=True)
class ChannelOutboundAuthorization:
    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ChannelOutboundIntentRecordPage:
    items: tuple[ChannelOutboundIntentRecord, ...]
    next_cursor: str | None


def evaluate_proactive_context(
    context: ChannelProactiveContext, *, as_of: datetime, generation_active: bool = False
) -> ChannelProactivePreview:
    """Pure preview and admission policy; never reserve or invoke a provider."""
    policy = context.policy.policy
    connection = context.connection
    binding = context.binding
    anchor = context.last_owner_turn
    due = context.episode_not_before_at
    expiry = context.episode_expires_at
    if anchor is not None and due is None:
        due = anchor.accepted_at + timedelta(minutes=policy.idle_minutes)
        expiry = due + timedelta(minutes=policy.ttl_minutes)
    reason = ChannelProactiveReason.ELIGIBLE
    if not policy.enabled:
        reason = ChannelProactiveReason.DISABLED
    elif connection is None or connection.configuration.provider_id != "qq_napcat":
        reason = ChannelProactiveReason.UNSUPPORTED_PROVIDER
    elif (
        not connection.configuration.enabled
        or connection.deleted_at is not None
        or connection.status is not ChannelConnectionStatus.READY
        or context.policy.authorized_route_revision != connection.revision
    ):
        reason = ChannelProactiveReason.CONNECTION_UNAVAILABLE
    elif binding is None or context.policy.binding_id != binding.binding_id:
        reason = ChannelProactiveReason.OWNER_BINDING_REQUIRED
    elif anchor is None:
        reason = ChannelProactiveReason.NO_OWNER_ACTIVITY
    elif context.episode_reserved or context.episode_revoked:
        reason = ChannelProactiveReason.EPISODE_ALREADY_RESERVED
    elif due is None or as_of < due:
        reason = ChannelProactiveReason.IDLE_THRESHOLD_NOT_REACHED
    elif expiry is None or as_of >= expiry:
        reason = ChannelProactiveReason.IDLE_WINDOW_EXPIRED
    elif proactive_quiet_hours(policy, as_of):
        reason = ChannelProactiveReason.QUIET_HOURS
    elif context.conversation_busy or generation_active:
        reason = ChannelProactiveReason.CONVERSATION_BUSY
    elif context.last_reserved_at is not None and as_of < (
        context.last_reserved_at + timedelta(minutes=policy.cooldown_minutes)
    ):
        reason = ChannelProactiveReason.COOLDOWN_ACTIVE
    elif context.reserved_today >= policy.daily_budget:
        reason = ChannelProactiveReason.DAILY_BUDGET_EXHAUSTED
    elif context.pending_request_id is not None or context.global_active_count >= 32:
        reason = ChannelProactiveReason.CAPACITY_REACHED
    return ChannelProactivePreview(
        connection_id=context.policy.connection_id,
        binding_id=binding.binding_id if binding is not None else None,
        policy_revision=context.policy.revision,
        eligible=reason is ChannelProactiveReason.ELIGIBLE,
        reason=reason,
        evaluated_at=as_of,
        last_owner_at=anchor.accepted_at if anchor is not None else None,
        next_eligible_at=due,
        expires_at=expiry,
        reserved_today=context.reserved_today,
        remaining_daily_budget=max(0, policy.daily_budget - context.reserved_today),
        last_reserved_at=context.last_reserved_at,
        pending_request_id=context.pending_request_id,
    )


def proactive_quiet_hours(policy: ChannelProactivePolicy, as_of: datetime) -> bool:
    if not policy.quiet_hours_enabled:
        return False
    current = as_of.astimezone(ZoneInfo(policy.timezone)).time().replace(tzinfo=None)
    start, end = time.fromisoformat(policy.quiet_start), time.fromisoformat(policy.quiet_end)
    if start == end:
        return True
    return start <= current < end if start < end else current >= start or current < end
