"""Ephemeral, bounded PCM stream fan-out for browser and WebRTC consumers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AudioStreamPacket:
    phase: Literal["started", "chunk", "completed", "cancelled"]
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    stream_id: UUID
    segment_id: UUID
    segment_index: int
    text: str
    sequence: int = 0
    sample_rate: int = 24_000
    channels: int = 1
    native_streaming: bool = False
    pcm16: bytes = b""
    duration_ms: int = 0
    provider_id: str = ""
    model: str = ""
    reason: str | None = None


@dataclass(eq=False)
class AudioStreamSubscription:
    session_id: UUID
    queue_size: int
    dropped: bool = False
    _queue: asyncio.Queue[AudioStreamPacket] = field(init=False)
    _closed: bool = False

    def __post_init__(self) -> None:
        self._queue = asyncio.Queue(maxsize=self.queue_size)

    def offer(self, packet: AudioStreamPacket) -> bool:
        if self._closed or self.dropped or packet.session_id != self.session_id:
            return False
        if self._queue.full():
            self.dropped = True
            while not self._queue.empty():
                self._queue.get_nowait()
            self._queue.put_nowait(
                AudioStreamPacket(
                    phase="cancelled",
                    session_id=packet.session_id,
                    turn_id=packet.turn_id,
                    generation_id=packet.generation_id,
                    stream_id=packet.stream_id,
                    segment_id=packet.segment_id,
                    segment_index=packet.segment_index,
                    text=packet.text,
                    native_streaming=packet.native_streaming,
                    reason="stream_backpressure_overflow",
                )
            )
            return False
        self._queue.put_nowait(packet)
        return True

    async def receive(self) -> AudioStreamPacket:
        return await self._queue.get()

    def close(self) -> None:
        self._closed = True


class AudioStreamHub:
    def __init__(self, queue_size: int = 64) -> None:
        self._queue_size = queue_size
        self._subscriptions: set[AudioStreamSubscription] = set()
        self._closed = False

    def subscribe(self, session_id: UUID) -> AudioStreamSubscription:
        if self._closed:
            raise RuntimeError("audio stream hub is closed")
        subscription = AudioStreamSubscription(session_id, self._queue_size)
        self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: AudioStreamSubscription) -> None:
        subscription.close()
        self._subscriptions.discard(subscription)

    async def publish(self, packet: AudioStreamPacket) -> int:
        if self._closed:
            return 0
        return sum(subscription.offer(packet) for subscription in tuple(self._subscriptions))

    async def close(self) -> None:
        self._closed = True
        for subscription in tuple(self._subscriptions):
            subscription.close()
        self._subscriptions.clear()
