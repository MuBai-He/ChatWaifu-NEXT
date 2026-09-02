"""Non-blocking bounded event hub with laned priority dispatch for Runtime consumers."""

from __future__ import annotations

import asyncio
import heapq
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

type RuntimeEvent = dict[str, object]
type EventFilter = Callable[[RuntimeEvent], bool]


class EventRetentionClass(StrEnum):
    CRITICAL = "critical"
    DOMAIN = "domain"
    EPHEMERAL = "ephemeral"


class EventDispatchMode(StrEnum):
    PREEMPTIVE = "preemptive"
    ORDERED = "ordered"


class EventDeliveryClass(StrEnum):
    CONTROL = "control"
    DOMAIN = "domain"
    TELEMETRY = "telemetry"


_DISPATCH_PRIO: dict[EventDispatchMode, int] = {
    EventDispatchMode.PREEMPTIVE: 0,
    EventDispatchMode.ORDERED: 1,
}

_RETENTION_WEIGHT: dict[EventRetentionClass, int] = {
    EventRetentionClass.EPHEMERAL: 0,
    EventRetentionClass.DOMAIN: 1,
    EventRetentionClass.CRITICAL: 2,
}

_PREEMPTIVE_EXACT_TYPES: frozenset[str] = frozenset(
    {
        "session.interrupted",
        "session.cancelled",
        "conversation.interruption_requested",
        "conversation.interrupted",
        "assistant.generation_cancelled",
        "skill.confirmation_requested",
        "skill.confirmation_decided",
        "skill.run_cancelled",
        "runtime_skill.permission_requested",
        "runtime_skill.permission_decided",
        "runtime_skill.cancelled",
        "system.error_raised",
    }
)

_CRITICAL_RETENTION_TYPES: frozenset[str] = frozenset(
    {
        "session.interrupted",
        "session.cancelled",
        "session.resumed",
        "session.state_changed",
        "conversation.interruption_requested",
        "conversation.interrupted",
        "assistant.generation_cancelled",
        "assistant.generation_failed",
        "assistant.generation_completed",
        "assistant.audio_chunk_queued",
        "assistant.text_segment_committed",
        "assistant.playback_stopped",
        "skill.confirmation_requested",
        "skill.confirmation_decided",
        "skill.run_cancelled",
        "runtime_skill.permission_requested",
        "runtime_skill.permission_decided",
        "runtime_skill.cancelled",
        "system.error_raised",
    }
)

_EPHEMERAL_RETENTION_TYPES: frozenset[str] = frozenset(
    {
        "assistant.text_delta",
        "assistant.playback_progress",
        "user.transcript_partial",
        "avatar.debug_sample",
        "audio.meter",
    }
)


def _extract_event_type(event: object) -> str:
    if isinstance(event, str):
        return event
    if isinstance(event, dict):
        event_dict = cast(dict[str, object], event)
        raw_type = event_dict.get("event_type")
        return str(raw_type) if raw_type is not None else ""
    raw_attr = getattr(event, "event_type", "")
    return str(raw_attr) if raw_attr is not None else ""


def classify_dispatch_mode(event: object) -> EventDispatchMode:
    event_type = _extract_event_type(event)
    if not event_type:
        return EventDispatchMode.ORDERED

    if event_type in _PREEMPTIVE_EXACT_TYPES or event_type.startswith("system."):
        return EventDispatchMode.PREEMPTIVE
    if (
        event_type.endswith(".cancelled")
        or event_type.endswith(".interrupted")
        or event_type.endswith(".interruption_requested")
    ):
        return EventDispatchMode.PREEMPTIVE

    return EventDispatchMode.ORDERED


def classify_retention(event: object) -> EventRetentionClass:
    event_type = _extract_event_type(event)
    if not event_type:
        return EventRetentionClass.DOMAIN

    if event_type in _CRITICAL_RETENTION_TYPES or event_type.startswith("system."):
        return EventRetentionClass.CRITICAL
    if (
        event_type.endswith(".cancelled")
        or event_type.endswith(".interrupted")
        or event_type.endswith(".interruption_requested")
        or event_type.endswith(".failed")
        or event_type.endswith(".completed")
        or event_type.endswith(".confirmation_requested")
        or event_type.endswith(".confirmation_decided")
        or event_type.endswith(".permission_requested")
        or event_type.endswith(".permission_decided")
    ):
        return EventRetentionClass.CRITICAL

    if event_type in _EPHEMERAL_RETENTION_TYPES or event_type.startswith("telemetry."):
        return EventRetentionClass.EPHEMERAL
    if (
        event_type.endswith(".progress")
        or event_type.endswith(".delta")
        or event_type.endswith(".meter")
    ):
        return EventRetentionClass.EPHEMERAL

    return EventRetentionClass.DOMAIN


def classify_event(event: object) -> EventDeliveryClass:
    retention = classify_retention(event)
    if retention == EventRetentionClass.CRITICAL:
        return EventDeliveryClass.CONTROL
    if retention == EventRetentionClass.EPHEMERAL:
        return EventDeliveryClass.TELEMETRY
    return EventDeliveryClass.DOMAIN


@dataclass(eq=False)
class EventSubscription:
    queue_size: int
    event_filter: EventFilter | None = None
    dropped_events: int = 0
    _queue: asyncio.PriorityQueue[tuple[int, int, int, RuntimeEvent]] = field(init=False)
    _seq: int = field(init=False, default=0)
    _closed: bool = False

    def __post_init__(self) -> None:
        self._queue = asyncio.PriorityQueue(maxsize=self.queue_size)
        self._seq = 0

    def offer(self, event: RuntimeEvent) -> None:
        if self._closed or (self.event_filter and not self.event_filter(event)):
            return

        dispatch_mode = classify_dispatch_mode(event)
        dispatch_prio = _DISPATCH_PRIO[dispatch_mode]
        retention = classify_retention(event)
        retention_weight = _RETENTION_WEIGHT[retention]

        if not self._queue.full():
            self._seq += 1
            self._queue.put_nowait((dispatch_prio, self._seq, retention_weight, event))
            return

        # Queue is full, handle laned retention-aware eviction
        items: list[tuple[int, int, int, RuntimeEvent]] | None = getattr(
            self._queue, "_queue", None
        )
        if items is None:
            return

        if retention == EventRetentionClass.EPHEMERAL:
            # Ephemeral events are discarded without displacing existing events
            self.dropped_events += 1
            return

        victim_idx: int | None = None
        if retention == EventRetentionClass.DOMAIN:
            # Try to evict oldest ephemeral event (weight 0)
            for i, (_, s, w, _) in enumerate(items):
                if w == 0:
                    if victim_idx is None or s < items[victim_idx][1]:
                        victim_idx = i
            # If no ephemeral, evict oldest domain event (weight 1)
            if victim_idx is None:
                for i, (_, s, w, _) in enumerate(items):
                    if w == 1:
                        if victim_idx is None or s < items[victim_idx][1]:
                            victim_idx = i
        elif retention == EventRetentionClass.CRITICAL:
            # Try finding oldest ephemeral first (weight 0)
            for i, (_, s, w, _) in enumerate(items):
                if w == 0:
                    if victim_idx is None or s < items[victim_idx][1]:
                        victim_idx = i
            # If no ephemeral, find oldest domain event (weight 1)
            if victim_idx is None:
                for i, (_, s, w, _) in enumerate(items):
                    if w == 1:
                        if victim_idx is None or s < items[victim_idx][1]:
                            victim_idx = i
            # If all are critical, evict oldest critical event (weight 2)
            if victim_idx is None:
                for i, (_, s, w, _) in enumerate(items):
                    if w == 2:
                        if victim_idx is None or s < items[victim_idx][1]:
                            victim_idx = i

        if victim_idx is not None:
            del items[victim_idx]
            heapq.heapify(items)
            self._seq += 1
            self._queue.put_nowait((dispatch_prio, self._seq, retention_weight, event))
            self.dropped_events += 1
            return

        # Cannot evict higher priority
        self.dropped_events += 1

    async def receive(self) -> RuntimeEvent:
        _, _, _, event = await self._queue.get()
        return event

    def close(self) -> None:
        self._closed = True


class EventHub:
    def __init__(self, default_queue_size: int = 128) -> None:
        self._default_queue_size = default_queue_size
        self._subscriptions: set[EventSubscription] = set()
        self._closed = False

    def subscribe(
        self, event_filter: EventFilter | None = None, *, queue_size: int | None = None
    ) -> EventSubscription:
        if self._closed:
            raise RuntimeError("event hub is closed")
        subscription = EventSubscription(queue_size or self._default_queue_size, event_filter)
        self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: EventSubscription) -> None:
        subscription.close()
        self._subscriptions.discard(subscription)

    async def publish(self, event: RuntimeEvent) -> None:
        if self._closed:
            return
        for subscription in tuple(self._subscriptions):
            subscription.offer(event)

    async def close(self) -> None:
        self._closed = True
        for subscription in tuple(self._subscriptions):
            subscription.close()
        self._subscriptions.clear()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    @property
    def dropped_events(self) -> int:
        return sum(subscription.dropped_events for subscription in self._subscriptions)
