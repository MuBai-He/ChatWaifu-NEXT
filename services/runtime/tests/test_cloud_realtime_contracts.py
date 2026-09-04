"""Tests for provider-neutral cloud realtime contracts and deterministic fake backend."""

import asyncio
from uuid import uuid4

import pytest
from chatwaifu_runtime.realtime.cloud.context import RealtimeContextPatchBuilder
from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    AuthorizedRealtimeSessionOpenRequest,
    CloudRealtimeBackend,
    CloudRealtimeSession,
    InputAudioCommittedEvent,
    OutputAudioEvent,
    ProviderErrorEvent,
    RealtimeCapabilities,
    RealtimeContextComponent,
    RealtimeContextPatch,
    RealtimeInputAudioFrame,
    RealtimeProviderEvent,
    RealtimeSessionIntent,
    RealtimeSessionLineage,
    RealtimeUsage,
    ResponseCancelledEvent,
    ResponseCompletedEvent,
    ResponseStartedEvent,
    SessionClosedEvent,
    SessionReadyEvent,
    UserTranscriptEvent,
)
from chatwaifu_runtime.realtime.cloud.fake import (
    FakeCloudRealtimeBackend,
    FakeCloudRealtimeSession,
)


def test_realtime_capabilities_defaults() -> None:
    caps = RealtimeCapabilities(backend_id="test_backend")
    assert caps.backend_id == "test_backend"
    assert caps.input_sample_rate == 16_000
    assert caps.output_sample_rate == 24_000
    assert caps.input_channels == 1
    assert caps.output_channels == 1
    assert caps.supports_interrupt is True
    assert caps.supports_tool_call is False
    assert "pcm16" in caps.supported_codecs


def test_context_patch_and_lineage_immutability() -> None:
    session_id = uuid4()
    patch_id = uuid4()
    comp = RealtimeContextComponent(
        kind="persona",
        text="Ayachi Nene character prompt",
        byte_count=28,
        estimated_tokens=7,
        priority=0,
    )
    patch = RealtimeContextPatch(
        patch_id=patch_id,
        components=(comp,),
        content_hash="abc123hash",
        total_bytes=28,
        estimated_tokens=7,
    )
    assert patch.total_bytes == 28
    assert len(patch.components) == 1
    assert patch.components[0].kind == "persona"

    lineage = RealtimeSessionLineage(
        session_id=session_id,
        backend_id="fake_backend",
        provider_session_id="p_sess_1",
    )
    assert lineage.revision == 0
    assert lineage.provider_response_id is None


@pytest.mark.asyncio
async def test_fake_backend_lifecycle() -> None:
    backend: CloudRealtimeBackend = FakeCloudRealtimeBackend(backend_id="test_fake")
    caps = await backend.capabilities()
    assert caps.backend_id == "test_fake"

    session_id = uuid4()
    gen_id = uuid4()

    req = AuthorizedRealtimeSessionOpenRequest(
        intent=RealtimeSessionIntent(session_id=session_id, character_id="ayachi_nene"),
        context_patch=RealtimeContextPatchBuilder().build_patch(),
        authorization_id=uuid4(),
    )

    session: CloudRealtimeSession = await backend.open_session(req)
    assert isinstance(session, FakeCloudRealtimeSession)
    assert session.session_id == session_id
    assert session.character_id == "ayachi_nene"

    # First event is SessionReadyEvent
    event = await session.receive()
    assert isinstance(event, SessionReadyEvent)
    assert event.session_id == session_id
    assert event.backend_id == "test_fake"

    # Send audio
    frame = RealtimeInputAudioFrame(
        session_id=session_id,
        generation_id=gen_id,
        sequence=0,
        pts_ms=0,
        sample_rate=16_000,
        channels=1,
        audio=b"\x00\x00" * 160,
    )
    await session.send_audio(frame)
    assert len(session.sent_audio_frames) == 1
    assert session.sent_audio_frames[0].audio == frame.audio

    # Commit input
    await session.commit_input()
    assert session.commit_calls == 1
    commit_event = await session.receive()
    assert isinstance(commit_event, InputAudioCommittedEvent)
    assert commit_event.session_id == session_id

    # Update context
    patch = RealtimeContextPatch(
        patch_id=uuid4(),
        components=(),
        content_hash="hash",
        total_bytes=0,
        estimated_tokens=0,
    )
    await session.update_context(patch)
    assert len(session.context_updates) == 2
    assert session.context_updates[-1] == patch
    assert session.lineage.revision == 1

    # Interrupt
    await session.interrupt(gen_id, reason="user_barge_in")
    assert len(session.interrupt_calls) == 1
    assert session.interrupt_calls[0] == (gen_id, "user_barge_in")
    interrupt_event = await session.receive()
    assert isinstance(interrupt_event, ResponseCancelledEvent)
    assert interrupt_event.generation_id == gen_id

    # Close session
    await session.close()
    close_event = await session.receive()
    assert isinstance(close_event, SessionClosedEvent)
    assert session.is_closed is True

    # Post-close actions fail
    with pytest.raises(RuntimeError):
        await session.send_audio(frame)

    with pytest.raises(RuntimeError):
        await session.commit_input()

    # Backend close
    await backend.close()


@pytest.mark.asyncio
async def test_fake_session_scripted_injection_and_events_iterator() -> None:
    session_id = uuid4()
    gen_id = uuid4()

    backend = FakeCloudRealtimeBackend(auto_ready=False)
    session_raw = await backend.open_session(
        AuthorizedRealtimeSessionOpenRequest(
            intent=RealtimeSessionIntent(session_id=session_id, character_id="ayachi_nene"),
            context_patch=RealtimeContextPatchBuilder().build_patch(),
            authorization_id=uuid4(),
        )
    )
    assert isinstance(session_raw, FakeCloudRealtimeSession)
    session: FakeCloudRealtimeSession = session_raw

    # Inoculate events
    session.inject_event(
        SessionReadyEvent(
            session_id=session_id,
            provider_session_id="p_123",
            backend_id=backend.backend_id,
        )
    )
    session.inject_user_transcript("你好，宁宁", phase="final")
    resp_id = session.inject_response_started(gen_id)
    session.inject_assistant_transcript("你好呀，找我有什么事吗？", gen_id, phase="final")
    session.inject_output_audio(gen_id, b"\x01\x02\x03\x04", sequence=0, is_final=True)
    session.inject_response_completed(
        gen_id,
        provider_response_id=resp_id,
        usage=RealtimeUsage(
            session_id=session_id,
            generation_id=gen_id,
            backend_id=backend.backend_id,
            total_tokens=42,
        ),
    )
    # Close session to terminate event iterator
    await session.close()

    received: list[RealtimeProviderEvent] = []
    async for ev in session.events():
        received.append(ev)

    assert len(received) == 7
    assert isinstance(received[0], SessionReadyEvent)
    assert isinstance(received[1], UserTranscriptEvent)
    assert received[1].candidate.text == "你好，宁宁"
    assert isinstance(received[2], ResponseStartedEvent)
    assert isinstance(received[3], AssistantTranscriptEvent)
    assert isinstance(received[4], OutputAudioEvent)
    assert isinstance(received[5], ResponseCompletedEvent)
    assert received[5].usage is not None and received[5].usage.total_tokens == 42
    assert isinstance(received[6], SessionClosedEvent)


@pytest.mark.asyncio
async def test_fake_session_tool_call_unsupported() -> None:
    backend = FakeCloudRealtimeBackend()
    session = await backend.open_session(
        AuthorizedRealtimeSessionOpenRequest(
            intent=RealtimeSessionIntent(session_id=uuid4(), character_id="ayachi_nene"),
            context_patch=RealtimeContextPatchBuilder().build_patch(),
            authorization_id=uuid4(),
        )
    )
    with pytest.raises(NotImplementedError) as exc_info:
        await session.submit_tool_result("call_123", '{"result": "ok"}')
    assert "Tool bridge is not supported in Phase 13" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fake_session_provider_error_injection() -> None:
    backend = FakeCloudRealtimeBackend()
    session_id = uuid4()
    session_raw = await backend.open_session(
        AuthorizedRealtimeSessionOpenRequest(
            intent=RealtimeSessionIntent(session_id=session_id, character_id="ayachi_nene"),
            context_patch=RealtimeContextPatchBuilder().build_patch(),
            authorization_id=uuid4(),
        )
    )
    assert isinstance(session_raw, FakeCloudRealtimeSession)
    session: FakeCloudRealtimeSession = session_raw
    _ = await session.receive()  # SessionReadyEvent

    session.inject_error(
        code="rate_limit_exceeded",
        message="Too many requests",
        retryable=True,
    )
    event = await session.receive()
    assert isinstance(event, ProviderErrorEvent)
    assert event.error.code == "rate_limit_exceeded"
    assert event.error.retryable is True


@pytest.mark.asyncio
async def test_fake_session_hook_triggers_deterministically() -> None:
    session_id = uuid4()
    backend = FakeCloudRealtimeBackend()
    session_raw = await backend.open_session(
        AuthorizedRealtimeSessionOpenRequest(
            intent=RealtimeSessionIntent(session_id=session_id, character_id="ayachi_nene"),
            context_patch=RealtimeContextPatchBuilder().build_patch(),
            authorization_id=uuid4(),
        )
    )
    assert isinstance(session_raw, FakeCloudRealtimeSession)
    session: FakeCloudRealtimeSession = session_raw
    _ = await session.receive()  # SessionReadyEvent

    audio_frames_seen: list[int] = []
    commit_event = asyncio.Event()

    session.set_on_audio_frame_hook(lambda f: audio_frames_seen.append(f.sequence))
    session.set_on_commit_hook(lambda: commit_event.set())

    frame = RealtimeInputAudioFrame(
        session_id=session_id,
        generation_id=None,
        sequence=1,
        pts_ms=20,
        sample_rate=16_000,
        channels=1,
        audio=b"\x00" * 320,
    )
    await session.send_audio(frame)
    assert audio_frames_seen == [1]

    await session.commit_input()
    assert commit_event.is_set()
