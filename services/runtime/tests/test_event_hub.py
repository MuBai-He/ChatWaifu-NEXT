"""Bounded event hub and laned queuing behavior."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.eventing.hub import (
    EventDeliveryClass,
    EventHub,
    classify_event,
)
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore


def test_classify_event() -> None:
    assert classify_event("assistant.text_delta") == EventDeliveryClass.TELEMETRY
    assert classify_event("assistant.playback_progress") == EventDeliveryClass.TELEMETRY
    assert classify_event("user.transcript_partial") == EventDeliveryClass.TELEMETRY
    assert classify_event("telemetry.metric") == EventDeliveryClass.TELEMETRY

    assert classify_event("session.cancelled") == EventDeliveryClass.CONTROL
    assert classify_event("session.interrupted") == EventDeliveryClass.CONTROL
    assert classify_event("assistant.generation_cancelled") == EventDeliveryClass.CONTROL
    assert classify_event("assistant.generation_failed") == EventDeliveryClass.CONTROL
    assert classify_event("assistant.playback_stopped") == EventDeliveryClass.CONTROL
    assert classify_event("runtime_skill.permission_requested") == EventDeliveryClass.CONTROL
    assert classify_event("system.error_raised") == EventDeliveryClass.CONTROL

    assert classify_event("user.turn_committed") == EventDeliveryClass.DOMAIN
    assert classify_event("session.created") == EventDeliveryClass.DOMAIN
    assert classify_event("memory.record_created") == EventDeliveryClass.DOMAIN
    assert classify_event("unknown_custom_event") == EventDeliveryClass.DOMAIN


@pytest.mark.asyncio
async def test_slow_subscriber_drops_oldest_domain_without_blocking_publish() -> None:
    hub = EventHub(default_queue_size=2)
    subscription = hub.subscribe()
    for index in range(5):
        await hub.publish({"index": index})

    assert subscription.dropped_events == 3
    assert await subscription.receive() == {"index": 3}
    assert await subscription.receive() == {"index": 4}
    await hub.close()


@pytest.mark.asyncio
async def test_telemetry_dropped_without_evicting_domain_events() -> None:
    hub = EventHub(default_queue_size=2)
    subscription = hub.subscribe()

    # Fill queue with 2 DOMAIN events
    await hub.publish({"event_type": "user.turn_committed", "id": 1})
    await hub.publish({"event_type": "user.turn_committed", "id": 2})

    # Publish TELEMETRY event - should be discarded because queue is full of DOMAIN events
    await hub.publish({"event_type": "assistant.text_delta", "delta": "hello"})

    assert subscription.dropped_events == 1
    # Domain events are still in queue
    event1 = await subscription.receive()
    event2 = await subscription.receive()
    assert event1["id"] == 1
    assert event2["id"] == 2
    await hub.close()


@pytest.mark.asyncio
async def test_control_events_evict_telemetry_events_under_saturation() -> None:
    hub = EventHub(default_queue_size=3)
    subscription = hub.subscribe()

    # Fill queue with 1 domain + 2 telemetry events
    await hub.publish({"event_type": "user.turn_committed", "id": 1})
    await hub.publish({"event_type": "assistant.text_delta", "id": 2})
    await hub.publish({"event_type": "assistant.text_delta", "id": 3})

    # Publish CONTROL event (e.g. session cancellation)
    await hub.publish({"event_type": "session.cancelled", "id": 4})

    assert subscription.dropped_events == 1
    # Should receive domain 1, telemetry 3 (first telemetry 2 was evicted), and control 4
    event1 = await subscription.receive()
    event2 = await subscription.receive()
    event3 = await subscription.receive()
    assert event1["id"] == 1
    assert event2["id"] == 3
    assert event3["id"] == 4
    await hub.close()


@pytest.mark.asyncio
async def test_domain_events_do_not_evict_control_events() -> None:
    hub = EventHub(default_queue_size=2)
    subscription = hub.subscribe()

    # Fill queue with 2 CONTROL events
    await hub.publish({"event_type": "session.cancelled", "id": 1})
    await hub.publish({"event_type": "assistant.generation_cancelled", "id": 2})

    # Offer a DOMAIN event
    await hub.publish({"event_type": "user.turn_committed", "id": 3})

    assert subscription.dropped_events == 1
    # Control events preserved
    event1 = await subscription.receive()
    event2 = await subscription.receive()
    assert event1["id"] == 1
    assert event2["id"] == 2
    await hub.close()


@pytest.mark.asyncio
async def test_ephemeral_events_bypass_event_store(tmp_path: Path) -> None:
    database = Database(tmp_path / "events.db", StorageConfig(database_path=tmp_path / "events.db"))
    await database.open()
    event_store = EventStore(database)
    hub = EventHub(default_queue_size=32)
    publisher = EventPublisher(event_store, hub)

    sub = hub.subscribe()
    session_id = uuid4()

    async with database.transaction() as connection:
        await connection.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(session_id),
                "ayachi_nene",
                "active",
                "idle",
                0,
                1,
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
            ),
        )

    # Emit persistent domain event
    persistent_event = GenericCoreEvent(
        event_id=uuid4(),
        event_type="assistant.generation_completed",
        session_id=session_id,
        occurred_at=datetime.now(UTC),
        source="test",
        privacy=PrivacyLevel.LOCAL,
        payload={"reason": "completed"},
    )
    await publisher.emit(persistent_event)

    # Publish ephemeral text delta
    ephemeral_event = GenericCoreEvent(
        event_id=uuid4(),
        event_type="assistant.text_delta",
        session_id=session_id,
        occurred_at=datetime.now(UTC),
        source="test",
        privacy=PrivacyLevel.LOCAL,
        payload={"text": "chunk"},
    )
    await publisher.publish_ephemeral(ephemeral_event)

    # Hub subscriber received both
    recv1 = await sub.receive()
    recv2 = await sub.receive()
    assert recv1["event_type"] == "assistant.generation_completed"
    assert recv2["event_type"] == "assistant.text_delta"

    # But EventStore / SQLite only has the persistent domain event!
    stream = await event_store.read_stream(session_id, limit=100)
    assert len(stream) == 1
    assert stream[0]["event_type"] == "assistant.generation_completed"

    await hub.close()
    await database.close()
