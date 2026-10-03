"""Operator-managed, fixed-owner external proactive text contracts.

Policy preview is read-only. It never requests model generation or provider send.
Provider account/sender identifiers, secrets and raw transport data stay outside
these DTOs; the connection and binding select the trusted destination.
"""

from __future__ import annotations

from datetime import time
from enum import StrEnum
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, ConfigDict, Field, field_validator

from chatwaifu_protocol.channels import ChannelDeliveryStatus, ChannelVersionedModel
from chatwaifu_protocol.errors import StructuredError


class ChannelProactivePolicy(ChannelVersionedModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    source: Literal["idle_check_in"] = "idle_check_in"
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=128)
    idle_minutes: int = Field(default=45, ge=1, le=1440)
    cooldown_minutes: int = Field(default=60, ge=1, le=10080)
    daily_budget: int = Field(default=3, ge=1, le=20)
    quiet_hours_enabled: bool = True
    quiet_start: str = Field(default="23:00", pattern=r"^\d{2}:\d{2}$")
    quiet_end: str = Field(default="08:00", pattern=r"^\d{2}:\d{2}$")
    ttl_minutes: int = Field(default=15, ge=1, le=60)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone must name an installed IANA time zone") from exc
        return value

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def validate_quiet_time(cls, value: str) -> str:
        time.fromisoformat(value)
        return value


class ChannelProactivePolicyUpdate(ChannelVersionedModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    policy: ChannelProactivePolicy


class ChannelProactivePolicySnapshot(ChannelVersionedModel):
    connection_id: UUID
    binding_id: UUID | None = None
    policy: ChannelProactivePolicy = Field(default_factory=ChannelProactivePolicy)
    revision: int = Field(default=0, ge=0)
    updated_at: AwareDatetime | None = None


class ChannelProactiveReason(StrEnum):
    ELIGIBLE = "eligible"
    DISABLED = "disabled"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    CONNECTION_UNAVAILABLE = "connection_unavailable"
    OWNER_BINDING_REQUIRED = "owner_binding_required"
    NO_OWNER_ACTIVITY = "no_owner_activity"
    IDLE_THRESHOLD_NOT_REACHED = "idle_threshold_not_reached"
    IDLE_WINDOW_EXPIRED = "idle_window_expired"
    QUIET_HOURS = "quiet_hours"
    CONVERSATION_BUSY = "conversation_busy"
    COOLDOWN_ACTIVE = "cooldown_active"
    DAILY_BUDGET_EXHAUSTED = "daily_budget_exhausted"
    EPISODE_ALREADY_RESERVED = "episode_already_reserved"
    CAPACITY_REACHED = "capacity_reached"


class ChannelProactivePreview(ChannelVersionedModel):
    connection_id: UUID
    binding_id: UUID | None = None
    policy_revision: int = Field(ge=0)
    eligible: bool
    reason: ChannelProactiveReason
    evaluated_at: AwareDatetime
    last_owner_at: AwareDatetime | None = None
    next_eligible_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    reserved_today: int = Field(default=0, ge=0)
    remaining_daily_budget: int = Field(default=0, ge=0)
    last_reserved_at: AwareDatetime | None = None
    pending_request_id: UUID | None = None


class ChannelOutboundIntentStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    PLANNED = "planned"
    SETTLED = "settled"


class ChannelOutboundIntentSnapshot(ChannelVersionedModel):
    """Intent lifecycle and actual provider facts remain separate.

    A settled cancellation does not erase a provider success which arrived
    during cancellation. UI must also display delivery_status/receipt presence.
    """

    request_id: UUID
    connection_id: UUID
    binding_id: UUID
    source: Literal["idle_check_in"] = "idle_check_in"
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    status: ChannelOutboundIntentStatus
    policy_revision: int = Field(ge=0)
    route_revision: int = Field(ge=0)
    revision: int = Field(ge=0)
    not_before_at: AwareDatetime
    expires_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime
    settled_at: AwareDatetime | None = None
    settled_reason: str | None = Field(default=None, min_length=1, max_length=128)
    cancel_requested_at: AwareDatetime | None = None
    cancel_reason: str | None = Field(default=None, min_length=1, max_length=128)
    reply_text: str | None = Field(default=None, min_length=1, max_length=2000)
    delivery_id: UUID | None = None
    delivery_status: ChannelDeliveryStatus | None = None
    provider_receipt_present: bool = False
    cancelable: bool = False
    error: StructuredError | None = None


class ChannelOutboundIntentPage(ChannelVersionedModel):
    items: list[ChannelOutboundIntentSnapshot] = Field(
        default_factory=list[ChannelOutboundIntentSnapshot], max_length=50
    )
    next_cursor: str | None = Field(default=None, min_length=1, max_length=256)


class ChannelOutboundIntentCancelRequest(ChannelVersionedModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
