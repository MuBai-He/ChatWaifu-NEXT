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
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelDeliveryRecord,
    ChannelTurnRecord,
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

    async def list_inflight_turns(self) -> tuple[ChannelTurnRecord, ...]: ...

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
    ) -> ChannelTurnRecord: ...

    async def create_delivery_plan(
        self,
        channel_turn_id: UUID,
        *,
        delivery_id: UUID,
        parts: Sequence[ChannelDeliveryPartDraft],
        created_at: datetime,
    ) -> ChannelDeliveryPlanRecord: ...

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
    ) -> ChannelDeliveryPartRecord | None: ...

    async def acknowledge_delivery_part(
        self,
        acknowledgement: ChannelDeliveryPartAcknowledgement,
        *,
        updated_at: datetime,
    ) -> tuple[ChannelDeliveryPlanRecord, ChannelDeliveryPartRecord]: ...

    async def cancel_remaining_delivery_parts(
        self,
        delivery_id: UUID,
        cancel_request: ChannelDeliveryPartsCancelRequest,
    ) -> ChannelDeliveryPlanRecord: ...

    async def recover_expired_delivery_part_leases(
        self,
        *,
        as_of: datetime,
    ) -> int: ...

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
