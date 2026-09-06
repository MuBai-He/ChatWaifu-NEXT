"""Typed, bounded native WeChat image burst intake coordinator (Phase 17.3F / ADR 0041).

Invariants enforced:
1. Sliding idle window (1.5s), hard ceiling (4.0s), product cap (4 images).
2. Single wire batch of 4 seals early with zero additional debounce delay.
3. Plain text immediately supersedes: cancels pending burst and active image generation.
4. Exactly one generation after collection; combined captions preserve actual user text in order.
5. Leader/member relations persist in channel_turn_burst_members; followers never mark completed
   at dispatch.
6. At most one active batch and one pending batch per binding; bounded globally.
7. Overflow terminates pending batch with explicit durable image failure recovery notice at its
   dispatch slot.
8. Bounded identity records for rejected items, no raw image bytes or CDN keys stored.
9. Controllable time and event barriers for deterministic testing without fixed sleeps.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol
from uuid import UUID, uuid4

from chatwaifu_protocol.channels import (
    ChannelInboundTextMessage,
    ChannelTurnReceipt,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError

from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.models import (
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.photo_memory.models import PhotoItemOrigin
from chatwaifu_runtime.providers.contracts import LlmInputImage

BURST_IDLE_WINDOW_SECONDS: float = 1.5
BURST_HARD_CEILING_SECONDS: float = 4.0
BURST_MAX_IMAGES: int = 4
MAX_GLOBAL_BATCHES: int = 16
MAX_RESERVED_CONTEXT_SLOTS: int = 2
MAX_WECHAT_CONTEXTS: int = 16

_IMAGE_FAILURE_RECOVERY_TEXT = "刚才发来的图片我没看清，能再发一次吗？"


def _burst_error(code: str, message: str, *, retryable: bool = False) -> StructuredError:
    return StructuredError(
        code=code,
        message=message,
        retryable=retryable,
        component="external_channels",
    )


def _turn_receipt(turn: ChannelTurnRecord, *, duplicate: bool = False) -> ChannelTurnReceipt:
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
    )


class BurstTimerHandle(Protocol):
    def cancel(self) -> None: ...


class BurstScheduler(Protocol):
    def call_later(self, delay: float, callback: Callable[[], None]) -> BurstTimerHandle: ...
    def monotonic(self) -> float: ...


class AsyncioBurstScheduler:
    def call_later(self, delay: float, callback: Callable[[], None]) -> BurstTimerHandle:
        loop = asyncio.get_running_loop()
        handle = loop.call_later(delay, callback)

        class _Handle:
            def cancel(self) -> None:
                handle.cancel()

        return _Handle()

    def monotonic(self) -> float:
        return asyncio.get_running_loop().time()


class BurstBatchState(Enum):
    COLLECTING = "collecting"
    SEALED_DEFERRED = "sealed_deferred"
    ACTIVE_PROCESSING = "active_processing"
    TERMINAL = "terminal"


@dataclass(slots=True)
class BurstItem:
    turn: ChannelTurnRecord
    message: ChannelInboundTextMessage
    raw_images: tuple[object, ...]
    loader: Callable[[], Awaitable[tuple[LlmInputImage, ...] | LlmInputImage]]
    caption: str
    received_at: datetime
    context_token: str | None
    item_origins: tuple[PhotoItemOrigin, ...]


def _default_items() -> list[BurstItem]:
    return []


@dataclass(slots=True)
class BurstBatch:
    burst_id: UUID
    binding_id: UUID
    connection_id: UUID
    session_id: UUID
    leader_turn: ChannelTurnRecord
    character_id: str
    principal_scope: str
    items: list[BurstItem] = field(default_factory=_default_items)
    state: BurstBatchState = BurstBatchState.COLLECTING
    created_at_mono: float = 0.0
    last_received_at_mono: float = 0.0
    idle_timer: BurstTimerHandle | None = None
    ceiling_timer: BurstTimerHandle | None = None
    overflow: bool = False
    access_token: str | None = None
    sealed_event: asyncio.Event = field(default_factory=asyncio.Event)
    dispatched_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False
    idle_revision: int = 0

    @property
    def total_images(self) -> int:
        return sum(len(item.item_origins) for item in self.items)


def combine_burst_captions(items: Sequence[BurstItem]) -> str:
    """Combine actual user captions in arrival order without system instructions."""
    captions: list[str] = []
    for item in items:
        text = (item.caption or item.message.text or "").strip()
        if text.startswith("[图片]"):
            user_part = text[len("[图片]") :].strip()
        elif text.startswith("[Image]"):
            user_part = text[len("[Image]") :].strip()
        else:
            user_part = text
        if user_part:
            captions.append(user_part)
    if not captions:
        return "[图片]"
    combined = "[图片] " + " ".join(captions)
    if len(combined) > 20000:
        combined = combined[:20000]
    return combined


class ImageBurstCoordinator:
    """Coordinates inbound WeChat image burst collection, deferred dispatch, and overflow."""

    def __init__(
        self,
        repository: ExternalChannelRepository,
        *,
        scheduler: BurstScheduler | None = None,
        on_dispatch_burst: Callable[[BurstBatch], Awaitable[None]] | None = None,
        on_turn_terminal: Callable[[ChannelTurnRecord], Awaitable[None]] | None = None,
        on_wake_scheduler: Callable[[UUID], None] | None = None,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler or AsyncioBurstScheduler()
        self._on_dispatch_burst = on_dispatch_burst
        self._on_turn_terminal = on_turn_terminal
        self._on_wake_scheduler = on_wake_scheduler
        self._publisher = publisher
        self._active_batches: dict[UUID, BurstBatch] = {}
        self._pending_batches: dict[UUID, BurstBatch] = {}
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._dispatch_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._stopping = False

    def set_scheduler_wake_callback(self, callback: Callable[[UUID], None]) -> None:
        self._on_wake_scheduler = callback

    def _spawn_background(
        self, coro: Coroutine[Any, Any, None], name: str | None = None
    ) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def is_live_burst_turn(self, channel_turn_id: UUID) -> bool:
        """Explicit live batch ownership guard while collecting/sealed/dispatch-in-progress."""
        for batch in itertools.chain(self._pending_batches.values(), self._active_batches.values()):
            if not batch.cancelled and batch.state is not BurstBatchState.TERMINAL:
                if batch.leader_turn.channel_turn_id == channel_turn_id:
                    return True
                if any(item.turn.channel_turn_id == channel_turn_id for item in batch.items):
                    return True
        return False

    def has_pending_burst(self, binding_id: UUID) -> bool:
        return binding_id in self._pending_batches

    def get_pending_batch(self, binding_id: UUID) -> BurstBatch | None:
        return self._pending_batches.get(binding_id)

    def get_active_batch(self, binding_id: UUID) -> BurstBatch | None:
        return self._active_batches.get(binding_id)

    async def admit_image(
        self,
        message: ChannelInboundTextMessage,
        turn: ChannelTurnRecord,
        raw_images: tuple[object, ...],
        loader: Callable[[], Awaitable[tuple[LlmInputImage, ...] | LlmInputImage]],
        *,
        character_id: str,
        principal_scope: str,
        context_token: str | None = None,
        access_token: str | None = None,
        pending_contexts_count: int = 0,
    ) -> ChannelTurnReceipt:
        """Admit durable identities without blocking subsequent inbound text on capacity."""
        reject = False
        async with self._lock:
            now = datetime.now(UTC)
            mono = self._scheduler.monotonic()
            count = max(1, len(raw_images))
            pending = self._pending_batches.get(turn.binding_id)
            active = self._active_batches.get(turn.binding_id)
            # One shared state lock makes global admission bounds atomic. Rejected
            # turns remain durable, but allocate no batch, timer, or private loader.
            if self._stopping or (
                pending is None
                and (len(self._pending_batches) + len(self._active_batches) >= MAX_GLOBAL_BATCHES)
            ):
                reject = True
            else:
                overflow = count > BURST_MAX_IMAGES or pending_contexts_count >= (
                    MAX_WECHAT_CONTEXTS - MAX_RESERVED_CONTEXT_SLOTS
                )
                if pending is None:
                    pending = BurstBatch(
                        burst_id=turn.channel_turn_id,
                        binding_id=turn.binding_id,
                        connection_id=turn.connection_id,
                        session_id=turn.session_id,
                        leader_turn=turn,
                        character_id=character_id,
                        principal_scope=principal_scope,
                        created_at_mono=mono,
                        last_received_at_mono=mono,
                        access_token=access_token,
                    )
                    self._pending_batches[turn.binding_id] = pending
                else:
                    overflow = (
                        overflow
                        or pending.overflow
                        or (
                            pending.state is not BurstBatchState.COLLECTING
                            or pending.total_images + count > BURST_MAX_IMAGES
                        )
                    )
                await self._repository.add_burst_member(
                    burst_id=pending.burst_id,
                    leader_channel_turn_id=pending.leader_turn.channel_turn_id,
                    member_channel_turn_id=turn.channel_turn_id,
                    ordinal=min(pending.total_images, BURST_MAX_IMAGES - 1),
                    received_at=message.received_at,
                    created_at=now,
                )
                pending.last_received_at_mono = mono
                pending.access_token = access_token or pending.access_token
                if overflow:
                    pending.overflow = True
                    # Rejected member identities are read from SQLite at terminal
                    # cleanup. Never retain unbounded descriptors/tokens in memory.
                    pending.items.clear()
                else:
                    origins = tuple(
                        PhotoItemOrigin(
                            channel_turn_id=turn.channel_turn_id,
                            external_message_id=turn.external_message_id,
                            received_at=message.received_at,
                        )
                        for _ in range(count)
                    )
                    pending.items.append(
                        BurstItem(
                            turn=turn,
                            message=message,
                            raw_images=raw_images,
                            loader=loader,
                            caption=message.text,
                            received_at=message.received_at,
                            context_token=context_token,
                            item_origins=origins,
                        )
                    )
                if pending.overflow or pending.total_images == BURST_MAX_IMAGES:
                    self._cancel_timers(pending)
                    pending.sealed_event.set()
                    if active is None:
                        self._pending_batches.pop(turn.binding_id, None)
                        pending.state = BurstBatchState.ACTIVE_PROCESSING
                        self._active_batches[turn.binding_id] = pending
                        self._spawn_dispatch_task(pending)
                    else:
                        pending.state = BurstBatchState.SEALED_DEFERRED
                elif pending.ceiling_timer is None:
                    self._start_timers(pending)
                else:
                    self._reset_idle_timer(pending)
        if reject:
            result = await self._repository.fail_turn_with_notice(
                turn.channel_turn_id,
                error=_burst_error("channel_image_overflow", "Image burst capacity reached."),
                notice_text=_IMAGE_FAILURE_RECOVERY_TEXT,
                delivery_id=uuid4(),
                completed_at=datetime.now(UTC),
            )
            if self._publisher is not None:
                for event in result.persisted_events:
                    await self._publisher.publish_persisted(event)
            if self._on_turn_terminal is not None:
                await self._on_turn_terminal(result.turn)
            if self._on_wake_scheduler is not None:
                self._on_wake_scheduler(turn.connection_id)
            return _turn_receipt(result.turn)
        return _turn_receipt(turn)

    def _start_timers(self, batch: BurstBatch) -> None:
        binding_id = batch.binding_id
        burst_id = batch.burst_id

        revision = batch.idle_revision

        def on_idle() -> None:
            self._spawn_background(self._on_timer_fire(binding_id, burst_id, revision))

        def on_ceiling() -> None:
            self._spawn_background(self._on_timer_fire(binding_id, burst_id))

        batch.idle_timer = self._scheduler.call_later(BURST_IDLE_WINDOW_SECONDS, on_idle)
        batch.ceiling_timer = self._scheduler.call_later(BURST_HARD_CEILING_SECONDS, on_ceiling)

    def _reset_idle_timer(self, batch: BurstBatch) -> None:
        batch.idle_revision += 1
        if batch.idle_timer is not None:
            batch.idle_timer.cancel()
            batch.idle_timer = None

        now_mono = self._scheduler.monotonic()
        remaining_ceiling = (batch.created_at_mono + BURST_HARD_CEILING_SECONDS) - now_mono
        if remaining_ceiling <= 0:
            binding_id = batch.binding_id
            burst_id = batch.burst_id
            self._spawn_background(self._on_timer_fire(binding_id, burst_id))
            return

        delay = min(BURST_IDLE_WINDOW_SECONDS, remaining_ceiling)
        binding_id = batch.binding_id
        burst_id = batch.burst_id

        revision = batch.idle_revision

        def on_idle() -> None:
            self._spawn_background(self._on_timer_fire(binding_id, burst_id, revision))

        batch.idle_timer = self._scheduler.call_later(delay, on_idle)

    def _cancel_timers(self, batch: BurstBatch) -> None:
        if batch.idle_timer is not None:
            batch.idle_timer.cancel()
            batch.idle_timer = None
        if batch.ceiling_timer is not None:
            batch.ceiling_timer.cancel()
            batch.ceiling_timer = None

    async def _on_timer_fire(
        self, binding_id: UUID, burst_id: UUID, idle_revision: int | None = None
    ) -> None:
        if self._stopping:
            return
        batch_to_dispatch: BurstBatch | None = None
        async with self._lock:
            batch = self._pending_batches.get(binding_id)
            if (
                batch is None
                or batch.burst_id != burst_id
                or batch.state is not BurstBatchState.COLLECTING
                or (idle_revision is not None and idle_revision != batch.idle_revision)
            ):
                return
            self._cancel_timers(batch)
            batch.sealed_event.set()
            active = self._active_batches.get(binding_id)
            if active is None:
                self._pending_batches.pop(binding_id, None)
                batch.state = BurstBatchState.ACTIVE_PROCESSING
                self._active_batches[binding_id] = batch
                batch_to_dispatch = batch
            else:
                batch.state = BurstBatchState.SEALED_DEFERRED

        if batch_to_dispatch is not None:
            self._spawn_dispatch_task(batch_to_dispatch)

    def _spawn_dispatch_task(self, batch: BurstBatch) -> asyncio.Task[None]:
        task = self._spawn_background(
            self._dispatch_sealed_batch(batch),
            name=f"burst-dispatch-{batch.burst_id}",
        )
        self._dispatch_tasks[batch.burst_id] = task
        task.add_done_callback(lambda _: self._dispatch_tasks.pop(batch.burst_id, None))
        return task

    async def _dispatch_sealed_batch(self, batch: BurstBatch) -> None:
        if self._stopping or batch.cancelled or batch.state is BurstBatchState.TERMINAL:
            return

        binding_id = batch.binding_id
        leader = await self._repository.get_turn(batch.leader_turn.channel_turn_id)
        if leader is None or leader.status not in (
            ChannelTurnStatus.ACCEPTED,
            ChannelTurnStatus.PROCESSING,
        ):
            batch.state = BurstBatchState.TERMINAL
            async with self._lock:
                if self._active_batches.get(binding_id) is batch:
                    self._active_batches.pop(binding_id, None)

            batch.dispatched_event.set()
            self._schedule_next_pending(binding_id)
            return

        if batch.overflow:
            batch.state = BurstBatchState.TERMINAL
            now = datetime.now(UTC)
            err = _burst_error(
                code="channel_image_overflow",
                message="Image burst exceeded capacity limits.",
            )
            fail_result = await self._repository.fail_turn_with_notice(
                batch.leader_turn.channel_turn_id,
                error=err,
                notice_text=_IMAGE_FAILURE_RECOVERY_TEXT,
                delivery_id=uuid4(),
                completed_at=now,
            )
            if self._publisher is not None:
                for event in fail_result.persisted_events:
                    await self._publisher.publish_persisted(event)
            if self._on_wake_scheduler is not None:
                self._on_wake_scheduler(batch.connection_id)
            if self._on_turn_terminal is not None:
                await self._on_turn_terminal(fail_result.turn)

            members = await self._repository.list_burst_members(batch.leader_turn.channel_turn_id)
            for m in members:
                if m.member_channel_turn_id != batch.leader_turn.channel_turn_id:
                    await self._set_turn_failed(m.member_channel_turn_id, err, now)

            async with self._lock:
                if self._active_batches.get(binding_id) is batch:
                    self._active_batches.pop(binding_id, None)

            batch.dispatched_event.set()
            self._schedule_next_pending(binding_id)
            return

        batch.state = BurstBatchState.ACTIVE_PROCESSING
        if self._on_dispatch_burst is not None:
            await self._on_dispatch_burst(batch)
        batch.dispatched_event.set()

    async def _set_turn_failed(
        self, channel_turn_id: UUID, error: StructuredError, completed_at: datetime
    ) -> None:
        turn = await self._repository.get_turn(channel_turn_id)
        if turn is not None and turn.status in (
            ChannelTurnStatus.ACCEPTED,
            ChannelTurnStatus.PROCESSING,
        ):
            record = await self._repository.set_turn_terminal(
                channel_turn_id,
                status=ChannelTurnStatus.FAILED,
                error=error,
                completed_at=completed_at,
            )
            if self._on_turn_terminal is not None:
                await self._on_turn_terminal(record)
        elif turn is not None and self._on_turn_terminal is not None:
            await self._on_turn_terminal(turn)

    async def on_turn_terminal(self, turn: ChannelTurnRecord) -> None:
        """Invoked when any channel turn reaches terminal status."""
        should_check_next = False
        async with self._lock:
            active = self._active_batches.get(turn.binding_id)
            if active is not None and active.leader_turn.channel_turn_id == turn.channel_turn_id:
                active.state = BurstBatchState.TERMINAL
                self._active_batches.pop(turn.binding_id, None)
                should_check_next = True

        if should_check_next:
            self._schedule_next_pending(turn.binding_id)

    def _schedule_next_pending(self, binding_id: UUID) -> None:
        self._spawn_background(self._check_next_pending(binding_id))

    async def _check_next_pending(self, binding_id: UUID) -> None:
        if self._stopping:
            return
        batch_to_dispatch: BurstBatch | None = None
        async with self._lock:
            if binding_id in self._active_batches:
                return
            pending = self._pending_batches.get(binding_id)
            if pending is not None and pending.state is BurstBatchState.SEALED_DEFERRED:
                self._pending_batches.pop(binding_id, None)
                pending.state = BurstBatchState.ACTIVE_PROCESSING
                self._active_batches[binding_id] = pending
                batch_to_dispatch = pending
        if batch_to_dispatch is not None:
            self._spawn_dispatch_task(batch_to_dispatch)

    async def cancel_pending_burst(
        self, binding_id: UUID, *, reason: str = "cancelled_by_user_text"
    ) -> list[ChannelTurnRecord]:
        """Cancel an open or deferred pending burst immediately on user plain text."""
        async with self._lock:
            pending = self._pending_batches.pop(binding_id, None)
            if pending is not None:
                self._cancel_timers(pending)
                pending.cancelled = True
                pending.state = BurstBatchState.TERMINAL

        if pending is None:
            return []

        now = datetime.now(UTC)
        err = _burst_error(
            code="channel_ingress_cancelled",
            message=f"Burst cancelled: {reason}",
        )
        cancelled_turns: list[ChannelTurnRecord] = []
        members = await self._repository.list_burst_members(pending.leader_turn.channel_turn_id)
        for m in members:
            cancelled = await self._cancel_turn(m.member_channel_turn_id, err, now)
            if cancelled is not None:
                cancelled_turns.append(cancelled)
        return cancelled_turns

    async def cancel_active_batch(
        self, binding_id: UUID, *, reason: str = "superseded_by_new_inbound_message"
    ) -> None:
        """Cancel and await active dispatch setup task on supersession."""
        dispatch_task: asyncio.Task[None] | None = None
        async with self._lock:
            active = self._active_batches.get(binding_id)
            if active is not None:
                active.cancelled = True
                active.state = BurstBatchState.TERMINAL
                dispatch_task = self._dispatch_tasks.get(active.burst_id)
                if dispatch_task is not None:
                    dispatch_task.cancel()
        if dispatch_task is not None:
            await asyncio.gather(dispatch_task, return_exceptions=True)

    async def _cancel_turn(
        self, channel_turn_id: UUID, error: StructuredError, completed_at: datetime
    ) -> ChannelTurnRecord | None:
        turn = await self._repository.get_turn(channel_turn_id)
        if turn is not None and turn.status in (
            ChannelTurnStatus.ACCEPTED,
            ChannelTurnStatus.PROCESSING,
            ChannelTurnStatus.CANCELLING,
        ):
            record = await self._repository.set_turn_terminal(
                channel_turn_id,
                status=ChannelTurnStatus.CANCELLED,
                error=error,
                completed_at=completed_at,
            )
            if self._on_turn_terminal is not None:
                await self._on_turn_terminal(record)
            return record
        return turn

    async def stop(self) -> None:
        """Gracefully stop coordinator, cancelling all timers, tasks, and pending bursts."""
        self._stopping = True
        for batch in list(self._pending_batches.values()):
            self._cancel_timers(batch)
            batch.cancelled = True
            batch.state = BurstBatchState.TERMINAL
            now = datetime.now(UTC)
            err = _burst_error(
                code="channel_shutdown",
                message="Channel burst intake stopped during shutdown.",
            )
            try:
                members = await self._repository.list_burst_members(
                    batch.leader_turn.channel_turn_id
                )
                for m in members:
                    await self._cancel_turn(m.member_channel_turn_id, err, now)
            except Exception:
                pass

        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._background_tasks.clear()
        self._dispatch_tasks.clear()
        self._pending_batches.clear()
        self._active_batches.clear()
