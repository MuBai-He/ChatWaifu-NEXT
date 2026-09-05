"""Provider-neutral External Channel Gateway application service."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
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
    ChannelInboundTextMessage,
    ChannelMessageKind,
    ChannelPresentationProfile,
    ChannelProviderCapabilities,
    ChannelProviderRegistration,
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
    ConversationSourceContext,
)
from chatwaifu_runtime.conversation.repository import ConversationRepository
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.models import (
    ChannelBindingRecord,
    ChannelConnectionRecord,
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelDeliveryRecord,
    ChannelTurnRecord,
    CompleteTurnResult,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.presentation import (
    DeliveryPlanFactory,
    InstantMessageDeliveryPlanFactory,
    SingleTextDeliveryPlanFactory,
)
from chatwaifu_runtime.sessions.service import SessionService

logger = logging.getLogger(__name__)

WEIXIN_ILINK_PROVIDER = ChannelProviderRegistration(
    provider_id="weixin_ilink",
    version="1.0.0",
    name="微信",
    description="通过腾讯 iLink 协议在本机扫码连接微信。",
    capabilities=ChannelProviderCapabilities(
        chat_types=[ChannelChatType.DIRECT],
        inbound_message_kinds=[ChannelMessageKind.TEXT],
        outbound_message_kinds=[ChannelMessageKind.TEXT],
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


__all__ = [
    "WEIXIN_ILINK_PROVIDER",
    "CreatedChannelConnection",
    "DeliveryPlanFactory",
    "ExternalChannelError",
    "ExternalChannelService",
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
    ) -> None:
        self._repository = repository
        self._conversation_repository = conversation_repository
        self._sessions = sessions
        self._conversation = conversation
        self._characters = characters
        self._event_hub = event_hub
        self._publisher = publisher
        self._providers = {item.provider_id: item for item in providers}
        self._delivery_plan_factory = delivery_plan_factory or InstantMessageDeliveryPlanFactory()
        self._ingress_lock = asyncio.Lock()
        self._turn_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._turn_sync_locks: dict[UUID, asyncio.Lock] = {}
        self._turn_terminal_listeners: list[Callable[[ChannelTurnRecord], Awaitable[None]]] = []
        self._stopping = False

    @property
    def repository(self) -> ExternalChannelRepository:
        return self._repository

    @property
    def publisher(self) -> EventPublisher:
        return self._publisher

    @property
    def event_hub(self) -> EventHub:
        return self._publisher.event_hub

    async def start(self) -> None:
        self._stopping = False
        for turn in await self._repository.list_inflight_turns():
            turn = await self._sync_turn(turn)
            if turn.status in {
                ChannelTurnStatus.ACCEPTED,
                ChannelTurnStatus.PROCESSING,
                ChannelTurnStatus.CANCELLING,
            }:
                self._ensure_turn_task(turn)

    async def stop(self) -> None:
        self._stopping = True
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
        try:
            updated = await self._repository.update_connection(
                configuration,
                expected_revision=expected_revision,
                access_token_hash=_token_hash(token) if token is not None else None,
                updated_at=datetime.now(UTC),
            )
        except KeyError as error:
            raise ChannelNotFoundError(str(error)) from error
        except ValueError as error:
            raise ChannelConflictError(str(error)) from error
        snapshot = self._connection_snapshot(updated)
        return CreatedChannelConnection(snapshot, token) if token is not None else snapshot

    async def delete_connection(self, connection_id: UUID) -> None:
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
    ) -> ChannelTurnReceipt:
        connection, binding, turn, duplicate = await self._admit_ingress(
            message, access_token=access_token, supersede_inflight=supersede_inflight
        )
        if duplicate:
            return self._turn_receipt(turn, duplicate=True)

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
        )
        policy = connection.configuration.presentation_policy
        profile = (
            policy.profile.value
            if policy is not None and hasattr(policy.profile, "value")
            else (str(policy.profile) if policy is not None else None)
        )
        options = replace(
            EXTERNAL_TEXT_TURN_OPTIONS,
            source_context=source_context,
            presentation_profile=profile,
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
            turn = await self._repository.set_turn_processing(
                turn.channel_turn_id, updated_at=datetime.now(UTC)
            )
            self._ensure_turn_task(turn, access_token=access_token)
        except asyncio.CancelledError:
            if generation_admitted:
                await self._conversation.cancel(binding.session_id, "channel_ingress_cancelled")
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
                await self._conversation.cancel(binding.session_id, "channel_admission_failed")
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
        await self._notify_turn_terminal(record)
        return record

    async def _admit_ingress(
        self,
        message: ChannelInboundTextMessage,
        *,
        access_token: str,
        supersede_inflight: bool = False,
    ) -> tuple[ChannelConnectionRecord, ChannelBindingRecord, ChannelTurnRecord, bool]:
        """Persist a unique channel turn without serializing model preparation.

        The process-local lock protects the compound binding/idempotency checks.
        Once the durable accepted turn exists, later messages observe it as
        inflight while Memory, Character Kernel, and LLM preparation continue
        outside this global critical section.
        """

        async with self._ingress_lock:
            connection = await self._authenticate(message.connection_id, access_token)
            self._validate_ingress(connection, message)
            digest = _message_digest(message)
            duplicate = await self._repository.find_turn_by_external_message(
                message.connection_id, message.external_message_id
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

            if supersede_inflight:
                for previous in await self._repository.list_inflight_turns(message.connection_id):
                    if (
                        previous.binding_id == binding.binding_id
                        and previous.status is ChannelTurnStatus.PROCESSING
                    ):
                        await self.interrupt(
                            message.connection_id,
                            previous.channel_turn_id,
                            access_token=access_token,
                            reason="superseded_by_new_inbound_message",
                        )

            if await self._repository.has_inflight_turn(binding.binding_id):
                raise ChannelBusyError(
                    "this external conversation already has an active generation"
                )
            if self._conversation.active_generation_id(binding.session_id) is not None:
                raise ChannelBusyError("the bound Runtime session is already generating")

            now = datetime.now(UTC)
            active_plans = await self._repository.list_active_delivery_plans_for_binding(
                binding.binding_id
            )
            for active_plan in active_plans:
                if active_plan.status in (
                    ChannelDeliveryStatus.PENDING,
                    ChannelDeliveryStatus.SENDING,
                ):
                    logger.info(
                        "cancelling unsent tail of active delivery plan: delivery_id=%s reason=%s",
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

        def matches(event: dict[str, object]) -> bool:
            return str(event.get("generation_id")) == str(channel_turn_id_generation)

        turn = await self._required_turn(connection_id, channel_turn_id)
        channel_turn_id_generation = turn.generation_id
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
        turn = await self._sync_turn(await self._required_turn(connection_id, channel_turn_id))
        if turn.status not in {ChannelTurnStatus.ACCEPTED, ChannelTurnStatus.PROCESSING}:
            return ChannelTurnCancelReceipt(
                channel_turn_id=turn.channel_turn_id,
                accepted=False,
                status=turn.status,
                revision=turn.revision,
                acknowledged_at=datetime.now(UTC),
            )
        turn = await self._repository.set_turn_cancelling(
            turn.channel_turn_id, updated_at=datetime.now(UTC)
        )
        cancelled = await self._conversation.cancel(turn.session_id, reason)
        turn = await self._sync_turn(await self._required_turn(connection_id, channel_turn_id))
        task = self._turn_tasks.pop(turn.channel_turn_id, None)
        if task is not None:
            task.cancel()
        return ChannelTurnCancelReceipt(
            channel_turn_id=turn.channel_turn_id,
            accepted=cancelled,
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
            generation = await self._conversation_repository.generation_result(turn.generation_id)
            now = datetime.now(UTC)
            if generation is None:
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
                profile_name: str = (
                    policy.profile.value
                    if policy is not None
                    else ChannelPresentationProfile.SINGLE_TEXT.value
                )
                fallback_reason: str | None = None
                factory = self._delivery_plan_factory
                if isinstance(
                    factory, (SingleTextDeliveryPlanFactory, InstantMessageDeliveryPlanFactory)
                ):
                    plan_result = factory.create_plan(generation.output_text, policy=policy)
                    parts = plan_result.parts
                    profile_name = plan_result.profile
                    fallback_reason = plan_result.fallback_reason
                else:
                    parts = factory.create_parts(generation.output_text, policy=policy)

                delays = [p.delay_after_ms for p in parts]
                chars_per_part: list[int] = [len(p.payload.text) for p in parts]
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
                persisted_events = getattr(turn_result, "persisted_events", ())
                if persisted_events:
                    for event in persisted_events:
                        await self._publisher.publish_persisted(event)
                else:
                    await self._emit_delivery_plan_created_event(turn_record, delivery_id, parts)
                return turn_record
            if generation.state is GenerationState.CANCELLED:
                return await self._set_turn_terminal(
                    turn.channel_turn_id,
                    status=ChannelTurnStatus.CANCELLED,
                    error=_error("generation_cancelled", "The channel turn was cancelled."),
                    completed_at=now,
                )
            if generation.state is GenerationState.FAILED:
                if (
                    generation.error_code == "provider_error"
                    and turn.status is not ChannelTurnStatus.CANCELLING
                ):
                    result = await self._repository.fail_turn_with_notice(
                        turn.channel_turn_id,
                        error=_error(
                            "provider_error", "The model generation failed before delivery."
                        ),
                        notice_text="系统通知：这条消息暂时没能回复，稍后再试试吧。",  # noqa: RUF001
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
        self, connection: ChannelConnectionRecord, message: ChannelInboundTextMessage
    ) -> None:
        configuration = connection.configuration
        provider = self._providers[configuration.provider_id]
        if not configuration.enabled:
            raise ChannelPolicyError("channel connection is disabled")
        if message.chat_type not in provider.capabilities.chat_types:
            raise ChannelPolicyError(
                f"provider does not allow {message.chat_type.value} conversations"
            )
        if message.kind not in provider.capabilities.inbound_message_kinds:
            raise ChannelPolicyError(f"provider does not allow {message.kind.value} messages")
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
        delivery_id=record.delivery_id,
        channel_turn_id=record.channel_turn_id,
        connection_id=record.connection_id,
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


def _message_digest(message: ChannelInboundTextMessage) -> str:
    parts = (
        message.account_key or "",
        message.external_message_id,
        message.conversation_key,
        message.sender_key,
        message.principal_scope,
        message.chat_type.value,
        message.kind.value,
        message.text,
        message.reply_to_external_message_id or "",
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _error(code: str, message: str, *, retryable: bool = False) -> StructuredError:
    return StructuredError(
        code=code,
        message=message,
        retryable=retryable,
        component="external_channels",
    )
