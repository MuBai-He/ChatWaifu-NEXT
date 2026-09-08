"""Controlled transport races for Phase13.4A; no timing sleeps as synchronization."""
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from chatwaifu_runtime.realtime.admission import InMemoryTurnAdmission
from chatwaifu_runtime.realtime.cloud import coordinator as coordinator_module
from chatwaifu_runtime.realtime.cloud import media as media_module
from chatwaifu_runtime.realtime.cloud.contracts import (
    OutputAudioEvent,
    RealtimeInputAudioFrame,
    RealtimeOutputAudioFrame,
    RealtimeProviderEvent,
    RealtimeSessionOpenRequest,
    ResponseCompletedEvent,
    SessionClosedEvent,
    SessionReadyEvent,
)
from chatwaifu_runtime.realtime.cloud.coordinator import InMemoryDomainSink
from chatwaifu_runtime.realtime.cloud.fake import FakeCloudRealtimeSession
from chatwaifu_runtime.realtime.cloud.media import CloudRealtimeMediaBridge
from pipecat.frames.frames import Frame, InputAudioRawFrame, InterruptionFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection


@pytest.fixture
async def bridge() -> AsyncIterator[CloudRealtimeMediaBridge]:
    sid = uuid4()
    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default")
    )
    result = CloudRealtimeMediaBridge.create(
        session_id=sid,
        backend_id=session.backend_id,
        session=session,
        admission=InMemoryTurnAdmission(),
        domain_sink=InMemoryDomainSink(),
        input_queue_capacity=2,
    )
    result.push_frame = AsyncMock()
    result._ensure_started()
    await result._handle_user_speaking_started()
    try:
        yield result
    finally:
        await result.coordinator.stop()
        await result.cleanup()


async def test_commit_is_ordered_after_every_admitted_audio_frame(
    bridge: CloudRealtimeMediaBridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release, committed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    sent: list[bytes] = []
    commits = 0

    async def send(frame: RealtimeInputAudioFrame) -> None:
        entered.set()
        await release.wait()
        sent.append(frame.audio)

    async def commit() -> None:
        nonlocal commits
        commits += 1
        committed.set()

    monkeypatch.setattr(bridge.coordinator.session, "send_audio", send)
    monkeypatch.setattr(bridge.coordinator.session, "commit_input", commit)
    bridge._handle_input_audio(
        InputAudioRawFrame(audio=b"\x00\x01", sample_rate=16000, num_channels=1)
    )
    await asyncio.wait_for(entered.wait(), 1)
    bridge._handle_input_audio(
        InputAudioRawFrame(audio=b"\x00\x02", sample_rate=16000, num_channels=1)
    )
    await bridge._handle_user_speaking_stopped()
    assert not committed.is_set(), "commit must not overtake the blocked audio sender"
    release.set()
    await asyncio.wait_for(committed.wait(), 1)
    assert sent == [b"\x00\x01", b"\x00\x02"]
    assert commits == 1
    # Repeated VAD stop cannot recommit, and silence after stop is not part of this utterance.
    await bridge._handle_user_speaking_stopped()
    bridge._handle_input_audio(
        InputAudioRawFrame(audio=b"\x00\x03", sample_rate=16000, num_channels=1)
    )
    await asyncio.wait_for(bridge._input_queue.join(), 1)
    assert sent == [b"\x00\x01", b"\x00\x02"]
    assert commits == 1


async def test_local_playback_is_flushed_before_provider_interrupt_returns(
    bridge: CloudRealtimeMediaBridge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    old = bridge.current_identity
    assert old is not None

    async def interrupt(*_args: object) -> None:
        entered.set()
        await release.wait()

    monkeypatch.setattr(bridge.coordinator.session, "interrupt", interrupt)
    start = asyncio.create_task(bridge._handle_user_speaking_started())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert bridge.coordinator.mirror.is_tombstoned(old.generation_id)
        assert isinstance(bridge.push_frame, AsyncMock)
        assert any(
            isinstance(call.args[0], InterruptionFrame) for call in bridge.push_frame.call_args_list
        )
        assert bridge.current_identity == old, "no new turn until remote input is fenced"
    finally:
        release.set()
        await start
    assert bridge.current_identity != old


@pytest.mark.parametrize("stream_fails", [False, True])
async def test_provider_end_terminates_generation_and_stops_pump(
    bridge: CloudRealtimeMediaBridge,
    monkeypatch: pytest.MonkeyPatch,
    stream_fails: bool,
) -> None:
    async def empty_stream() -> AsyncIterator[RealtimeProviderEvent]:
        if stream_fails:
            raise OSError("socket read failed")
        return
        yield  # pragma: no cover

    await bridge.coordinator.stop()
    # A replacement coordinator/session is needed after closure; reuse the fixture's
    # factory to exercise a fresh stream that ends without SessionClosedEvent.
    sid = uuid4()
    session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=sid, character_id="default")
    )
    monkeypatch.setattr(session, "events", empty_stream)
    other = CloudRealtimeMediaBridge.create(
        session_id=sid,
        backend_id=session.backend_id,
        session=session,
        admission=InMemoryTurnAdmission(),
    )
    other.push_frame = AsyncMock()
    try:
        await other._handle_user_speaking_started()
        identity = other.current_identity
        assert identity is not None
        other._ensure_started()
        pump = other.coordinator._pump_task
        assert pump is not None
        await asyncio.wait_for(pump, 1)
        assert not other.coordinator.is_running
        assert other.coordinator.mirror.is_tombstoned(identity.generation_id)
        assert session.is_closed
        assert other._fatal_media_failure
    finally:
        await other.cleanup()


async def test_full_audio_queue_retains_commit_barrier(
    bridge: CloudRealtimeMediaBridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    session = bridge.coordinator.session
    original_send = session.send_audio

    async def blocked_send(frame: RealtimeInputAudioFrame) -> None:
        entered.set()
        await release.wait()
        await original_send(frame)

    monkeypatch.setattr(session, "send_audio", blocked_send)
    bridge._handle_input_audio(InputAudioRawFrame(b"first", 16000, 1))
    await asyncio.wait_for(entered.wait(), 1)
    for audio in (b"dropped", b"kept-1", b"kept-2"):
        bridge._handle_input_audio(InputAudioRawFrame(audio, 16000, 1))
    await bridge._handle_user_speaking_stopped()
    assert bridge._input_queue.qsize() == 3  # Two audio slots plus the reserved commit slot.
    assert bridge.dropped_input_frames == 1
    release.set()
    await asyncio.wait_for(bridge._input_queue.join(), 1)
    assert isinstance(session, FakeCloudRealtimeSession)
    assert [frame.audio for frame in session.sent_audio_frames] == [b"first", b"kept-1", b"kept-2"]
    assert session.commit_calls == 1


@pytest.mark.parametrize("operation", ["send_audio", "commit_input", "interrupt"])
async def test_stalled_media_operation_has_a_terminal_deadline(
    bridge: CloudRealtimeMediaBridge, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    entered, cancelled, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    session = bridge.coordinator.session
    original_close = session.close
    old = bridge.current_identity
    assert old is not None

    async def stalled(*_args: object) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def close() -> None:
        await original_close()
        closed.set()

    # Exercise the actual operation deadline, not a sleep used to synchronize the test.
    monkeypatch.setattr(media_module, "MEDIA_OPERATION_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(session, operation, stalled)
    monkeypatch.setattr(session, "close", close)
    if operation == "send_audio":
        bridge._handle_input_audio(InputAudioRawFrame(b"input", 16000, 1))
    elif operation == "commit_input":
        await bridge._handle_user_speaking_stopped()
    else:
        await bridge._handle_user_speaking_started()
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(closed.wait(), 1)
    await bridge.coordinator.stop()  # Join durable terminal notification too.
    await asyncio.wait_for(bridge._input_queue.join(), 1)
    assert cancelled.is_set()
    assert bridge._fatal_media_failure
    sink = bridge.coordinator.domain_sink
    assert isinstance(sink, InMemoryDomainSink)
    assert len(sink.responses_failed) + len(sink.responses_cancelled) == 1
    assert len(sink.provider_errors) == 1
    assert len(sink.session_closures) == 1
    await bridge._handle_user_speaking_started()
    assert bridge.current_identity == old


async def test_blocked_output_handoff_cannot_resume_after_barge_in(
    bridge: CloudRealtimeMediaBridge,
) -> None:
    entered, release, flushed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    pushed: list[Frame] = []
    old = bridge.current_identity
    assert old is not None

    async def push(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if isinstance(frame, OutputAudioRawFrame):
            entered.set()
            await release.wait()
        pushed.append(frame)
        if isinstance(frame, InterruptionFrame):
            flushed.set()

    bridge.push_frame = push
    frame = RealtimeOutputAudioFrame(
        session_id=bridge.session_id,
        generation_id=old.generation_id,
        sequence=1,
        pts_ms=0,
        sample_rate=24000,
        channels=1,
        audio=b"stale",
        is_final=True,
    )
    handoff = asyncio.create_task(bridge.coordinator.dispatch_event(OutputAudioEvent(frame=frame)))
    await asyncio.wait_for(entered.wait(), 1)
    await bridge._handle_user_speaking_started()
    assert flushed.is_set()
    release.set()
    await asyncio.wait_for(handoff, 1)
    await bridge.coordinator.dispatch_event(OutputAudioEvent(frame=frame))
    assert [type(item) for item in pushed] == [InterruptionFrame]
    assert bridge.coordinator.is_running


async def test_new_speech_flushes_audio_buffered_after_response_completion(
    bridge: CloudRealtimeMediaBridge,
) -> None:
    old = bridge.current_identity
    assert old is not None
    await bridge.coordinator.dispatch_event(
        ResponseCompletedEvent(
            session_id=bridge.session_id,
            generation_id=old.generation_id,
            provider_response_id="completed-response",
            final_text="Done.",
        )
    )
    assert bridge.coordinator.mirror.active_generation_id is None
    await bridge._handle_user_speaking_started()
    assert isinstance(bridge.push_frame, AsyncMock)
    assert any(
        isinstance(call.args[0], InterruptionFrame) for call in bridge.push_frame.call_args_list
    )
    assert bridge.current_identity != old


async def test_close_seals_late_events_and_is_idempotent(
    bridge: CloudRealtimeMediaBridge,
) -> None:
    old = bridge.current_identity
    sink = bridge.coordinator.domain_sink
    assert isinstance(sink, InMemoryDomainSink)
    await bridge.coordinator.dispatch_event(
        SessionClosedEvent(session_id=bridge.session_id, backend_id="fake", reason="socket_closed")
    )
    ready_count = len(sink.session_readies)
    await bridge.coordinator.dispatch_event(
        SessionReadyEvent(
            session_id=bridge.session_id, backend_id="fake", provider_session_id="late"
        )
    )
    await bridge._handle_user_speaking_started()
    bridge._handle_input_audio(InputAudioRawFrame(b"late", 16000, 1))
    bridge.coordinator.start()
    await asyncio.gather(bridge.coordinator.stop(), bridge.coordinator.stop(), bridge.cleanup())
    assert bridge.current_identity == old
    assert not bridge.coordinator.is_running
    assert bridge._input_queue.empty()
    assert len(sink.session_readies) == ready_count
    assert len(sink.responses_cancelled) == 1
    assert sink.session_closures == [(bridge.session_id, "socket_closed")]
    with pytest.raises(RuntimeError, match="closing cloud session"):
        bridge.coordinator.admit_turn(uuid4(), uuid4())


@pytest.mark.parametrize("via_bridge", [False, True])
async def test_cancelled_stop_waiter_does_not_abandon_bounded_session_close(
    bridge: CloudRealtimeMediaBridge, monkeypatch: pytest.MonkeyPatch, via_bridge: bool
) -> None:
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls = 0

    async def close() -> None:
        nonlocal calls
        calls += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(bridge.coordinator.session, "close", close)
    monkeypatch.setattr(coordinator_module, "SESSION_CLOSE_TIMEOUT_SECONDS", 0.03)
    stop = bridge.cleanup if via_bridge else bridge.coordinator.stop
    first = asyncio.create_task(stop())
    await asyncio.wait_for(entered.wait(), 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(stop(), 1)
    assert cancelled.is_set()
    assert calls == 1
    sink = bridge.coordinator.domain_sink
    assert isinstance(sink, InMemoryDomainSink)
    assert len(sink.responses_cancelled) == 1
    assert len(sink.session_closures) == 1
