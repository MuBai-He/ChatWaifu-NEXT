"""Fixed small-group application policy; Conversation owns every model generation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from chatwaifu_protocol.channel_groups import (
    ChannelGroupAudienceRequest,
    ChannelGroupAudienceSnapshot,
    ChannelGroupPauseReason,
    ChannelGroupRouteCreate,
    ChannelGroupRouteMemberSnapshot,
    ChannelGroupRoutePage,
    ChannelGroupRouteSnapshot,
    ChannelGroupRouteUpdate,
    ChannelGroupTurnCancelRequest,
    ChannelGroupTurnPage,
    ChannelGroupTurnSnapshot,
    ChannelParticipantLinkCreate,
    ChannelParticipantLinkPage,
    ChannelParticipantLinkSnapshot,
    ChannelParticipantLinkUpdate,
)
from chatwaifu_protocol.channels import (
    ChannelAudioDeliveryPartPayload,
    ChannelConnectionStatus,
    ChannelDeliveryPartStatus,
    ChannelGroupDeliveryTarget,
    ChannelPresentationPolicy,
    ChannelTurnReceipt,
    ChannelTurnSnapshot,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import GenericCoreEvent, PrivacyLevel
from chatwaifu_protocol.session import GenerationState, SessionSnapshot

from chatwaifu_runtime.config.group_discussion import GroupDiscussionConfig
from chatwaifu_runtime.conversation.discussion_models import GroupDiscussionContext
from chatwaifu_runtime.conversation.models import ConversationSourceContext, ConversationTurnOptions
from chatwaifu_runtime.conversation.repository import ConversationRepository
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.group_discussion import GroupDiscussionCache
from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupAdmission,
    ChannelGroupAdmissionResult,
    ChannelGroupAudienceObservation,
    ChannelGroupInboundDescriptor,
    ChannelGroupRouteMember,
    ChannelGroupRouteRecord,
    ChannelGroupTransition,
    ChannelParticipantLinkRecord,
    audience_fingerprint,
    qq_id,
)
from chatwaifu_runtime.external_channels.group_ports import ChannelGroupRepository
from chatwaifu_runtime.external_channels.group_voice import requests_group_voice
from chatwaifu_runtime.external_channels.models import (
    ChannelConnectionRecord,
    ChannelDeliveryPlanRecord,
    ChannelInboundImageInput,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.presentation import (
    InstantMessageDeliveryPlanFactory,
    group_text_parts_match_reply,
    messaging_presentation_policy,
)
from chatwaifu_runtime.external_channels.service import (
    ChannelAuthenticationError,
    ChannelBusyError,
    ChannelConflictError,
    ChannelNotFoundError,
    ChannelPolicyError,
    normalize_and_sanitize_inbound_images,
)
from chatwaifu_runtime.providers.contracts import LlmInputImage
from chatwaifu_runtime.sessions.service import SessionService
from chatwaifu_runtime.sticker_library.models import StickerLearningSource
from chatwaifu_runtime.sticker_library.service import StickerLibraryService

type AudienceReader = Callable[[UUID, str], Awaitable[tuple[str, tuple[str, ...]]]]
type Authenticator = Callable[[UUID, str], Awaitable[ChannelConnectionRecord]]

_TERMINAL_EVENTS = frozenset(
    {"assistant.generation_completed", "assistant.generation_cancelled", "system.error_raised"}
)
_JOIN_SECONDS = 5.0
_MAX_TRACKED_GROUP_EPOCHS = 128
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Admission:
    message: ChannelGroupInboundDescriptor
    task: asyncio.Task[object]
    connection_epoch: int
    group_epoch: int
    channel_turn_id: UUID = field(default_factory=uuid4)
    turn_id: UUID = field(default_factory=uuid4)
    generation_id: UUID = field(default_factory=uuid4)
    session_id: UUID | None = None
    route: ChannelGroupRouteRecord | None = None
    revoked: bool = False


@dataclass(slots=True)
class _Input:
    admission: ChannelGroupAdmissionResult
    message: ChannelGroupInboundDescriptor
    connection_epoch: int
    group_epoch: int
    presentation_policy: ChannelPresentationPolicy
    image_input: ChannelInboundImageInput | None = None
    learning_revision: int | None = None
    discussion: GroupDiscussionContext | None = None
    voice_requested: bool = False
    revoked: bool = False
    task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _Gate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


@dataclass(slots=True)
class _ImageReferenceContext:
    route_id: UUID
    route_revision: int
    scene_id: str
    settings_revision: int
    image_input: ChannelInboundImageInput
    expires_at: datetime
    used_by: str | None = None


class ChannelGroupService:
    def __init__(
        self,
        repository: ChannelGroupRepository,
        channels: ExternalChannelRepository,
        conversation: ConversationService,
        sessions: SessionService,
        publisher: EventPublisher,
        *,
        conversation_repository: ConversationRepository,
        sticker_library: StickerLibraryService | None = None,
        discussion_policy: GroupDiscussionConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._channels = channels
        self._conversation = conversation
        self._sessions = sessions
        self._publisher = publisher
        self._conversation_repository = conversation_repository
        self._sticker_library = sticker_library
        self._clock = clock
        self._delivery_plan_factory = InstantMessageDeliveryPlanFactory()
        self._audience_reader: AudienceReader | None = None
        self._authenticator: Authenticator | None = None
        self._transport_ready: Callable[[UUID], bool] = lambda _: False
        self._scheduler_wake: Callable[[UUID], None] | None = None
        self._admissions: dict[UUID, _Admission] = {}
        self._workflows: dict[UUID, _Input] = {}
        self._pending: dict[UUID, _Input] = {}
        self._gates: dict[UUID, _Gate] = {}
        self._turn_gates: dict[UUID, _Gate] = {}
        self._connection_epochs: dict[UUID, int] = {}
        self._group_epochs: dict[tuple[UUID, str], int] = {}
        self._blocked_connections: set[UUID] = set()
        self._blocked_groups: set[tuple[UUID, str]] = set()
        self._blocked_scenes: set[str] = set()
        self._image_context: dict[tuple[UUID, str, str], _ImageReferenceContext] = {}
        self._discussion = GroupDiscussionCache(discussion_policy or GroupDiscussionConfig())
        self._started = False
        self._stopping = False

    @property
    def active_count(self) -> int:
        return len(self._admissions) + len(self._workflows) + len(self._pending)

    def set_audience_reader(self, reader: AudienceReader) -> None:
        self._audience_reader = reader

    def set_transport_ready(self, ready: Callable[[UUID], bool]) -> None:
        self._transport_ready = ready

    def set_authenticator(self, authenticate: Authenticator) -> None:
        self._authenticator = authenticate

    def set_scheduler_wake_callback(self, wake: Callable[[UUID], None]) -> None:
        self._scheduler_wake = wake

    async def sticker_library_scope(self, route_id: UUID) -> str:
        """Operator-only callers get a scope derived from a durable current route."""
        route = await self._repository.get_route(route_id)
        if route is None or route.deleted_at is not None or route.character_id != "default":
            raise ChannelNotFoundError("Group sticker library unavailable")
        connection = await self._connection(route.connection_id)
        if connection.deleted_at is not None:
            raise ChannelNotFoundError("Group sticker library unavailable")
        return f"scene:{route.scene_id}"

    async def authorize_sticker_source(self, source: StickerLearningSource) -> bool:
        target = source.group_target
        if (
            target is None
            or source.principal_scope != f"scene:{target.scene_id}"
            or source.connection_id != target.connection_id
            or not self._started
            or self._stopping
            or not self._transport_ready(target.connection_id)
            or target.connection_id in self._blocked_connections
            or (target.connection_id, target.group_id) in self._blocked_groups
            or target.scene_id in self._blocked_scenes
        ):
            return False
        record = await self._repository.get_group_turn(target.channel_turn_id)
        if (
            record is None
            or record.turn.generation_id != source.generation_id
            or record.turn.status is not ChannelTurnStatus.COMPLETED
            or record.lineage.route_id != target.route_id
            or record.lineage.route_revision != target.route_revision
            or record.lineage.scene_id != target.scene_id
            or record.lineage.audience_fingerprint != target.audience_fingerprint
        ):
            return False
        authorized = await self._repository.authorize_group_turn(record.lineage)
        return (
            authorized.allowed
            and self._started
            and not self._stopping
            and self._transport_ready(target.connection_id)
            and target.connection_id not in self._blocked_connections
            and (target.connection_id, target.group_id) not in self._blocked_groups
            and target.scene_id not in self._blocked_scenes
        )

    async def start(self) -> None:
        if self._started:
            return
        self._stopping = False
        # Restart never submits an old accepted mention or resumes an old audience.
        for connection in await self._channels.list_connections():
            if connection.configuration.provider_id == "qq_napcat":
                await self.pause_connection(
                    connection.configuration.connection_id, ChannelGroupPauseReason.RECONNECT
                )
        self._started = True

    async def stop(self) -> None:
        self._image_context.clear()
        self._discussion.clear()
        if not self._started and not self.active_count:
            self._stopping = True
            return
        self._stopping = True
        self._started = False
        # Fence registrations before the first database await, including auth/prepare.
        for connection_id in {
            *(item.message.connection_id for item in self._admissions.values()),
            *(item.message.connection_id for item in self._workflows.values()),
            *(item.message.connection_id for item in self._pending.values()),
        }:
            self.fence_connection(connection_id, "runtime_stopping")
        for connection in await self._channels.list_connections():
            if connection.configuration.provider_id == "qq_napcat":
                await self.pause_connection(
                    connection.configuration.connection_id, ChannelGroupPauseReason.RECONNECT
                )
        await self._join_tasks()

    async def _connection(
        self, connection_id: UUID, *, mutate: bool = False
    ) -> ChannelConnectionRecord:
        connection = await self._channels.get_connection(connection_id, include_deleted=not mutate)
        if connection is None or connection.configuration.provider_id != "qq_napcat":
            raise ChannelNotFoundError("QQ connection unavailable")
        if mutate:
            configuration = connection.configuration
            if (
                not self._started
                or self._stopping
                or connection_id in self._blocked_connections
                or connection.deleted_at is not None
                or not configuration.enabled
                or connection.status is not ChannelConnectionStatus.READY
                or configuration.account_key is None
                or not self._transport_ready(connection_id)
            ):
                raise ChannelPolicyError("QQ connection must be enabled and transport ready")
            qq_id(configuration.account_key)
        return connection

    async def _route(self, connection_id: UUID, route_id: UUID) -> ChannelGroupRouteRecord:
        await self._connection(connection_id)
        route = await self._repository.get_route(route_id)
        if route is None or route.connection_id != connection_id:
            raise ChannelNotFoundError("group route unavailable")
        return route

    async def _observation(
        self, connection_id: UUID, observation_id: UUID
    ) -> ChannelGroupAudienceObservation:
        connection = await self._connection(connection_id, mutate=True)
        observation = await self._repository.get_observation(observation_id)
        if observation is None or observation.connection_id != connection_id:
            raise ChannelNotFoundError("audience observation unavailable")
        if (
            observation.account_key != connection.configuration.account_key
            or observation.connection_revision != connection.revision
            or not observation.observed_at <= self._clock() < observation.expires_at
            or (connection_id, observation.group_id) in self._blocked_groups
        ):
            raise ChannelPolicyError("fresh account-matched audience observation required")
        return observation

    async def observe_audience(
        self, connection_id: UUID, request: ChannelGroupAudienceRequest
    ) -> ChannelGroupAudienceSnapshot:
        connection = await self._connection(connection_id, mutate=True)
        reader = self._audience_reader
        if reader is None:
            raise ChannelPolicyError("group audience reader unavailable")
        epoch = self._connection_epochs.get(connection_id, 0)
        key = (connection_id, request.group_id)
        group_epoch = self._group_epochs.get(key, 0)
        if key in self._blocked_groups:
            raise ChannelPolicyError("group audience is being revalidated")
        try:
            async with asyncio.timeout(20):
                account, members = await reader(connection_id, request.group_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ChannelPolicyError("group audience observation unavailable") from None
        fresh = await self._connection(connection_id, mutate=True)
        if (
            fresh.revision != connection.revision
            or fresh.configuration.account_key != account
            or self._connection_epochs.get(connection_id, 0) != epoch
            or self._group_epochs.get(key, 0) != group_epoch
            or key in self._blocked_groups
        ):
            raise ChannelConflictError("connection changed during audience observation")
        now = self._clock()
        observation = ChannelGroupAudienceObservation(
            uuid4(),
            connection_id,
            fresh.revision,
            account,
            request.group_id,
            members,
            now,
            now + timedelta(seconds=60),
        )
        await self._repository.create_observation(observation)
        if (
            self._connection_epochs.get(connection_id, 0) != epoch
            or self._group_epochs.get(key, 0) != group_epoch
            or key in self._blocked_groups
            or not self._transport_ready(connection_id)
        ):
            raise ChannelConflictError("audience changed during observation")
        return ChannelGroupAudienceSnapshot(
            observation_id=observation.observation_id,
            connection_id=connection_id,
            connection_revision=observation.connection_revision,
            account_key=account,
            group_id=observation.group_id,
            member_ids=list(members),
            member_fingerprint=observation.member_fingerprint,
            observed_at=now,
            expires_at=observation.expires_at,
        )

    async def list_links(
        self, connection_id: UUID, *, limit: int = 25, cursor: str | None = None
    ) -> ChannelParticipantLinkPage:
        await self._connection(connection_id)
        links, next_cursor = await self._repository.list_links(
            connection_id, limit=limit, cursor=cursor
        )
        return ChannelParticipantLinkPage(
            items=[_link_snapshot(link) for link in links], next_cursor=next_cursor
        )

    async def create_link(
        self, connection_id: UUID, request: ChannelParticipantLinkCreate
    ) -> ChannelParticipantLinkSnapshot:
        await self._observation(connection_id, request.observation_id)
        link = await self._repository.create_link(
            request.observation_id,
            request.sender_key,
            request.participant_id,
            created_at=self._clock(),
        )
        return _link_snapshot(link)

    async def update_link(
        self, connection_id: UUID, link_id: UUID, request: ChannelParticipantLinkUpdate
    ) -> ChannelParticipantLinkSnapshot:
        connection = await self._connection(connection_id, mutate=True)
        link = await self._repository.get_link(link_id)
        if link is None or link.account_key != connection.configuration.account_key:
            raise ChannelNotFoundError("participant link unavailable")
        transition = await self._repository.update_link(
            link_id,
            enabled=request.enabled,
            expected_revision=request.expected_revision,
            updated_at=self._clock(),
        )
        self._discussion.clear_link(link_id)
        for item in tuple(self._admissions.values()):
            if item.route is not None and any(m.link_id == link_id for m in item.route.members):
                self._revoke(item, "link_updated")
        await self._apply_transition(transition, "link_updated")
        assert transition.link is not None
        return _link_snapshot(transition.link)

    async def list_routes(
        self, connection_id: UUID, *, limit: int = 25, cursor: str | None = None
    ) -> ChannelGroupRoutePage:
        await self._connection(connection_id)
        routes, next_cursor = await self._repository.list_routes(
            connection_id, limit=limit, cursor=cursor
        )
        return ChannelGroupRoutePage(
            items=[_route_snapshot(route) for route in routes], next_cursor=next_cursor
        )

    async def _members(
        self, connection_id: UUID, observation: ChannelGroupAudienceObservation, speakers: list[str]
    ) -> tuple[ChannelGroupRouteMember, ...]:
        if not set(speakers).issubset(observation.member_ids):
            raise ChannelPolicyError("speakers must be a subset of the observed audience")
        links: dict[str, ChannelParticipantLinkRecord] = {}
        cursor = None
        while True:
            page, cursor = await self._repository.list_links(connection_id, limit=50, cursor=cursor)
            links.update({link.sender_key: link for link in page})
            if cursor is None:
                break
        members: list[ChannelGroupRouteMember] = []
        for sender in observation.member_ids:
            link = links.get(sender)
            if link is None or not link.enabled or link.account_key != observation.account_key:
                raise ChannelPolicyError("every observed audience member needs an enabled link")
            members.append(
                ChannelGroupRouteMember(
                    link.link_id, sender, link.participant_id, sender in speakers
                )
            )
        result = tuple(members)
        audience_fingerprint(result)
        return result

    async def create_route(
        self, connection_id: UUID, request: ChannelGroupRouteCreate
    ) -> ChannelGroupRouteSnapshot:
        observation = await self._observation(connection_id, request.observation_id)
        connection = await self._connection(connection_id, mutate=True)
        members = await self._members(connection_id, observation, request.speaker_sender_keys)
        scene = await self._sessions.create_scene(
            request.display_name, [m.participant_id for m in members]
        )
        now = self._clock()
        route = ChannelGroupRouteRecord(
            uuid4(),
            connection_id,
            observation.account_key,
            observation.group_id,
            connection.configuration.character_id,
            scene.scene_id,
            request.display_name,
            1,
            False,
            None,
            observation.observation_id,
            members,
            now,
            now,
            allow_requested_voice=request.allow_requested_voice,
        )
        return _route_snapshot(await self._repository.create_route(route))

    async def update_route(
        self, connection_id: UUID, route_id: UUID, request: ChannelGroupRouteUpdate
    ) -> ChannelGroupRouteSnapshot:
        await self._connection(connection_id, mutate=True)
        old = await self._route(connection_id, route_id)
        if (connection_id, old.group_id) in self._blocked_groups:
            raise ChannelPolicyError("group route is being paused")
        if old.revision != request.expected_revision:
            raise ChannelConflictError("group route revision changed")
        if request.observation_id is not None:
            observation = await self._observation(connection_id, request.observation_id)
            if observation.group_id != old.group_id:
                raise ChannelPolicyError("observation belongs to another group")
            if (
                request.enabled
                and old.pause_reason is not None
                and (
                    observation.observation_id == old.observation_id
                    or observation.observed_at < old.updated_at
                )
            ):
                raise ChannelPolicyError("paused route requires a new audience observation")
            members = await self._members(connection_id, observation, request.speaker_sender_keys)
        else:
            if not set(request.speaker_sender_keys).issubset(m.sender_key for m in old.members):
                raise ChannelPolicyError("unknown group speaker")
            members = tuple(
                replace(m, can_speak=m.sender_key in request.speaker_sender_keys)
                for m in old.members
            )
        scene_id = old.scene_id
        if audience_fingerprint(members) != old.audience_fingerprint:
            scene = await self._sessions.create_scene(
                old.display_name, [m.participant_id for m in members]
            )
            scene_id = scene.scene_id
        prior_admissions = frozenset(self._admissions)
        transition = await self._repository.update_route(
            route_id,
            expected_revision=request.expected_revision,
            enabled=request.enabled,
            observation_id=request.observation_id,
            members=members,
            scene_id=scene_id,
            updated_at=self._clock(),
            allow_requested_voice=request.allow_requested_voice,
        )
        assert transition.route is not None
        self._fence_old_route(transition.route, prior_admissions)
        await self._apply_transition(transition, "route_updated")
        return _route_snapshot(transition.route)

    async def list_turns(
        self, connection_id: UUID, route_id: UUID, *, limit: int = 25, cursor: str | None = None
    ) -> ChannelGroupTurnPage:
        await self._route(connection_id, route_id)
        records, next_cursor = await self._repository.list_group_turns(
            route_id, limit=limit, cursor=cursor
        )
        return ChannelGroupTurnPage(
            items=[await self._turn_snapshot(record) for record in records], next_cursor=next_cursor
        )

    async def cancel_turn(
        self,
        connection_id: UUID,
        route_id: UUID,
        channel_turn_id: UUID,
        request: ChannelGroupTurnCancelRequest,
    ) -> ChannelGroupTurnSnapshot:
        await self._connection(connection_id, mutate=True)
        await self._route(connection_id, route_id)
        record = await self._repository.get_group_turn(channel_turn_id)
        if (
            record is None
            or record.lineage.route_id != route_id
            or record.turn.connection_id != connection_id
        ):
            raise ChannelNotFoundError("group turn unavailable")
        transition = await self._repository.cancel_group_turn(
            channel_turn_id,
            expected_revision=request.expected_revision,
            reason="operator_cancelled",
            updated_at=self._clock(),
        )
        await self._apply_transition(transition, "operator_cancelled")
        fresh = await self._repository.get_group_turn(channel_turn_id)
        assert fresh is not None
        return await self._turn_snapshot(fresh)

    def _live(self, item: _Admission | _Input) -> bool:
        message = item.message
        return (
            self._started
            and not self._stopping
            and not item.revoked
            and message.connection_id not in self._blocked_connections
            and (message.connection_id, message.group_id) not in self._blocked_groups
            and item.connection_epoch == self._connection_epochs.get(message.connection_id, 0)
            and item.group_epoch
            == self._group_epochs.get((message.connection_id, message.group_id), 0)
            and self._transport_ready(message.connection_id)
        )

    def _check_admission(self, item: _Admission) -> None:
        if not self._live(item) or item.task.cancelling():
            raise ChannelPolicyError("group admission revoked")

    @asynccontextmanager
    async def _gate(self, registry: dict[UUID, _Gate], key: UUID) -> AsyncGenerator[None]:
        gate = registry.setdefault(key, _Gate())
        gate.users += 1
        try:
            async with gate.lock:
                yield
        finally:
            gate.users -= 1
            if gate.users == 0:
                registry.pop(key, None)

    async def ingest_group(
        self,
        descriptor: ChannelGroupInboundDescriptor,
        *,
        access_token: str,
        image_input: ChannelInboundImageInput | None = None,
    ) -> ChannelTurnReceipt:
        receipt = await self._process_group(
            descriptor, access_token=access_token, image_input=image_input
        )
        assert receipt is not None
        return receipt

    async def observe_group_image_reference(
        self,
        descriptor: ChannelGroupInboundDescriptor,
        *,
        access_token: str,
        image_input: ChannelInboundImageInput,
    ) -> None:
        """Keep a short-lived descriptor only; no download, generation, learning or transcript."""
        await self._process_group(
            descriptor, access_token=access_token, image_input=image_input, observe_only=True
        )

    async def observe_group_text(
        self, descriptor: ChannelGroupInboundDescriptor, *, access_token: str
    ) -> None:
        """Collect only authorized dialogue; no turn, generation, model or memory writes."""
        await self._process_group(
            descriptor,
            access_token=access_token,
            image_input=None,
            observe_only=True,
            observe_text=True,
        )

    async def _process_group(
        self,
        descriptor: ChannelGroupInboundDescriptor,
        *,
        access_token: str,
        image_input: ChannelInboundImageInput | None,
        observe_only: bool = False,
        observe_text: bool = False,
    ) -> ChannelTurnReceipt | None:
        if descriptor.image_fingerprint != (
            image_input.source_fingerprint if image_input is not None else None
        ):
            raise ChannelPolicyError("Group image descriptor differs from admitted content")
        # A synchronous host fence can revoke even an admission suspended in auth.
        task = asyncio.current_task()
        if task is None or not self._started or self._stopping:
            raise ChannelPolicyError("group runtime unavailable")
        if self.active_count >= 32:
            raise ChannelBusyError("group admission capacity reached")
        item = _Admission(
            descriptor,
            cast(asyncio.Task[object], task),
            self._connection_epochs.get(descriptor.connection_id, 0),
            self._group_epochs.get((descriptor.connection_id, descriptor.group_id), 0),
        )
        self._admissions[item.channel_turn_id] = item
        try:
            self._check_admission(item)
            if self._authenticator is None:
                raise ChannelAuthenticationError("group transport authentication unavailable")
            connection = await self._authenticator(descriptor.connection_id, access_token)
            self._check_admission(item)
            if (
                connection.configuration.connection_id != descriptor.connection_id
                or connection.configuration.provider_id != "qq_napcat"
                or connection.configuration.account_key != descriptor.account_key
                or connection.deleted_at is not None
                or not connection.configuration.enabled
                or connection.status is not ChannelConnectionStatus.READY
                or not secrets.compare_digest(
                    connection.access_token_hash, hashlib.sha256(access_token.encode()).hexdigest()
                )
            ):
                raise ChannelAuthenticationError("group transport identity unavailable")
            route = await self._repository.find_route(descriptor.connection_id, descriptor.group_id)
            self._check_admission(item)
            if route is None or not route.enabled or route.deleted_at is not None:
                raise ChannelPolicyError("group route must be explicitly enabled")
            now = self._clock()
            for key, context in tuple(self._image_context.items()):
                if context.expires_at <= now:
                    self._image_context.pop(key, None)
            context_key = (descriptor.connection_id, descriptor.group_id, descriptor.sender_key)
            cached = (
                self._image_context.get(context_key)
                if not observe_only and not descriptor.mention_only
                else None
            )
            if (
                image_input is None
                and cached is not None
                and (
                    cached.route_id == route.route_id
                    and cached.route_revision == route.revision
                    and cached.scene_id == route.scene_id
                    and cached.used_by in (None, descriptor.external_message_id)
                )
            ):
                image_input = cached.image_input
                descriptor = replace(descriptor, image_fingerprint=image_input.source_fingerprint)
                item.message = descriptor
            elif image_input is not None and not observe_only:
                self._image_context.pop(context_key, None)
                cached = None
            learning_revision: int | None = None
            if image_input is not None:
                if self._sticker_library is None or route.character_id != "default":
                    raise ChannelPolicyError("Group sticker learning unavailable")
                settings = await self._sticker_library.repository.get_settings(
                    f"scene:{route.scene_id}", route.character_id
                )
                self._check_admission(item)
                if not settings.learning_enabled:
                    raise ChannelPolicyError("Group image learning must be explicitly enabled")
                if (
                    cached is not None
                    and image_input is cached.image_input
                    and (settings.revision != cached.settings_revision)
                ):
                    raise ChannelPolicyError("Group image reference learning policy changed")
                learning_revision = settings.revision
            item.route = route
            async with self._gate(self._gates, route.route_id):
                self._check_admission(item)
                fresh = await self._repository.get_route(route.route_id)
                self._check_admission(item)
                if fresh is None or fresh.revision != route.revision or not fresh.enabled:
                    raise ChannelPolicyError("group route changed")
                member = next(
                    (m for m in fresh.members if m.sender_key == descriptor.sender_key), None
                )
                if (
                    member is None
                    or (not observe_text and not member.can_speak)
                    or fresh.scene_id in self._blocked_scenes
                ):
                    raise ChannelPolicyError("group speaker is not granted")
                if observe_only:
                    link = await self._repository.get_link(member.link_id)
                    self._check_admission(item)
                    if (
                        link is None
                        or not link.enabled
                        or link.provider_id != "qq_napcat"
                        or link.participant_id != member.participant_id
                        or link.sender_key != descriptor.sender_key
                        or link.account_key != descriptor.account_key
                        or fresh.account_key != descriptor.account_key
                        or fresh.character_id != connection.configuration.character_id
                    ):
                        raise ChannelPolicyError("Group image reference speaker unavailable")
                    if observe_text:
                        result = self._discussion.observe(
                            descriptor, fresh, connection.revision, self._clock()
                        )
                        logger.debug("group.discussion_collected status=%s", result)
                        return None
                    if image_input is None or learning_revision is None:
                        raise ChannelPolicyError("Group image reference unavailable")
                    if context_key not in self._image_context and len(self._image_context) >= 32:
                        raise ChannelBusyError("Group image reference capacity reached")
                    self._image_context[context_key] = _ImageReferenceContext(
                        fresh.route_id,
                        fresh.revision,
                        fresh.scene_id,
                        learning_revision,
                        image_input,
                        self._clock() + timedelta(seconds=60),
                    )
                    return None
                if cached is not None and image_input is cached.image_input:
                    cached.used_by = descriptor.external_message_id
                duplicate = await self._repository.find_group_turn(
                    descriptor.connection_id, descriptor.group_id, descriptor.external_message_id
                )
                self._check_admission(item)
                if duplicate is not None:
                    if duplicate.turn.content_sha256 != descriptor.content_sha256:
                        raise ChannelConflictError(
                            "provider message identity has conflicting content"
                        )
                    return _receipt(duplicate.turn, duplicate=True)
                binding = await self._repository.find_group_binding(
                    route.route_id, route.scene_id, descriptor.sender_key
                )
                self._check_admission(item)
                if binding is None:
                    session = await self._sessions.create_session(
                        route.character_id,
                        participant_id=member.participant_id,
                        scene_id=route.scene_id,
                    )
                    item.session_id = session.session_id
                else:
                    item.session_id = binding.session_id
                self._check_admission(item)
                admitted = await self._repository.admit_group_turn(
                    ChannelGroupAdmission(
                        descriptor,
                        route.route_id,
                        route.revision,
                        item.session_id,
                        item.channel_turn_id,
                        item.turn_id,
                        item.generation_id,
                        self._clock(),
                    )
                )
                self._check_admission(item)
                if admitted.duplicate:
                    return _receipt(admitted.turn, duplicate=True)
                pending = _Input(
                    admitted,
                    descriptor,
                    item.connection_epoch,
                    item.group_epoch,
                    messaging_presentation_policy(connection.configuration.presentation_policy),
                    image_input,
                    learning_revision,
                    self._discussion.snapshot(
                        fresh,
                        connection.revision,
                        descriptor.external_message_id,
                        self._clock(),
                        mention_only=descriptor.mention_only,
                    ),
                )
                if image_input is None and not descriptor.mention_only:
                    self._discussion.observe(descriptor, fresh, connection.revision, self._clock())
                # Install the durable latest pending before an old actor can finish/release.
                if admitted.dispatch_now:
                    self._launch(pending)
                else:
                    self._pending[admitted.turn.channel_turn_id] = pending
            await self._apply_transition(
                ChannelGroupTransition(
                    displaced_turn_ids=admitted.displaced_turn_ids,
                    persisted_events=admitted.persisted_events,
                ),
                "superseded",
            )
            return _receipt(admitted.turn, duplicate=False)
        except BaseException:
            await self._abort_admission(item)
            raise
        finally:
            self._admissions.pop(item.channel_turn_id, None)

    async def _abort_admission(self, item: _Admission) -> None:
        # Lookup only our preallocated ID; a cancelled duplicate never cancels its original.
        self._revoke(item, "admission_revoked")
        for registry in (self._workflows, self._pending):
            actor = registry.get(item.channel_turn_id)
            if actor is not None:
                self._revoke(actor, "admission_revoked")
        record: ChannelGroupAdmissionResult | None = None
        transition: ChannelGroupTransition | None = None
        for attempt in range(2):
            record = await self._repository.get_group_turn(item.channel_turn_id)
            if record is None:
                return
            try:
                transition = await self._repository.cancel_group_turn(
                    item.channel_turn_id,
                    expected_revision=record.turn.revision,
                    reason="admission_revoked",
                    updated_at=self._clock(),
                )
                break
            except ChannelConflictError:
                if attempt:
                    # The actor is already irrevocably fenced. Do not mask the
                    # caller's cancellation with a cleanup-only CAS conflict.
                    return
        assert transition is not None and record is not None
        await self._apply_transition(transition, "admission_revoked")
        if item.channel_turn_id not in self._workflows:
            await self._release(record)

    def _revoke(self, item: _Admission | _Input, reason: str) -> None:
        already_revoked = item.revoked
        item.revoked = True
        if isinstance(item, _Admission):
            session_id, generation_id, task = item.session_id, item.generation_id, item.task
        else:
            session_id = item.admission.turn.session_id
            generation_id = item.admission.turn.generation_id
            task = item.task
        if session_id is not None:
            self._conversation.request_cancel(
                session_id, expected_generation_id=generation_id, reason=reason
            )
        if (
            not already_revoked
            and task is not None
            and task is not asyncio.current_task()
            and not task.done()
            and not task.cancelling()
        ):
            task.cancel()

    def fence_connection(
        self, connection_id: UUID, reason: str, group_id: str | None = None
    ) -> None:
        self._discussion.clear(connection_id, group_id)
        for key in tuple(self._image_context):
            if key[0] == connection_id and (group_id is None or key[1] == group_id):
                self._image_context.pop(key, None)
        if self._sticker_library is not None:
            self._sticker_library.request_cancel_group(connection_id, group_id=group_id)
        if group_id is not None:
            group_id = qq_id(group_id)
            if (connection_id, group_id) not in self._group_epochs and len(
                self._group_epochs
            ) >= _MAX_TRACKED_GROUP_EPOCHS:
                # Never evict/reuse epochs: an old suspended guard could otherwise
                # compare equal again. Unknown groups at capacity revoke the connection.
                group_id = None
        if group_id is None:
            self._connection_epochs[connection_id] = (
                self._connection_epochs.get(connection_id, 0) + 1
            )
            self._blocked_connections.add(connection_id)
        else:
            key = (connection_id, group_id)
            self._group_epochs[key] = self._group_epochs.get(key, 0) + 1
            self._blocked_groups.add(key)
        for item in (
            *self._admissions.values(),
            *self._workflows.values(),
            *self._pending.values(),
        ):
            if item.message.connection_id == connection_id and (
                group_id is None or item.message.group_id == group_id
            ):
                self._revoke(item, reason)

    def _fence_old_route(self, route: ChannelGroupRouteRecord, prior: frozenset[UUID]) -> None:
        self._discussion.clear_route(route.route_id)
        for key, context in tuple(self._image_context.items()):
            if context.route_id == route.route_id and context.route_revision != route.revision:
                self._image_context.pop(key, None)
        for key, item in tuple(self._admissions.items()):
            if (
                item.message.connection_id != route.connection_id
                or item.message.group_id != route.group_id
            ):
                continue
            if (item.route is not None and item.route.revision != route.revision) or (
                item.route is None and key in prior
            ):
                self._revoke(item, "route_updated")

    async def _apply_transition(
        self, transition: ChannelGroupTransition, reason: str, *, join: bool = True
    ) -> None:
        tasks: set[asyncio.Task[object] | asyncio.Task[None]] = set()
        for key in transition.displaced_turn_ids:
            for registry in (self._admissions, self._workflows, self._pending):
                item = registry.get(key)
                if item is not None:
                    self._revoke(item, reason)
                    if item.task is not None:
                        tasks.add(item.task)
            self._pending.pop(key, None)
        for session_id in transition.affected_session_ids:
            # Expected IDs from the registrations above prevent cancelling a newer generation.
            for item in tuple(self._workflows.values()):
                if (
                    item.admission.turn.session_id == session_id
                    and item.admission.turn.channel_turn_id in transition.displaced_turn_ids
                ):
                    self._revoke(item, reason)
        for event in transition.persisted_events:
            await self._publisher.publish_persisted(event)
        if join:
            await self._join_tasks(tasks)

    async def _join_tasks(
        self, tasks: set[asyncio.Task[object] | asyncio.Task[None]] | None = None
    ) -> None:
        if tasks is None:
            tasks = {item.task for item in self._admissions.values()}
            tasks.update(item.task for item in self._workflows.values() if item.task is not None)
        waiting = {task for task in tasks if task is not asyncio.current_task() and not task.done()}
        if waiting:
            # No locks held; do not timeout-cancel an actor during its durable cleanup.
            await asyncio.wait(waiting, timeout=_JOIN_SECONDS)

    async def pause_connection(
        self, connection_id: UUID, reason: ChannelGroupPauseReason, group_id: str | None = None
    ) -> None:
        self.fence_connection(connection_id, reason.value, group_id)
        if group_id is not None and (connection_id, group_id) not in self._group_epochs:
            # The synchronous fence fell back to the connection at capacity.
            # Persist the same scope, then clear only that connection's captured epoch.
            group_id = None
        epoch = (
            self._connection_epochs.get(connection_id, 0)
            if group_id is None
            else self._group_epochs[(connection_id, group_id)]
        )
        group_epochs = (
            {key: value for key, value in self._group_epochs.items() if key[0] == connection_id}
            if group_id is None
            else {}
        )
        transitions = await self._repository.pause_routes(
            connection_id=connection_id, group_id=group_id, reason=reason, updated_at=self._clock()
        )
        for transition in transitions:
            await self._apply_transition(transition, reason.value)
            for key in transition.displaced_turn_ids:
                if key not in self._workflows:
                    record = await self._repository.get_group_turn(key)
                    if record is not None:
                        await self._release(record)
        if group_id is None and self._connection_epochs.get(connection_id, 0) == epoch:
            self._blocked_connections.discard(connection_id)
            for key, value in group_epochs.items():
                if self._group_epochs.get(key) == value:
                    self._blocked_groups.discard(key)
        elif group_id is not None and self._group_epochs.get((connection_id, group_id), 0) == epoch:
            self._blocked_groups.discard((connection_id, group_id))

    async def before_scope_reset(self, session: SessionSnapshot) -> None:
        scene_id = session.scene_id
        if scene_id is None or not await self._repository.is_group_scene(scene_id):
            return
        self._blocked_scenes.add(scene_id)
        self._discussion.clear_scene(scene_id)
        if self._sticker_library is not None:
            self._sticker_library.request_cancel_group(scene_id=scene_id)
        for item in (
            *self._admissions.values(),
            *self._workflows.values(),
            *self._pending.values(),
        ):
            scene = (
                item.route.scene_id
                if isinstance(item, _Admission) and item.route is not None
                else (item.admission.lineage.scene_id if isinstance(item, _Input) else None)
            )
            if scene == scene_id:
                self._revoke(item, "scene_reset")
        transitions = await self._repository.pause_routes(
            scene_id=scene_id, reason=ChannelGroupPauseReason.SCENE_RESET, updated_at=self._clock()
        )
        for transition in transitions:
            # Conversation holds its start lock here. Never wait on a child needing that lock.
            await self._apply_transition(transition, "scene_reset", join=False)
        self._blocked_scenes.discard(scene_id)

    def _launch(self, item: _Input) -> None:
        key = item.admission.turn.channel_turn_id
        self._workflows[key] = item
        item.task = asyncio.create_task(self._run(item), name=f"qq-group:{key}")

    async def _guard(self, item: _Input) -> bool:
        if not self._live(item) or item.admission.lineage.scene_id in self._blocked_scenes:
            return False
        authorized = await self._repository.authorize_group_turn(item.admission.lineage)
        return (
            authorized.allowed
            and self._live(item)
            and item.admission.lineage.scene_id not in self._blocked_scenes
        )

    async def _run(self, item: _Input) -> None:
        turn = item.admission.turn
        completed: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        subscription = self._publisher.event_hub.subscribe(
            lambda event: (
                event.get("generation_id") == str(turn.generation_id)
                and event.get("event_type") in _TERMINAL_EVENTS
            ),
            queue_size=8,
        )
        try:
            if (
                not await self._guard(item)
                or not await self._repository.begin_group_turn(
                    item.admission.lineage, updated_at=self._clock()
                )
                or not self._live(item)
            ):
                await self._terminal(turn, ChannelTurnStatus.CANCELLED)
                return
            identity = await self._sessions.conversation_identity(turn.session_id)
            if not await self._guard(item):
                await self._terminal(turn, ChannelTurnStatus.CANCELLED)
                return
            route = await self._repository.get_route(item.admission.lineage.route_id)
            item.voice_requested = (
                route is not None
                and route.allow_requested_voice
                and not item.message.mention_only
                and requests_group_voice(item.message.text)
            )
            voice_skills: frozenset[str] = (
                frozenset({"channel.voice"}) if item.voice_requested else frozenset()
            )
            await self._conversation.submit_text(
                turn.session_id,
                item.message.text,
                turn_id=turn.turn_id,
                generation_id=turn.generation_id,
                options=ConversationTurnOptions(
                    origin="external_channel",
                    presentation_profile=item.presentation_policy.profile.value,
                    output_modes=frozenset({"text"}),
                    allow_tools=item.voice_requested,
                    allowed_skill_ids=voice_skills,
                    contextual_skill_ids=voice_skills,
                    allow_shared_voice=item.voice_requested,
                    trusted_identity=identity,
                    before_generation=lambda: self._guard(item),
                    image_loader=self._learning_image_loader(item, completed),
                    allow_shared_images=item.image_input is not None,
                    group_discussion=item.discussion,
                    failure_recovery_text="刚才发来的图片我没看清，能再发一次吗？"
                    if item.image_input is not None
                    else None,
                    source_context=ConversationSourceContext(
                        provider_id="qq_napcat",
                        connection_id=turn.connection_id,
                        account_key=turn.account_key,
                        principal_scope=turn.principal_scope,
                        chat_type="group",
                        conversation_key=turn.conversation_key,
                        sender_key=turn.sender_key,
                        received_at=item.message.received_at,
                        audience_ids=identity.audience_ids,
                        route_revision=item.admission.lineage.route_revision,
                        group_route_id=item.admission.lineage.route_id,
                        participant_id=identity.participant_id,
                        scene_id=identity.scene_id,
                    ),
                ),
            )
            await subscription.receive()
            # Completion publishing happens before the Conversation child has fully exited.
            await self._settle_generation(turn, "group_terminal_join")
            await self.sync_turn(turn)
        except asyncio.CancelledError:
            item.revoked = True
            self._conversation.request_cancel(
                turn.session_id, expected_generation_id=turn.generation_id, reason="group_revoked"
            )
            await self._settle_generation(turn, "group_revoked")
            await self._terminal(turn, ChannelTurnStatus.CANCELLED)
            raise
        except Exception as exc:
            logger.error(
                "Group workflow failed generation_id=%s error_type=%s",
                turn.generation_id,
                type(exc).__name__,
            )
            self._conversation.request_cancel(
                turn.session_id, expected_generation_id=turn.generation_id, reason="group_failed"
            )
            await self._settle_generation(turn, "group_failed")
            await self._terminal(turn, ChannelTurnStatus.FAILED)
        finally:
            self._publisher.event_hub.unsubscribe(subscription)
            if not completed.done():
                fresh = await self._channels.get_turn(turn.channel_turn_id)
                completed.set_result(
                    fresh is not None
                    and fresh.status is ChannelTurnStatus.COMPLETED
                    and self._live(item)
                )
            if self._conversation.active_generation_id(turn.session_id) != turn.generation_id:
                self._workflows.pop(turn.channel_turn_id, None)
                await self._release(item.admission)

    def _learning_image_loader(
        self,
        item: _Input,
        completed: asyncio.Future[bool],
    ) -> Callable[[], Awaitable[tuple[LlmInputImage, ...]]] | None:
        image_input = item.image_input
        library = self._sticker_library
        if image_input is None or library is None:
            return None
        turn = item.admission.turn
        lineage = item.admission.lineage
        assert turn.account_key is not None
        target = ChannelGroupDeliveryTarget(
            connection_id=turn.connection_id,
            account_key=turn.account_key,
            group_id=item.message.group_id,
            route_id=lineage.route_id,
            route_revision=lineage.route_revision,
            channel_turn_id=turn.channel_turn_id,
            scene_id=lineage.scene_id,
            audience_fingerprint=lineage.audience_fingerprint,
        )

        async def wait_for_completion() -> bool:
            return await asyncio.shield(completed)

        async def load() -> tuple[LlmInputImage, ...]:
            if not await self._guard(item):
                raise ChannelPolicyError("Group image source revoked")
            raw = await image_input.load()
            images = normalize_and_sanitize_inbound_images(raw)
            settings = await library.repository.get_settings(turn.principal_scope, "default")
            if (
                not await self._guard(item)
                or not settings.learning_enabled
                or settings.revision != item.learning_revision
            ):
                raise ChannelPolicyError("Group image learning changed")
            await library.observe_batch(
                StickerLearningSource(
                    principal_scope=turn.principal_scope,
                    character_id="default",
                    connection_id=turn.connection_id,
                    generation_id=turn.generation_id,
                    group_target=target,
                ),
                image_input.sticker_learning_images(images)
                if image_input.sticker_learning_images is not None
                else images,
                wait_for_completion=wait_for_completion,
                on_saved=image_input.on_sticker_saved,
            )
            return images

        return load

    async def _settle_generation(self, turn: ChannelTurnRecord, reason: str) -> None:
        try:
            await self._conversation.cancel(
                turn.session_id, reason, expected_generation_id=turn.generation_id
            )
        except asyncio.CancelledError:
            # Finish channel cleanup after an already-cancelling caller has joined
            # its child; _run still re-raises the original cancellation.
            if self._conversation.active_generation_id(turn.session_id) == turn.generation_id:
                raise

    async def _release(self, record: ChannelGroupAdmissionResult) -> None:
        promoted = await self._repository.release_group_active(
            record.lineage.route_id, record.turn.channel_turn_id, updated_at=self._clock()
        )
        if promoted is None:
            return
        pending = self._pending.pop(promoted, None)
        if pending is not None and self._live(pending):
            self._launch(pending)
        else:
            lost = await self._repository.get_group_turn(promoted)
            if lost is not None:
                await self._terminal(lost.turn, ChannelTurnStatus.CANCELLED)
                await self._release(lost)

    async def _terminal(
        self, turn: ChannelTurnRecord, status: ChannelTurnStatus
    ) -> ChannelTurnRecord:
        async with self._gate(self._turn_gates, turn.channel_turn_id):
            return await self._terminal_unlocked(turn, status)

    async def _terminal_unlocked(
        self, turn: ChannelTurnRecord, status: ChannelTurnStatus
    ) -> ChannelTurnRecord:
        fresh = await self._channels.get_turn(turn.channel_turn_id)
        if fresh is None:
            raise ChannelNotFoundError("group turn unavailable")
        if fresh.status not in {
            ChannelTurnStatus.ACCEPTED,
            ChannelTurnStatus.PROCESSING,
            ChannelTurnStatus.CANCELLING,
        }:
            return fresh
        terminal = await self._channels.set_turn_terminal(
            turn.channel_turn_id,
            status=status,
            completed_at=self._clock(),
            error=StructuredError(
                code="group_generation_failed",
                message="Group reply unavailable",
                retryable=False,
                component="external_channels",
            )
            if status is ChannelTurnStatus.FAILED
            else None,
        )
        await self._publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "channel.turn_cancelled"
                    if status is ChannelTurnStatus.CANCELLED
                    else "channel.turn_failed",
                    "session_id": terminal.session_id,
                    "turn_id": terminal.turn_id,
                    "generation_id": terminal.generation_id,
                    "occurred_at": self._clock(),
                    "source": "runtime.external_channels",
                    "privacy": PrivacyLevel.PRIVATE,
                    "payload": {
                        "connection_id": str(terminal.connection_id),
                        "channel_turn_id": str(terminal.channel_turn_id),
                        "group_route_id": str(terminal.group_route_id),
                        "status": terminal.status.value,
                    },
                }
            )
        )
        return terminal

    async def sync_turn(self, turn: ChannelTurnRecord) -> ChannelTurnRecord:
        async with self._gate(self._turn_gates, turn.channel_turn_id):
            fresh = await self._sync_turn(turn)
        actor = self._workflows.get(turn.channel_turn_id)
        if (
            actor is not None
            and actor.task is not None
            and actor.task.done()
            and self._conversation.active_generation_id(turn.session_id) != turn.generation_id
        ):
            self._workflows.pop(turn.channel_turn_id, None)
            await self._release(actor.admission)
        return fresh

    async def _sync_turn(self, turn: ChannelTurnRecord) -> ChannelTurnRecord:
        if turn.group_lineage_version != 1:
            return turn
        fresh = await self._channels.get_turn(turn.channel_turn_id)
        if fresh is None:
            raise ChannelNotFoundError("group turn unavailable")
        if fresh.status not in {
            ChannelTurnStatus.ACCEPTED,
            ChannelTurnStatus.PROCESSING,
            ChannelTurnStatus.CANCELLING,
        }:
            return fresh
        registered = self._workflows.get(turn.channel_turn_id)
        result = await self._conversation_repository.generation_result(turn.generation_id)
        if result is None or result.state in {GenerationState.CREATED, GenerationState.RUNNING}:
            if (
                registered is not None
                or turn.channel_turn_id in self._admissions
                or turn.channel_turn_id in self._pending
            ):
                return fresh
            return await self._terminal_unlocked(fresh, ChannelTurnStatus.FAILED)
        if result.state is not GenerationState.COMPLETED:
            return await self._terminal_unlocked(
                fresh,
                ChannelTurnStatus.CANCELLED
                if result.state in {GenerationState.CANCELLED, GenerationState.CANCELLING}
                else ChannelTurnStatus.FAILED,
            )
        if registered is None or not await self._guard(registered):
            return await self._terminal_unlocked(fresh, ChannelTurnStatus.CANCELLED)
        if (
            (result.session_id, result.turn_id) != (turn.session_id, turn.turn_id)
            or not result.output_text
            or not result.output_text.strip()
            or len(result.output_text) > 20000
        ):
            return await self._terminal_unlocked(fresh, ChannelTurnStatus.FAILED)
        plan = await self._repository.create_group_plan(
            registered.admission.lineage,
            reply_text=result.output_text,
            delivery_id=uuid4(),
            completed_at=self._clock(),
            parts=self._delivery_plan_factory.create_parts(
                result.output_text,
                policy=registered.presentation_policy,
                can_send_sticker=False,
            ),
        )
        if not self._live(registered):
            latest = await self._repository.get_group_turn(turn.channel_turn_id)
            if latest is not None:
                await self._apply_transition(
                    await self._repository.cancel_group_turn(
                        turn.channel_turn_id,
                        expected_revision=latest.turn.revision,
                        reason="group_revoked",
                        updated_at=self._clock(),
                    ),
                    "group_revoked",
                    join=False,
                )
        for event in plan.persisted_events:
            await self._publisher.publish_persisted(event)
        if self._live(registered) and self._scheduler_wake is not None:
            self._scheduler_wake(turn.connection_id)
        latest = await self._channels.get_turn(turn.channel_turn_id)
        assert latest is not None
        return latest

    async def authorize_delivery(self, plan: ChannelDeliveryPlanRecord) -> bool:
        target = plan.group_target
        if (
            target is None
            or plan.channel_turn_id is None
            or target.channel_turn_id != plan.channel_turn_id
            or target.connection_id != plan.connection_id
            or not 1 <= plan.part_count <= 11
        ):
            return False
        connection_id = target.connection_id
        epoch = self._connection_epochs.get(connection_id, 0)
        group_epoch = self._group_epochs.get((connection_id, target.group_id), 0)

        def live() -> bool:
            return (
                self._started
                and not self._stopping
                and self._transport_ready(connection_id)
                and connection_id not in self._blocked_connections
                and (connection_id, target.group_id) not in self._blocked_groups
                and target.scene_id not in self._blocked_scenes
                and epoch == self._connection_epochs.get(connection_id, 0)
                and group_epoch == self._group_epochs.get((connection_id, target.group_id), 0)
            )

        if not live():
            return False
        record = await self._repository.get_group_turn(target.channel_turn_id)
        if not live() or record is None:
            return False
        lineage = record.lineage
        route = await self._repository.get_route(lineage.route_id)
        if route is None or not live():
            return False
        if (
            record.turn.account_key != target.account_key
            or record.turn.conversation_key != f"group:{target.group_id}"
            or record.turn.delivery_id != plan.delivery_id
            or not group_text_parts_match_reply(
                plan.parts,
                record.turn.reply_text or "",
                allow_sticker=False,
                allow_voice=route.allow_requested_voice,
            )
            or lineage.route_id != target.route_id
            or lineage.route_revision != target.route_revision
            or lineage.scene_id != target.scene_id
            or lineage.audience_fingerprint != target.audience_fingerprint
        ):
            return False
        authorization = await self._repository.authorize_group_turn(lineage)
        return authorization.allowed and live()

    async def authorize_reply_voice(self, turn: ChannelTurnRecord) -> bool:
        """Only the owned, current explicitly requested group reply gets a voice grant."""
        item = self._workflows.get(turn.channel_turn_id)
        if (
            item is None
            or not item.voice_requested
            or item.message.mention_only
            or not requests_group_voice(item.message.text)
            or (
                item.admission.turn.session_id,
                item.admission.turn.turn_id,
                item.admission.turn.generation_id,
            )
            != (turn.session_id, turn.turn_id, turn.generation_id)
            or not await self._guard(item)
        ):
            return False
        route = await self._repository.get_route(item.admission.lineage.route_id)
        return route is not None and route.allow_requested_voice and self._live(item)

    async def publish_reply_voice(
        self,
        turn: ChannelTurnRecord,
        payload: ChannelAudioDeliveryPartPayload,
        delivery_id: UUID,
        created_at: datetime,
    ) -> None:
        if not await self.authorize_reply_voice(turn):
            raise ChannelPolicyError("group voice reply is no longer authorized")
        item = self._workflows[turn.channel_turn_id]
        plan = await self._repository.create_group_voice_plan(
            item.admission.lineage, payload=payload, delivery_id=delivery_id, created_at=created_at
        )
        for event in plan.persisted_events:
            await self._publisher.publish_persisted(event)
        if self._live(item) and self._scheduler_wake is not None:
            self._scheduler_wake(turn.connection_id)

    async def on_plan_terminal(self, plan: ChannelDeliveryPlanRecord) -> None:
        if plan.channel_turn_id is None or plan.channel_turn_id in self._workflows:
            return
        record = await self._repository.get_group_turn(plan.channel_turn_id)
        if record is not None:
            await self._release(record)

    async def _turn_snapshot(self, record: ChannelGroupAdmissionResult) -> ChannelGroupTurnSnapshot:
        turn = record.turn
        assert record.binding.participant_id is not None
        plan = (
            await self._channels.get_delivery_plan(turn.delivery_id)
            if turn.delivery_id is not None
            else None
        )
        receipt = plan is not None and any(
            part.status is ChannelDeliveryPartStatus.DELIVERED
            and part.provider_message_id is not None
            for part in plan.parts
        )
        cancelable = not receipt and (
            turn.status
            in {
                ChannelTurnStatus.ACCEPTED,
                ChannelTurnStatus.PROCESSING,
                ChannelTurnStatus.CANCELLING,
            }
            or (
                plan is not None
                and any(part.status is ChannelDeliveryPartStatus.PENDING for part in plan.parts)
            )
        )
        return ChannelGroupTurnSnapshot(
            route_id=record.lineage.route_id,
            route_revision=record.lineage.route_revision,
            scene_id=record.lineage.scene_id,
            participant_id=record.binding.participant_id,
            turn=_snapshot(turn),
            provider_receipt_present=receipt,
            cancelable=cancelable,
        )


def _link_snapshot(link: ChannelParticipantLinkRecord) -> ChannelParticipantLinkSnapshot:
    return ChannelParticipantLinkSnapshot(
        link_id=link.link_id,
        account_key=link.account_key,
        sender_key=link.sender_key,
        participant_id=link.participant_id,
        enabled=link.enabled,
        revision=link.revision,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


def _route_snapshot(route: ChannelGroupRouteRecord) -> ChannelGroupRouteSnapshot:
    return ChannelGroupRouteSnapshot(
        route_id=route.route_id,
        connection_id=route.connection_id,
        account_key=route.account_key,
        group_id=route.group_id,
        character_id=route.character_id,
        scene_id=route.scene_id,
        display_name=route.display_name,
        revision=route.revision,
        enabled=route.enabled,
        allow_requested_voice=route.allow_requested_voice,
        pause_reason=route.pause_reason,
        observation_id=route.observation_id,
        audience_fingerprint=route.audience_fingerprint,
        members=[
            ChannelGroupRouteMemberSnapshot(
                link_id=m.link_id,
                sender_key=m.sender_key,
                participant_id=m.participant_id,
                can_speak=m.can_speak,
            )
            for m in route.members
        ],
        created_at=route.created_at,
        updated_at=route.updated_at,
        deleted_at=route.deleted_at,
    )


def _receipt(turn: ChannelTurnRecord, *, duplicate: bool) -> ChannelTurnReceipt:
    return ChannelTurnReceipt(
        channel_turn_id=turn.channel_turn_id,
        connection_id=turn.connection_id,
        account_key=turn.account_key,
        external_message_id=turn.external_message_id,
        conversation_key=turn.conversation_key,
        sender_key=turn.sender_key,
        principal_scope=turn.principal_scope,
        chat_type=turn.chat_type,
        conversation_label=turn.conversation_label,
        sender_display_name=turn.sender_display_name,
        session_id=turn.session_id,
        turn_id=turn.turn_id,
        generation_id=turn.generation_id,
        status=turn.status,
        duplicate=duplicate,
        revision=turn.revision,
        accepted_at=turn.accepted_at,
        poll_after_ms=250
        if turn.status in {ChannelTurnStatus.ACCEPTED, ChannelTurnStatus.PROCESSING}
        else None,
    )


def _snapshot(turn: ChannelTurnRecord) -> ChannelTurnSnapshot:
    return ChannelTurnSnapshot(
        channel_turn_id=turn.channel_turn_id,
        connection_id=turn.connection_id,
        account_key=turn.account_key,
        external_message_id=turn.external_message_id,
        conversation_key=turn.conversation_key,
        sender_key=turn.sender_key,
        principal_scope=turn.principal_scope,
        chat_type=turn.chat_type,
        conversation_label=turn.conversation_label,
        sender_display_name=turn.sender_display_name,
        session_id=turn.session_id,
        turn_id=turn.turn_id,
        generation_id=turn.generation_id,
        status=turn.status,
        reply_text=turn.reply_text,
        delivery_id=turn.delivery_id,
        delivery_status=turn.delivery_status,
        error=turn.error,
        revision=turn.revision,
        created_at=turn.created_at,
        updated_at=turn.updated_at,
        completed_at=turn.completed_at,
    )
