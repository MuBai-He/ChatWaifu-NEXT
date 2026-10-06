"""Group persistence transactions return facts; callers own cancellation and publishing."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from chatwaifu_protocol.channel_groups import ChannelGroupPauseReason
from chatwaifu_protocol.channels import ChannelAudioDeliveryPartPayload, ChannelDeliveryPartDraft

from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupAdmission,
    ChannelGroupAdmissionResult,
    ChannelGroupAudienceObservation,
    ChannelGroupAuthorization,
    ChannelGroupPlanResult,
    ChannelGroupRouteLineage,
    ChannelGroupRouteMember,
    ChannelGroupRouteRecord,
    ChannelGroupTransition,
    ChannelParticipantLinkRecord,
)
from chatwaifu_runtime.external_channels.models import ChannelBindingRecord


class ChannelGroupRepository(Protocol):
    async def create_observation(self, observation: ChannelGroupAudienceObservation) -> None: ...

    async def get_observation(
        self, observation_id: UUID
    ) -> ChannelGroupAudienceObservation | None: ...

    async def get_link(self, link_id: UUID) -> ChannelParticipantLinkRecord | None: ...

    async def list_links(
        self, connection_id: UUID, *, limit: int = 25, cursor: str | None = None
    ) -> tuple[tuple[ChannelParticipantLinkRecord, ...], str | None]: ...

    async def create_link(
        self, observation_id: UUID, sender_key: str, participant_id: str, *, created_at: datetime
    ) -> ChannelParticipantLinkRecord: ...

    async def update_link(
        self, link_id: UUID, *, enabled: bool, expected_revision: int, updated_at: datetime
    ) -> ChannelGroupTransition: ...

    async def get_route(self, route_id: UUID) -> ChannelGroupRouteRecord | None: ...

    async def find_route(
        self, connection_id: UUID, group_id: str
    ) -> ChannelGroupRouteRecord | None: ...

    async def is_group_scene(self, scene_id: str) -> bool: ...

    async def list_routes(
        self, connection_id: UUID, *, limit: int = 25, cursor: str | None = None
    ) -> tuple[tuple[ChannelGroupRouteRecord, ...], str | None]: ...

    async def create_route(self, route: ChannelGroupRouteRecord) -> ChannelGroupRouteRecord: ...

    async def update_route(
        self,
        route_id: UUID,
        *,
        expected_revision: int,
        enabled: bool,
        observation_id: UUID | None,
        members: tuple[ChannelGroupRouteMember, ...],
        scene_id: str,
        updated_at: datetime,
        allow_requested_voice: bool | None = None,
    ) -> ChannelGroupTransition: ...

    async def pause_routes(
        self,
        *,
        reason: ChannelGroupPauseReason,
        updated_at: datetime,
        connection_id: UUID | None = None,
        group_id: str | None = None,
        scene_id: str | None = None,
        link_id: UUID | None = None,
    ) -> tuple[ChannelGroupTransition, ...]: ...

    async def find_group_binding(
        self, route_id: UUID, scene_id: str, sender_key: str
    ) -> ChannelBindingRecord | None: ...

    async def admit_group_turn(
        self, admission: ChannelGroupAdmission
    ) -> ChannelGroupAdmissionResult: ...

    async def get_group_turn(self, channel_turn_id: UUID) -> ChannelGroupAdmissionResult | None: ...

    async def find_group_turn(
        self, connection_id: UUID, group_id: str, external_message_id: str
    ) -> ChannelGroupAdmissionResult | None: ...

    async def list_group_turns(
        self, route_id: UUID, *, limit: int = 25, cursor: str | None = None
    ) -> tuple[tuple[ChannelGroupAdmissionResult, ...], str | None]: ...

    async def begin_group_turn(
        self, lineage: ChannelGroupRouteLineage, *, updated_at: datetime
    ) -> bool: ...

    async def authorize_group_turn(
        self, lineage: ChannelGroupRouteLineage
    ) -> ChannelGroupAuthorization: ...

    async def release_group_active(
        self, route_id: UUID, channel_turn_id: UUID, *, updated_at: datetime
    ) -> UUID | None: ...

    async def create_group_plan(
        self,
        lineage: ChannelGroupRouteLineage,
        *,
        reply_text: str,
        delivery_id: UUID,
        completed_at: datetime,
        parts: tuple[ChannelDeliveryPartDraft, ...] | None = None,
    ) -> ChannelGroupPlanResult: ...

    async def create_group_voice_plan(
        self,
        lineage: ChannelGroupRouteLineage,
        *,
        payload: ChannelAudioDeliveryPartPayload,
        delivery_id: UUID,
        created_at: datetime,
    ) -> ChannelGroupPlanResult: ...

    async def cancel_group_turn(
        self, channel_turn_id: UUID, *, expected_revision: int, reason: str, updated_at: datetime
    ) -> ChannelGroupTransition: ...
