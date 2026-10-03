"""Operator-managed QQ group routes. Creation never enables a route."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from chatwaifu_protocol.channels import ChannelTurnSnapshot, ChannelVersionedModel


class ChannelGroupPauseReason(StrEnum):
    OPERATOR_DISABLED = "operator_disabled"
    RECONNECT = "reconnect"
    MEMBERSHIP_CHANGED = "membership_changed"
    ACCOUNT_CHANGED = "account_changed"
    CONNECTION_DISABLED = "connection_disabled"
    CONNECTION_DELETED = "connection_deleted"
    CONFIGURATION_CHANGED = "configuration_changed"
    LINK_REVOKED = "link_revoked"
    SCENE_RESET = "scene_reset"
    ROUTE_DELETED = "route_deleted"


class ChannelGroupInput(ChannelVersionedModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def bounded_identifiers(cls, value: object) -> object:
        if isinstance(value, str) and (value != value.strip() or not value):
            raise ValueError("identifiers and names cannot have surrounding whitespace")
        return value


class ChannelGroupAudienceRequest(ChannelGroupInput):
    group_id: str = Field(min_length=1, max_length=20, pattern=r"^[1-9][0-9]{0,19}$")


class ChannelParticipantLinkCreate(ChannelGroupInput):
    observation_id: UUID
    sender_key: str = Field(min_length=1, max_length=20, pattern=r"^[1-9][0-9]{0,19}$")
    participant_id: str = Field(min_length=1, max_length=128)


class ChannelParticipantLinkUpdate(ChannelGroupInput):
    enabled: bool = Field(strict=True)
    expected_revision: int = Field(strict=True, ge=1)


class ChannelParticipantLinkSnapshot(ChannelVersionedModel):
    link_id: UUID
    provider_id: Literal["qq_napcat"] = "qq_napcat"
    account_key: str
    sender_key: str
    participant_id: str
    enabled: bool
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ChannelGroupAudienceSnapshot(ChannelVersionedModel):
    observation_id: UUID
    connection_id: UUID
    connection_revision: int = Field(ge=1)
    account_key: str
    group_id: str
    member_ids: list[str] = Field(min_length=2, max_length=32)
    member_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_observation(self) -> ChannelGroupAudienceSnapshot:
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("audience members must be unique")
        if not 0 < (self.expires_at - self.observed_at).total_seconds() <= 60:
            raise ValueError("observation must expire within 60 seconds")
        return self


class ChannelGroupRouteCreate(ChannelGroupInput):
    observation_id: UUID
    display_name: str = Field(min_length=1, max_length=80)
    speaker_sender_keys: list[str] = Field(default_factory=list[str], max_length=32)

    @field_validator("speaker_sender_keys")
    @classmethod
    def unique_speakers(cls, value: list[str]) -> list[str]:
        return _speakers(value)


class ChannelGroupRouteUpdate(ChannelGroupInput):
    enabled: bool = Field(strict=True)
    expected_revision: int = Field(strict=True, ge=1)
    observation_id: UUID | None = None
    speaker_sender_keys: list[str] = Field(max_length=32)

    @field_validator("speaker_sender_keys")
    @classmethod
    def unique_speakers(cls, value: list[str]) -> list[str]:
        return _speakers(value)

    @model_validator(mode="after")
    def explicit_revalidation(self) -> ChannelGroupRouteUpdate:
        if self.enabled and (self.observation_id is None or not self.speaker_sender_keys):
            raise ValueError("enabling requires an observation and at least one speaker")
        return self


class ChannelGroupRouteMemberSnapshot(ChannelVersionedModel):
    link_id: UUID
    sender_key: str
    participant_id: str
    can_speak: bool


class ChannelGroupRouteSnapshot(ChannelVersionedModel):
    route_id: UUID
    connection_id: UUID
    account_key: str
    group_id: str
    character_id: str
    scene_id: str
    display_name: str
    revision: int = Field(ge=1)
    enabled: bool = False
    pause_reason: ChannelGroupPauseReason | None = None
    observation_id: UUID
    audience_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    members: list[ChannelGroupRouteMemberSnapshot] = Field(min_length=2, max_length=32)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    deleted_at: AwareDatetime | None = None


class ChannelGroupRoutePage(ChannelVersionedModel):
    items: list[ChannelGroupRouteSnapshot] = Field(
        default_factory=list[ChannelGroupRouteSnapshot], max_length=50
    )
    next_cursor: str | None = Field(default=None, min_length=1, max_length=256)


class ChannelParticipantLinkPage(ChannelVersionedModel):
    items: list[ChannelParticipantLinkSnapshot] = Field(
        default_factory=list[ChannelParticipantLinkSnapshot], max_length=50
    )
    next_cursor: str | None = Field(default=None, min_length=1, max_length=256)


class ChannelGroupTurnCancelRequest(ChannelGroupInput):
    expected_revision: int = Field(strict=True, ge=0)


class ChannelGroupTurnSnapshot(ChannelVersionedModel):
    route_id: UUID
    route_revision: int = Field(ge=1)
    scene_id: str
    participant_id: str
    turn: ChannelTurnSnapshot
    provider_receipt_present: bool = False
    cancelable: bool = False


class ChannelGroupTurnPage(ChannelVersionedModel):
    items: list[ChannelGroupTurnSnapshot] = Field(
        default_factory=list[ChannelGroupTurnSnapshot], max_length=50
    )
    next_cursor: str | None = Field(default=None, min_length=1, max_length=256)


class ChannelGroupDeliveryTarget(ChannelVersionedModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["group"] = "group"
    connection_id: UUID
    account_key: str
    group_id: str
    route_id: UUID
    route_revision: int = Field(strict=True, ge=1)
    channel_turn_id: UUID
    scene_id: str
    audience_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def _speakers(value: list[str]) -> list[str]:
    if len(set(value)) != len(value) or any(
        not re.fullmatch(r"[1-9][0-9]{0,19}", item) for item in value
    ):
        raise ValueError("speakers must be unique canonical QQ identifiers")
    return value
