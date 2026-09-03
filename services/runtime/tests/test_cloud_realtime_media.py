"""Tests for Pipecat Fake Cloud Media Bridge (Phase 13.2).

Validates:
1. Fake Provider Script execution: input PCM -> user transcript -> response started
   -> assistant transcript -> 2 output PCM chunks -> response completed;
2. Correct sample rate, PTS progression, and frame ordering;
3. Barge-in causal sequence: invalidation -> interrupt -> InterruptionFrame -> late drop;
4. Late output audio frames are dropped by tombstone fence;
5. Bounded input audio queue with oldest-frame drop backpressure;
6. WebRTC pipeline teardown closes underlying CloudRealtimeSession;
7. Domain sink and event store receive no raw PCM bytes;
8. PipecatMediaAdapter connection_mode switching (cascade vs cloud_realtime).
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from chatwaifu_runtime.config.settings import RealtimeConfig, SttConfig
from chatwaifu_runtime.realtime.admission import InMemoryTurnAdmission
from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    OutputAudioEvent,
    RealtimeOutputAudioFrame,
    RealtimeSessionOpenRequest,
    RealtimeTranscriptCandidate,
    ResponseCompletedEvent,
    ResponseStartedEvent,
    UserTranscriptEvent,
)
from chatwaifu_runtime.realtime.cloud.coordinator import (
    InMemoryDomainSink,
)
from chatwaifu_runtime.realtime.cloud.fake import FakeCloudRealtimeSession
from chatwaifu_runtime.realtime.cloud.media import CloudRealtimeMediaBridge
from chatwaifu_runtime.realtime.pipecat.session import PipecatMediaAdapter
from pipecat.frames.frames import (
    CancelFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection


class FrameCollector:
    """Downstream collector for testing Pipecat frames emitted by the bridge."""

    def __init__(self) -> None:
        self.frames: list[Frame] = []

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        self.frames.append(frame)


@pytest.mark.asyncio
async def test_fake_provider_script_execution() -> None:
    """7.3 Test Harness:

    Input PCM -> user transcript final -> response started
    -> assistant transcript delta/final -> 2 output PCM chunks -> response completed.
    """
    session_id = uuid4()
    admission = InMemoryTurnAdmission()

    request = RealtimeSessionOpenRequest(
        session_id=session_id,
        character_id="ayachi_nene",
    )
    fake_session = FakeCloudRealtimeSession(request, auto_ready=True)
    domain_sink = InMemoryDomainSink()
    bridge = CloudRealtimeMediaBridge.create(
        session_id=session_id,
        backend_id=fake_session.backend_id,
        session=fake_session,
        admission=admission,
        domain_sink=domain_sink,
        sample_rate=16_000,
        channels=1,
    )

    collector = FrameCollector()
    bridge.push_frame = collector.push_frame  # type: ignore[assignment]

    try:
        # 1. Start pipeline
        await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

        # 2. User starts speaking -> admitted
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert bridge.current_identity is not None
        gen_id = bridge.current_identity.generation_id

        # Feed 3 input PCM frames (16kHz, 16-bit mono -> 320 bytes = 10ms per frame)
        pcm_chunk_1 = b"\x01\x00" * 160
        pcm_chunk_2 = b"\x02\x00" * 160
        pcm_chunk_3 = b"\x03\x00" * 160

        await bridge.process_frame(
            InputAudioRawFrame(audio=pcm_chunk_1, sample_rate=16_000, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
        await bridge.process_frame(
            InputAudioRawFrame(audio=pcm_chunk_2, sample_rate=16_000, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
        await bridge.process_frame(
            InputAudioRawFrame(audio=pcm_chunk_3, sample_rate=16_000, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )

        # 3. User stops speaking
        await bridge.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

        # Allow send loop to push input frames to fake session
        await asyncio.sleep(0.02)

        assert len(fake_session.sent_audio_frames) == 3
        assert fake_session.sent_audio_frames[0].sequence == 1
        assert fake_session.sent_audio_frames[0].pts_ms == 0
        assert fake_session.sent_audio_frames[0].audio == pcm_chunk_1
        assert fake_session.sent_audio_frames[1].sequence == 2
        assert fake_session.sent_audio_frames[1].pts_ms == 10
        assert fake_session.sent_audio_frames[2].sequence == 3
        assert fake_session.sent_audio_frames[2].pts_ms == 20
        assert fake_session.commit_calls == 1

        # 4. Provider script execution
        # (a) Emit user transcript final. The adapter must attach the Runtime
        # generation identity; identity-less provider events are dropped.
        await bridge.coordinator.dispatch_event(
            UserTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=session_id,
                    generation_id=gen_id,
                    role="user",
                    phase="final",
                    text="こんにちは、寧々ちゃん",
                    source="provider",
                )
            )
        )

        # (b) Emit response started
        await bridge.coordinator.dispatch_event(
            ResponseStartedEvent(
                session_id=session_id,
                generation_id=gen_id,
                provider_response_id="resp_harness_1",
            )
        )

        # (c) Emit assistant transcript delta & final
        await bridge.coordinator.dispatch_event(
            AssistantTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=session_id,
                    generation_id=gen_id,
                    role="assistant",
                    phase="delta",
                    text="はい、こんにちは！",
                    source="provider",
                )
            )
        )

        # (d) Emit two output PCM chunks
        out_pcm_1 = b"\x10\x00" * 240  # 24kHz
        out_pcm_2 = b"\x20\x00" * 240
        await bridge.coordinator.dispatch_event(
            OutputAudioEvent(
                frame=RealtimeOutputAudioFrame(
                    session_id=session_id,
                    generation_id=gen_id,
                    sequence=1,
                    pts_ms=0,
                    sample_rate=24_000,
                    channels=1,
                    audio=out_pcm_1,
                    is_final=False,
                )
            )
        )
        await bridge.coordinator.dispatch_event(
            OutputAudioEvent(
                frame=RealtimeOutputAudioFrame(
                    session_id=session_id,
                    generation_id=gen_id,
                    sequence=2,
                    pts_ms=10,
                    sample_rate=24_000,
                    channels=1,
                    audio=out_pcm_2,
                    is_final=True,
                )
            )
        )

        # (e) Emit response completed
        await bridge.coordinator.dispatch_event(
            ResponseCompletedEvent(
                session_id=session_id,
                generation_id=gen_id,
                provider_response_id="resp_harness_1",
            )
        )

        # 5. Verify outputs downstream
        output_audio_frames = [f for f in collector.frames if isinstance(f, OutputAudioRawFrame)]
        assert len(output_audio_frames) == 2
        assert output_audio_frames[0].audio == out_pcm_1
        assert output_audio_frames[0].sample_rate == 24_000
        assert output_audio_frames[1].audio == out_pcm_2
        assert output_audio_frames[1].sample_rate == 24_000

        # TTSStoppedFrame emitted on final audio chunk
        tts_stopped_frames = [f for f in collector.frames if isinstance(f, TTSStoppedFrame)]
        assert len(tts_stopped_frames) == 1

        # Domain sink received normalized events
        user_finals = [t for t in domain_sink.transcript_finals if t[4] == "user"]
        assert len(user_finals) == 1
        assert user_finals[0][3] == "こんにちは、寧々ちゃん"
        assert len(domain_sink.responses_started) == 1
        assert len(domain_sink.transcript_deltas) == 1
        assert len(domain_sink.responses_completed) == 1
        assert domain_sink.responses_completed[0][3] == "はい、こんにちは！"

    finally:
        await bridge.cleanup()


@pytest.mark.asyncio
async def test_barge_in_invalidation_and_late_audio_drop() -> None:
    """User barge-in sequence:

    Runtime Generation invalidation -> Provider interrupt -> InterruptionFrame -> late drop.
    """
    session_id = uuid4()
    admission = InMemoryTurnAdmission()

    request = RealtimeSessionOpenRequest(
        session_id=session_id,
        character_id="ayachi_nene",
    )
    fake_session = FakeCloudRealtimeSession(request, auto_ready=True)
    domain_sink = InMemoryDomainSink()
    bridge = CloudRealtimeMediaBridge.create(
        session_id=session_id,
        backend_id=fake_session.backend_id,
        session=fake_session,
        admission=admission,
        domain_sink=domain_sink,
    )
    collector = FrameCollector()
    bridge.push_frame = collector.push_frame  # type: ignore[assignment]

    try:
        await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

        # User starts speaking -> admitted
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert bridge.current_identity is not None
        gen_id = bridge.current_identity.generation_id
        assert bridge.current_identity.turn_id is not None

        # Response starts
        await bridge.coordinator.dispatch_event(
            ResponseStartedEvent(
                session_id=session_id,
                generation_id=gen_id,
                provider_response_id="resp_barge_1",
            )
        )
        assert bridge.coordinator.mirror.is_active(gen_id)

        # Frame 1 plays normally
        await bridge.coordinator.dispatch_event(
            OutputAudioEvent(
                frame=RealtimeOutputAudioFrame(
                    session_id=session_id,
                    generation_id=gen_id,
                    sequence=1,
                    pts_ms=0,
                    sample_rate=24_000,
                    channels=1,
                    audio=b"audio-1",
                    is_final=False,
                )
            )
        )
        assert len([f for f in collector.frames if isinstance(f, OutputAudioRawFrame)]) == 1

        # User starts speaking (Barge-in)
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)

        # 1. Generation is invalidated and tombstoned in mirror
        assert bridge.coordinator.mirror.is_tombstoned(gen_id)
        assert not bridge.coordinator.mirror.is_active(gen_id)

        # 2. Provider interrupt called
        assert len(fake_session.interrupt_calls) == 1
        assert fake_session.interrupt_calls[0] == (gen_id, "user_barge_in")

        # 3. InterruptionFrame pushed downstream to clear playback buffer
        interruption_frames = [f for f in collector.frames if isinstance(f, InterruptionFrame)]
        assert len(interruption_frames) == 1

        # 4. Domain sink notified of cancellation
        assert len(domain_sink.responses_cancelled) == 1
        assert domain_sink.responses_cancelled[0][2] == gen_id

        # 5. Late output audio frame arrives for the tombstoned generation
        await bridge.coordinator.dispatch_event(
            OutputAudioEvent(
                frame=RealtimeOutputAudioFrame(
                    session_id=session_id,
                    generation_id=gen_id,
                    sequence=2,
                    pts_ms=10,
                    sample_rate=24_000,
                    channels=1,
                    audio=b"late-audio-chunk",
                    is_final=False,
                )
            )
        )

        # Ensure no additional output audio frame was pushed downstream
        assert len([f for f in collector.frames if isinstance(f, OutputAudioRawFrame)]) == 1

    finally:
        await bridge.cleanup()


@pytest.mark.asyncio
async def test_bounded_input_queue_drops_oldest_on_overflow() -> None:
    """Input audio queue enforces bounds and drops oldest frames under backpressure."""
    session_id = uuid4()
    request = RealtimeSessionOpenRequest(session_id=session_id, character_id="ayachi_nene")
    fake_session = FakeCloudRealtimeSession(request, auto_ready=True)

    # Set tiny capacity of 3 frames
    bridge = CloudRealtimeMediaBridge.create(
        session_id=session_id,
        backend_id=fake_session.backend_id,
        session=fake_session,
        input_queue_capacity=3,
    )

    try:
        # Push 6 frames without starting the pump task
        for i in range(6):
            bridge._handle_input_audio(
                InputAudioRawFrame(
                    audio=f"chunk-{i}".encode(),
                    sample_rate=16_000,
                    num_channels=1,
                )
            )

        # 3 frames dropped, 3 frames remain in queue
        assert bridge.dropped_input_frames == 3
        assert bridge._input_queue.qsize() == 3

        # Remaining frames are the 3 most recent (chunk-3, chunk-4, chunk-5)
        f3 = bridge._input_queue.get_nowait()
        f4 = bridge._input_queue.get_nowait()
        f5 = bridge._input_queue.get_nowait()

        assert f3.audio == b"chunk-3"
        assert f4.audio == b"chunk-4"
        assert f5.audio == b"chunk-5"

    finally:
        await bridge.cleanup()


@pytest.mark.asyncio
async def test_pipeline_teardown_closes_cloud_session() -> None:
    """Pipeline CancelFrame or EndFrame closes underlying CloudRealtimeSession."""
    session_id = uuid4()
    request = RealtimeSessionOpenRequest(session_id=session_id, character_id="ayachi_nene")
    fake_session = FakeCloudRealtimeSession(request, auto_ready=True)
    bridge = CloudRealtimeMediaBridge.create(
        session_id=session_id,
        backend_id=fake_session.backend_id,
        session=fake_session,
    )

    await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
    assert not fake_session.is_closed

    # Send CancelFrame to trigger teardown
    await bridge.process_frame(CancelFrame(), FrameDirection.DOWNSTREAM)

    assert fake_session.is_closed
    await bridge.cleanup()


@pytest.mark.asyncio
async def test_domain_sink_never_receives_raw_pcm() -> None:
    """Domain sink and durable event boundaries must never receive raw PCM bytes."""
    session_id = uuid4()
    gen_id = uuid4()
    request = RealtimeSessionOpenRequest(session_id=session_id, character_id="ayachi_nene")
    fake_session = FakeCloudRealtimeSession(request, auto_ready=True)
    domain_sink = InMemoryDomainSink()
    bridge = CloudRealtimeMediaBridge.create(
        session_id=session_id,
        backend_id=fake_session.backend_id,
        session=fake_session,
        domain_sink=domain_sink,
    )

    try:
        await bridge.coordinator.dispatch_event(
            OutputAudioEvent(
                frame=RealtimeOutputAudioFrame(
                    session_id=session_id,
                    generation_id=gen_id,
                    sequence=1,
                    pts_ms=0,
                    sample_rate=24_000,
                    channels=1,
                    audio=b"\xff\xfe\x00\x01" * 100,
                    is_final=True,
                )
            )
        )

        # Inspect all fields of domain sink - ensure no audio bytes leaked
        for item in (
            domain_sink.responses_started
            + domain_sink.transcript_deltas
            + domain_sink.transcript_finals
            + domain_sink.responses_completed
            + domain_sink.responses_cancelled
        ):
            for field in item:
                assert not isinstance(field, (bytes, bytearray))

    finally:
        await bridge.cleanup()


def test_pipecat_media_adapter_connection_mode_configuration() -> None:
    """PipecatMediaAdapter respects connection_mode: cascade by default, cloud_realtime."""
    cascade_config = RealtimeConfig(connection_mode="cascade")
    cloud_config = RealtimeConfig(connection_mode="cloud_realtime", cloud_backend="fake")
    stt_config = SttConfig()

    mock_publisher = MagicMock()
    mock_hub = MagicMock()
    mock_conversation = MagicMock()
    mock_assets = MagicMock()
    mock_stt = MagicMock()
    mock_settings = MagicMock()
    mock_activity = MagicMock()

    # Cascade adapter
    adapter_cascade = PipecatMediaAdapter(
        config=cascade_config,
        stt_config=stt_config,
        publisher=mock_publisher,
        event_hub=mock_hub,
        conversation=mock_conversation,
        audio_assets=mock_assets,
        stt=mock_stt,
        companion_settings=mock_settings,
        activity=mock_activity,
        resource_activity=lambda: None,
    )
    assert adapter_cascade._config.connection_mode == "cascade"

    # Cloud adapter with factory
    mock_factory = AsyncMock()
    adapter_cloud = PipecatMediaAdapter(
        config=cloud_config,
        stt_config=stt_config,
        publisher=mock_publisher,
        event_hub=mock_hub,
        conversation=mock_conversation,
        audio_assets=mock_assets,
        stt=mock_stt,
        companion_settings=mock_settings,
        activity=mock_activity,
        resource_activity=lambda: None,
        cloud_bridge_factory=mock_factory,
    )
    assert adapter_cloud._config.connection_mode == "cloud_realtime"
    assert adapter_cloud._cloud_bridge_factory is mock_factory
