"""Trusted SDK-free group values; no provider, model, or cancellation I/O."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from chatwaifu_protocol.channel_groups import (
    ChannelGroupDeliveryTarget,
    ChannelGroupPauseReason,
)
from chatwaifu_protocol.events import GenericCoreEvent

from chatwaifu_runtime.external_channels.models import ChannelBindingRecord, ChannelTurnRecord


def qq_id(value: object) -> str:
    if type(value) is not str or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ValueError("QQ identity must be a canonical positive numeric string")
    return value


def aware(value: datetime) -> datetime:
    raw = cast(object, value)
    if not isinstance(raw, datetime) or raw.tzinfo is None or raw.utcoffset() is None:
        raise ValueError("timestamp must be timezone aware")
    return value


def revision(value: object, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("revision must be an integer")
    return value


def strict_bool(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("flag must be boolean")
    return value


@dataclass(frozen=True, slots=True)
class ChannelGroupInboundDescriptor:
    connection_id: UUID
    account_key: str
    group_id: str
    sender_key: str
    external_message_id: str
    text: str
    received_at: datetime

    def __post_init__(self) -> None:
        raw = cast(object, self.connection_id)
        if not isinstance(raw, UUID):
            raise ValueError("connection must be UUID")
        for value in (self.account_key, self.group_id, self.sender_key):
            qq_id(value)
        if (
            type(self.external_message_id) is not str
            or not re.fullmatch(r"-?[0-9]{1,20}", self.external_message_id)
            or str(int(self.external_message_id)) != self.external_message_id
            or int(self.external_message_id) == 0
        ):
            raise ValueError("raw provider message ID must be a signed numeric string")
        if type(self.text) is not str or not self.text.strip() or len(self.text) > 20000:
            raise ValueError("group text must be nonempty and bounded")
        if self.sender_key == self.account_key:
            raise ValueError("self messages cannot be admitted")
        aware(self.received_at)

    @property
    def content_sha256(self) -> str:
        payload = (
            self.account_key,
            self.group_id,
            self.sender_key,
            self.external_message_id,
            self.text,
        )
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ChannelParticipantLinkRecord:
    link_id: UUID
    provider_id: str
    account_key: str
    sender_key: str
    participant_id: str
    enabled: bool
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.provider_id != "qq_napcat":
            raise ValueError("unsupported group provider")
        qq_id(self.account_key)
        qq_id(self.sender_key)
        strict_bool(self.enabled)
        revision(self.revision)
        aware(self.created_at)
        aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class ChannelGroupAudienceObservation:
    observation_id: UUID
    connection_id: UUID
    connection_revision: int
    account_key: str
    group_id: str
    member_ids: tuple[str, ...]
    observed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        revision(self.connection_revision)
        qq_id(self.account_key)
        qq_id(self.group_id)
        if type(self.member_ids) is not tuple or not 2 <= len(self.member_ids) <= 32:
            raise ValueError("group audience must contain 2..32 members")
        if len(set(self.member_ids)) != len(self.member_ids) or self.account_key in self.member_ids:
            raise ValueError("audience excludes duplicate and self identities")
        for member in self.member_ids:
            qq_id(member)
        aware(self.observed_at)
        aware(self.expires_at)
        if not 0 < (self.expires_at - self.observed_at).total_seconds() <= 60:
            raise ValueError("observation expires within 60 seconds")

    @property
    def member_fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(sorted(self.member_ids)).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ChannelGroupRouteMember:
    link_id: UUID
    sender_key: str
    participant_id: str
    can_speak: bool

    def __post_init__(self) -> None:
        qq_id(self.sender_key)
        if type(self.participant_id) is not str or not 1 <= len(self.participant_id) <= 128:
            raise ValueError("participant identity must be bounded")
        strict_bool(self.can_speak)


def audience_fingerprint(members: tuple[ChannelGroupRouteMember, ...]) -> str:
    if type(members) is not tuple or not 2 <= len(members) <= 32:
        raise ValueError("group audience must contain 2..32 members")
    if len({m.sender_key for m in members}) != len(members) or len(
        {m.participant_id for m in members}
    ) != len(members):
        raise ValueError("audience mapping must be one-to-one")
    payload = sorted((m.sender_key, m.participant_id) for m in members)
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ChannelGroupRouteRecord:
    route_id: UUID
    connection_id: UUID
    account_key: str
    group_id: str
    character_id: str
    scene_id: str
    display_name: str
    revision: int
    enabled: bool
    pause_reason: ChannelGroupPauseReason | None
    observation_id: UUID
    members: tuple[ChannelGroupRouteMember, ...]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        qq_id(self.account_key)
        qq_id(self.group_id)
        revision(self.revision)
        strict_bool(self.enabled)
        audience_fingerprint(self.members)
        if self.enabled and (
            self.pause_reason is not None or not any(m.can_speak for m in self.members)
        ):
            raise ValueError("enabled route requires speakers and no pause")
        if not self.scene_id or not self.character_id or not self.display_name.strip():
            raise ValueError("route identity and name required")
        aware(self.created_at)
        aware(self.updated_at)

    @property
    def audience_fingerprint(self) -> str:
        return audience_fingerprint(self.members)


@dataclass(frozen=True, slots=True)
class ChannelGroupRouteLineage:
    route_id: UUID
    route_revision: int
    channel_turn_id: UUID
    binding_id: UUID
    scene_id: str
    audience_fingerprint: str

    def __post_init__(self) -> None:
        revision(self.route_revision)
        if not self.scene_id or not re.fullmatch(r"[0-9a-f]{64}", self.audience_fingerprint):
            raise ValueError("complete group lineage required")


@dataclass(frozen=True, slots=True)
class ChannelGroupAdmission:
    message: ChannelGroupInboundDescriptor
    route_id: UUID
    expected_route_revision: int
    session_id: UUID
    channel_turn_id: UUID
    turn_id: UUID
    generation_id: UUID
    admitted_at: datetime

    def __post_init__(self) -> None:
        revision(self.expected_route_revision)
        aware(self.admitted_at)


@dataclass(frozen=True, slots=True)
class ChannelGroupAdmissionResult:
    turn: ChannelTurnRecord
    binding: ChannelBindingRecord
    lineage: ChannelGroupRouteLineage
    duplicate: bool
    dispatch_now: bool = False
    displaced_turn_ids: tuple[UUID, ...] = ()
    persisted_events: tuple[GenericCoreEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class ChannelGroupAuthorization:
    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ChannelGroupTransition:
    route: ChannelGroupRouteRecord | None = None
    link: ChannelParticipantLinkRecord | None = None
    displaced_turn_ids: tuple[UUID, ...] = ()
    affected_session_ids: tuple[UUID, ...] = ()
    persisted_events: tuple[GenericCoreEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class ChannelGroupPlanResult:
    delivery_id: UUID
    target: ChannelGroupDeliveryTarget
    persisted_events: tuple[GenericCoreEvent, ...] = ()
