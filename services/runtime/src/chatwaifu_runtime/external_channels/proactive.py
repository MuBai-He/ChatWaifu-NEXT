"""Bounded owner-opt-in proactive text coordination, without provider I/O."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from chatwaifu_protocol.channel_proactive import (
    ChannelOutboundIntentCancelRequest,
    ChannelOutboundIntentPage,
    ChannelOutboundIntentSnapshot,
    ChannelOutboundIntentStatus,
    ChannelProactivePolicySnapshot,
    ChannelProactivePolicyUpdate,
    ChannelProactivePreview,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.session import GenerationState

from chatwaifu_runtime.conversation.models import ConversationSourceContext, ConversationTurnOptions
from chatwaifu_runtime.conversation.repository import ConversationRepository
from chatwaifu_runtime.conversation.service import ConversationService
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.models import ChannelDeliveryPlanRecord
from chatwaifu_runtime.external_channels.proactive_models import (
    ChannelOutboundIntentRecord,
    ChannelProactivePolicyRecord,
    evaluate_proactive_context,
)
from chatwaifu_runtime.external_channels.proactive_ports import ChannelProactiveRepository
from chatwaifu_runtime.external_channels.service import (
    ChannelConflictError,
    ChannelNotFoundError,
    ExternalChannelService,
)

logger = logging.getLogger(__name__)
_TERMINAL_EVENTS = frozenset(
    {
        "assistant.generation_completed",
        "assistant.generation_cancelled",
        "system.error_raised",
    }
)


class ChannelProactiveService:
    def __init__(
        self,
        repository: ChannelProactiveRepository,
        conversation: ConversationService,
        conversation_repository: ConversationRepository,
        gateway: ExternalChannelService,
        publisher: EventPublisher,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        poll_seconds: float = 15,
    ) -> None:
        self._repository = repository
        self._conversation = conversation
        self._conversation_repository = conversation_repository
        self._gateway = gateway
        self._publisher = publisher
        self._clock = clock
        self._poll_seconds = max(1.0, poll_seconds)
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._reset_task: asyncio.Task[None] | None = None
        self._workflows: dict[UUID, tuple[ChannelOutboundIntentRecord, asyncio.Task[None]]] = {}
        self._connection_epochs: dict[UUID, int] = {}
        self._binding_epochs: dict[UUID, int] = {}
        self._intent_epochs: dict[UUID, tuple[int, int]] = {}
        self._operator_cancelling: set[UUID] = set()
        self._scan_cursor: UUID | None = None
        self._evaluation_lock = asyncio.Lock()
        self._started = False
        self._ready = False
        self._stopping = False

    @property
    def active_count(self) -> int:
        return len(self._workflows)

    def wake(self) -> None:
        self._changed.set()

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._ready = False
        self._stopping = False
        # An admitted generation is never submitted again after process loss.
        for intent in await self._repository.list_active_intents(limit=32):
            self._intent_epochs[intent.request_id] = (
                self._connection_epochs.get(intent.connection_id, 0),
                self._binding_epochs.get(intent.binding_id, 0),
            )
            if intent.status is ChannelOutboundIntentStatus.GENERATING:
                await self._restore_generation(intent)
            elif intent.status is ChannelOutboundIntentStatus.PLANNED:
                updated = await self._repository.sync_delivery_result(
                    intent.request_id, updated_at=self._clock()
                )
                await self._publish_record(updated)
        self._ready = True
        self._task = asyncio.create_task(self._run(), name="channel-proactive-scheduler")
        self._reset_task = asyncio.create_task(
            self._listen_resets(), name="channel-proactive-resets"
        )
        self.wake()

    async def stop(self) -> None:
        self._stopping = True
        self._ready = False
        if not self._started:
            return
        self._started = False
        task, self._task = self._task, None
        reset_task, self._reset_task = self._reset_task, None
        if task is not None:
            task.cancel("runtime_stopping")
        if reset_task is not None:
            reset_task.cancel("runtime_stopping")
        for intent, workflow in tuple(self._workflows.values()):
            self._fence_workflow(intent, workflow, "runtime_stopping")
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if reset_task is not None:
            await asyncio.gather(reset_task, return_exceptions=True)
        # Includes a durable admission whose reserve call had not yet returned.
        for intent in await self._repository.list_active_intents(limit=32):
            await self._cancel_record(intent, "runtime_stopping")
        await self._join_workflows()

    def fence_connection(self, connection_id: UUID, reason: str) -> None:
        self._connection_epochs[connection_id] = self._connection_epochs.get(connection_id, 0) + 1
        for intent, task in tuple(self._workflows.values()):
            if intent.connection_id == connection_id:
                self._fence_workflow(intent, task, reason)

    def fence_binding(self, binding_id: UUID, reason: str) -> None:
        self._binding_epochs[binding_id] = self._binding_epochs.get(binding_id, 0) + 1
        for intent, task in tuple(self._workflows.values()):
            if intent.binding_id == binding_id:
                self._fence_workflow(intent, task, reason)

    def fence_route_revision(self, connection_id: UUID, revision: int) -> None:
        for intent, task in tuple(self._workflows.values()):
            if intent.connection_id == connection_id and intent.route_revision != revision:
                self._fence_workflow(intent, task, "connection_changed")

    async def connection_updated(self, connection_id: UUID, *, revision: int) -> None:
        self.fence_route_revision(connection_id, revision)
        for intent in await self._repository.list_active_intents(connection_id, limit=32):
            if intent.route_revision != revision:
                await self._cancel_record(intent, "connection_changed")
        tasks = [
            task
            for intent, task in tuple(self._workflows.values())
            if intent.connection_id == connection_id
            and intent.route_revision != revision
            and task is not asyncio.current_task()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.wake()

    def _fence_workflow(
        self, intent: ChannelOutboundIntentRecord, task: asyncio.Task[None], reason: str
    ) -> None:
        self._conversation.request_cancel(
            intent.session_id, expected_generation_id=intent.generation_id, reason=reason
        )
        if task is not asyncio.current_task() and not task.cancelling():
            task.cancel(reason)

    async def cancel_for_connection(self, connection_id: UUID, *, reason: str) -> None:
        self.fence_connection(connection_id, reason)
        records = await self._repository.cancel_for_connection(
            connection_id, reason=reason, requested_at=self._clock()
        )
        for record in records:
            await self._publish_record(record)
            await self._conversation.cancel(
                record.session_id, reason, expected_generation_id=record.generation_id
            )
        await self._join_workflows(connection_id=connection_id)
        self._gateway.wake_delivery_scheduler(connection_id)
        self.wake()

    async def cancel_for_binding(self, binding_id: UUID, *, reason: str) -> None:
        self.fence_binding(binding_id, reason)
        records = await self._repository.cancel_for_binding(
            binding_id, reason=reason, requested_at=self._clock()
        )
        for record in records:
            await self._publish_record(record)
            await self._conversation.cancel(
                record.session_id, reason, expected_generation_id=record.generation_id
            )
            self._gateway.wake_delivery_scheduler(record.connection_id)
        await self._join_workflows(binding_id=binding_id)
        self.wake()

    async def _join_workflows(
        self, *, connection_id: UUID | None = None, binding_id: UUID | None = None
    ) -> None:
        tasks = [
            task
            for record, task in tuple(self._workflows.values())
            if (connection_id is None or record.connection_id == connection_id)
            and (binding_id is None or record.binding_id == binding_id)
            and task is not asyncio.current_task()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def get_policy(self, connection_id: UUID) -> ChannelProactivePolicySnapshot:
        try:
            context = await self._repository.get_context(connection_id, as_of=self._clock())
            if context.connection is None:
                raise ChannelNotFoundError("unknown channel connection")
            snapshot = _policy_snapshot(context.policy)
            return snapshot.model_copy(
                update={
                    "binding_id": context.binding.binding_id
                    if context.binding is not None
                    else None
                }
            )
        except KeyError as error:
            raise ChannelNotFoundError("unknown channel connection") from error

    async def update_policy(
        self, connection_id: UUID, body: ChannelProactivePolicyUpdate
    ) -> ChannelProactivePolicySnapshot:
        try:
            result = await self._repository.update_policy(
                connection_id, body, updated_at=self._clock()
            )
        except KeyError as error:
            raise ChannelNotFoundError("unknown channel connection") from error
        except ValueError as error:
            raise ChannelConflictError("proactive policy revision conflict") from error
        # CAS and revocation commit atomically. A rejected write must have no
        # cancellation side effects. New-revision work admitted while this
        # repository call was returning remains valid.
        revoked = [
            (intent, task)
            for intent, task in tuple(self._workflows.values())
            if intent.connection_id == connection_id and intent.policy_revision != result.revision
        ]
        for intent, task in revoked:
            self._fence_workflow(intent, task, "policy_changed")
        for intent, task in revoked:
            await self._conversation.cancel(
                intent.session_id, "policy_changed", expected_generation_id=intent.generation_id
            )
            if task is not asyncio.current_task():
                await asyncio.gather(task, return_exceptions=True)
        self.wake()
        await self._publish_record(result)
        context = await self._repository.get_context(connection_id, as_of=self._clock())
        return _policy_snapshot(result).model_copy(
            update={
                "binding_id": context.binding.binding_id if context.binding is not None else None
            }
        )

    async def preview(self, connection_id: UUID) -> ChannelProactivePreview:
        now = self._clock()
        try:
            context = await self._repository.get_context(connection_id, as_of=now)
        except KeyError as error:
            raise ChannelNotFoundError("unknown channel connection") from error
        if context.connection is None:
            raise ChannelNotFoundError("unknown channel connection")
        active = (
            context.binding is not None
            and self._conversation.active_generation_id(context.binding.session_id) is not None
        )
        return evaluate_proactive_context(context, as_of=now, generation_active=active)

    async def list_intents(
        self, connection_id: UUID, limit: int = 25, cursor: str | None = None
    ) -> ChannelOutboundIntentPage:
        if not 1 <= limit <= 50:
            raise ChannelConflictError("intent page limit must be between 1 and 50")
        await self.get_policy(connection_id)
        try:
            page = await self._repository.list_intents(connection_id, limit=limit, cursor=cursor)
        except ValueError as error:
            raise ChannelConflictError("invalid intent cursor") from error
        return ChannelOutboundIntentPage(
            items=[_intent_snapshot(record) for record in page.items], next_cursor=page.next_cursor
        )

    async def cancel_intent(
        self, connection_id: UUID, request_id: UUID, body: ChannelOutboundIntentCancelRequest
    ) -> ChannelOutboundIntentSnapshot:
        intent = await self._repository.get_intent(request_id)
        if intent is None or intent.connection_id != connection_id:
            raise ChannelNotFoundError("unknown outbound intent")
        if intent.revision != body.expected_revision:
            raise ChannelConflictError("outbound intent revision conflict")
        if intent.status is ChannelOutboundIntentStatus.SETTLED:
            return _intent_snapshot(intent)
        owned = self._workflows.get(request_id)
        self._operator_cancelling.add(request_id)
        try:
            if owned is not None:
                self._fence_workflow(intent, owned[1], "operator_cancelled")
            result = await self._repository.settle_intent(
                request_id,
                reason="operator_cancelled",
                settled_at=self._clock(),
                cancel=True,
                expected_revision=body.expected_revision,
            )
            await self._publish_record(result)
            self._intent_epochs.pop(request_id, None)
            await self._conversation.cancel(
                intent.session_id, "operator_cancelled", expected_generation_id=intent.generation_id
            )
            if owned is not None and owned[1] is not asyncio.current_task():
                await asyncio.gather(owned[1], return_exceptions=True)
        except ValueError as error:
            raise ChannelConflictError("outbound intent revision conflict") from error
        finally:
            self._operator_cancelling.discard(request_id)
        self._gateway.wake_delivery_scheduler(connection_id)
        return _intent_snapshot(result)

    async def authorize_delivery(self, plan: ChannelDeliveryPlanRecord) -> bool:
        if plan.outbound_intent_id is None:
            return True
        if self._stopping or not self._ready:
            return False
        intent = await self._repository.get_intent(plan.outbound_intent_id)
        if intent is None or intent.delivery_id != plan.delivery_id:
            return False
        result = await self._conversation_repository.generation_result(intent.generation_id)
        if (
            result is None
            or result.state is not GenerationState.COMPLETED
            or result.session_id != intent.session_id
            or result.turn_id != intent.turn_id
        ):
            return False
        epochs = self._intent_epochs.get(intent.request_id)
        if epochs is not None and not self._epochs_match(intent, *epochs):
            return False
        authorization = await self._repository.authorize_intent(
            intent.request_id, as_of=self._clock()
        )
        return (
            authorization.allowed
            and not self._stopping
            and (epochs is None or self._epochs_match(intent, *epochs))
        )

    async def on_plan_terminal(self, plan: ChannelDeliveryPlanRecord) -> None:
        if plan.outbound_intent_id is not None:
            updated = await self._repository.sync_delivery_result(
                plan.outbound_intent_id, updated_at=self._clock()
            )
            await self._publish_record(updated)
            if updated.status is ChannelOutboundIntentStatus.SETTLED:
                self._intent_epochs.pop(updated.request_id, None)
            self.wake()

    async def evaluate_once(self) -> int:
        async with self._evaluation_lock:
            if self._stopping:
                return 0
            for intent in await self._repository.list_active_intents(limit=32):
                if intent.request_id in self._workflows:
                    authorization = await self._repository.authorize_intent(
                        intent.request_id, as_of=self._clock()
                    )
                    if not authorization.allowed:
                        owned = self._workflows[intent.request_id]
                        self._fence_workflow(intent, owned[1], authorization.reason)
                        await self._cancel_record(intent, authorization.reason)
                    continue
                if intent.status is ChannelOutboundIntentStatus.GENERATING:
                    await self._restore_generation(intent)
                elif intent.status is ChannelOutboundIntentStatus.PLANNED:
                    updated = await self._repository.sync_delivery_result(
                        intent.request_id, updated_at=self._clock()
                    )
                    await self._publish_record(updated)
                    if updated.status is ChannelOutboundIntentStatus.SETTLED:
                        self._intent_epochs.pop(updated.request_id, None)
                    if updated.status is not ChannelOutboundIntentStatus.SETTLED:
                        auth = await self._repository.authorize_intent(
                            intent.request_id, as_of=self._clock()
                        )
                        if not auth.allowed:
                            await self._cancel_record(intent, auth.reason)
                elif intent.status is ChannelOutboundIntentStatus.PENDING:
                    await self._begin(intent)
            # One keyset page per tick bounds database/model work and rotates
            # through all connections instead of starving after the first 32.
            contexts = await self._repository.list_contexts(
                as_of=self._clock(), limit=32, after_connection_id=self._scan_cursor
            )
            self._scan_cursor = contexts[-1].policy.connection_id if len(contexts) == 32 else None
            created = 0
            for context in contexts:
                if self._stopping:
                    break
                connection_id = context.policy.connection_id
                epoch = self._connection_epochs.get(connection_id, 0)
                binding_epoch = (
                    self._binding_epochs.get(context.binding.binding_id, 0)
                    if context.binding is not None
                    else 0
                )
                active = (
                    context.binding is not None
                    and self._conversation.active_generation_id(context.binding.session_id)
                    is not None
                )
                reserved = await self._repository.reserve_intent(
                    connection_id, as_of=self._clock(), generation_active=active
                )
                if reserved.created and reserved.intent is not None:
                    await self._publish_record(reserved.intent)
                    if not self._epochs_match(reserved.intent, epoch, binding_epoch):
                        await self._cancel_record(reserved.intent, "admission_revoked")
                    else:
                        created += 1
                        await self._begin(reserved.intent)
            return created

    async def _begin(self, intent: ChannelOutboundIntentRecord) -> None:
        if self._stopping or intent.request_id in self._workflows:
            return
        connection_epoch = self._connection_epochs.get(intent.connection_id, 0)
        binding_epoch = self._binding_epochs.get(intent.binding_id, 0)
        claimed = await self._repository.claim_generation(
            intent.request_id, expected_revision=intent.revision, claimed_at=self._clock()
        )
        if claimed is None:
            return
        if claimed.status is not ChannelOutboundIntentStatus.GENERATING:
            await self._publish_record(claimed)
            return
        if not self._epochs_match(claimed, connection_epoch, binding_epoch):
            await self._cancel_record(claimed, "admission_revoked")
            return
        task = asyncio.create_task(
            self._generate(claimed, connection_epoch, binding_epoch),
            name=f"channel-proactive-{claimed.request_id}",
        )
        self._intent_epochs[claimed.request_id] = connection_epoch, binding_epoch
        self._workflows[claimed.request_id] = claimed, task

    def _epochs_match(
        self, intent: ChannelOutboundIntentRecord, connection: int, binding: int
    ) -> bool:
        return (
            not self._stopping
            and self._connection_epochs.get(intent.connection_id, 0) == connection
            and self._binding_epochs.get(intent.binding_id, 0) == binding
        )

    async def _generate(
        self, intent: ChannelOutboundIntentRecord, connection_epoch: int, binding_epoch: int
    ) -> None:
        async def guard() -> bool:
            if not self._epochs_match(intent, connection_epoch, binding_epoch):
                return False
            authorization = await self._repository.authorize_intent(
                intent.request_id, as_of=self._clock()
            )
            return authorization.allowed and self._epochs_match(
                intent, connection_epoch, binding_epoch
            )

        subscription = self._publisher.event_hub.subscribe(
            lambda event: (
                str(event.get("generation_id")) == str(intent.generation_id)
                and event.get("event_type") in _TERMINAL_EVENTS
            ),
            queue_size=16,
        )
        try:
            await self._publish_record(intent)
            if not await guard():
                if intent.request_id in self._operator_cancelling:
                    return
                await self._cancel_record(intent, "generation_not_authorized")
                return
            await self._conversation.submit_proactive(
                intent.session_id,
                turn_id=intent.turn_id,
                generation_id=intent.generation_id,
                audio_stream_id=intent.audio_stream_id,
                options=ConversationTurnOptions(
                    origin="proactive",
                    output_modes=frozenset({"text"}),
                    allow_tools=False,
                    source_context=_source_context(intent),
                    before_generation=guard,
                ),
            )
            await subscription.receive()
            if intent.request_id in self._operator_cancelling:
                return
            if not await guard():
                await self._cancel_record(intent, "generation_not_authorized")
                return
            await self._restore_generation(intent)
        except asyncio.CancelledError:
            await self._conversation.cancel(
                intent.session_id,
                "proactive_cancelled",
                expected_generation_id=intent.generation_id,
            )
            if intent.request_id not in self._operator_cancelling:
                await self._cancel_record(intent, "proactive_cancelled")
            raise
        except Exception:
            self._conversation.request_cancel(
                intent.session_id,
                expected_generation_id=intent.generation_id,
                reason="proactive_generation_failed",
            )
            await self._conversation.cancel(
                intent.session_id,
                "proactive_generation_failed",
                expected_generation_id=intent.generation_id,
            )
            settled = await self._repository.settle_intent(
                intent.request_id,
                reason="generation_failed",
                settled_at=self._clock(),
                error=StructuredError(
                    code="channel_proactive_generation_failed",
                    message="主动文字生成失败。",
                    retryable=False,
                    component="external_channels",
                ),
            )
            await self._publish_record(settled)
            self._intent_epochs.pop(intent.request_id, None)
        finally:
            subscription.close()
            self._workflows.pop(intent.request_id, None)
            self.wake()

    async def _restore_generation(self, intent: ChannelOutboundIntentRecord) -> None:
        result = await self._conversation_repository.generation_result(intent.generation_id)
        authorization = await self._repository.authorize_intent(
            intent.request_id, as_of=self._clock()
        )
        if not authorization.allowed:
            await self._cancel_record(intent, authorization.reason)
        elif (
            result is None
            or result.state is not GenerationState.COMPLETED
            or result.session_id != intent.session_id
            or result.turn_id != intent.turn_id
            or result.audio_stream_id != intent.audio_stream_id
        ):
            settled = await self._repository.settle_intent(
                intent.request_id, reason="generation_not_completed", settled_at=self._clock()
            )
            await self._publish_record(settled)
            self._intent_epochs.pop(intent.request_id, None)
        elif (
            not result.output_text
            or not result.output_text.strip()
            or not 1 <= len(result.output_text) <= 2000
        ):
            settled = await self._repository.settle_intent(
                intent.request_id, reason="reply_not_bounded", settled_at=self._clock()
            )
            await self._publish_record(settled)
            self._intent_epochs.pop(intent.request_id, None)
        else:
            transition = await self._repository.create_outbound_text_plan(
                intent.request_id, reply_text=result.output_text, created_at=self._clock()
            )
            for event in transition.persisted_events:
                await self._publisher.publish_persisted(event)
            self._gateway.wake_delivery_scheduler(intent.connection_id)

    async def _cancel_record(self, intent: ChannelOutboundIntentRecord, reason: str) -> None:
        settled = await self._repository.settle_intent(
            intent.request_id, reason=reason, settled_at=self._clock(), cancel=True
        )
        await self._publish_record(settled)
        await self._conversation.cancel(
            intent.session_id, reason, expected_generation_id=intent.generation_id
        )
        self._intent_epochs.pop(intent.request_id, None)
        self._gateway.wake_delivery_scheduler(intent.connection_id)

    async def _publish_record(
        self, record: ChannelProactivePolicyRecord | ChannelOutboundIntentRecord
    ) -> None:
        for event in record.persisted_events:
            await self._publisher.publish_persisted(event)

    async def _listen_resets(self) -> None:
        subscription = self._publisher.event_hub.subscribe(
            lambda event: event.get("event_type") == "session.data_reset", queue_size=64
        )
        try:
            while not self._stopping:
                await subscription.receive()
                # Persistence checks the reset scope and invalidated generation
                # in each authorization. Never infer authority from event text.
                for intent in await self._repository.list_active_intents(limit=32):
                    authorization = await self._repository.authorize_intent(
                        intent.request_id, as_of=self._clock()
                    )
                    if not authorization.allowed:
                        owned = self._workflows.get(intent.request_id)
                        if owned is not None:
                            self._fence_workflow(intent, owned[1], "session_reset")
                        await self._cancel_record(intent, "session_reset")
                self.wake()
        finally:
            subscription.close()

    async def _run(self) -> None:
        while not self._stopping:
            self._changed.clear()
            try:
                await self.evaluate_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("channel proactive evaluation failed")
            try:
                await asyncio.wait_for(self._changed.wait(), self._poll_seconds)
            except TimeoutError:
                pass


def _policy_snapshot(record: ChannelProactivePolicyRecord) -> ChannelProactivePolicySnapshot:
    return ChannelProactivePolicySnapshot(
        connection_id=record.connection_id,
        binding_id=record.binding_id,
        policy=record.policy,
        revision=record.revision,
        updated_at=record.updated_at,
    )


def _intent_snapshot(record: ChannelOutboundIntentRecord) -> ChannelOutboundIntentSnapshot:
    return ChannelOutboundIntentSnapshot(
        request_id=record.request_id,
        connection_id=record.connection_id,
        binding_id=record.binding_id,
        source="idle_check_in",
        session_id=record.session_id,
        turn_id=record.turn_id,
        generation_id=record.generation_id,
        status=record.status,
        policy_revision=record.policy_revision,
        route_revision=record.route_revision,
        revision=record.revision,
        not_before_at=record.not_before_at,
        expires_at=record.expires_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        settled_at=record.settled_at,
        settled_reason=record.settled_reason,
        cancel_requested_at=record.cancel_requested_at,
        cancel_reason=record.cancel_reason,
        reply_text=record.reply_text,
        delivery_id=record.delivery_id,
        delivery_status=record.delivery_status,
        provider_receipt_present=record.provider_receipt_present,
        cancelable=record.status is not ChannelOutboundIntentStatus.SETTLED
        and not record.provider_receipt_present,
        error=record.error,
    )


def _source_context(intent: ChannelOutboundIntentRecord) -> ConversationSourceContext:
    return ConversationSourceContext(
        provider_id="qq_napcat",
        connection_id=intent.connection_id,
        account_key=intent.account_key,
        principal_scope=intent.principal_scope,
        chat_type="direct",
        conversation_key=intent.conversation_key,
        sender_key=intent.sender_key,
        outbound_intent_id=intent.request_id,
        source_event_key=intent.source_event_key,
        policy_revision=intent.policy_revision,
        route_revision=intent.route_revision,
    )
