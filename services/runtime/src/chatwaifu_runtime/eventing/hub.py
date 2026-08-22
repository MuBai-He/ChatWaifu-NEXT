"""Non-blocking bounded event hub for Runtime consumers."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

type RuntimeEvent = dict[str, object]
type EventFilter = Callable[[RuntimeEvent], bool]


@dataclass(eq=False)
class EventSubscription:
    queue_size: int
    event_filter: EventFilter | None = None
    dropped_events: int = 0
    _queue: asyncio.Queue[RuntimeEvent] = field(init=False)
    _closed: bool = False

    def __post_init__(self) -> None:
        self._queue = asyncio.Queue(maxsize=self.queue_size)

    def offer(self, event: RuntimeEvent) -> None:
        if self._closed or (self.event_filter and not self.event_filter(event)):
            return
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped_events += 1
        self._queue.put_nowait(event)

    async def receive(self) -> RuntimeEvent:
        return await self._queue.get()

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
