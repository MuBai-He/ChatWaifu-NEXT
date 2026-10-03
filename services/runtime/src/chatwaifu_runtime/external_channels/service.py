"""Provider-neutral External Channel Gateway application service."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.channel_groups import ChannelGroupPauseReason
from chatwaifu_protocol.channels import (
    ChannelAuthorizationMethod,
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionSnapshot,
    ChannelConnectionStatus,
    ChannelDeliveryAcknowledgement,
    ChannelDeliveryClaimRequest,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryPartSnapshot,
    ChannelDeliveryPartStatus,
    ChannelDeliveryPlanSnapshot,
    ChannelDeliverySnapshot,
    ChannelDeliveryStatus,
    ChannelImageDeliveryPartPayload,
    ChannelInboundTextMessage,
    ChannelMessageKind,
    ChannelPresentationProfile,
    ChannelProviderCapabilities,
    ChannelProviderRegistration,
    ChannelTextDeliveryPartPayload,
    ChannelTurnCancelReceipt,
    ChannelTurnReceipt,
    ChannelTurnSnapshot,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_protocol.session import GenerationState

from chatwaifu_runtime.character_kernel.service import USER_SCOPE
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.conversation.models import (
    EXTERNAL_TEXT_TURN_OPTIONS,
    ConversationQuotedMessage,
    ConversationSourceContext,
)
from chatwaifu_runtime.conversation.repository import ConversationRepository
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.burst import (
    BurstBatch,
    BurstScheduler,
    ImageBurstCoordinator,
    combine_burst_captions,
)
from chatwaifu_runtime.external_channels.group_models import qq_id
from chatwaifu_runtime.external_channels.models import (
    ChannelBindingRecord,
    ChannelConnectionRecord,
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelDeliveryRecord,
    ChannelInboundAudioInput,
    ChannelInboundImageInput,
    ChannelTranscriptionIdentity,
    ChannelTurnRecord,
    CompleteTurnResult,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.presentation import (
    DeliveryPlanFactory,
    InstantMessageDeliveryPlanFactory,
    SingleTextDeliveryPlanFactory,
)
from chatwaifu_runtime.external_channels.stickers import PresetStickerCatalog
from chatwaifu_runtime.photo_memory.metadata import strip_image_exif
from chatwaifu_runtime.photo_memory.observer import PhotoMemoryObserver, PhotoObservationSource
from chatwaifu_runtime.providers.contracts import LlmInputImage
from chatwaifu_runtime.sessions.service import SessionService
from chatwaifu_runtime.sticker_library.selection import StickerSelectionHints, selection_hints
from chatwaifu_runtime.sticker_library.service import StickerLearningSource, StickerLibraryService

if TYPE_CHECKING:
    from chatwaifu_runtime.external_channels.groups import ChannelGroupService
    from chatwaifu_runtime.external_channels.proactive import ChannelProactiveService

logger = logging.getLogger(__name__)

BURST_LOAD_TIMEOUT_SECONDS = 20.0
AUDIO_PREPROCESS_TIMEOUT_SECONDS = 60.0
MAX_AUDIO_PREPROCESSING_TASKS = 32

WEIXIN_ILINK_PROVIDER = ChannelProviderRegistration(
    provider_id="weixin_ilink",
    version="1.0.0",
    name="微信",
    description="通过腾讯 iLink 协议在本机扫码连接微信。",
    capabilities=ChannelProviderCapabilities(
        chat_types=[ChannelChatType.DIRECT],
        inbound_message_kinds=[ChannelMessageKind.TEXT, ChannelMessageKind.IMAGE],
        outbound_message_kinds=[ChannelMessageKind.TEXT, ChannelMessageKind.IMAGE],
        authorization_methods=[ChannelAuthorizationMethod.QR_CODE],
        supports_typing=True,
        supports_partial_replies=False,
        supports_delivery_ack=True,
        supports_cancellation=True,
        supports_proactive_messages=False,
    ),
)


class ExternalChannelError(RuntimeError):
    code = "channel_error"
    http_status = 409
    retryable = False


class ChannelNotFoundError(ExternalChannelError):
    code = "channel_not_found"
    http_status = 404


class ChannelAuthenticationError(ExternalChannelError):
    code = "channel_authentication_failed"
    http_status = 401


class ChannelPolicyError(ExternalChannelError):
    code = "channel_policy_rejected"
    http_status = 403


class ChannelConflictError(ExternalChannelError):
    code = "channel_idempotency_conflict"
    http_status = 409


class ChannelDeliveryMultipartConflictError(ExternalChannelError):
    code = "channel_delivery_multipart_conflict"
    http_status = 409


class ChannelBusyError(ExternalChannelError):
    code = "channel_busy"
    http_status = 409
    retryable = True


class ChannelDeliveryBusyError(ExternalChannelError):
    code = "channel_delivery_busy"
    http_status = 409
    retryable = True


@dataclass(frozen=True, slots=True)
class CreatedChannelConnection:
    snapshot: ChannelConnectionSnapshot
    access_token: str


_PROVIDER_FAILURE_RECOVERY_TEXT = "唔，刚才的话好像没能顺利说出来……能再和我说一次吗？"
_IMAGE_FAILURE_RECOVERY_TEXT = "刚才发来的图片我没看清，能再发一次吗？"
_AUDIO_FAILURE_RECOVERY_TEXT = "刚才发来的语音我没听清，能再说一次或发文字吗？"


def _normalize_and_sanitize_inbound_images(
    raw_loaded: object,
) -> tuple[LlmInputImage, ...]:
    if isinstance(raw_loaded, tuple):
        images = cast(tuple[object, ...], raw_loaded)
    elif isinstance(raw_loaded, LlmInputImage):
        images = (raw_loaded,)
    else:
        raise ValueError(f"unsupported raw image input type: {type(raw_loaded)}")

    if not images or len(images) > 4:
        raise ValueError(f"inbound images must be 1..4 items, got {len(images)}")

    sanitized: list[LlmInputImage] = []
    for img in images:
        if not isinstance(img, LlmInputImage):
            raise ValueError(f"inbound image item is not LlmInputImage: {type(img)}")
        sanitized.append(strip_image_exif(img))

    return tuple(sanitized)


__all__ = [
    "WEIXIN_ILINK_PROVIDER",
    "ChannelInboundAudioInput",
    "ChannelInboundImageInput",
    "ChannelTranscriptionIdentity",
    "CreatedChannelConnection",
    "DeliveryPlanFactory",
    "ExternalChannelError",
    "ExternalChannelService",
    "ImageBurstCoordinator",
    "InstantMessageDeliveryPlanFactory",
    "SingleTextDeliveryPlanFactory",
]


class ExternalChannelService:
    """Coordinate external identities, durable turns, generation, and delivery.

    Provider SDK objects and provider-only state never cross this service. The
    provider registrations are transport-neutral and no lifecycle method
    branches on a provider id.
    """

    def __init__(
        self,
        repository: ExternalChannelRepository,
        conversation_repository: ConversationRepository,
        sessions: SessionService,
        conversation: ConversationService,
        characters: CharacterService,
        event_hub: EventHub,
        publisher: EventPublisher,
        *,
        providers: tuple[ChannelProviderRegistration, ...] = (WEIXIN_ILINK_PROVIDER,),
        delivery_plan_factory: DeliveryPlanFactory | None = None,
        sticker_catalog: PresetStickerCatalog | None = None,
        sticker_library: StickerLibraryService | None = None,
        photo_observer: PhotoMemoryObserver | None = None,
        burst_scheduler: BurstScheduler | None = None,
        tool_policy: Callable[[ChannelConnectionConfiguration, str], frozenset[str]] | None = None,
    ) -> None:
        self._repository = repository
        self._conversation_repository = conversation_repository
        self._sessions = sessions
        self._conversation = conversation
        self._characters = characters
        self._event_hub = event_hub
        self._publisher = publisher
        self._providers = {item.provider_id: item for item in providers}
        self._tool_policy = tool_policy
        self._sticker_catalog = sticker_catalog
        self._sticker_library = sticker_library
        self._photo_observer = photo_observer
        self._delivery_plan_factory = delivery_plan_factory or InstantMessageDeliveryPlanFactory(
            sticker_catalog=sticker_catalog
        )
        self._ingress_lock = asyncio.Lock()
        self._turn_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._audio_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._audio_lifecycle_lock = asyncio.Lock()
        self._audio_task_connections: dict[UUID, UUID] = {}
        self._turn_sync_locks: dict[UUID, asyncio.Lock] = {}
        self._turn_terminal_listeners: list[Callable[[ChannelTurnRecord], Awaitable[None]]] = []
        self._stopping = False
        self._started = False
        self._on_wake_scheduler: Callable[[UUID], None] | None = None
        self._proactive: ChannelProactiveService | None = None
        self._groups: ChannelGroupService | None = None
        self._burst_coordinator = ImageBurstCoordinator(
            repository=repository,
            scheduler=burst_scheduler,
            on_dispatch_burst=self._dispatch_burst,
            on_turn_terminal=self._notify_turn_terminal,
            publisher=self._publisher,
        )
        self.add_turn_terminal_listener(self._burst_coordinator.on_turn_terminal)

    @property
    def active_preprocessing_count(self) -> int:
        return len(self._audio_tasks)

    @property
    def burst_coordinator(self) -> ImageBurstCoordinator:
        return self._burst_coordinator

    def set_scheduler_wake_callback(self, callback: Callable[[UUID], None]) -> None:
        self._on_wake_scheduler = callback
        self._burst_coordinator.set_scheduler_wake_callback(callback)

    def set_proactive_service(self, service: ChannelProactiveService) -> None:
        self._proactive = service

    def set_group_service(self, service: ChannelGroupService) -> None:
        self._groups = service

    def wake_delivery_scheduler(self, connection_id: UUID) -> None:
        if self._on_wake_scheduler is not None:
            self._on_wake_scheduler(connection_id)

    async def authorize_proactive_delivery(self, plan: ChannelDeliveryPlanRecord) -> bool:
        if plan.outbound_intent_id is None:
            return True
        return self._proactive is not None and await self._proactive.authorize_delivery(plan)

    async def authorize_channel_delivery(self, plan: ChannelDeliveryPlanRecord) -> bool:
        if plan.group_target is not None:
            return self._groups is not None and await self._groups.authorize_delivery(plan)
        if plan.channel_turn_id is not None:
            turn = await self._repository.get_turn(plan.channel_turn_id)
            if turn is None or turn.chat_type is ChannelChatType.GROUP:
                return False
        return await self.authorize_proactive_delivery(plan)

    async def proactive_delivery_terminal(self, plan: ChannelDeliveryPlanRecord) -> None:
        if self._proactive is not None:
            await self._proactive.on_plan_terminal(plan)

    async def cancel_proactive_connection(self, connection_id: UUID, *, reason: str) -> None:
        if self._proactive is not None:
            await self._proactive.cancel_for_connection(connection_id, reason=reason)

    @property
    def repository(self) -> ExternalChannelRepository:
        return self._repository

    @property
    def sticker_catalog(self) -> PresetStickerCatalog | None:
        return self._sticker_catalog

    @property
    def publisher(self) -> EventPublisher:
        return self._publisher

    @property
    def event_hub(self) -> EventHub:
        return self._publisher.event_hub

    async def start(self) -> None:
        self._stopping = False
        self._started = True
        for turn in await self._repository.list_inflight_turns():
            turn = await self._sync_turn(turn)
            if turn.status in {
                ChannelTurnStatus.ACCEPTED,
                ChannelTurnStatus.PROCESSING,
                ChannelTurnStatus.CANCELLING,
            }:
                member_rec = await self._repository.find_burst_leader(turn.channel_turn_id)
                if (
                    member_rec is not None
                    and member_rec.leader_channel_turn_id != turn.channel_turn_id
                ):
                    continue
                self._ensure_turn_task(turn)

    async def stop(self) -> None:
        self._stopping = True
        # Constructor-owned cleanup can run before the database is opened.
        # Only a started gateway can own durable admissions to fence.
        if self._started:
            self._started = False
            await self._cancel_connection_audio()
        await self._burst_coordinator.stop()
        tasks = list(self._turn_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._turn_tasks.clear()

    def providers(self) -> tuple[ChannelProviderRegistration, ...]:
        return tuple(self._providers.values())

    @property
    def delivery_plan_factory(self) -> DeliveryPlanFactory:
        return self._delivery_plan_factory

    @delivery_plan_factory.setter
    def delivery_plan_factory(self, factory: DeliveryPlanFactory) -> None:
        self._delivery_plan_factory = factory

    async def list_connections(self) -> tuple[ChannelConnectionSnapshot, ...]:
        records = await self._repository.list_connections()
        return tuple(self._connection_snapshot(item) for item in records)

    async def get_connection(self, connection_id: UUID) -> ChannelConnectionSnapshot:
        return self._connection_snapshot(await self._required_connection(connection_id))

    async def create_connection(
        self,
        configuration: ChannelConnectionConfiguration,
        *,
        access_token: str | None = None,
    ) -> CreatedChannelConnection:
        self._validate_configuration(configuration)
        token = access_token or secrets.token_urlsafe(32)
        if len(token) < 32:
            raise ChannelPolicyError("channel connection access token is too short")
        try:
            record = await self._repository.create_connection(
                configuration,
                access_token_hash=_token_hash(token),
                created_at=datetime.now(UTC),
            )
        except ValueError as error:
            raise ChannelConflictError(str(error)) from error
        return CreatedChannelConnection(self._connection_snapshot(record), token)

    async def update_connection(
        self,
        configuration: ChannelConnectionConfiguration,
        *,
        expected_revision: int,
        rotate_access_token: bool,
    ) -> CreatedChannelConnection | ChannelConnectionSnapshot:
        current = await self._required_connection(configuration.connection_id)
        if current.configuration.character_id != configuration.character_id:
            raise ChannelConflictError(
                "character_id is immutable after channel connection creation"
            )
        if current.configuration.provider_id != configuration.provider_id:
            raise ChannelConflictError(
                "provider_id is immutable after channel connection creation; "
                "create a new connection"
            )
        if current.configuration.account_key != configuration.account_key:
            raise ChannelConflictError(
                "account_key is immutable after channel connection creation; "
                "create a new connection"
            )
        if current.configuration.principal_scope != configuration.principal_scope:
            raise ChannelConflictError(
                "principal_scope is immutable after channel connection creation"
            )
        self._validate_configuration(configuration)
        token = secrets.token_urlsafe(32) if rotate_access_token else None
        records: list[ChannelTurnRecord] = []
        tasks: list[asyncio.Task[None]] = []
        revoke_audio = (
            not configuration.enabled
            or current.configuration.allowed_sender_keys != configuration.allowed_sender_keys
        )
        try:
            async with self._audio_lifecycle_lock:
                if revoke_audio:
                    # Cancel registered preparation synchronously before the
                    # first database await, so revocation cannot yield into LLM
                    # startup. Registering new audio waits for this same lock.
                    await self._fence_audio_locked(configuration.connection_id, records, tasks)
                updated = await self._repository.update_connection(
                    configuration,
                    expected_revision=expected_revision,
                    access_token_hash=_token_hash(token) if token is not None else None,
                    updated_at=datetime.now(UTC),
                )
                if self._proactive is not None:
                    self._proactive.fence_route_revision(
                        configuration.connection_id, updated.revision
                    )
                if self._groups is not None:
                    # A rejected revision CAS must not cancel valid group work.
                    # Revoke synchronously after the successful repository write.
                    self._groups.fence_connection(
                        configuration.connection_id, reason="configuration_changed"
                    )
        except KeyError as error:
            raise ChannelNotFoundError(str(error)) from error
        except ValueError as error:
            raise ChannelConflictError(str(error)) from error
        finally:
            await self._finish_audio_cancellation(records, tasks)
        if self._proactive is not None:
            await self._proactive.connection_updated(
                configuration.connection_id, revision=updated.revision
            )
        if self._groups is not None:
            await self._groups.pause_connection(
                configuration.connection_id,
                reason=ChannelGroupPauseReason.CONFIGURATION_CHANGED
                if configuration.enabled
                else ChannelGroupPauseReason.CONNECTION_DISABLED,
            )
        snapshot = self._connection_snapshot(updated)
        return CreatedChannelConnection(snapshot, token) if token is not None else snapshot

    async def delete_connection(self, connection_id: UUID) -> None:
        await self._required_connection(connection_id)
        if self._groups is not None:
            self._groups.fence_connection(connection_id, reason="connection_deleted")
            await self._groups.pause_connection(
                connection_id, reason=ChannelGroupPauseReason.CONNECTION_DELETED
            )
        await self.cancel_proactive_connection(connection_id, reason="connection_deleted")
        await self._cancel_connection_audio(connection_id)
        try:
            removed = await self._repository.soft_delete_connection(
                connection_id, deleted_at=datetime.now(UTC)
            )
        except ValueError as error:
            raise ChannelConflictError(str(error)) from error
        if not removed:
            raise ChannelNotFoundError(f"unknown channel connection {connection_id}")

    async def test_connection(self, connection_id: UUID) -> ChannelConnectionSnapshot:
        record = await self._required_connection(connection_id)
        now = datetime.now(UTC)
        if not record.configuration.enabled:
            status = ChannelConnectionStatus.DISABLED
            error = None
        elif record.last_seen_at is None:
            status = ChannelConnectionStatus.DEGRADED
            error = _error(
                "channel_adapter_not_seen",
                "No authenticated adapter request has reached this connection yet.",
                retryable=True,
            )
        elif now - record.last_seen_at > timedelta(
            seconds=record.configuration.timeout_seconds * 2
        ):
            status = ChannelConnectionStatus.DEGRADED
            error = _error(
                "channel_adapter_stale",
                "The channel adapter has not contacted Runtime within its health window.",
                retryable=True,
            )
        else:
            status = ChannelConnectionStatus.READY
            error = None
        updated = await self._repository.set_connection_status(
            connection_id,
            status=status,
            last_error=error,
            updated_at=now,
        )
        return self._connection_snapshot(updated)

    async def ingest(
        self,
        message: ChannelInboundTextMessage,
        *,
        access_token: str,
        supersede_inflight: bool = False,
        image_input: ChannelInboundImageInput | None = None,
        audio_input: ChannelInboundAudioInput | None = None,
        image_retention_allowed: bool = True,
        burst_intake: bool = False,
        raw_images: tuple[object, ...] = (),
        context_token: str | None = None,
        pending_contexts_count: int = 0,
    ) -> ChannelTurnReceipt:
        if audio_input is not None and (image_input is not None or burst_intake or raw_images):
            raise ChannelPolicyError("Audio ingress cannot be combined with image ingress")
        if burst_intake and not image_retention_allowed:
            raise ChannelPolicyError("Ephemeral ingress does not support image burst collection")
        connection, binding, turn, duplicate = await self._admit_ingress(
            message,
            access_token=access_token,
            supersede_inflight=supersede_inflight,
            image_fingerprint=image_input.source_fingerprint if image_input is not None else None,
            burst_intake=burst_intake,
            audio_fingerprint=audio_input.source_fingerprint if audio_input is not None else None,
        )
        if duplicate:
            return self._turn_receipt(turn, duplicate=True)

        if audio_input is not None:
            turn = await self._register_audio(
                message, connection, binding, turn, audio_input, access_token
            )
            return self._turn_receipt(turn, duplicate=False)

        if burst_intake and image_input is not None:
            receipt = await self._burst_coordinator.admit_image(
                message=message,
                turn=turn,
                raw_images=raw_images,
                loader=image_input.load,
                character_id=connection.configuration.character_id,
                principal_scope=connection.configuration.principal_scope,
                context_token=context_token,
                access_token=access_token,
                pending_contexts_count=pending_contexts_count,
            )
            await self._repository.touch_connection(
                message.connection_id,
                status=ChannelConnectionStatus.READY,
                seen_at=datetime.now(UTC),
            )
            return receipt

        return await self._submit_admitted(
            message,
            connection,
            binding,
            turn,
            access_token=access_token,
            image_input=image_input,
            image_retention_allowed=image_retention_allowed,
        )

    async def _submit_admitted(
        self,
        message: ChannelInboundTextMessage,
        connection: ChannelConnectionRecord,
        binding: ChannelBindingRecord,
        turn: ChannelTurnRecord,
        *,
        access_token: str,
        image_input: ChannelInboundImageInput | None = None,
        image_retention_allowed: bool = True,
    ) -> ChannelTurnReceipt:
        source_context = ConversationSourceContext(
            provider_id=connection.configuration.provider_id,
            connection_id=connection.configuration.connection_id,
            account_key=message.account_key,
            principal_scope=message.principal_scope,
            chat_type=message.chat_type.value,
            conversation_key=message.conversation_key,
            sender_key=message.sender_key,
            received_at=message.received_at,
            conversation_label=message.conversation_label,
            sender_display_name=message.sender_display_name,
            reply_to_external_message_id=message.reply_to_external_message_id,
        )
        policy = connection.configuration.presentation_policy
        profile = (
            policy.profile.value
            if policy is not None and hasattr(policy.profile, "value")
            else (str(policy.profile) if policy is not None else None)
        )
        recovery_text = (
            _IMAGE_FAILURE_RECOVERY_TEXT
            if image_input is not None
            else _PROVIDER_FAILURE_RECOVERY_TEXT
        )
        image_loader = image_input.load if image_input is not None else None
        library = self._sticker_library
        photos = self._photo_observer
        if (
            image_loader is not None
            and image_retention_allowed
            and (library is not None or photos is not None)
            and connection.configuration.character_id == "default"
            and message.chat_type is ChannelChatType.DIRECT
        ):
            original_loader = image_loader

            async def wait_for_completion() -> bool:
                result = await self.wait_for_turn(
                    turn.connection_id, turn.channel_turn_id, wait_seconds=30
                )
                return result.status is ChannelTurnStatus.COMPLETED

            async def learning_loader() -> tuple[LlmInputImage, ...]:
                raw_loaded = await original_loader()
                sanitized_images = _normalize_and_sanitize_inbound_images(raw_loaded)
                original_images = raw_loaded if isinstance(raw_loaded, tuple) else (raw_loaded,)
                try:
                    if library is not None:
                        await library.observe_batch(
                            StickerLearningSource(
                                principal_scope=connection.configuration.principal_scope,
                                character_id=connection.configuration.character_id,
                                connection_id=turn.connection_id,
                                generation_id=turn.generation_id,
                            ),
                            original_images,
                            wait_for_completion=wait_for_completion,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "sticker learning observation skipped generation_id=%s", turn.generation_id
                    )
                try:
                    if photos is not None:
                        await photos.observe_batch(
                            PhotoObservationSource(
                                principal_scope=connection.configuration.principal_scope,
                                character_id=connection.configuration.character_id,
                                connection_id=turn.connection_id,
                                generation_id=turn.generation_id,
                            ),
                            original_images,
                            wait_for_completion=wait_for_completion,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("photo observation skipped generation_id=%s", turn.generation_id)
                return sanitized_images

            image_loader = learning_loader
        elif image_loader is not None:
            raw_base_loader = image_loader

            async def sanitized_image_loader() -> tuple[LlmInputImage, ...]:
                raw_loaded = await raw_base_loader()
                return _normalize_and_sanitize_inbound_images(raw_loaded)

            image_loader = sanitized_image_loader
        allowed_skills: frozenset[str] = (
            self._tool_policy(connection.configuration, message.text)
            if self._tool_policy is not None
            else frozenset()
        )
        options = replace(
            EXTERNAL_TEXT_TURN_OPTIONS,
            allow_tools=bool(allowed_skills),
            allowed_skill_ids=allowed_skills,
            contextual_skill_ids=allowed_skills,
            source_context=source_context,
            presentation_profile=profile,
            failure_recovery_text=recovery_text,
            image_loader=image_loader,
            quoted_message_loader=(
                self._quoted_message_loader(message, binding)
                if message.reply_to_external_message_id is not None
                else None
            ),
        )
        generation_admitted = False
        try:
            accepted = await self._conversation.submit_text(
                binding.session_id,
                message.text,
                options=options,
                turn_id=turn.turn_id,
                generation_id=turn.generation_id,
            )
            if accepted.turn_id != turn.turn_id or accepted.generation_id != turn.generation_id:
                raise RuntimeError("conversation did not preserve preallocated channel identity")
            generation_admitted = True
            if (
                turn.input_kind is ChannelMessageKind.AUDIO
                and not await self._audio_context_is_active(
                    message,
                    binding,
                    turn,
                    expected_character_id=connection.configuration.character_id,
                )
            ):
                await self._conversation.cancel(
                    binding.session_id,
                    "channel_ingress_cancelled",
                    expected_generation_id=turn.generation_id,
                )
                raise asyncio.CancelledError
            turn = await self._repository.set_turn_processing(
                turn.channel_turn_id, updated_at=datetime.now(UTC)
            )
            if turn.status is not ChannelTurnStatus.PROCESSING:
                await self._conversation.cancel(
                    binding.session_id,
                    "channel_ingress_cancelled",
                    expected_generation_id=turn.generation_id,
                )
                raise asyncio.CancelledError
            self._ensure_turn_task(turn, access_token=access_token)
        except asyncio.CancelledError:
            if generation_admitted:
                await self._conversation.cancel(
                    binding.session_id,
                    "channel_ingress_cancelled",
                    expected_generation_id=turn.generation_id,
                )
            await self._set_turn_terminal(
                turn.channel_turn_id,
                status=ChannelTurnStatus.CANCELLED,
                error=_error(
                    "channel_ingress_cancelled",
                    "Channel ingress was cancelled before admission completed.",
                ),
                completed_at=datetime.now(UTC),
            )
            raise
        except Exception as error:
            if generation_admitted:
                await self._conversation.cancel(
                    binding.session_id,
                    "channel_admission_failed",
                    expected_generation_id=turn.generation_id,
                )
            await self._set_turn_terminal(
                turn.channel_turn_id,
                status=ChannelTurnStatus.FAILED,
                error=_error(
                    "channel_submission_failed",
                    str(error),
                ),
                completed_at=datetime.now(UTC),
            )
            raise
        await self._repository.touch_connection(
            message.connection_id,
            status=ChannelConnectionStatus.READY,
            seen_at=datetime.now(UTC),
        )
        return self._turn_receipt(turn, duplicate=False)

    def _audio_is_current(self, turn: ChannelTurnRecord) -> bool:
        task = asyncio.current_task()
        return (
            not self._stopping
            and task is not None
            and not task.cancelling()
            and self._audio_tasks.get(turn.channel_turn_id) is task
        )

    async def _audio_context_is_active(
        self,
        message: ChannelInboundTextMessage,
        binding: ChannelBindingRecord,
        turn: ChannelTurnRecord,
        *,
        expected_character_id: str,
        require_current_task: bool = True,
    ) -> bool:
        current = await self._repository.get_turn(turn.channel_turn_id)
        connection = await self._repository.get_connection(turn.connection_id)
        current_binding = await self._repository.find_binding(
            turn.connection_id, turn.conversation_key
        )
        if (
            self._stopping
            or (require_current_task and not self._audio_is_current(turn))
            or current is None
            or current.status is not ChannelTurnStatus.ACCEPTED
            or connection is None
            or connection.deleted_at is not None
            or connection.configuration.character_id != expected_character_id
            or current_binding is None
            or current_binding.binding_id != binding.binding_id
            or current_binding.session_id != binding.session_id
            or current_binding.sender_key != binding.sender_key
        ):
            return False
        try:
            self._validate_ingress(connection, message, has_audio=True)
        except ChannelPolicyError:
            return False
        return True

    async def _register_audio(
        self,
        message: ChannelInboundTextMessage,
        connection: ChannelConnectionRecord,
        binding: ChannelBindingRecord,
        turn: ChannelTurnRecord,
        audio: ChannelInboundAudioInput,
        access_token: str,
    ) -> ChannelTurnRecord:
        cancelled: ChannelTurnRecord | None = None
        # A lifecycle operation can fence the committed row while admission is
        # still returning. Re-read it and register atomically with lifecycle
        # cancellation; never hold this lock while joining a task or doing IO.
        async with self._audio_lifecycle_lock:
            active = await self._audio_context_is_active(
                message,
                binding,
                turn,
                expected_character_id=connection.configuration.character_id,
                require_current_task=False,
            )
            if active:
                self._audio_task_connections[turn.channel_turn_id] = turn.connection_id
                self._audio_tasks[turn.channel_turn_id] = asyncio.create_task(
                    self._preprocess_audio(message, connection, binding, turn, audio, access_token),
                    name=f"channel-audio-{turn.channel_turn_id}",
                )
                return turn
            fresh = await self._repository.get_turn(turn.channel_turn_id)
            if fresh is not None and fresh.status is ChannelTurnStatus.ACCEPTED:
                cancelled = await self._repository.set_turn_terminal(
                    turn.channel_turn_id,
                    status=ChannelTurnStatus.CANCELLED,
                    error=_error("channel_ingress_cancelled", "Audio ingress was cancelled."),
                    completed_at=datetime.now(UTC),
                )
            else:
                turn = fresh if fresh is not None else turn
        if cancelled is not None:
            await self._notify_turn_terminal(cancelled)
            return cancelled
        return turn

    async def _preprocess_audio(
        self,
        message: ChannelInboundTextMessage,
        connection: ChannelConnectionRecord,
        binding: ChannelBindingRecord,
        turn: ChannelTurnRecord,
        audio: ChannelInboundAudioInput,
        access_token: str,
    ) -> None:
        try:
            # Keep the complete preprocessing phase bounded, including an adapter
            # that catches the deadline cancellation and returns a late result.
            async with asyncio.timeout(AUDIO_PREPROCESS_TIMEOUT_SECONDS) as deadline:
                async with self._audio_lifecycle_lock:
                    active = await self._audio_context_is_active(
                        message,
                        binding,
                        turn,
                        expected_character_id=connection.configuration.character_id,
                    )
                if not active:
                    if self._audio_is_current(turn):
                        await self._set_turn_terminal(
                            turn.channel_turn_id,
                            status=ChannelTurnStatus.CANCELLED,
                            error=_error(
                                "channel_ingress_cancelled", "Audio ingress was cancelled."
                            ),
                            completed_at=datetime.now(UTC),
                        )
                    raise asyncio.CancelledError
                transcript = await audio.load(
                    ChannelTranscriptionIdentity(
                        session_id=turn.session_id,
                        turn_id=turn.turn_id,
                        generation_id=turn.generation_id,
                    )
                )
                if deadline.expired():
                    raise TimeoutError
                if not self._audio_is_current(turn):
                    raise asyncio.CancelledError
                if not isinstance(cast(object, transcript), str):
                    raise ValueError("invalid transcript")
                transcript = transcript.strip()
                if not transcript or len(transcript) > 20000:
                    raise ValueError("invalid transcript")
                async with self._ingress_lock:
                    active = await self._audio_context_is_active(
                        message,
                        binding,
                        turn,
                        expected_character_id=connection.configuration.character_id,
                    )
                if not active:
                    if self._audio_is_current(turn):
                        await self._set_turn_terminal(
                            turn.channel_turn_id,
                            status=ChannelTurnStatus.CANCELLED,
                            error=_error(
                                "channel_ingress_cancelled", "Audio ingress was cancelled."
                            ),
                            completed_at=datetime.now(UTC),
                        )
                    raise asyncio.CancelledError
            # Never hold admission's lock across asynchronous Conversation
            # preparation. Supersession cancels and joins this same task.
            if not self._audio_is_current(turn):
                raise asyncio.CancelledError
            await self._submit_admitted(
                message.model_copy(update={"text": transcript}),
                connection,
                binding,
                turn,
                access_token=access_token,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._audio_is_current(turn):
                async with self._ingress_lock:
                    active = await self._audio_context_is_active(
                        message,
                        binding,
                        turn,
                        expected_character_id=connection.configuration.character_id,
                    )
                if self._audio_is_current(turn):
                    if active:
                        await self._fail_audio_turn(turn)
                    else:
                        await self._set_turn_terminal(
                            turn.channel_turn_id,
                            status=ChannelTurnStatus.CANCELLED,
                            error=_error(
                                "channel_ingress_cancelled", "Audio ingress was cancelled."
                            ),
                            completed_at=datetime.now(UTC),
                        )
        finally:
            if self._audio_tasks.get(turn.channel_turn_id) is asyncio.current_task():
                self._audio_tasks.pop(turn.channel_turn_id, None)
                self._audio_task_connections.pop(turn.channel_turn_id, None)

    async def _fail_audio_turn(self, turn: ChannelTurnRecord) -> ChannelTurnRecord:
        result = await self._repository.fail_turn_with_notice(
            turn.channel_turn_id,
            error=_error("audio_transcription_failed", "Inbound audio could not be transcribed."),
            notice_text=_AUDIO_FAILURE_RECOVERY_TEXT,
            delivery_id=uuid4(),
            completed_at=datetime.now(UTC),
        )
        for event in result.persisted_events:
            await self._publisher.publish_persisted(event)
        if result.turn.status is ChannelTurnStatus.FAILED:
            if self._on_wake_scheduler is not None:
                self._on_wake_scheduler(turn.connection_id)
            await self._notify_turn_terminal(result.turn)
        return result.turn

    async def _cancel_audio_task(self, channel_turn_id: UUID) -> None:
        task = self._audio_tasks.pop(channel_turn_id, None)
        self._audio_task_connections.pop(channel_turn_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _fence_audio_locked(
        self,
        connection_id: UUID | None,
        records: list[ChannelTurnRecord],
        tasks: list[asyncio.Task[None]],
    ) -> None:
        # This first pass contains no await. In particular, permission revocation
        # must cancel Conversation preparation before database scheduling yields.
        for channel_turn_id, task in tuple(self._audio_tasks.items()):
            if (
                connection_id is not None
                and self._audio_task_connections.get(channel_turn_id) != connection_id
            ):
                continue
            self._audio_tasks.pop(channel_turn_id, None)
            self._audio_task_connections.pop(channel_turn_id, None)
            if task is not asyncio.current_task():
                task.cancel()
                tasks.append(task)
        for turn in await self._repository.list_inflight_turns(connection_id):
            if turn.input_kind is not ChannelMessageKind.AUDIO:
                continue
            # Include durable admissions that have not registered a task.
            record = await self._repository.set_turn_terminal(
                turn.channel_turn_id,
                status=ChannelTurnStatus.CANCELLED,
                error=_error("channel_ingress_cancelled", "Audio ingress was cancelled."),
                completed_at=datetime.now(UTC),
            )
            records.append(record)

    async def _finish_audio_cancellation(
        self,
        records: list[ChannelTurnRecord],
        tasks: list[asyncio.Task[None]],
    ) -> None:
        # Joining and callbacks run outside the lifecycle lock: cancellation can
        # itself finish a terminal callback or try to check the final fence.
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for turn in records:
            await self._conversation.cancel(
                turn.session_id,
                "channel_ingress_cancelled",
                expected_generation_id=turn.generation_id,
            )
            await self._notify_turn_terminal(turn)

    async def _cancel_connection_audio(self, connection_id: UUID | None = None) -> None:
        records: list[ChannelTurnRecord] = []
        tasks: list[asyncio.Task[None]] = []
        try:
            async with self._audio_lifecycle_lock:
                await self._fence_audio_locked(connection_id, records, tasks)
        finally:
            await self._finish_audio_cancellation(records, tasks)

    def _quoted_message_loader(
        self, message: ChannelInboundTextMessage, binding: ChannelBindingRecord
    ) -> Callable[[], Awaitable[ConversationQuotedMessage | None]]:
        async def load() -> ConversationQuotedMessage | None:
            reference = message.reply_to_external_message_id
            if reference is None or reference == message.external_message_id:
                return None
            # This port reads only admitted or confirmed messages from this exact route.
            # It deliberately cannot fetch arbitrary provider history or other peers.
            return await self._repository.resolve_quoted_message(
                message.connection_id, binding.binding_id, reference
            )

        return load

    async def _dispatch_burst(self, batch: BurstBatch) -> None:
        connection = await self._repository.get_connection(batch.connection_id)
        if connection is None:
            logger.error("burst dispatch failed: missing connection %s", batch.connection_id)
            return

        leader_turn = batch.leader_turn
        first_item = batch.items[0]
        combined_text = combine_burst_captions(batch.items)

        source_context = ConversationSourceContext(
            provider_id=connection.configuration.provider_id,
            connection_id=connection.configuration.connection_id,
            account_key=first_item.message.account_key,
            principal_scope=first_item.message.principal_scope,
            chat_type=first_item.message.chat_type.value,
            conversation_key=first_item.message.conversation_key,
            sender_key=first_item.message.sender_key,
            received_at=first_item.message.received_at,
            conversation_label=first_item.message.conversation_label,
            sender_display_name=first_item.message.sender_display_name,
        )
        policy = connection.configuration.presentation_policy
        profile = (
            policy.profile.value
            if policy is not None and hasattr(policy.profile, "value")
            else (str(policy.profile) if policy is not None else None)
        )

        async def combined_base_loader() -> tuple[LlmInputImage, ...]:
            all_images: list[LlmInputImage] = []
            async with asyncio.timeout(BURST_LOAD_TIMEOUT_SECONDS):
                for item in batch.items:
                    loaded = await item.loader()
                    item_images = loaded if isinstance(loaded, tuple) else (loaded,)
                    if len(item_images) != len(item.item_origins):
                        raise ValueError(
                            f"burst item returned {len(item_images)} images, "
                            f"expected {len(item.item_origins)} from wire descriptor"
                        )
                    all_images.extend(item_images)
            return tuple(all_images)

        batch_item_origins = tuple(origin for item in batch.items for origin in item.item_origins)
        library = self._sticker_library
        photos = self._photo_observer

        if (
            (library is not None or photos is not None)
            and connection.configuration.character_id == "default"
            and first_item.message.chat_type is ChannelChatType.DIRECT
        ):

            async def wait_for_completion() -> bool:
                result = await self.wait_for_turn(
                    leader_turn.connection_id, leader_turn.channel_turn_id, wait_seconds=30
                )
                return result.status is ChannelTurnStatus.COMPLETED

            async def learning_loader() -> tuple[LlmInputImage, ...]:
                raw_loaded = await combined_base_loader()
                sanitized_images = _normalize_and_sanitize_inbound_images(raw_loaded)
                original_images = raw_loaded
                try:
                    if library is not None:
                        await library.observe_batch(
                            StickerLearningSource(
                                principal_scope=connection.configuration.principal_scope,
                                character_id=connection.configuration.character_id,
                                connection_id=leader_turn.connection_id,
                                generation_id=leader_turn.generation_id,
                            ),
                            original_images,
                            wait_for_completion=wait_for_completion,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "sticker learning observation skipped generation_id=%s",
                        leader_turn.generation_id,
                    )
                try:
                    if photos is not None:
                        await photos.observe_batch(
                            PhotoObservationSource(
                                principal_scope=connection.configuration.principal_scope,
                                character_id=connection.configuration.character_id,
                                connection_id=leader_turn.connection_id,
                                generation_id=leader_turn.generation_id,
                            ),
                            original_images,
                            wait_for_completion=wait_for_completion,
                            item_origins=batch_item_origins,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "photo observation skipped generation_id=%s", leader_turn.generation_id
                    )
                return sanitized_images

            image_loader = learning_loader
        else:

            async def sanitized_image_loader() -> tuple[LlmInputImage, ...]:
                raw_loaded = await combined_base_loader()
                return _normalize_and_sanitize_inbound_images(raw_loaded)

            image_loader = sanitized_image_loader

        options = replace(
            EXTERNAL_TEXT_TURN_OPTIONS,
            source_context=source_context,
            presentation_profile=profile,
            failure_recovery_text=_IMAGE_FAILURE_RECOVERY_TEXT,
            image_loader=image_loader,
        )
        generation_admitted = False
        try:
            accepted = await self._conversation.submit_text(
                batch.session_id,
                combined_text,
                options=options,
                turn_id=leader_turn.turn_id,
                generation_id=leader_turn.generation_id,
            )
            if (
                accepted.turn_id != leader_turn.turn_id
                or accepted.generation_id != leader_turn.generation_id
            ):
                raise RuntimeError("conversation did not preserve preallocated channel identity")
            generation_admitted = True
            updated_leader = await self._repository.set_turn_processing(
                leader_turn.channel_turn_id, updated_at=datetime.now(UTC)
            )
            self._ensure_turn_task(updated_leader, access_token=batch.access_token)
        except asyncio.CancelledError:
            if generation_admitted:
                await self._conversation.cancel(batch.session_id, "channel_ingress_cancelled")
            await self._set_turn_terminal(
                leader_turn.channel_turn_id,
                status=ChannelTurnStatus.CANCELLED,
                error=_error(
                    "channel_ingress_cancelled",
                    "Channel ingress was cancelled before admission completed.",
                ),
                completed_at=datetime.now(UTC),
            )
            raise
        except Exception as error:
            if generation_admitted:
                await self._conversation.cancel(batch.session_id, "channel_admission_failed")
            await self._set_turn_terminal(
                leader_turn.channel_turn_id,
                status=ChannelTurnStatus.FAILED,
                error=_error(
                    "channel_submission_failed",
                    str(error),
                ),
                completed_at=datetime.now(UTC),
            )
            raise

    def add_turn_terminal_listener(
        self, listener: Callable[[ChannelTurnRecord], Awaitable[None]]
    ) -> None:
        self._turn_terminal_listeners.append(listener)

    async def _notify_turn_terminal(self, turn: ChannelTurnRecord) -> None:
        if self._turn_terminal_listeners:
            for listener in list(self._turn_terminal_listeners):
                try:
                    await listener(turn)
                except Exception:
                    logger.exception(
                        "turn terminal listener failed for turn %s", turn.channel_turn_id
                    )
        # Successful completion already has a durable delivery-plan event.
        # Notify local listeners without mislabelling it as a failure event.
        if turn.status is ChannelTurnStatus.COMPLETED:
            return
        now = datetime.now(UTC)
        event_type = (
            "channel.turn_cancelled"
            if turn.status is ChannelTurnStatus.CANCELLED
            else "channel.turn_failed"
        )
        try:
            await self._publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": event_type,
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "generation_id": turn.generation_id,
                        "occurred_at": now,
                        "source": "runtime.external_channels",
                        "privacy": PrivacyLevel.PRIVATE,
                        "payload": {
                            "connection_id": str(turn.connection_id),
                            "channel_turn_id": str(turn.channel_turn_id),
                            "external_message_id": turn.external_message_id,
                            "status": turn.status.value,
                        },
                    }
                )
            )
        except Exception:
            logger.exception("failed to emit turn terminal event for %s", turn.channel_turn_id)

    async def _set_turn_terminal(
        self,
        channel_turn_id: UUID,
        *,
        status: ChannelTurnStatus,
        error: StructuredError | None,
        completed_at: datetime,
    ) -> ChannelTurnRecord:
        record = await self._repository.set_turn_terminal(
            channel_turn_id,
            status=status,
            error=error,
            completed_at=completed_at,
        )
        await self._cancel_audio_task(channel_turn_id)
        await self._notify_turn_terminal(record)
        members = await self._repository.list_burst_members(channel_turn_id)
        for member in members:
            if member.member_channel_turn_id != channel_turn_id:
                mem_turn = await self._repository.get_turn(member.member_channel_turn_id)
                if mem_turn is not None:
                    await self._notify_turn_terminal(mem_turn)
        return record

    async def _admit_ingress(
        self,
        message: ChannelInboundTextMessage,
        *,
        access_token: str,
        supersede_inflight: bool = False,
        image_fingerprint: str | None = None,
        burst_intake: bool = False,
        audio_fingerprint: str | None = None,
    ) -> tuple[ChannelConnectionRecord, ChannelBindingRecord, ChannelTurnRecord, bool]:
        """Persist a unique channel turn without serializing model preparation.

        The process-local lock protects the compound binding/idempotency checks.
        Once the durable accepted turn exists, later messages observe it as
        inflight while Memory, Character Kernel, and LLM preparation continue
        outside this global critical section.
        """

        async with self._ingress_lock:
            connection = await self._authenticate(message.connection_id, access_token)
            self._validate_ingress(
                connection,
                message,
                has_image=image_fingerprint is not None,
                has_audio=audio_fingerprint is not None,
            )
            if self._stopping:
                raise ChannelBusyError("channel ingress is stopping")
            digest = _message_digest(
                message, image_fingerprint=image_fingerprint, audio_fingerprint=audio_fingerprint
            )
            duplicate = await self._repository.find_turn_by_external_message(
                message.connection_id,
                message.external_message_id,
                conversation_key=message.conversation_key,
            )
            if duplicate is not None:
                if duplicate.content_sha256 != digest:
                    raise ChannelConflictError(
                        "external_message_id was already used with different message content"
                    )
                binding = await self._repository.find_binding(
                    message.connection_id, message.conversation_key
                )
                if binding is None or binding.binding_id != duplicate.binding_id:
                    raise ChannelConflictError(
                        "duplicate channel turn no longer has its durable binding"
                    )
                return connection, binding, duplicate, True

            if (
                audio_fingerprint is not None
                and len(self._audio_tasks) >= MAX_AUDIO_PREPROCESSING_TASKS
            ):
                raise ChannelBusyError("audio preprocessing capacity is exhausted")
            binding = await self._repository.find_binding(
                message.connection_id, message.conversation_key
            )
            if binding is None:
                session = await self._sessions.create_session(connection.configuration.character_id)
                binding = await self._repository.create_binding(
                    binding_id=uuid4(),
                    connection_id=message.connection_id,
                    conversation_key=message.conversation_key,
                    sender_key=message.sender_key,
                    session_id=session.session_id,
                    created_at=datetime.now(UTC),
                )
            elif binding.sender_key != message.sender_key:
                raise ChannelPolicyError(
                    "conversation_key is already bound to a different sender identity"
                )

            if self._proactive is not None:
                self._proactive.fence_binding(binding.binding_id, "owner_input")
                await self._proactive.cancel_for_binding(binding.binding_id, reason="owner_input")

            if supersede_inflight:
                await self._burst_coordinator.cancel_pending_burst(
                    binding.binding_id, reason="superseded_by_new_inbound_message"
                )
                await self._burst_coordinator.cancel_active_batch(
                    binding.binding_id, reason="superseded_by_new_inbound_message"
                )
                for previous in await self._repository.list_inflight_turns(message.connection_id):
                    if previous.binding_id == binding.binding_id:
                        if previous.status is ChannelTurnStatus.PROCESSING:
                            await self.interrupt(
                                message.connection_id,
                                previous.channel_turn_id,
                                access_token=access_token,
                                reason="superseded_by_new_inbound_message",
                            )
                        elif previous.status is ChannelTurnStatus.ACCEPTED:
                            await self._set_turn_terminal(
                                previous.channel_turn_id,
                                status=ChannelTurnStatus.CANCELLED,
                                error=_error(
                                    "channel_ingress_cancelled",
                                    "Superseded by new inbound message while pending.",
                                ),
                                completed_at=datetime.now(UTC),
                            )

            if burst_intake:
                inflight = await self._repository.list_inflight_turns(message.connection_id)
                binding_inflight = [t for t in inflight if t.binding_id == binding.binding_id]
                for previous in binding_inflight:
                    member = await self._repository.find_burst_leader(previous.channel_turn_id)
                    live_id = (
                        member.leader_channel_turn_id
                        if member is not None
                        else previous.channel_turn_id
                    )
                    if self._burst_coordinator.is_live_burst_turn(live_id):
                        continue
                    if previous.status is ChannelTurnStatus.PROCESSING:
                        await self.interrupt(
                            message.connection_id,
                            previous.channel_turn_id,
                            access_token=access_token,
                            reason="superseded_by_new_inbound_message",
                        )
                    elif previous.status is ChannelTurnStatus.ACCEPTED:
                        await self._set_turn_terminal(
                            previous.channel_turn_id,
                            status=ChannelTurnStatus.CANCELLED,
                            error=_error(
                                "channel_ingress_cancelled",
                                "Superseded by new inbound message while pending.",
                            ),
                            completed_at=datetime.now(UTC),
                        )
            else:
                if await self._repository.has_inflight_turn(binding.binding_id):
                    raise ChannelBusyError(
                        "this external conversation already has an active generation"
                    )
                if self._conversation.active_generation_id(binding.session_id) is not None:
                    raise ChannelBusyError("the bound Runtime session is already generating")

            now = datetime.now(UTC)
            if not burst_intake:
                active_plans = await self._repository.list_active_delivery_plans_for_binding(
                    binding.binding_id
                )
                for active_plan in active_plans:
                    if active_plan.status in (
                        ChannelDeliveryStatus.PENDING,
                        ChannelDeliveryStatus.SENDING,
                    ):
                        logger.info(
                            "cancelling unsent tail of active delivery plan: "
                            "delivery_id=%s reason=%s",
                            active_plan.delivery_id,
                            "superseded_by_new_inbound_message",
                        )
                        cancel_res = await self._repository.cancel_remaining_delivery_parts(
                            active_plan.delivery_id,
                            ChannelDeliveryPartsCancelRequest(
                                reason="superseded_by_new_inbound_message",
                                requested_at=now,
                            ),
                        )
                        for ev in cancel_res.persisted_events:
                            await self._publisher.publish_persisted(ev)

            turn = ChannelTurnRecord(
                channel_turn_id=uuid4(),
                connection_id=message.connection_id,
                binding_id=binding.binding_id,
                external_message_id=message.external_message_id,
                content_sha256=digest,
                account_key=message.account_key,
                conversation_key=message.conversation_key,
                chat_type=message.chat_type,
                conversation_label=message.conversation_label,
                sender_key=message.sender_key,
                sender_display_name=message.sender_display_name,
                principal_scope=message.principal_scope,
                session_id=binding.session_id,
                turn_id=uuid4(),
                generation_id=uuid4(),
                status=ChannelTurnStatus.ACCEPTED,
                reply_text=None,
                error=None,
                delivery_id=None,
                delivery_status=None,
                revision=0,
                accepted_at=now,
                created_at=now,
                updated_at=now,
                completed_at=None,
                input_kind=(
                    ChannelMessageKind.AUDIO
                    if audio_fingerprint is not None
                    else ChannelMessageKind.IMAGE
                    if image_fingerprint is not None
                    else ChannelMessageKind.TEXT
                ),
            )
            turn = await self._repository.create_turn(turn)
            return connection, binding, turn, False

    async def wait_for_turn(
        self,
        connection_id: UUID,
        channel_turn_id: UUID,
        *,
        access_token: str | None = None,
        wait_seconds: float,
    ) -> ChannelTurnSnapshot:
        if access_token is not None:
            await self._authenticate(connection_id, access_token)

        turn = await self._required_turn(connection_id, channel_turn_id)
        member_rec = await self._repository.find_burst_leader(turn.channel_turn_id)
        effective_generation_id = turn.generation_id
        if member_rec is not None and member_rec.leader_channel_turn_id != turn.channel_turn_id:
            leader_turn = await self._repository.get_turn(member_rec.leader_channel_turn_id)
            if leader_turn is not None:
                effective_generation_id = leader_turn.generation_id

        def matches(event: dict[str, object]) -> bool:
            gen_id = str(event.get("generation_id"))
            return gen_id in {str(turn.generation_id), str(effective_generation_id)}

        subscription = self._event_hub.subscribe(matches) if wait_seconds > 0 else None
        deadline = asyncio.get_running_loop().time() + wait_seconds
        try:
            while True:
                turn = await self._sync_turn(
                    await self._required_turn(connection_id, channel_turn_id)
                )
                if turn.status not in {
                    ChannelTurnStatus.ACCEPTED,
                    ChannelTurnStatus.PROCESSING,
                    ChannelTurnStatus.CANCELLING,
                }:
                    return self._turn_snapshot(turn)
                remaining = deadline - asyncio.get_running_loop().time()
                if subscription is None or remaining <= 0:
                    return self._turn_snapshot(turn)
                try:
                    await asyncio.wait_for(subscription.receive(), timeout=min(remaining, 2.0))
                except TimeoutError:
                    continue
        finally:
            if subscription is not None:
                self._event_hub.unsubscribe(subscription)

    def _ensure_turn_task(self, turn: ChannelTurnRecord, access_token: str | None = None) -> None:
        if self._stopping:
            return
        existing = self._turn_tasks.get(turn.channel_turn_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._orchestrate_turn(turn, access_token=access_token),
            name=f"channel-turn-{turn.channel_turn_id}",
        )
        self._turn_tasks[turn.channel_turn_id] = task
        task.add_done_callback(
            lambda completed, owned_id=turn.channel_turn_id: self._turn_tasks.pop(owned_id, None)
        )

    async def _orchestrate_turn(
        self, turn: ChannelTurnRecord, access_token: str | None = None
    ) -> None:
        try:
            while not self._stopping:
                snapshot = await self.wait_for_turn(
                    turn.connection_id,
                    turn.channel_turn_id,
                    access_token=access_token,
                    wait_seconds=30.0,
                )
                if snapshot.status not in {
                    ChannelTurnStatus.ACCEPTED,
                    ChannelTurnStatus.PROCESSING,
                    ChannelTurnStatus.CANCELLING,
                }:
                    # Reconcile the durable terminal fact even if a listener was
                    # missed. This is idempotent and releases any deferred burst.
                    record = await self._repository.get_turn(turn.channel_turn_id)
                    if record is not None:
                        await self._burst_coordinator.on_turn_terminal(record)
                    return
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("turn orchestration error for %s", turn.channel_turn_id)

    async def interrupt(
        self,
        connection_id: UUID,
        channel_turn_id: UUID,
        *,
        access_token: str,
        reason: str,
    ) -> ChannelTurnCancelReceipt:
        await self._authenticate(connection_id, access_token)
        member_rec = await self._repository.find_burst_leader(channel_turn_id)
        target_turn_id = (
            member_rec.leader_channel_turn_id if member_rec is not None else channel_turn_id
        )
        # Establish the durable cancellation fence before waiting for optional
        # reply preparation (including sticker-history reads) under _sync_turn.
        turn = await self._required_turn(connection_id, target_turn_id)
        if turn.status not in {ChannelTurnStatus.ACCEPTED, ChannelTurnStatus.PROCESSING}:
            return ChannelTurnCancelReceipt(
                channel_turn_id=channel_turn_id,
                accepted=False,
                status=turn.status,
                revision=turn.revision,
                acknowledged_at=datetime.now(UTC),
            )
        turn = await self._repository.set_turn_cancelling(
            turn.channel_turn_id, updated_at=datetime.now(UTC)
        )
        if turn.status not in {ChannelTurnStatus.CANCELLING, ChannelTurnStatus.CANCELLED}:
            return ChannelTurnCancelReceipt(
                channel_turn_id=channel_turn_id,
                accepted=False,
                status=turn.status,
                revision=turn.revision,
                acknowledged_at=datetime.now(UTC),
            )
        await self._cancel_audio_task(turn.channel_turn_id)
        task = self._turn_tasks.pop(turn.channel_turn_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        cancelled = await self._conversation.cancel(
            turn.session_id, reason, expected_generation_id=turn.generation_id
        )
        if self._sticker_library is not None:
            await self._sticker_library.cancel_generation(turn.generation_id)
        if self._photo_observer is not None:
            await self._photo_observer.cancel_generation(turn.generation_id)
        turn = await self._sync_turn(await self._required_turn(connection_id, target_turn_id))
        return ChannelTurnCancelReceipt(
            channel_turn_id=channel_turn_id,
            accepted=cancelled or turn.status is ChannelTurnStatus.CANCELLED,
            status=turn.status,
            revision=turn.revision,
            acknowledged_at=datetime.now(UTC),
        )

    async def acknowledge_delivery(
        self,
        connection_id: UUID,
        delivery_id: UUID,
        acknowledgement: ChannelDeliveryAcknowledgement,
        *,
        access_token: str,
    ) -> ChannelDeliverySnapshot:
        await self._authenticate(connection_id, access_token)
        if acknowledgement.delivery_id != delivery_id:
            raise ChannelConflictError("delivery id path/body mismatch")
        turn = await self._required_turn(connection_id, acknowledgement.channel_turn_id)
        if turn.delivery_id != delivery_id:
            raise ChannelConflictError("delivery does not belong to the channel turn")
        plan = await self._repository.get_delivery_plan(delivery_id)
        if plan is None or plan.connection_id != connection_id:
            raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
        if plan.part_count > 1:
            raise ChannelDeliveryMultipartConflictError(
                "legacy whole-delivery operations are not supported for multipart delivery plans"
            )
        if not plan.parts:
            raise ChannelConflictError("delivery plan has no parts")

        if acknowledgement.status is ChannelDeliveryStatus.CANCELLED:
            cancel_req = ChannelDeliveryPartsCancelRequest(
                reason=(
                    acknowledgement.error.message
                    if acknowledgement.error
                    else "legacy_delivery_cancelled"
                ),
                requested_at=acknowledgement.acknowledged_at,
            )
            await self.cancel_remaining_delivery_parts(
                connection_id,
                delivery_id,
                cancel_req,
                access_token=access_token,
                cancel_sending_lease_id=acknowledgement.lease_id,
            )
            updated_plan = await self._repository.get_delivery_plan(delivery_id)
            if updated_plan is None:
                raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
            return _delivery_snapshot(updated_plan.delivery)

        part = plan.parts[0]
        part_status = (
            ChannelDeliveryPartStatus.DELIVERED
            if acknowledgement.status is ChannelDeliveryStatus.DELIVERED
            else ChannelDeliveryPartStatus.FAILED
        )
        part_ack = ChannelDeliveryPartAcknowledgement(
            delivery_id=acknowledgement.delivery_id,
            part_id=part.part_id,
            lease_id=acknowledgement.lease_id,
            status=part_status,
            provider_message_id=acknowledgement.provider_message_id,
            error=acknowledgement.error,
            acknowledged_at=acknowledgement.acknowledged_at,
        )
        await self.acknowledge_delivery_part(
            connection_id,
            delivery_id,
            part_ack,
            access_token=access_token,
        )
        updated_plan = await self._repository.get_delivery_plan(delivery_id)
        if updated_plan is None:
            raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
        return _delivery_snapshot(updated_plan.delivery)

    async def claim_delivery(
        self,
        connection_id: UUID,
        delivery_id: UUID,
        claim: ChannelDeliveryClaimRequest,
        *,
        access_token: str,
    ) -> ChannelDeliverySnapshot:
        await self._authenticate(connection_id, access_token)
        if claim.delivery_id != delivery_id:
            raise ChannelConflictError("delivery id path/body mismatch")
        turn = await self._required_turn(connection_id, claim.channel_turn_id)
        if turn.delivery_id != delivery_id:
            raise ChannelConflictError("delivery does not belong to the channel turn")
        plan = await self._repository.get_delivery_plan(delivery_id)
        if plan is None or plan.connection_id != connection_id:
            raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
        if plan.part_count > 1:
            raise ChannelDeliveryMultipartConflictError(
                "legacy whole-delivery operations are not supported for multipart delivery plans"
            )
        part_claim = ChannelDeliveryPartClaimRequest(
            delivery_id=claim.delivery_id,
            part_id=None,
            lease_id=claim.lease_id,
            lease_seconds=claim.lease_seconds,
        )
        part_snapshot = await self.claim_next_delivery_part(
            connection_id,
            delivery_id,
            part_claim,
            access_token=access_token,
        )
        if part_snapshot is None:
            raise ChannelDeliveryBusyError(
                "another adapter invocation currently owns this delivery lease"
            )
        updated_plan = await self._repository.get_delivery_plan(delivery_id)
        if updated_plan is None:
            raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
        return _delivery_snapshot(updated_plan.delivery)

    async def get_delivery_plan(
        self,
        connection_id: UUID,
        delivery_id: UUID,
        *,
        access_token: str,
    ) -> ChannelDeliveryPlanSnapshot:
        await self._authenticate(connection_id, access_token)
        plan = await self._repository.get_delivery_plan(delivery_id)
        if plan is None or plan.connection_id != connection_id:
            raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
        return _delivery_plan_snapshot(plan)

    async def list_delivery_parts(
        self,
        connection_id: UUID,
        delivery_id: UUID,
        *,
        access_token: str,
    ) -> list[ChannelDeliveryPartSnapshot]:
        await self._authenticate(connection_id, access_token)
        plan = await self._repository.get_delivery_plan(delivery_id)
        if plan is None or plan.connection_id != connection_id:
            raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
        return [_delivery_part_snapshot(part) for part in plan.parts]

    async def claim_next_delivery_part(
        self,
        connection_id: UUID,
        delivery_id: UUID,
        claim: ChannelDeliveryPartClaimRequest,
        *,
        access_token: str,
    ) -> ChannelDeliveryPartSnapshot | None:
        await self._authenticate(connection_id, access_token)
        if claim.delivery_id != delivery_id:
            raise ChannelConflictError("delivery id path/body mismatch")
        plan = await self._repository.get_delivery_plan(delivery_id)
        if plan is None or plan.connection_id != connection_id:
            raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
        if plan.channel_turn_id is None:
            raise ChannelPolicyError("outbound deliveries require the proactive owner policy")
        turn = await self._required_turn(connection_id, plan.channel_turn_id)
        try:
            result = await self._repository.claim_next_delivery_part(
                claim,
                claimed_at=datetime.now(UTC),
            )
        except (KeyError, ValueError) as error:
            raise ChannelConflictError(str(error)) from error
        if result is None or result.part is None:
            return None
        if result.persisted_events:
            for ev in result.persisted_events:
                await self._publisher.publish_persisted(ev)
        else:
            await self._emit_delivery_part_claimed_event(turn, result.part)
        return _delivery_part_snapshot(result.part)

    async def acknowledge_delivery_part(
        self,
        connection_id: UUID,
        delivery_id: UUID,
        acknowledgement: ChannelDeliveryPartAcknowledgement,
        *,
        access_token: str,
    ) -> ChannelDeliveryPartSnapshot:
        await self._authenticate(connection_id, access_token)
        if acknowledgement.delivery_id != delivery_id:
            raise ChannelConflictError("delivery id path/body mismatch")
        plan = await self._repository.get_delivery_plan(delivery_id)
        if plan is None or plan.connection_id != connection_id:
            raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
        if plan.channel_turn_id is None:
            raise ChannelPolicyError("outbound deliveries require the proactive owner policy")
        turn = await self._required_turn(connection_id, plan.channel_turn_id)
        try:
            result = await self._repository.acknowledge_delivery_part(
                acknowledgement,
                updated_at=datetime.now(UTC),
            )
        except (KeyError, ValueError) as error:
            raise ChannelConflictError(str(error)) from error

        if result.persisted_events:
            for ev in result.persisted_events:
                await self._publisher.publish_persisted(ev)
        elif result.applied and result.part is not None:
            await self._emit_delivery_part_acknowledged_events(turn, result.plan, result.part)

        part_record = (
            result.part
            if result.part is not None
            else next(
                (p for p in result.plan.parts if p.part_id == acknowledgement.part_id),
                result.plan.parts[0],
            )
        )
        return _delivery_part_snapshot(part_record)

    async def cancel_remaining_delivery_parts(
        self,
        connection_id: UUID,
        delivery_id: UUID,
        cancel_request: ChannelDeliveryPartsCancelRequest,
        *,
        access_token: str,
        cancel_sending_lease_id: UUID | None = None,
    ) -> ChannelDeliveryPlanSnapshot:
        await self._authenticate(connection_id, access_token)
        plan = await self._repository.get_delivery_plan(delivery_id)
        if plan is None or plan.connection_id != connection_id:
            raise ChannelNotFoundError(f"unknown channel delivery plan {delivery_id}")
        if plan.channel_turn_id is None:
            raise ChannelPolicyError("outbound deliveries require the proactive owner policy")
        turn = await self._required_turn(connection_id, plan.channel_turn_id)
        try:
            result = await self._repository.cancel_remaining_delivery_parts(
                delivery_id,
                cancel_request,
                cancel_sending_lease_id=cancel_sending_lease_id,
            )
        except (KeyError, ValueError) as error:
            raise ChannelConflictError(str(error)) from error
        if result.persisted_events:
            for ev in result.persisted_events:
                await self._publisher.publish_persisted(ev)
        else:
            now = datetime.now(UTC)
            await self._publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "channel.delivery_plan_cancel_requested",
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "generation_id": turn.generation_id,
                        "occurred_at": now,
                        "source": "runtime.external_channels",
                        "privacy": PrivacyLevel.PRIVATE,
                        "payload": {
                            "connection_id": str(turn.connection_id),
                            "channel_turn_id": str(turn.channel_turn_id),
                            "delivery_id": str(delivery_id),
                            "reason": cancel_request.reason,
                        },
                    }
                )
            )
            if result.plan.status is ChannelDeliveryStatus.CANCELLED:
                await self._publisher.emit(
                    GenericCoreEvent.model_validate(
                        {
                            "event_id": uuid4(),
                            "event_type": "channel.delivery_plan_cancelled",
                            "session_id": turn.session_id,
                            "turn_id": turn.turn_id,
                            "generation_id": turn.generation_id,
                            "occurred_at": now,
                            "source": "runtime.external_channels",
                            "privacy": PrivacyLevel.PRIVATE,
                            "payload": {
                                "connection_id": str(turn.connection_id),
                                "channel_turn_id": str(turn.channel_turn_id),
                                "delivery_id": str(delivery_id),
                            },
                        }
                    )
                )
        return _delivery_plan_snapshot(result.plan)

    async def _emit_delivery_plan_created_event(
        self,
        turn: ChannelTurnRecord,
        delivery_id: UUID,
        parts: Sequence[ChannelDeliveryPartDraft],
    ) -> None:
        await self._publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "channel.delivery_plan_created",
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "generation_id": turn.generation_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.external_channels",
                    "privacy": PrivacyLevel.PRIVATE,
                    "payload": {
                        "connection_id": str(turn.connection_id),
                        "channel_turn_id": str(turn.channel_turn_id),
                        "delivery_id": str(delivery_id),
                        "part_count": len(parts),
                        "chat_type": turn.chat_type.value,
                        "conversation_key": turn.conversation_key,
                        "sender_key": turn.sender_key,
                    },
                }
            )
        )

    async def _emit_delivery_part_claimed_event(
        self, turn: ChannelTurnRecord, part: ChannelDeliveryPartRecord
    ) -> None:
        await self._publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "channel.delivery_part_claimed",
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "generation_id": turn.generation_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.external_channels",
                    "privacy": PrivacyLevel.PRIVATE,
                    "payload": {
                        "connection_id": str(turn.connection_id),
                        "channel_turn_id": str(turn.channel_turn_id),
                        "delivery_id": str(part.delivery_id),
                        "part_id": str(part.part_id),
                        "ordinal": part.ordinal,
                        "attempt": part.attempt,
                        "lease_id": str(part.lease_id) if part.lease_id else None,
                        "provider_client_id": part.provider_client_id,
                    },
                }
            )
        )

    async def _emit_delivery_part_acknowledged_events(
        self,
        turn: ChannelTurnRecord,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
    ) -> None:
        now = datetime.now(UTC)
        await self._publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "channel.delivery_part_acknowledged",
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "generation_id": turn.generation_id,
                    "occurred_at": now,
                    "source": "runtime.external_channels",
                    "privacy": PrivacyLevel.PRIVATE,
                    "payload": {
                        "connection_id": str(turn.connection_id),
                        "channel_turn_id": str(turn.channel_turn_id),
                        "delivery_id": str(part.delivery_id),
                        "part_id": str(part.part_id),
                        "ordinal": part.ordinal,
                        "status": part.status.value,
                        "provider_message_id": part.provider_message_id,
                    },
                }
            )
        )
        if part.status is ChannelDeliveryPartStatus.DELIVERED:
            await self._publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "channel.delivery_part_delivered",
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "generation_id": turn.generation_id,
                        "occurred_at": now,
                        "source": "runtime.external_channels",
                        "privacy": PrivacyLevel.PRIVATE,
                        "payload": {
                            "connection_id": str(turn.connection_id),
                            "channel_turn_id": str(turn.channel_turn_id),
                            "delivery_id": str(part.delivery_id),
                            "part_id": str(part.part_id),
                            "ordinal": part.ordinal,
                            "provider_message_id": part.provider_message_id,
                        },
                    }
                )
            )
        elif part.status is ChannelDeliveryPartStatus.FAILED:
            await self._publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "channel.delivery_part_failed",
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "generation_id": turn.generation_id,
                        "occurred_at": now,
                        "source": "runtime.external_channels",
                        "privacy": PrivacyLevel.PRIVATE,
                        "payload": {
                            "connection_id": str(turn.connection_id),
                            "channel_turn_id": str(turn.channel_turn_id),
                            "delivery_id": str(part.delivery_id),
                            "part_id": str(part.part_id),
                            "ordinal": part.ordinal,
                            "error": (
                                part.last_error.model_dump(mode="json") if part.last_error else None
                            ),
                        },
                    }
                )
            )
        if plan.status is ChannelDeliveryStatus.DELIVERED:
            await self._publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "channel.delivery_plan_completed",
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "generation_id": turn.generation_id,
                        "occurred_at": now,
                        "source": "runtime.external_channels",
                        "privacy": PrivacyLevel.PRIVATE,
                        "payload": {
                            "connection_id": str(turn.connection_id),
                            "channel_turn_id": str(turn.channel_turn_id),
                            "delivery_id": str(plan.delivery_id),
                            "part_count": plan.part_count,
                        },
                    }
                )
            )
            # Legacy event for backwards compatibility
            await self._emit_delivery_event(turn, plan.delivery)
        elif plan.status is ChannelDeliveryStatus.CANCELLED:
            await self._publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "channel.delivery_plan_cancelled",
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "generation_id": turn.generation_id,
                        "occurred_at": now,
                        "source": "runtime.external_channels",
                        "privacy": PrivacyLevel.PRIVATE,
                        "payload": {
                            "connection_id": str(turn.connection_id),
                            "channel_turn_id": str(turn.channel_turn_id),
                            "delivery_id": str(plan.delivery_id),
                        },
                    }
                )
            )
        elif plan.status is ChannelDeliveryStatus.FAILED:
            await self._publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "channel.delivery_plan_failed",
                        "session_id": turn.session_id,
                        "turn_id": turn.turn_id,
                        "generation_id": turn.generation_id,
                        "occurred_at": now,
                        "source": "runtime.external_channels",
                        "privacy": PrivacyLevel.PRIVATE,
                        "payload": {
                            "connection_id": str(turn.connection_id),
                            "channel_turn_id": str(turn.channel_turn_id),
                            "delivery_id": str(plan.delivery_id),
                        },
                    }
                )
            )

    async def _sync_turn(self, turn: ChannelTurnRecord) -> ChannelTurnRecord:
        if turn.chat_type is ChannelChatType.GROUP:
            if turn.status not in {
                ChannelTurnStatus.ACCEPTED,
                ChannelTurnStatus.PROCESSING,
                ChannelTurnStatus.CANCELLING,
            }:
                return turn
            if self._groups is None or turn.group_lineage_version != 1:
                return await self._set_turn_terminal(
                    turn.channel_turn_id,
                    status=ChannelTurnStatus.FAILED,
                    error=_error("group_runtime_unavailable", "The group runtime is unavailable."),
                    completed_at=datetime.now(UTC),
                )
            return await self._groups.sync_turn(turn)
        lock = self._turn_sync_locks.setdefault(turn.channel_turn_id, asyncio.Lock())
        async with lock:
            fresh_turn = await self._repository.get_turn(turn.channel_turn_id)
            if fresh_turn is not None:
                turn = fresh_turn
            if turn.status not in {
                ChannelTurnStatus.ACCEPTED,
                ChannelTurnStatus.PROCESSING,
                ChannelTurnStatus.CANCELLING,
            }:
                return turn
            if turn.status is ChannelTurnStatus.CANCELLING:
                if self._conversation.active_generation_id(turn.session_id) == turn.generation_id:
                    # Do not release queued work while the old generation is
                    # still tearing down. interrupt() joins it before syncing.
                    return turn
                return await self._set_turn_terminal(
                    turn.channel_turn_id,
                    status=ChannelTurnStatus.CANCELLED,
                    error=_error("generation_cancelled", "The channel turn was cancelled."),
                    completed_at=datetime.now(UTC),
                )
            if (
                turn.status is ChannelTurnStatus.ACCEPTED
                and turn.input_kind is ChannelMessageKind.AUDIO
                and turn.channel_turn_id in self._audio_tasks
            ):
                return turn
            member_rec = await self._repository.find_burst_leader(turn.channel_turn_id)
            if member_rec is not None and member_rec.leader_channel_turn_id != turn.channel_turn_id:
                leader_turn = await self._repository.get_turn(member_rec.leader_channel_turn_id)
                if leader_turn is not None:
                    await self._sync_turn(leader_turn)
                refreshed = await self._repository.get_turn(turn.channel_turn_id)
                return refreshed if refreshed is not None else turn
            generation = await self._conversation_repository.generation_result(turn.generation_id)
            now = datetime.now(UTC)
            if generation is None:
                if turn.input_kind is ChannelMessageKind.AUDIO:
                    if turn.channel_turn_id in self._audio_tasks:
                        return turn
                    return await self._fail_audio_turn(turn)
                if self._burst_coordinator.is_live_burst_turn(turn.channel_turn_id):
                    return turn
                members = await self._repository.list_burst_members(turn.channel_turn_id)
                if members:
                    result = await self._repository.fail_turn_with_notice(
                        turn.channel_turn_id,
                        error=_error(
                            "burst_interrupted",
                            "The image burst collection or dispatch was interrupted "
                            "before generation.",
                        ),
                        notice_text=_IMAGE_FAILURE_RECOVERY_TEXT,
                        delivery_id=uuid4(),
                        completed_at=now,
                    )
                    for event in result.persisted_events:
                        await self._publisher.publish_persisted(event)
                    if self._on_wake_scheduler:
                        self._on_wake_scheduler(turn.connection_id)
                    await self._notify_turn_terminal(result.turn)
                    for member in members:
                        if member.member_channel_turn_id != turn.channel_turn_id:
                            mem_turn = await self._repository.get_turn(
                                member.member_channel_turn_id
                            )
                            if mem_turn is not None:
                                await self._notify_turn_terminal(mem_turn)
                    return result.turn
                return await self._set_turn_terminal(
                    turn.channel_turn_id,
                    status=ChannelTurnStatus.FAILED,
                    error=_error(
                        "generation_missing",
                        "The Runtime generation record is missing.",
                    ),
                    completed_at=now,
                )
            if generation.state is GenerationState.COMPLETED:
                if not generation.output_text:
                    return await self._set_turn_terminal(
                        turn.channel_turn_id,
                        status=ChannelTurnStatus.FAILED,
                        error=_error(
                            "empty_generation",
                            "The model completed without a deliverable text reply.",
                        ),
                        completed_at=now,
                    )
                delivery_id = turn.delivery_id or uuid4()
                connection = await self._repository.get_connection(turn.connection_id)
                policy = (
                    connection.configuration.presentation_policy
                    if connection and connection.configuration
                    else None
                )
                provider = (
                    self._providers.get(connection.configuration.provider_id)
                    if connection and connection.configuration
                    else None
                )
                provider_supports_image = (
                    provider is not None
                    and ChannelMessageKind.IMAGE in provider.capabilities.outbound_message_kinds
                )
                is_default_character = (
                    connection is not None and connection.configuration.character_id == "default"
                )
                is_instant_message = (
                    policy is not None
                    and policy.profile is ChannelPresentationProfile.INSTANT_MESSAGE
                )
                stickers_enabled = policy is not None and policy.stickers_enabled

                can_send_sticker = (
                    stickers_enabled
                    and is_instant_message
                    and provider_supports_image
                    and is_default_character
                )

                response_plan = None
                sticker_hints = StickerSelectionHints(blocked=True)
                if can_send_sticker:
                    try:
                        response_plan = (
                            await self._conversation_repository.generation_response_plan(
                                turn.generation_id
                            )
                        )
                        sticker_hints = selection_hints(
                            await self._conversation_repository.generation_user_input_context(
                                turn.generation_id
                            )
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning(
                            "sticker input context unavailable generation_id=%s", turn.generation_id
                        )
                    can_send_sticker = not sticker_hints.blocked

                learned_sticker: ChannelImageDeliveryPartPayload | None = None
                if (
                    can_send_sticker
                    and connection is not None
                    and self._sticker_library is not None
                ):
                    try:
                        learned_sticker = await self._sticker_library.match(
                            connection.configuration.principal_scope,
                            connection.configuration.character_id,
                            response_plan,
                            hints=sticker_hints,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning(
                            "learned sticker selection unavailable generation_id=%s",
                            turn.generation_id,
                        )
                profile_name: str = (
                    policy.profile.value
                    if policy is not None
                    else ChannelPresentationProfile.SINGLE_TEXT.value
                )
                fallback_reason: str | None = None
                factory = self._delivery_plan_factory
                if isinstance(factory, InstantMessageDeliveryPlanFactory):
                    plan_result = factory.create_plan(
                        generation.output_text,
                        policy=policy,
                        response_plan=response_plan,
                        can_send_sticker=can_send_sticker,
                        learned_sticker=learned_sticker,
                        allow_preset_sticker=sticker_hints.interaction is None,
                    )
                    parts = plan_result.parts
                    profile_name = plan_result.profile
                    fallback_reason = plan_result.fallback_reason
                elif isinstance(factory, SingleTextDeliveryPlanFactory):
                    plan_result = factory.create_plan(generation.output_text, policy=policy)
                    parts = plan_result.parts
                    profile_name = plan_result.profile
                    fallback_reason = plan_result.fallback_reason
                else:
                    parts = factory.create_parts(generation.output_text, policy=policy)

                delays = [p.delay_after_ms for p in parts]
                chars_per_part: list[int] = [
                    len(p.payload.text)
                    if isinstance(p.payload, ChannelTextDeliveryPartPayload)
                    else 0
                    for p in parts
                ]
                logger.info(
                    "channel delivery plan created: delivery_id=%s profile=%s part_count=%d "
                    "chars_per_part=%s delays=%s total_delay_ms=%d fallback_reason=%s",
                    delivery_id,
                    profile_name,
                    len(parts),
                    chars_per_part,
                    delays,
                    sum(delays),
                    fallback_reason,
                )
                turn_result = await self._repository.complete_turn(
                    turn.channel_turn_id,
                    reply_text=generation.output_text,
                    delivery_id=delivery_id,
                    completed_at=now,
                    parts=parts,
                )
                turn_record = (
                    turn_result.turn if isinstance(turn_result, CompleteTurnResult) else turn_result
                )
                if turn_record.status is not ChannelTurnStatus.COMPLETED:
                    return turn_record
                persisted_events = getattr(turn_result, "persisted_events", ())
                if persisted_events:
                    for event in persisted_events:
                        await self._publisher.publish_persisted(event)
                else:
                    await self._emit_delivery_plan_created_event(turn_record, delivery_id, parts)
                await self._notify_turn_terminal(turn_record)
                members = await self._repository.list_burst_members(turn.channel_turn_id)
                for member in members:
                    if member.member_channel_turn_id != turn.channel_turn_id:
                        mem_turn = await self._repository.get_turn(member.member_channel_turn_id)
                        if mem_turn is not None:
                            await self._notify_turn_terminal(mem_turn)
                return turn_record
            if generation.state is GenerationState.CANCELLED:
                return await self._set_turn_terminal(
                    turn.channel_turn_id,
                    status=ChannelTurnStatus.CANCELLED,
                    error=_error("generation_cancelled", "The channel turn was cancelled."),
                    completed_at=now,
                )
            if generation.state is GenerationState.FAILED:
                if generation.error_code in {"provider_error", "image_input_error"}:
                    result = await self._repository.fail_turn_with_notice(
                        turn.channel_turn_id,
                        error=_error(
                            generation.error_code, "The model generation failed before delivery."
                        ),
                        notice_text=(
                            _IMAGE_FAILURE_RECOVERY_TEXT
                            if generation.error_code == "image_input_error"
                            else _PROVIDER_FAILURE_RECOVERY_TEXT
                        ),
                        delivery_id=uuid4(),
                        completed_at=now,
                    )
                    if result.turn.status is ChannelTurnStatus.CANCELLING:
                        return await self._set_turn_terminal(
                            turn.channel_turn_id,
                            status=ChannelTurnStatus.CANCELLED,
                            error=_error("generation_cancelled", "The channel turn was cancelled."),
                            completed_at=now,
                        )
                    for event in result.persisted_events:
                        await self._publisher.publish_persisted(event)
                    await self._notify_turn_terminal(result.turn)
                    members = await self._repository.list_burst_members(turn.channel_turn_id)
                    for member in members:
                        if member.member_channel_turn_id != turn.channel_turn_id:
                            mem_turn = await self._repository.get_turn(
                                member.member_channel_turn_id
                            )
                            if mem_turn is not None:
                                await self._notify_turn_terminal(mem_turn)
                    return result.turn
                return await self._set_turn_terminal(
                    turn.channel_turn_id,
                    status=ChannelTurnStatus.FAILED,
                    error=_error(
                        generation.error_code or "generation_failed",
                        "The model generation failed before delivery.",
                    ),
                    completed_at=now,
                )
            if self._conversation.active_generation_id(turn.session_id) != turn.generation_id:
                return await self._set_turn_terminal(
                    turn.channel_turn_id,
                    status=ChannelTurnStatus.FAILED,
                    error=_error(
                        "generation_not_active",
                        "The generation is no longer active in this Runtime instance.",
                    ),
                    completed_at=now,
                )
            return turn

    async def _authenticate(
        self, connection_id: UUID, access_token: str
    ) -> ChannelConnectionRecord:
        connection = await self._required_connection(connection_id)
        if not access_token or not secrets.compare_digest(
            connection.access_token_hash, _token_hash(access_token)
        ):
            raise ChannelAuthenticationError("invalid channel connection access token")
        return connection

    async def authenticate_group_transport(
        self, connection_id: UUID, access_token: str
    ) -> ChannelConnectionRecord:
        """Authenticate the QQ transport; only the group domain grants speakers."""
        connection = await self._authenticate(connection_id, access_token)
        configuration = connection.configuration
        if (
            configuration.provider_id != "qq_napcat"
            or not configuration.enabled
            or connection.deleted_at is not None
            or connection.status is not ChannelConnectionStatus.READY
            or configuration.account_key is None
        ):
            raise ChannelPolicyError("group transport must be enabled and ready")
        try:
            qq_id(configuration.account_key)
        except ValueError as error:
            raise ChannelPolicyError("group transport requires a canonical QQ account") from error
        return connection

    async def _required_connection(self, connection_id: UUID) -> ChannelConnectionRecord:
        connection = await self._repository.get_connection(connection_id)
        if connection is None:
            raise ChannelNotFoundError(f"unknown channel connection {connection_id}")
        return connection

    async def _required_turn(self, connection_id: UUID, channel_turn_id: UUID) -> ChannelTurnRecord:
        turn = await self._repository.get_turn(channel_turn_id)
        if turn is None or turn.connection_id != connection_id:
            raise ChannelNotFoundError(f"unknown channel turn {channel_turn_id}")
        return turn

    def _validate_configuration(self, configuration: ChannelConnectionConfiguration) -> None:
        if configuration.provider_id not in self._providers:
            raise ChannelPolicyError(f"unknown channel provider {configuration.provider_id}")
        if self._characters.get(configuration.character_id) is None:
            raise ChannelPolicyError(f"unknown character {configuration.character_id}")
        if configuration.principal_scope != USER_SCOPE:
            raise ChannelPolicyError(f"v1 supports only the owner principal_scope {USER_SCOPE!r}")
        if configuration.account_key is None:
            raise ChannelPolicyError("owner-only v1 requires a stable account_key")
        if len(configuration.allowed_sender_keys) != 1:
            raise ChannelPolicyError("owner-only v1 requires exactly one allowed sender key")
        if any(item != item.strip() for item in configuration.allowed_sender_keys):
            raise ChannelPolicyError("allowed sender keys cannot have surrounding whitespace")
        if len(set(configuration.allowed_sender_keys)) != len(configuration.allowed_sender_keys):
            raise ChannelPolicyError("allowed sender keys must be unique")
        if configuration.account_key != configuration.account_key.strip():
            raise ChannelPolicyError("account_key cannot have surrounding whitespace")

    def _validate_ingress(
        self,
        connection: ChannelConnectionRecord,
        message: ChannelInboundTextMessage,
        *,
        has_image: bool = False,
        has_audio: bool = False,
    ) -> None:
        configuration = connection.configuration
        provider = self._providers[configuration.provider_id]
        if not configuration.enabled:
            raise ChannelPolicyError("channel connection is disabled")
        if message.chat_type is not ChannelChatType.DIRECT:
            raise ChannelPolicyError("group messages require a registered group route")
        if message.chat_type not in provider.capabilities.chat_types:
            raise ChannelPolicyError(
                f"provider does not allow {message.chat_type.value} conversations"
            )
        if message.kind not in provider.capabilities.inbound_message_kinds:
            raise ChannelPolicyError(f"provider does not allow {message.kind.value} messages")
        if (
            has_image
            and ChannelMessageKind.IMAGE not in provider.capabilities.inbound_message_kinds
        ):
            raise ChannelPolicyError("provider does not allow image messages")
        if (
            has_audio
            and ChannelMessageKind.AUDIO not in provider.capabilities.inbound_message_kinds
        ):
            raise ChannelPolicyError("provider does not allow audio messages")
        if message.principal_scope != configuration.principal_scope:
            raise ChannelPolicyError("message principal_scope does not match connection policy")
        if (
            configuration.account_key is not None
            and message.account_key != configuration.account_key
        ):
            raise ChannelPolicyError("message account_key does not match connection policy")
        if message.sender_key not in configuration.allowed_sender_keys:
            raise ChannelPolicyError("message sender is not in the owner allowlist")
        if message.received_at > datetime.now(UTC) + timedelta(minutes=5):
            raise ChannelPolicyError("message received_at is unexpectedly far in the future")

    def _connection_snapshot(self, record: ChannelConnectionRecord) -> ChannelConnectionSnapshot:
        provider = self._providers.get(record.configuration.provider_id)
        capabilities = (
            provider.capabilities if provider is not None else ChannelProviderCapabilities()
        )
        return ChannelConnectionSnapshot(
            configuration=record.configuration,
            revision=record.revision,
            status=record.status,
            capabilities=capabilities,
            last_error=record.last_error,
            last_seen_at=record.last_seen_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _turn_receipt(turn: ChannelTurnRecord, *, duplicate: bool) -> ChannelTurnReceipt:
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
            poll_after_ms=(250 if turn.status is ChannelTurnStatus.PROCESSING else None),
        )

    @staticmethod
    def _turn_snapshot(turn: ChannelTurnRecord) -> ChannelTurnSnapshot:
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

    async def _emit_delivery_event(
        self, turn: ChannelTurnRecord, delivery: ChannelDeliveryRecord
    ) -> None:
        await self._publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "channel.delivery_acknowledged",
                    "session_id": turn.session_id,
                    "turn_id": turn.turn_id,
                    "generation_id": turn.generation_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.external_channels",
                    "privacy": PrivacyLevel.PRIVATE,
                    "payload": {
                        "connection_id": str(turn.connection_id),
                        "channel_turn_id": str(turn.channel_turn_id),
                        "delivery_id": str(delivery.delivery_id),
                        "delivery_status": delivery.status.value,
                        "chat_type": turn.chat_type.value,
                        "conversation_key": turn.conversation_key,
                        "sender_key": turn.sender_key,
                    },
                }
            )
        )


def _delivery_part_snapshot(record: ChannelDeliveryPartRecord) -> ChannelDeliveryPartSnapshot:
    return ChannelDeliveryPartSnapshot(
        part_id=record.part_id,
        delivery_id=record.delivery_id,
        ordinal=record.ordinal,
        kind=record.kind,
        payload=record.payload,
        required=record.required,
        status=record.status,
        delay_after_ms=record.delay_after_ms,
        not_before_at=record.not_before_at,
        attempt=record.attempt,
        lease_id=record.lease_id,
        lease_expires_at=record.lease_expires_at,
        provider_client_id=record.provider_client_id,
        provider_message_id=record.provider_message_id,
        last_error=record.last_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        delivered_at=record.delivered_at,
    )


def _delivery_plan_snapshot(record: ChannelDeliveryPlanRecord) -> ChannelDeliveryPlanSnapshot:
    return ChannelDeliveryPlanSnapshot(
        schema_version="1.1" if record.outbound_intent_id is not None else "1.0",
        outbound_intent_id=record.outbound_intent_id,
        delivery_id=record.delivery_id,
        channel_turn_id=record.channel_turn_id,
        connection_id=record.connection_id,
        group_target=record.group_target,
        status=record.status,
        plan_version=record.plan_version,
        part_count=record.part_count,
        delivered_part_count=record.delivered_part_count,
        next_pending_ordinal=record.next_pending_ordinal,
        cancel_requested_at=record.cancel_requested_at,
        parts=[_delivery_part_snapshot(part) for part in record.parts],
        created_at=record.created_at,
        updated_at=record.updated_at,
        delivered_at=record.delivered_at,
    )


def _delivery_snapshot(record: ChannelDeliveryRecord) -> ChannelDeliverySnapshot:
    return ChannelDeliverySnapshot(
        schema_version="1.1" if record.outbound_intent_id is not None else "1.0",
        outbound_intent_id=record.outbound_intent_id,
        delivery_id=record.delivery_id,
        channel_turn_id=record.channel_turn_id,
        connection_id=record.connection_id,
        status=record.status,
        attempt=record.attempt,
        lease_id=record.lease_id,
        lease_expires_at=record.lease_expires_at,
        provider_message_id=record.provider_message_id,
        last_error=record.last_error,
        plan_version=record.plan_version,
        part_count=record.part_count,
        delivered_part_count=record.delivered_part_count,
        cancel_requested_at=record.cancel_requested_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        delivered_at=record.delivered_at,
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _message_digest(
    message: ChannelInboundTextMessage,
    image_fingerprint: str | None = None,
    audio_fingerprint: str | None = None,
) -> str:
    parts = [
        message.account_key or "",
        message.external_message_id,
        message.conversation_key,
        message.sender_key,
        message.principal_scope,
        message.chat_type.value,
        message.kind.value,
        message.text,
        message.reply_to_external_message_id or "",
    ]
    if image_fingerprint is not None:
        parts.append(f"image:{image_fingerprint}")
    if audio_fingerprint is not None:
        parts.append(f"audio:{audio_fingerprint}")
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _error(code: str, message: str, *, retryable: bool = False) -> StructuredError:
    return StructuredError(
        code=code,
        message=message,
        retryable=retryable,
        component="external_channels",
    )
