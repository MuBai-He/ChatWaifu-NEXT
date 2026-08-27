"""Bounded ephemeral audio fan-out tests."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.audio.streaming import AudioStreamHub, AudioStreamPacket


def _packet(*, sequence: int, session_id: UUID | None = None) -> AudioStreamPacket:
    resolved_session = session_id or uuid4()
    return AudioStreamPacket(
        phase="chunk",
        session_id=resolved_session,
        turn_id=uuid4(),
        generation_id=uuid4(),
        stream_id=uuid4(),
        segment_id=uuid4(),
        segment_index=0,
        text="测试",
        sequence=sequence,
        pcm16=b"\x00\x00",
    )


@pytest.mark.asyncio
async def test_audio_stream_hub_is_session_scoped_and_bounded() -> None:
    session_id = uuid4()
    other_session = uuid4()
    hub = AudioStreamHub(queue_size=1)
    subscription = hub.subscribe(session_id)

    assert await hub.publish(_packet(sequence=0, session_id=other_session)) == 0
    first = _packet(sequence=0, session_id=session_id)
    second = replace(first, sequence=1)
    assert await hub.publish(first) == 1
    assert await hub.publish(second) == 0
    overflow = await subscription.receive()
    assert overflow.phase == "cancelled"
    assert overflow.reason == "stream_backpressure_overflow"
    await hub.close()
