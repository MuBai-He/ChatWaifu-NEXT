# pyright: reportPrivateUsage=false
"""Realtime media boundary tests that do not require a microphone."""

import asyncio
import json
from collections.abc import Coroutine
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx2
import pytest
from chatwaifu_protocol.events import GenericCoreEvent, UserTranscriptFinalEvent
from chatwaifu_runtime.companion.models import CompanionSettings
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.realtime.contracts import SttRequest, SttResult, VoiceTurnIdentity
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


class _VoiceBridgeTestHarness:
    """Deterministic test harness for VoiceDomainBridgeProcessor transcribe boundaries."""

    def __init__(
        self,
        *,
        activation_mode: str = "open_mic",
        wake_phrase_enabled: bool = True,
        wake_phrases: tuple[str, ...] = ("宁宁", "绫地宁宁"),
    ) -> None:
        self.session_id = uuid4()
        self.identity = VoiceTurnIdentity(
            session_id=self.session_id,
            utterance_id=uuid4(),
            audio_stream_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
        )
        self.settings = CompanionSettings(
            wake_phrase_enabled=wake_phrase_enabled,
            wake_phrases=wake_phrases,
        )
        self.companion_settings = MagicMock()
        self.companion_settings.get.return_value = self.settings

        self.emitted_events: list[object] = []

        async def fake_emit(event: object) -> None:
            self.emitted_events.append(event)

        self.publisher = MagicMock()
        self.publisher.emit = AsyncMock(side_effect=fake_emit)

        self.conversation = MagicMock()
        self.conversation.submit_voice_transcript = AsyncMock()
        self.conversation.cancel = AsyncMock()

        self.activity = MagicMock()
        self.stt = MagicMock()
        self.stt.kind = "mock-stt"
        self.stt.transcribe = AsyncMock()
        self.stt.cancel = AsyncMock()

        self.pushed_frames: list[Frame] = []

        async def fake_push_frame(
            frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
        ) -> None:
            self.pushed_frames.append(frame)

        self.created_tasks: list[asyncio.Task[object]] = []
        task_manager = MagicMock()

        def fake_create_task(
            coro: object,
            name: str | None = None,
            *args: object,
            **kwargs: object,
        ) -> asyncio.Task[object]:
            task = asyncio.create_task(cast(Coroutine[object, object, object], coro))
            self.created_tasks.append(task)
            return task

        async def fake_cancel_task(
            task: asyncio.Task[object], *args: object, **kwargs: object
        ) -> None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        task_manager.create_task = fake_create_task
        task_manager.cancel_task = fake_cancel_task

        self.processor = VoiceDomainBridgeProcessor(
            session_id=self.session_id,
            sample_rate=16000,
            channels=1,
            pre_roll_ms=100,
            max_utterance_seconds=10,
            echo_enabled=False,
            publisher=self.publisher,
            event_hub=EventHub(),
            conversation=self.conversation,
            audio_assets=MagicMock(),
            stt=self.stt,
            stt_language=None,
            companion_settings=self.companion_settings,
            activity=self.activity,
            resource_activity=MagicMock(),
            activation_mode=activation_mode,
        )
        self.processor._identity = self.identity
        self.processor._task_manager = task_manager
        self.processor.push_frame = fake_push_frame  # type: ignore[assignment]

    async def drain_tasks(self) -> None:
        if self.created_tasks:
            await asyncio.gather(*self.created_tasks)


@pytest.mark.asyncio
async def test_voice_domain_bridge_transcribe_open_mic_unaddressed_speech_remains_silent() -> None:
    harness = _VoiceBridgeTestHarness(activation_mode="open_mic")
    unaddressed_text = "我刚才和朋友聊起宁宁的事情"
    harness.stt.transcribe.return_value = SttResult(
        text=unaddressed_text,
        language="zh",
        provider="mock-stt",
    )

    await harness.processor._transcribe(harness.identity, b"audio-payload")
    await harness.drain_tasks()

    # 1. No voice transcript submitted to conversation
    harness.conversation.submit_voice_transcript.assert_not_called()
    # 2. No barge-in interruption or cancellation triggered
    harness.conversation.cancel.assert_not_called()
    assert not any(isinstance(f, InterruptionFrame) for f in harness.pushed_frames)
    # 3. No UserTranscriptFinalEvent emitted
    assert not any(isinstance(e, UserTranscriptFinalEvent) for e in harness.emitted_events)
    # 4. Activity tracker untouched on ignored speech
    harness.activity.touch.assert_not_called()

    # 5. Exactly one ignored reason event emitted with privacy preservation
    ignored_events = [
        e
        for e in harness.emitted_events
        if isinstance(e, GenericCoreEvent) and e.event_type == "voice.utterance_ignored"
    ]
    assert len(ignored_events) == 1
    event = ignored_events[0]
    assert event.session_id == harness.identity.session_id
    assert event.turn_id == harness.identity.turn_id
    assert event.generation_id == harness.identity.generation_id
    assert event.source == "runtime.companion.attention"
    assert event.payload == {
        "reason": "not_addressed",
        "wake_phrase": None,
    }
    # Unaddressed speech content must not be exposed in metadata
    assert unaddressed_text not in json.dumps(event.payload, ensure_ascii=False)
    assert "text" not in event.payload


@pytest.mark.asyncio
async def test_voice_domain_bridge_transcribe_open_mic_wake_without_followup_ignored() -> None:
    harness = _VoiceBridgeTestHarness(activation_mode="open_mic")
    harness.stt.transcribe.return_value = SttResult(
        text="宁宁",
        language="zh",
        provider="mock-stt",
    )

    await harness.processor._transcribe(harness.identity, b"audio-payload")
    await harness.drain_tasks()

    harness.conversation.submit_voice_transcript.assert_not_called()
    harness.conversation.cancel.assert_not_called()
    assert not any(isinstance(e, UserTranscriptFinalEvent) for e in harness.emitted_events)
    harness.activity.touch.assert_not_called()

    ignored_events = [
        e
        for e in harness.emitted_events
        if isinstance(e, GenericCoreEvent) and e.event_type == "voice.utterance_ignored"
    ]
    assert len(ignored_events) == 1
    assert ignored_events[0].payload == {
        "reason": "empty",
        "wake_phrase": "宁宁",
    }


@pytest.mark.asyncio
async def test_voice_domain_bridge_transcribe_open_mic_wake_phrase_submits_stripped_text() -> None:
    harness = _VoiceBridgeTestHarness(activation_mode="open_mic")
    harness.stt.transcribe.return_value = SttResult(
        text="宁宁，今天天气怎么样？",
        language="zh",
        provider="mock-stt",
    )

    await harness.processor._transcribe(harness.identity, b"audio-payload")
    await harness.drain_tasks()

    # 1. InterruptionFrame pushed downstream and conversation barge-in cancelled
    assert any(isinstance(f, InterruptionFrame) for f in harness.pushed_frames)
    harness.conversation.cancel.assert_awaited_once_with(harness.identity.session_id, "barge_in")

    # 2. Companion wake_detected event emitted with the detected wake phrase
    wake_events = [
        e
        for e in harness.emitted_events
        if isinstance(e, GenericCoreEvent) and e.event_type == "voice.wake_detected"
    ]
    assert len(wake_events) == 1
    assert wake_events[0].session_id == harness.identity.session_id
    assert wake_events[0].turn_id == harness.identity.turn_id
    assert wake_events[0].generation_id == harness.identity.generation_id
    assert wake_events[0].payload == {"wake_phrase": "宁宁"}

    # 3. No ignored event emitted
    assert not any(
        isinstance(e, GenericCoreEvent) and e.event_type == "voice.utterance_ignored"
        for e in harness.emitted_events
    )

    # 4. Activity touched
    harness.activity.touch.assert_called_once_with(harness.identity.session_id)

    # 5. UserTranscriptFinalEvent emitted with stripped text
    expected_stripped_text = "今天天气怎么样？"
    transcript_events = [
        e for e in harness.emitted_events if isinstance(e, UserTranscriptFinalEvent)
    ]
    assert len(transcript_events) == 1
    transcript_event = transcript_events[0]
    assert transcript_event.session_id == harness.identity.session_id
    assert transcript_event.turn_id == harness.identity.turn_id
    assert transcript_event.generation_id == harness.identity.generation_id
    assert transcript_event.payload.text == expected_stripped_text
    assert transcript_event.payload.utterance_id == harness.identity.utterance_id
    assert transcript_event.payload.is_final is True
    assert transcript_event.payload.language == "zh"
    assert transcript_event.payload.provider == "mock-stt"

    # 6. Stripped text submitted to conversation
    harness.conversation.submit_voice_transcript.assert_awaited_once_with(
        harness.identity.session_id,
        expected_stripped_text,
        turn_id=harness.identity.turn_id,
        generation_id=harness.identity.generation_id,
    )


@pytest.mark.asyncio
async def test_voice_domain_bridge_transcribe_push_to_talk_accepts_direct_question() -> None:
    harness = _VoiceBridgeTestHarness(activation_mode="push_to_talk")
    direct_question = "今天北京天气怎么样？"
    harness.stt.transcribe.return_value = SttResult(
        text=direct_question,
        language="zh",
        provider="mock-stt",
    )

    await harness.processor._transcribe(harness.identity, b"audio-payload")
    await harness.drain_tasks()

    # 1. In push-to-talk, transcribe does not trigger wake detection or barge-in interruption
    assert not any(isinstance(f, InterruptionFrame) for f in harness.pushed_frames)
    harness.conversation.cancel.assert_not_called()
    assert not any(
        isinstance(e, GenericCoreEvent)
        and e.event_type in {"voice.wake_detected", "voice.utterance_ignored"}
        for e in harness.emitted_events
    )

    # 2. Activity tracker updated
    harness.activity.touch.assert_called_once_with(harness.identity.session_id)

    # 3. UserTranscriptFinalEvent emitted with unmodified full text
    transcript_events = [
        e for e in harness.emitted_events if isinstance(e, UserTranscriptFinalEvent)
    ]
    assert len(transcript_events) == 1
    transcript_event = transcript_events[0]
    assert transcript_event.session_id == harness.identity.session_id
    assert transcript_event.turn_id == harness.identity.turn_id
    assert transcript_event.generation_id == harness.identity.generation_id
    assert transcript_event.payload.text == direct_question
    assert transcript_event.payload.utterance_id == harness.identity.utterance_id
    assert transcript_event.payload.is_final is True

    # 4. Direct question submitted to conversation
    harness.conversation.submit_voice_transcript.assert_awaited_once_with(
        harness.identity.session_id,
        direct_question,
        turn_id=harness.identity.turn_id,
        generation_id=harness.identity.generation_id,
    )


@pytest.mark.asyncio
async def test_voice_domain_bridge_transcribe_stale_identity_silently_drops_result() -> None:
    harness = _VoiceBridgeTestHarness(activation_mode="open_mic")
    stale_identity = harness.identity
    transcribe_started = asyncio.Event()
    release_transcription = asyncio.Event()

    async def delayed_result(_request: SttRequest) -> SttResult:
        transcribe_started.set()
        await release_transcription.wait()
        return SttResult(
            text="宁宁，这条语音迟到了",
            language="zh",
            provider="mock-stt",
        )

    harness.stt.transcribe.side_effect = delayed_result
    task = asyncio.create_task(harness.processor._transcribe(stale_identity, b"audio-payload"))
    await transcribe_started.wait()
    superseding_identity = VoiceTurnIdentity(
        session_id=harness.session_id,
        utterance_id=uuid4(),
        audio_stream_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
    )
    # Processor identity advances while STT is still in flight.
    harness.processor._identity = superseding_identity
    release_transcription.set()
    await task
    await harness.drain_tasks()

    # Outdated turn result must be silently discarded: no events, no submission, no cancel
    assert len(harness.emitted_events) == 0
    harness.conversation.submit_voice_transcript.assert_not_called()
    harness.conversation.cancel.assert_not_called()
    harness.activity.touch.assert_not_called()
    assert len(harness.pushed_frames) == 0


@pytest.mark.asyncio
async def test_voice_domain_bridge_transcribe_cancellation_propagates_without_error_event() -> None:
    harness = _VoiceBridgeTestHarness(activation_mode="open_mic")
    harness.stt.transcribe.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await harness.processor._transcribe(harness.identity, b"audio-payload")

    await harness.drain_tasks()

    # CancelledError must not be swallowed or reported as an STT error event
    assert not any(
        isinstance(e, GenericCoreEvent) and e.event_type == "error.raised"
        for e in harness.emitted_events
    )
    assert len(harness.emitted_events) == 0
    harness.conversation.submit_voice_transcript.assert_not_called()
    harness.conversation.cancel.assert_not_called()
    assert harness.processor._stt_task is None
