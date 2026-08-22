"""Bounded event hub behavior."""

import pytest
from chatwaifu_runtime.eventing.hub import EventHub


@pytest.mark.asyncio
async def test_slow_subscriber_drops_oldest_without_blocking_publish() -> None:
    hub = EventHub(default_queue_size=2)
    subscription = hub.subscribe()
    for index in range(5):
        await hub.publish({"index": index})

    assert subscription.dropped_events == 3
    assert await subscription.receive() == {"index": 3}
    assert await subscription.receive() == {"index": 4}
    await hub.close()
