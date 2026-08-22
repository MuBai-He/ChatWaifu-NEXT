"""Persistence, sequence, and outbox tests."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings


@pytest.mark.asyncio
async def test_concurrent_append_assigns_unique_monotonic_sequences(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")

        async def append(index: int) -> int:
            event = await container.event_store.append(
                GenericCoreEvent(
                    event_id=uuid4(),
                    event_type="user.transcript_final",
                    session_id=session.session_id,
                    occurred_at=datetime.now(UTC),
                    source="test",
                    privacy=PrivacyLevel.LOCAL,
                    payload={"text": f"message-{index}"},
                )
            )
            assert event.sequence is not None
            return event.sequence

        sequences = await asyncio.gather(*(append(index) for index in range(20)))
        assert sorted(sequences) == list(range(2, 22))
        stored = await container.event_store.read_stream(session.session_id, limit=100)
        assert [event["sequence"] for event in stored] == list(range(1, 22))
        pending = await container.event_store.pending_outbox(limit=100)
        assert len(pending) == 20
    finally:
        await container.stop()
