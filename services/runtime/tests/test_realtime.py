# pyright: reportPrivateUsage=false
"""Realtime media boundary tests that do not require a microphone."""

import asyncio
import json
from unittest.mock import MagicMock
from uuid import uuid4

import httpx2
import pytest
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.realtime.contracts import SttRequest, VoiceTurnIdentity
from chatwaifu_runtime.realtime.pipecat.processor import (
    UtteranceBuffer,
    VoiceDomainBridgeProcessor,
    build_playback_marker,
)
from chatwaifu_runtime.realtime.stt import FasterWhisperWorkerSttBackend
from fastapi.testclient import TestClient
from pipecat.frames.frames import Frame, InterruptionFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection


def test_utterance_buffer_keeps_preroll_and_bounds_recording() -> None:
    buffer = UtteranceBuffer(
        sample_rate=1000,
        channels=1,
        pre_roll_ms=100,
        max_seconds=1,
    )
    buffer.push(b"a" * 120)
    buffer.push(b"b" * 120)
    buffer.start()
    buffer.push(b"c" * 2_000)

    audio = buffer.finish()

    assert audio.startswith(b"b")
    assert len(audio) == 2_000


def test_webrtc_offer_requires_an_existing_session(client: TestClient) -> None:
    response = client.post(
        "/v1/sessions/00000000-0000-4000-8000-000000000099/webrtc/offer",
        json={"sdp": "not-used-for-an-unknown-session", "type": "offer"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"


def test_webrtc_playback_marker_preserves_registered_segment_identity() -> None:
    generation_id = uuid4()
    stream_id = uuid4()
    segment_id = uuid4()

    marker = build_playback_marker(
        {
            "stream_id": str(stream_id),
            "segment_id": str(segment_id),
            "duration_ms": 1640,
        },
        generation_id,
        "started",
    )

    assert marker == {
        "type": "chatwaifu.playback_segment",
        "schema_version": "1.0",
        "phase": "started",
        "generation_id": str(generation_id),
        "stream_id": str(stream_id),
        "segment_id": str(segment_id),
        "duration_ms": 1640,
    }


@pytest.mark.asyncio
async def test_stt_worker_adapter_preserves_generation_identity_and_auth() -> None:
    identity = VoiceTurnIdentity(
        session_id=uuid4(),
        utterance_id=uuid4(),
        audio_stream_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
    )

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer secret-token"
        if request.url.path.endswith("/cancel"):
            assert request.url.path.endswith(f"/{identity.generation_id}/cancel")
            return httpx2.Response(200, json={"cancelled": True})
        body = json.loads(request.content)
        assert body["generation_id"] == str(identity.generation_id)
        return httpx2.Response(
            200,
            json={
                "schema_version": "1.0",
                "request_id": body["request_id"],
                "session_id": body["session_id"],
                "turn_id": body["turn_id"],
                "generation_id": body["generation_id"],
                "job_id": body["job_id"],
                "text": "真实语音输入",
                "language": "zh",
                "confidence": None,
                "duration_ms": 20,
                "provider": "faster-whisper",
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    backend = FasterWhisperWorkerSttBackend(
        base_url="http://worker.local",
        token="secret-token",
        timeout_seconds=5,
        client=client,
    )
    try:
        result = await backend.transcribe(
            SttRequest(
                identity=identity,
                audio=b"\x00\x00" * 320,
                sample_rate=16_000,
                channels=1,
                language="zh",
            )
        )
        await backend.cancel(identity.generation_id)
    finally:
        await backend.close()

    assert result is not None
    assert result.text == "真实语音输入"
    assert result.provider == "faster-whisper"


@pytest.mark.asyncio
async def test_stt_worker_adapter_rejects_a_stale_generation_result() -> None:
    identity = VoiceTurnIdentity(
        session_id=uuid4(),
        utterance_id=uuid4(),
        audio_stream_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
    )

    async def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        body.update(
            {
                "generation_id": str(uuid4()),
                "text": "过期结果",
                "language": "zh",
                "confidence": None,
                "duration_ms": 20,
                "provider": "faster-whisper",
            }
        )
        body.pop("audio_base64")
        body.pop("sample_rate")
        body.pop("channels")
        return httpx2.Response(200, json=body)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    backend = FasterWhisperWorkerSttBackend(
        base_url="http://worker.local",
        token="secret-token",
        timeout_seconds=5,
        client=client,
    )
    try:
        with pytest.raises(RuntimeError, match="mismatched generation_id"):
            await backend.transcribe(
                SttRequest(
                    identity=identity,
                    audio=b"\x00\x00" * 320,
                    sample_rate=16_000,
                    channels=1,
                    language="zh",
                )
            )
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_pipecat_processor_cancellation_tombstone_prevents_late_resurrection() -> None:
    session_id = uuid4()
    generation_id = uuid4()
    hub = EventHub()
    audio_assets = MagicMock()

    processor = VoiceDomainBridgeProcessor(
        session_id=session_id,
        sample_rate=16000,
        channels=1,
        pre_roll_ms=100,
        max_utterance_seconds=10,
        echo_enabled=False,
        publisher=MagicMock(),
        event_hub=hub,
        conversation=MagicMock(),
        audio_assets=audio_assets,
        stt=MagicMock(),
        stt_language=None,
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=MagicMock(),
        activation_mode="always_on",
    )
    pushed_frames: list[Frame] = []

    async def mock_push_frame(
        frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        pushed_frames.append(frame)

    processor.push_frame = mock_push_frame  # type: ignore[assignment]
    processor._subscription = hub.subscribe(
        lambda event: str(event.get("session_id")) == str(session_id),
        queue_size=64,
    )
    task = asyncio.create_task(processor._forward_runtime_audio())

    try:
        # Publish generation_started, audio_chunk_queued, conversation.interrupted.
        # EventHub laned priority dispatch yields conversation.interrupted (priority 0)
        # ahead of generation_started (priority 1) and audio_chunk_queued (priority 1).
        await hub.publish(
            {
                "session_id": session_id,
                "event_type": "assistant.generation_started",
                "generation_id": generation_id,
                "payload": {},
            }
        )
        await hub.publish(
            {
                "session_id": session_id,
                "event_type": "assistant.audio_chunk_queued",
                "generation_id": generation_id,
                "payload": {"asset_id": str(uuid4()), "index": 0, "streamed_live": False},
            }
        )
        await hub.publish(
            {
                "session_id": session_id,
                "event_type": "conversation.interrupted",
                "generation_id": generation_id,
                "payload": {},
            }
        )

        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert generation_id in processor._invalidated_generations
    assert processor._active_output_generation is None
    audio_assets.resolve.assert_not_called()
    assert any(isinstance(f, InterruptionFrame) for f in pushed_frames)
    assert not any(isinstance(f, OutputAudioRawFrame) for f in pushed_frames)


@pytest.mark.asyncio
async def test_voice_domain_bridge_processor_forwarder_filters_unrelated_events() -> None:
    session_id = uuid4()
    other_session_id = uuid4()
    hub = EventHub()

    processor = VoiceDomainBridgeProcessor(
        session_id=session_id,
        sample_rate=16000,
        channels=1,
        pre_roll_ms=100,
        max_utterance_seconds=10,
        echo_enabled=False,
        publisher=MagicMock(),
        event_hub=hub,
        conversation=MagicMock(),
        audio_assets=MagicMock(),
        stt=MagicMock(),
        stt_language=None,
        companion_settings=MagicMock(),
        activity=MagicMock(),
        resource_activity=MagicMock(),
        activation_mode="always_on",
    )

    task_manager = MagicMock()

    def fake_create_task(
        coro: object,
        name: str | None = None,
        *args: object,
        **kwargs: object,
    ) -> asyncio.Task[object]:
        from collections.abc import Coroutine
        from typing import cast
        return asyncio.create_task(cast(Coroutine[object, object, object], coro))

    async def fake_cancel_task(task: asyncio.Task[object], *args: object, **kwargs: object) -> None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    task_manager.create_task = fake_create_task
    task_manager.cancel_task = fake_cancel_task
    processor._task_manager = task_manager

    processor._start_event_forwarder()
    subscription = processor._subscription
    assert subscription is not None

    filter_fn = subscription.event_filter
    assert filter_fn is not None
    assert not filter_fn({"session_id": session_id, "event_type": "assistant.text_delta"})
    assert not filter_fn({"session_id": session_id, "event_type": "conversation.started"})
    assert not filter_fn(
        {"session_id": other_session_id, "event_type": "assistant.generation_started"}
    )
    assert filter_fn({"session_id": session_id, "event_type": "assistant.generation_started"})
    assert filter_fn({"session_id": session_id, "event_type": "assistant.audio_chunk_queued"})
    assert filter_fn({"session_id": session_id, "event_type": "assistant.generation_cancelled"})
    assert filter_fn({"session_id": session_id, "event_type": "conversation.interrupted"})

    await processor.cleanup()
