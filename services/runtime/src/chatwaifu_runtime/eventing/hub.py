"""Non-blocking bounded event hub with laned priority for Runtime consumers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

type RuntimeEvent = dict[str, object]
type EventFilter = Callable[[RuntimeEvent], bool]


class EventDeliveryClass(StrEnum):
    CONTROL = "control"
    DOMAIN = "domain"
    TELEMETRY = "telemetry"


_CONTROL_EXACT_TYPES: frozenset[str] = frozenset(
    {
        "session.interrupted",
        "session.cancelled",
        "session.resumed",
        "session.state_changed",
        "assistant.generation_cancelled",
        "assistant.generation_failed",
        "assistant.generation_completed",
        "assistant.playback_stopped",
        "runtime_skill.permission_requested",
        "runtime_skill.permission_decided",
        "runtime_skill.cancelled",
        "system.error_raised",
    }
)

_TELEMETRY_EXACT_TYPES: frozenset[str] = frozenset(
    {
        "assistant.text_delta",
        "assistant.playback_progress",
        "user.transcript_partial",
        "user.speech_started",
        "user.speech_stopped",
        "avatar.debug_sample",
        "audio.meter",
    }
)


def classify_event(event: object) -> EventDeliveryClass:
    if isinstance(event, str):
        event_type = event
    elif isinstance(event, dict):
        event_dict = cast(dict[str, object], event)
        raw_type = event_dict.get("event_type")
        event_type = str(raw_type) if raw_type is not None else ""
    else:
        raw_attr = getattr(event, "event_type", "")
        event_type = str(raw_attr) if raw_attr is not None else ""

    if not event_type:
        return EventDeliveryClass.DOMAIN

    if event_type in _CONTROL_EXACT_TYPES or event_type.startswith("system."):
        return EventDeliveryClass.CONTROL
    if (
        event_type.endswith(".cancelled")
        or event_type.endswith(".interrupted")
        or event_type.endswith(".failed")
        or event_type.endswith(".permission_requested")
        or event_type.endswith(".permission_decided")
    ):
        return EventDeliveryClass.CONTROL

    if event_type in _TELEMETRY_EXACT_TYPES or event_type.startswith("telemetry."):
        return EventDeliveryClass.TELEMETRY
    if (
        event_type.endswith(".progress")
        or event_type.endswith(".delta")
        or event_type.endswith(".meter")
    ):
        return EventDeliveryClass.TELEMETRY

    return EventDeliveryClass.DOMAIN


@dataclass(eq=False)
class EventSubscription:
    queue_size: int
    event_filter: EventFilter | None = None
    dropped_events: int = 0
    _queue: asyncio.Queue[tuple[EventDeliveryClass, RuntimeEvent]] = field(init=False)
    _closed: bool = False

    def __post_init__(self) -> None:
        self._queue = asyncio.Queue(maxsize=self.queue_size)

    def offer(self, event: RuntimeEvent) -> None:
        if self._closed or (self.event_filter and not self.event_filter(event)):
            return

        delivery_class = classify_event(event)

        if not self._queue.full():
            self._queue.put_nowait((delivery_class, event))
            return

        # Queue is full, handle laned eviction
        items = getattr(self._queue, "_queue", None)
        if items is None:
            return

        if delivery_class == EventDeliveryClass.TELEMETRY:
            # Telemetry is discarded without displacing existing events
            self.dropped_events += 1
            return

        # Try to find a telemetry event to evict
        victim_idx: int | None = None
        for i, (cls, _) in enumerate(items):
            if cls == EventDeliveryClass.TELEMETRY:
                victim_idx = i
                break

        # If no telemetry event, try finding a domain event
        if victim_idx is None:
            for i, (cls, _) in enumerate(items):
                if cls == EventDeliveryClass.DOMAIN:
                    victim_idx = i
                    break

        if victim_idx is not None:
            del items[victim_idx]
            items.append((delivery_class, event))
            self.dropped_events += 1
            return

        # All items are CONTROL
        if delivery_class == EventDeliveryClass.CONTROL:
            # Evict oldest CONTROL event to guarantee freshest control
            items.popleft()
            items.append((delivery_class, event))
            self.dropped_events += 1
        else:
            # Domain event cannot displace control events
            self.dropped_events += 1

    async def receive(self) -> RuntimeEvent:
        _, event = await self._queue.get()
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
