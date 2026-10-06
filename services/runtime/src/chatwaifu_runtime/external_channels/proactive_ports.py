"""Persistence boundary for owner-opt-in proactive work, without provider I/O."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.channel_proactive import ChannelProactivePolicyUpdate
from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.external_channels.models import DeliveryTransitionResult
from chatwaifu_runtime.external_channels.proactive_models import (
    ChannelOutboundAuthorization,
    ChannelOutboundIntentRecord,
    ChannelOutboundIntentRecordPage,
    ChannelOutboundReservationResult,
    ChannelProactiveContext,
    ChannelProactivePolicyRecord,
)


class ChannelProactiveRepository(Protocol):
    async def get_policy(self, connection_id: UUID) -> ChannelProactivePolicyRecord: ...

    async def update_policy(
        self, connection_id: UUID, update: ChannelProactivePolicyUpdate, *, updated_at: datetime
    ) -> ChannelProactivePolicyRecord: ...

    async def get_context(
        self, connection_id: UUID, *, as_of: datetime
    ) -> ChannelProactiveContext: ...

    async def list_contexts(
        self, *, as_of: datetime, limit: int = 32, after_connection_id: UUID | None = None
    ) -> tuple[ChannelProactiveContext, ...]: ...

    async def reserve_intent(
        self, connection_id: UUID, *, as_of: datetime, generation_active: bool = False
    ) -> ChannelOutboundReservationResult: ...

    async def get_intent(self, request_id: UUID) -> ChannelOutboundIntentRecord | None: ...

    async def list_intents(
        self, connection_id: UUID, *, limit: int = 50, cursor: str | None = None
    ) -> ChannelOutboundIntentRecordPage: ...

    async def list_active_intents(
        self, connection_id: UUID | None = None, *, limit: int = 32
    ) -> tuple[ChannelOutboundIntentRecord, ...]: ...

    async def claim_generation(
        self, request_id: UUID, *, expected_revision: int, claimed_at: datetime
    ) -> ChannelOutboundIntentRecord | None: ...

    async def authorize_intent(
        self, request_id: UUID, *, as_of: datetime
    ) -> ChannelOutboundAuthorization: ...

    async def create_outbound_text_plan(
        self, request_id: UUID, *, reply_text: str, created_at: datetime
    ) -> DeliveryTransitionResult: ...

    async def settle_intent(
        self,
        request_id: UUID,
        *,
        reason: str,
        settled_at: datetime,
        cancel: bool = False,
        expected_revision: int | None = None,
        error: StructuredError | None = None,
    ) -> ChannelOutboundIntentRecord: ...

    async def cancel_for_connection(
        self, connection_id: UUID, *, reason: str, requested_at: datetime
    ) -> tuple[ChannelOutboundIntentRecord, ...]: ...

    async def cancel_for_binding(
        self, binding_id: UUID, *, reason: str, requested_at: datetime
    ) -> tuple[ChannelOutboundIntentRecord, ...]: ...

    async def sync_delivery_result(
        self, request_id: UUID, *, updated_at: datetime
    ) -> ChannelOutboundIntentRecord: ...

    async def is_channel_session(self, session_id: UUID) -> bool: ...
