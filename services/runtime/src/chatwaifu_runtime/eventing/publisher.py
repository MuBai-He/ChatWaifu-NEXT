"""Persist-then-publish event delivery helper."""

from chatwaifu_protocol.events import EventModel

from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.persistence.event_store import EventStore


class EventPublisher:
    def __init__(self, event_store: EventStore, event_hub: EventHub) -> None:
        self._event_store = event_store
        self._event_hub = event_hub

    async def emit[EventT: EventModel](self, event: EventT) -> EventT:
        persisted = await self._event_store.append(event)
        await self.publish_persisted(persisted)
        return persisted

    async def publish_persisted(self, event: EventModel) -> None:
        await self._event_hub.publish(event.model_dump(mode="json"))
        await self._event_store.mark_published(event.event_id)

    async def publish_ephemeral(self, event: EventModel) -> None:
        """Publish an ephemeral telemetry event to EventHub without persisting to SQLite."""
        await self._event_hub.publish(event.model_dump(mode="json"))
