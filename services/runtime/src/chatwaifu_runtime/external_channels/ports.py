"""Persistence ports owned by the External Channel Gateway domain."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.channels import (
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
    ChannelDeliveryAcknowledgement,
    ChannelDeliveryClaimRequest,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartsCancelRequest,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.external_channels.models import (
    ChannelBindingRecord,
    ChannelConnectionRecord,
    ChannelDeliveryPartDeferRequest,
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelDeliveryRecord,
    ChannelTurnRecord,
    CompleteTurnResult,
    DeliveryTransitionResult,
    LeaseRecoveryResult,
)


class ExternalChannelRepository(Protocol):
    async def list_connections(self) -> tuple[ChannelConnectionRecord, ...]: ...

    async def get_connection(self, connection_id: UUID) -> ChannelConnectionRecord | None: ...

    async def create_connection(
        self,
        configuration: ChannelConnectionConfiguration,
        *,
        access_token_hash: str,
        created_at: datetime,
    ) -> ChannelConnectionRecord: ...

    async def update_connection(
        self,
        configuration: ChannelConnectionConfiguration,
        *,
        expected_revision: int,
        access_token_hash: str | None,
        updated_at: datetime,
    ) -> ChannelConnectionRecord: ...

    async def soft_delete_connection(
        self, connection_id: UUID, *, deleted_at: datetime
    ) -> bool: ...

    async def touch_connection(
        self,
        connection_id: UUID,
        *,
        status: ChannelConnectionStatus,
        seen_at: datetime,
        last_error: StructuredError | None = None,
    ) -> ChannelConnectionRecord: ...

    async def find_binding(
        self, connection_id: UUID, conversation_key: str
    ) -> ChannelBindingRecord | None: ...

    async def create_binding(
        self,
        *,
        binding_id: UUID,
        connection_id: UUID,
        conversation_key: str,
        sender_key: str,
        session_id: UUID,
        created_at: datetime,
    ) -> ChannelBindingRecord: ...

    async def find_turn_by_external_message(
        self, connection_id: UUID, external_message_id: str
    ) -> ChannelTurnRecord | None: ...

    async def get_turn(self, channel_turn_id: UUID) -> ChannelTurnRecord | None: ...

    async def list_inflight_turns(
        self, connection_id: UUID | None = None
    ) -> tuple[ChannelTurnRecord, ...]: ...

    async def has_inflight_turn(self, binding_id: UUID) -> bool: ...

    async def create_turn(self, turn: ChannelTurnRecord) -> ChannelTurnRecord: ...

    async def set_turn_processing(
        self, channel_turn_id: UUID, *, updated_at: datetime
    ) -> ChannelTurnRecord: ...

    async def complete_turn(
        self,
        channel_turn_id: UUID,
        *,
        reply_text: str,
        delivery_id: UUID,
        completed_at: datetime,
        parts: Sequence[ChannelDeliveryPartDraft] | None = None,
    ) -> CompleteTurnResult | ChannelTurnRecord: ...

    async def create_delivery_plan(
        self,
        channel_turn_id: UUID,
        *,
        delivery_id: UUID,
        parts: Sequence[ChannelDeliveryPartDraft],
        created_at: datetime,
    ) -> DeliveryTransitionResult | ChannelDeliveryPlanRecord: ...

    async def get_delivery_plan(self, delivery_id: UUID) -> ChannelDeliveryPlanRecord | None: ...

    async def get_delivery_plan_by_turn(
        self, channel_turn_id: UUID
    ) -> ChannelDeliveryPlanRecord | None: ...

    async def list_delivery_parts(
        self, delivery_id: UUID
    ) -> tuple[ChannelDeliveryPartRecord, ...]: ...

    async def claim_next_delivery_part(
        self,
        claim: ChannelDeliveryPartClaimRequest,
        *,
        claimed_at: datetime,
    ) -> DeliveryTransitionResult | None: ...

    async def acknowledge_delivery_part(
        self,
        acknowledgement: ChannelDeliveryPartAcknowledgement,
        *,
        updated_at: datetime,
    ) -> DeliveryTransitionResult: ...

    async def defer_delivery_part(
        self,
        defer_request: ChannelDeliveryPartDeferRequest,
        *,
        updated_at: datetime,
    ) -> DeliveryTransitionResult: ...

    async def cancel_remaining_delivery_parts(
        self,
        delivery_id: UUID,
        cancel_request: ChannelDeliveryPartsCancelRequest,
        *,
        cancel_sending_lease_id: UUID | None = None,
    ) -> DeliveryTransitionResult: ...

    async def cancel_active_delivery_plans_for_connection(
        self,
        connection_id: UUID,
        cancel_request: ChannelDeliveryPartsCancelRequest,
    ) -> int: ...

    async def find_active_delivery_plan_for_binding(
        self,
        binding_id: UUID,
    ) -> ChannelDeliveryPlanRecord | None: ...

    async def list_active_delivery_plans_for_binding(
        self,
        binding_id: UUID,
    ) -> tuple[ChannelDeliveryPlanRecord, ...]: ...

    async def list_nonterminal_delivery_plans(
        self,
        connection_id: UUID | None = None,
        *,
        limit: int = 50,
    ) -> tuple[ChannelDeliveryPlanRecord, ...]: ...

    async def next_delivery_wakeup_at(
        self,
        connection_id: UUID | None = None,
    ) -> datetime | None: ...

    async def recover_expired_delivery_part_leases(
        self,
        *,
        as_of: datetime,
    ) -> LeaseRecoveryResult: ...

    async def set_turn_terminal(
        self,
        channel_turn_id: UUID,
        *,
        status: ChannelTurnStatus,
        error: StructuredError | None,
        completed_at: datetime,
    ) -> ChannelTurnRecord: ...

    async def set_turn_cancelling(
        self, channel_turn_id: UUID, *, updated_at: datetime
    ) -> ChannelTurnRecord: ...

    async def acknowledge_delivery(
        self,
        acknowledgement: ChannelDeliveryAcknowledgement,
        *,
        updated_at: datetime,
    ) -> ChannelDeliveryRecord: ...

    async def claim_delivery(
        self,
        claim: ChannelDeliveryClaimRequest,
        *,
        claimed_at: datetime,
    ) -> ChannelDeliveryRecord | None: ...

    async def reconcile_inflight(
        self,
        *,
        status: ChannelTurnStatus,
        error: StructuredError,
        updated_at: datetime,
    ) -> int: ...

    async def set_connection_status(
        self,
        connection_id: UUID,
        *,
        status: ChannelConnectionStatus,
        last_error: StructuredError | None,
        updated_at: datetime,
    ) -> ChannelConnectionRecord: ...

    async def get_adapter_cursor(self, connection_id: UUID) -> str: ...

    async def set_adapter_cursor(
        self,
        connection_id: UUID,
        *,
        cursor: str,
        updated_at: datetime,
    ) -> None: ...
