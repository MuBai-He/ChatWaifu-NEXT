# pyright: reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportIndexIssue=false

"""End-to-End Vertical Tests for Cloud Realtime Playback Confirmation (Phase 13.4C).

Validates the full playback confirmation vertical slice:
1. Interruption during buffered audio cancels generation and commits 0 full text.
2. Playout ACK before transcript/duration and reverse order both commit once.
3. Duplicate/reordered ACKs, late chunks, and old socket close cannot mutate new active generation.
4. Disconnect during playback cancels work and prevents ghost output.
5. Reopening services / replaying facts cannot duplicate commits (CAS idempotency).
6. Empty/no-audio and missing-ACK cases terminate under defined policy without false text.
7. Cascade tests stay green (backward compatibility).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.commands import PlaybackAckCommand, PlaybackAckPayload
from chatwaifu_protocol.memory import MemoryRecordDraft
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.main import create_app
from chatwaifu_runtime.memory.extractor import ExtractedMemoryCandidate
from chatwaifu_runtime.memory.ports import SpokenMemoryFact
from chatwaifu_runtime.playback.service import PlaybackService
from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    OutputAudioEvent,
    RealtimeOutputAudioFrame,
    RealtimeTranscriptCandidate,
    ResponseCompletedEvent,
    ResponseStartedEvent,
)
from chatwaifu_runtime.realtime.cloud.domain import RuntimeRealtimeDomainSink
from httpx import ASGITransport, AsyncClient
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    StartFrame,
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


def create_cloud_settings(tmp_path: Path, **overrides: object) -> Settings:
    data: dict[str, object] = {
        "config_dir": tmp_path / "config",
        "data_dir": tmp_path,
        "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
        "llm": {"provider": "demo", "demo_chunk_delay_ms": 0},
        "tts": {"provider": "fake"},
        "privacy": {"cloud_egress": "allow"},
        "realtime": {
            "connection_mode": "cloud_realtime",
            "cloud_backend": "fake",
        },
    }
    data.update(overrides)
    return Settings.model_validate(data)


def create_ack_command(
    session_id: UUID,
    generation_id: UUID,
    stream_id: UUID,
    segment_id: UUID,
    phase: Literal["started", "progress", "stopped", "queue_cleared"],
    played_pts_ms: int,
    reason: Literal["ended", "interrupted", "error", "queue_cleared"] | None = None,
    command_id: UUID | None = None,
    buffered_ms: int = 0,
    client_clock_ms: int = 0,
    transport: Literal["audio_element", "webrtc"] = "webrtc",
    issuer: str = "test-client",
) -> PlaybackAckCommand:
    return PlaybackAckCommand(
        command_id=command_id or uuid4(),
        session_id=session_id,
        generation_id=generation_id,
        issued_at=datetime.now(UTC),
        issuer=issuer,
        payload=PlaybackAckPayload(
            stream_id=stream_id,
            segment_id=segment_id,
            phase=phase,
            played_pts_ms=played_pts_ms,
            buffered_ms=buffered_ms,
            client_clock_ms=client_clock_ms,
            transport=transport,
            reason=reason,
        ),
    )


# ==============================================================================
# 1. Interruption during buffered audio cancels generation and commits 0 text
# ==============================================================================


@pytest.mark.asyncio
async def test_01_interruption_during_buffered_audio_commits_zero_text(tmp_path: Path) -> None:
    """Acceptance 1: Interruption during buffered audio cancels generation
    and commits 0 full text.
    """
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        # Wire factory and create authorized bridge
        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session_id)
        collector = FrameCollector()
        bridge.push_frame = collector.push_frame  # type: ignore[assignment]

        try:
            await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

            # User speaks -> turn admitted
            await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            assert bridge.current_identity is not None
            gen_id = bridge.current_identity.generation_id
            stream_id = bridge.current_identity.audio_stream_id

            # Model starts response and streams audio
            await bridge.coordinator.dispatch_event(
                ResponseStartedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-1",
                )
            )
            # Send 2 audio chunks (24kHz mono PCM16 -> 240 samples = 10ms each)
            pcm_chunk = b"\x10\x00" * 240
            await bridge.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_id,
                        sequence=1,
                        pts_ms=0,
                        sample_rate=24_000,
                        channels=1,
                        audio=pcm_chunk,
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
                        audio=pcm_chunk,
                        is_final=True,
                    )
                )
            )

            # Verify started marker emitted immediately on first output chunk
            msg_frames = [
                f.message
                for f in collector.frames
                if isinstance(f, OutputTransportMessageFrame) and isinstance(f.message, dict)
            ]
            assert len(msg_frames) == 1
            started_marker = msg_frames[0]
            assert started_marker["phase"] == "started"
            assert started_marker["duration_ms"] == 0
            segment_id = UUID(str(started_marker["segment_id"]))

            # Model emits final transcript
            await bridge.coordinator.dispatch_event(
                AssistantTranscriptEvent(
                    candidate=RealtimeTranscriptCandidate(
                        session_id=session_id,
                        generation_id=gen_id,
                        role="assistant",
                        phase="final",
                        text="Unplayed long assistant speech.",
                        provider_response_id="resp-1",
                    )
                )
            )
            await bridge.coordinator.dispatch_event(
                ResponseCompletedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-1",
                    final_text="Unplayed long assistant speech.",
                )
            )

            # Whole-response audio finalization emits buffered marker after response completion
            msg_frames = [
                f.message
                for f in collector.frames
                if isinstance(f, OutputTransportMessageFrame) and isinstance(f.message, dict)
            ]
            assert len(msg_frames) == 2
            buffered_marker = msg_frames[1]
            assert buffered_marker["phase"] == "buffered"
            assert buffered_marker["duration_ms"] == 20  # 480 samples at 24kHz = 20ms
            assert UUID(str(buffered_marker["segment_id"])) == segment_id

            # At this moment, audio is buffered on client, but NOT yet played!
            # Verify generation in DB is STILL active / spoken_text is empty
            status = await container.playback.status(session_id, gen_id)
            assert status["spoken_text"] == ""

            # User BARGES IN at 5ms out of 20ms!
            await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)

            # Downstream receives InterruptionFrame
            interruption_frames = [f for f in collector.frames if isinstance(f, InterruptionFrame)]
            assert len(interruption_frames) >= 1

            # Client sends interrupted playback ACK
            ack_res = await container.playback.acknowledge(
                create_ack_command(
                    session_id=session_id,
                    generation_id=gen_id,
                    stream_id=stream_id,
                    segment_id=segment_id,
                    phase="stopped",
                    played_pts_ms=5,
                    reason="interrupted",
                )
            )
            assert ack_res.completed is False
            assert ack_res.state == "stopped"
            assert ack_res.spoken_text == ""
            assert ack_res.committed_event_id is None

            # Verify durable state in database: spoken_text remains empty!
            final_status = await container.playback.status(session_id, gen_id)
            assert final_status["spoken_text"] == ""
            seg = final_status["segments"][0]
            assert seg["stop_reason"] == "interrupted"
            assert seg["state"] == "stopped"

            # Check DB generations table directly
            gen_row = await container.database.fetchone(
                "SELECT state, spoken_text FROM generations WHERE generation_id = ?",
                (str(gen_id),),
            )
            assert gen_row is not None
            assert gen_row["spoken_text"] == ""
            assert gen_row["state"] == "cancelled"

            # Verify NO AssistantSpokenTextCommittedEvent was persisted
            events = await container.event_store.read_stream(session_id)
            committed_events = [
                e for e in events if e.get("event_type") == "assistant.spoken_text_committed"
            ]
            assert len(committed_events) == 0

        finally:
            await bridge.cleanup()
    finally:
        await container.stop()


# ==============================================================================
# 2. Playout ACK before transcript/duration and reverse order both commit once
# ==============================================================================


@pytest.mark.asyncio
async def test_02_ack_before_transcript_and_reverse_order_both_commit_once(
    tmp_path: Path,
) -> None:
    """Acceptance 2: Transactionally join duration, transcript, and ACK
    regardless of arrival order.
    """
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id
        playback = container.playback

        # --- Subtest 2A: Playout ACK arrives BEFORE transcript ---
        gen_a = uuid4()
        turn_a = uuid4()
        stream_a = uuid4()
        seg_a = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_a,
            generation_id=gen_a,
            audio_stream_id=stream_a,
        )
        # Register segment with finalized duration, but unfinalized transcript
        await playback.register_segment(
            session_id=session_id,
            generation_id=gen_a,
            stream_id=stream_a,
            segment_id=seg_a,
            segment_index=0,
            text="",
            duration_ms=1000,
            duration_finalized=True,
            transcript_finalized=False,
        )

        # 1. Client finishes playing first and sends ended ACK
        ack_cmd_a = create_ack_command(
            session_id=session_id,
            generation_id=gen_a,
            stream_id=stream_a,
            segment_id=seg_a,
            phase="stopped",
            played_pts_ms=1000,
            reason="ended",
        )
        ack_res_a = await playback.acknowledge(ack_cmd_a)
        assert ack_res_a.completed is False
        assert ack_res_a.state == "stopped"
        assert ack_res_a.spoken_text == ""
        assert ack_res_a.committed_event_id is None

        # 2. Provider transcript arrives later
        commit_res_a = await playback.attach_generation_transcript(
            gen_a, "Transcript that arrived after playout finished."
        )
        assert commit_res_a is not None
        assert commit_res_a.completed is True
        assert commit_res_a.all_segments_completed is True
        assert commit_res_a.spoken_text == "Transcript that arrived after playout finished."
        assert commit_res_a.committed_event_id is not None

        # Verify DB state
        status_a = await playback.status(session_id, gen_a)
        assert status_a["spoken_text"] == "Transcript that arrived after playout finished."
        assert status_a["segments"][0]["state"] == "completed"

        # --- Subtest 2B: Transcript arrives BEFORE playout ACK (standard order) ---
        gen_b = uuid4()
        turn_b = uuid4()
        stream_b = uuid4()
        seg_b = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_b,
            generation_id=gen_b,
            audio_stream_id=stream_b,
        )
        await playback.register_segment(
            session_id=session_id,
            generation_id=gen_b,
            stream_id=stream_b,
            segment_id=seg_b,
            segment_index=0,
            text="",
            duration_ms=800,
            duration_finalized=True,
            transcript_finalized=False,
        )
        # Transcript arrives first
        commit_res_b1 = await playback.attach_generation_transcript(
            gen_b, "Transcript that arrived before playout."
        )
        # Not committed yet because playout hasn't ended
        assert commit_res_b1 is None

        # Playout finishes and ACK arrives
        ack_cmd_b = create_ack_command(
            session_id=session_id,
            generation_id=gen_b,
            stream_id=stream_b,
            segment_id=seg_b,
            phase="stopped",
            played_pts_ms=800,
            reason="ended",
        )
        ack_res_b = await playback.acknowledge(ack_cmd_b)
        assert ack_res_b.completed is True
        assert ack_res_b.state == "completed"
        assert ack_res_b.all_segments_completed is True
        assert ack_res_b.spoken_text == "Transcript that arrived before playout."
        assert ack_res_b.committed_event_id is not None

        # --- Subtest 2C: Playout ACK arrives BEFORE duration finalization ---
        gen_c = uuid4()
        turn_c = uuid4()
        stream_c = uuid4()
        seg_c = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_c,
            generation_id=gen_c,
            audio_stream_id=stream_c,
        )
        await playback.register_segment(
            session_id=session_id,
            generation_id=gen_c,
            stream_id=stream_c,
            segment_id=seg_c,
            segment_index=0,
            text="Duration pending text.",
            duration_ms=0,
            duration_finalized=False,
            transcript_finalized=True,
        )
        # Client sends ended ACK
        ack_cmd_c = create_ack_command(
            session_id=session_id,
            generation_id=gen_c,
            stream_id=stream_c,
            segment_id=seg_c,
            phase="stopped",
            played_pts_ms=600,
            reason="ended",
        )
        ack_res_c = await playback.acknowledge(ack_cmd_c)
        assert ack_res_c.completed is False

        # Now finalize duration
        commit_res_c = await playback.finalize_segment(seg_c, 600)
        assert commit_res_c is not None
        assert commit_res_c.completed is True
        assert commit_res_c.spoken_text == "Duration pending text."
    finally:
        await container.stop()


# ==============================================================================
# 3. Duplicate/reordered ACKs and late frames cannot mutate new generation
# ==============================================================================


@pytest.mark.asyncio
async def test_03_duplicate_and_out_of_order_acks_cannot_mutate_generations(
    tmp_path: Path,
) -> None:
    """Acceptance 3: Duplicate/reordered ACKs, late chunks, and old socket close
    cannot mutate new active generation.
    """
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id
        playback = container.playback

        gen_1 = uuid4()
        turn_1 = uuid4()
        stream_1 = uuid4()
        seg_1 = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_1,
            generation_id=gen_1,
            audio_stream_id=stream_1,
        )
        await playback.register_segment(
            session_id=session_id,
            generation_id=gen_1,
            stream_id=stream_1,
            segment_id=seg_1,
            segment_index=0,
            text="First turn text.",
            duration_ms=1000,
            duration_finalized=True,
            transcript_finalized=True,
        )

        # 1. Complete segment cleanly
        cmd_stop = create_ack_command(
            session_id=session_id,
            generation_id=gen_1,
            stream_id=stream_1,
            segment_id=seg_1,
            phase="stopped",
            played_pts_ms=1000,
            reason="ended",
        )
        res1 = await playback.acknowledge(cmd_stop)
        assert res1.completed is True
        assert res1.duplicate is False

        # 2. Resend exact same command ID -> duplicate=True
        res2 = await playback.acknowledge(cmd_stop)
        assert res2.duplicate is True
        assert res2.completed is True

        # 3. Send a new command ID for already completed segment -> duplicate=True
        cmd_late_progress = create_ack_command(
            session_id=session_id,
            generation_id=gen_1,
            stream_id=stream_1,
            segment_id=seg_1,
            phase="progress",
            played_pts_ms=500,
        )
        res3 = await playback.acknowledge(cmd_late_progress)
        assert res3.duplicate is True
        assert res3.state == "completed"

        # 4. Admit new Generation 2
        gen_2 = uuid4()
        turn_2 = uuid4()
        stream_2 = uuid4()
        seg_2 = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_2,
            generation_id=gen_2,
            audio_stream_id=stream_2,
        )
        await playback.register_segment(
            session_id=session_id,
            generation_id=gen_2,
            stream_id=stream_2,
            segment_id=seg_2,
            segment_index=0,
            text="Second turn text.",
            duration_ms=1200,
            duration_finalized=True,
            transcript_finalized=True,
        )

        # 5. Stale ACK from Generation 1 targeting Generation 2's session/generation
        # must be rejected
        mismatched_cmd = create_ack_command(
            session_id=session_id,
            generation_id=gen_2,  # claiming gen_2
            stream_id=stream_1,  # but with stream_1 and seg_1
            segment_id=seg_1,
            phase="stopped",
            played_pts_ms=1000,
            reason="ended",
        )
        with pytest.raises(ValueError, match="playback acknowledgement identity does not match"):
            await playback.acknowledge(mismatched_cmd)

        # Generation 2's state is untouched
        status_2 = await playback.status(session_id, gen_2)
        assert status_2["spoken_text"] == ""
        assert status_2["segments"][0]["state"] == "queued"
    finally:
        await container.stop()


# ==============================================================================
# 4. Disconnect during playback cancels work and prevents ghost output
# ==============================================================================


@pytest.mark.asyncio
async def test_04_disconnect_during_playback_cancels_work(tmp_path: Path) -> None:
    """Acceptance 4: Disconnect during playback cancels work and prevents ghost output."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session_id)
        collector = FrameCollector()
        bridge.push_frame = collector.push_frame  # type: ignore[assignment]

        try:
            await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            assert bridge.current_identity is not None
            gen_id = bridge.current_identity.generation_id

            # Stream audio
            await bridge.coordinator.dispatch_event(
                ResponseStartedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-disc",
                )
            )
            pcm_chunk = b"\x20\x00" * 240
            await bridge.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_id,
                        sequence=1,
                        pts_ms=0,
                        sample_rate=24_000,
                        channels=1,
                        audio=pcm_chunk,
                        is_final=True,
                    )
                )
            )

            # WebRTC transport disconnects or session is cancelled
            await bridge.session_terminated()
            await bridge.coordinator.stop()

            # Verify generation cancelled in SQLite
            gen_row = await container.database.fetchone(
                "SELECT state, spoken_text FROM generations WHERE generation_id = ?",
                (str(gen_id),),
            )
            assert gen_row is not None
            assert gen_row["state"] == "cancelled"
            assert gen_row["spoken_text"] == ""

            # Active generation in ConversationService is cleared
            recovery = await container.conversation.recovery_state(session_id)
            assert recovery.active_generation_id is None

            # No spoken commit events
            events = await container.event_store.read_stream(session_id)
            assert not any(e.get("event_type") == "assistant.spoken_text_committed" for e in events)

        finally:
            await bridge.cleanup()
    finally:
        await container.stop()


# ==============================================================================
# 5. Reopening services / replaying facts cannot duplicate commits
# ==============================================================================


@pytest.mark.asyncio
async def test_05_reopening_services_and_replay_cannot_duplicate_commits(
    tmp_path: Path,
) -> None:
    """Acceptance 5: Reopening services / replaying facts cannot duplicate commits
    (CAS idempotency).
    """
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        gen_id = uuid4()
        turn_id = uuid4()
        stream_id = uuid4()
        seg_id = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=gen_id,
            audio_stream_id=stream_id,
        )
        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            segment_index=0,
            text="Deterministic speech.",
            duration_ms=1000,
            duration_finalized=True,
            transcript_finalized=True,
        )

        # First commit
        cmd = create_ack_command(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            phase="stopped",
            played_pts_ms=1000,
            reason="ended",
        )
        res = await container.playback.acknowledge(cmd)
        assert res.completed is True
        assert res.committed_event_id is not None

        # Verify exactly 1 spoken commit in event store
        events_1 = await container.event_store.read_stream(session_id)
        commits_1 = [
            e for e in events_1 if e.get("event_type") == "assistant.spoken_text_committed"
        ]
        assert len(commits_1) == 1

        # Simulate service restart: create fresh PlaybackService on same database & event store
        fresh_playback = PlaybackService(
            database=container.database,
            event_store=container.event_store,
            publisher=container.event_publisher,
        )

        # Replay finalize_segment -> must NOT duplicate commit
        dup_finalize = await fresh_playback.finalize_segment(seg_id, 1000)
        assert dup_finalize is None

        # Replay attach_transcript -> must NOT duplicate commit
        dup_attach = await fresh_playback.attach_transcript(seg_id, "Deterministic speech.")
        assert dup_attach is None

        # Verify event store STILL has exactly 1 commit event
        events_2 = await container.event_store.read_stream(session_id)
        commits_2 = [
            e for e in events_2 if e.get("event_type") == "assistant.spoken_text_committed"
        ]
        assert len(commits_2) == 1
    finally:
        await container.stop()


# ==============================================================================
# 6. Empty/no-audio and missing-ACK cases terminate cleanly without false text
# ==============================================================================


@pytest.mark.asyncio
async def test_06_text_only_and_deliberate_empty_transcript_cases(tmp_path: Path) -> None:
    """Acceptance 6: Empty/no-audio and deliberate empty transcripts
    terminate under defined policy.
    """
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        # --- Case 6A: Text-Only (0 audio chunks produced) completes immediately without hanging ---
        gen_text_only = uuid4()
        turn_text_only = uuid4()
        stream_text_only = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_text_only,
            generation_id=gen_text_only,
            audio_stream_id=stream_text_only,
        )

        # Domain sink handles response_completed for text-only generation
        domain_sink = RuntimeRealtimeDomainSink(
            container.conversation,
            playback=container.playback,
            event_hub=container.event_hub,
            backend_id="fake",
        )
        await domain_sink.response_completed(
            session_id=session_id,
            turn_id=turn_text_only,
            generation_id=gen_text_only,
            text="Text-only answer without audio.",
        )

        # Verify generation in DB completed immediately
        row_text = await container.database.fetchone(
            "SELECT state, spoken_text FROM generations WHERE generation_id = ?",
            (str(gen_text_only),),
        )
        assert row_text is not None
        assert row_text["state"] == "completed"
        # Turn message inserted
        messages = await container.conversation.list_messages(session_id)
        assert any(
            "Text-only answer without audio." in str(m.get("committed_text", "")) for m in messages
        )

        # --- Case 6B: Deliberate empty final transcript ("") with audio playout ---
        gen_empty = uuid4()
        turn_empty = uuid4()
        stream_empty = uuid4()
        seg_empty = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_empty,
            generation_id=gen_empty,
            audio_stream_id=stream_empty,
        )
        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_empty,
            stream_id=stream_empty,
            segment_id=seg_empty,
            segment_index=0,
            text="",
            duration_ms=500,
            duration_finalized=True,
            transcript_finalized=False,
        )
        # Client finishes playing
        await container.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_empty,
                stream_id=stream_empty,
                segment_id=seg_empty,
                phase="stopped",
                played_pts_ms=500,
                reason="ended",
            )
        )
        # Provider finalizes transcript with explicit empty string ""
        commit_empty = await container.playback.attach_generation_transcript(gen_empty, "")
        assert commit_empty is not None
        assert commit_empty.completed is True
        assert commit_empty.spoken_text == ""
        assert commit_empty.committed_event_id is None

        # --- Case 6C: Automatic missing-ACK timeout cleanup ---
        assert container.cloud_realtime_factory is not None
        bridge_missing = await container.cloud_realtime_factory.create_bridge(session_id)
        collector_missing = FrameCollector()
        bridge_missing.push_frame = collector_missing.push_frame  # type: ignore[assignment]
        try:
            await bridge_missing.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
            await bridge_missing.process_frame(
                VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            await bridge_missing.process_frame(
                VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            assert bridge_missing.current_identity is not None
            gen_missing = bridge_missing.current_identity.generation_id

            # Inject an event to deterministically trigger the missing-ACK timeout
            timeout_event = asyncio.Event()
            bridge_missing.coordinator.inject_ack_timeout_event(timeout_event)

            # Model produces response with audio
            await bridge_missing.coordinator.dispatch_event(
                ResponseStartedEvent(
                    session_id=session_id,
                    generation_id=gen_missing,
                    provider_response_id="resp-missing",
                )
            )
            await bridge_missing.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_missing,
                        sequence=1,
                        pts_ms=0,
                        sample_rate=24_000,
                        channels=1,
                        audio=b"\x11\x00" * 240,
                        is_final=True,
                    )
                )
            )
            await bridge_missing.coordinator.dispatch_event(
                AssistantTranscriptEvent(
                    candidate=RealtimeTranscriptCandidate(
                        session_id=session_id,
                        generation_id=gen_missing,
                        role="assistant",
                        phase="final",
                        text="Will time out waiting for ACK.",
                        provider_response_id="resp-missing",
                    )
                )
            )
            await bridge_missing.coordinator.dispatch_event(
                ResponseCompletedEvent(
                    session_id=session_id,
                    generation_id=gen_missing,
                    provider_response_id="resp-missing",
                    final_text="Will time out waiting for ACK.",
                )
            )

            # Generation is running and waiting for client ACK
            status_pre = await container.playback.status(session_id, gen_missing)
            assert status_pre["spoken_text"] == ""

            # Trigger injected timeout event and await task completion deterministically
            ack_task = bridge_missing.coordinator._missing_ack_tasks.get(gen_missing)
            timeout_event.set()
            if ack_task is not None:
                await ack_task

            # Generation in DB must be cancelled with zero spoken text
            row_missing = await container.database.fetchone(
                "SELECT state, spoken_text FROM generations WHERE generation_id = ?",
                (str(gen_missing),),
            )
            assert row_missing is not None
            assert row_missing["state"] == "cancelled"
            assert row_missing["spoken_text"] == ""
        finally:
            await bridge_missing.cleanup()
    finally:
        await container.stop()


# ==============================================================================
# 7. Media bridge + HTTP ACK integration via httpx.AsyncClient
# ==============================================================================


@pytest.mark.asyncio
async def test_07_http_ack_integration(tmp_path: Path) -> None:
    """Acceptance 7: Media bridge + HTTP ACK integration via httpx.AsyncClient.

    Verifies that client receiving markers over media bridge and posting ACK
    to /v1/sessions/{session_id}/playback/ack triggers coordinator completion,
    ConversationService generation completion, and durable SpokenMemoryObserver.
    """
    settings = create_cloud_settings(tmp_path)
    app = create_app(settings)
    container: RuntimeContainer = app.state.container
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id
        token = container.capability_token
        headers = {"Authorization": f"Bearer {token}"}

        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session_id)
        collector = FrameCollector()
        bridge.push_frame = collector.push_frame  # type: ignore[assignment]

        try:
            await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            assert bridge.current_identity is not None
            gen_id = bridge.current_identity.generation_id
            stream_id = bridge.current_identity.audio_stream_id

            await bridge.coordinator.dispatch_event(
                ResponseStartedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-http",
                )
            )
            await bridge.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_id,
                        sequence=1,
                        pts_ms=0,
                        sample_rate=24_000,
                        channels=1,
                        audio=b"\x12\x00" * 240,  # 10ms
                        is_final=True,
                    )
                )
            )
            await bridge.coordinator.dispatch_event(
                AssistantTranscriptEvent(
                    candidate=RealtimeTranscriptCandidate(
                        session_id=session_id,
                        generation_id=gen_id,
                        role="assistant",
                        phase="final",
                        text="HTTP confirmed speech.",
                        provider_response_id="resp-http",
                    )
                )
            )
            await bridge.coordinator.dispatch_event(
                ResponseCompletedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-http",
                    final_text="HTTP confirmed speech.",
                )
            )

            # Check markers emitted to downstream client
            msg_frames = [
                f.message
                for f in collector.frames
                if isinstance(f, OutputTransportMessageFrame) and isinstance(f.message, dict)
            ]
            assert len(msg_frames) == 2
            buffered_marker = msg_frames[1]
            assert buffered_marker["phase"] == "buffered"
            segment_id = UUID(str(buffered_marker["segment_id"]))
            duration_ms = int(str(buffered_marker["duration_ms"]))

            # Client posts playback ACK via HTTP route
            ack_cmd = {
                "command_id": str(uuid4()),
                "schema_version": "1.0",
                "command_type": "cmd.playback.ack",
                "issued_at": datetime.now(UTC).isoformat(),
                "issuer": "web.chat",
                "session_id": str(session_id),
                "generation_id": str(gen_id),
                "payload": {
                    "stream_id": str(stream_id),
                    "segment_id": str(segment_id),
                    "phase": "stopped",
                    "played_pts_ms": duration_ms,
                    "buffered_ms": duration_ms,
                    "client_clock_ms": duration_ms,
                    "transport": "webrtc",
                    "reason": "ended",
                },
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as http_client:
                ack_res = await http_client.post(
                    f"/v1/sessions/{session_id}/playback/ack",
                    json=ack_cmd,
                    headers=headers,
                )
            assert ack_res.status_code == 200
            ack_json = ack_res.json()
            assert ack_json["completed"] is True
            assert ack_json["spoken_text"] == "HTTP confirmed speech."

            # Verify generation is completed in SQLite
            row = await container.database.fetchone(
                "SELECT state, spoken_text FROM generations WHERE generation_id = ?",
                (str(gen_id),),
            )
            assert row is not None
            assert row["state"] == "completed"
            assert row["spoken_text"] == "HTTP confirmed speech."

            # Verify assistant.spoken_text_committed event
            events = await container.event_store.read_stream(session_id)
            spoken_events = [
                e for e in events if e.get("event_type") == "assistant.spoken_text_committed"
            ]
            assert len(spoken_events) == 1
        finally:
            await bridge.cleanup()
    finally:
        await container.stop()


# ==============================================================================
# 8. Multi-item queued output creates exactly 1 segment
# ==============================================================================


@pytest.mark.asyncio
async def test_08_multi_item_queued_output_one_segment(tmp_path: Path) -> None:
    """Acceptance 8: Multi-item queued output creates exactly 1 segment.

    A provider may produce multiple audio items/chunks in a single turn.
    Whole-response segment contract requires that exactly 1 segment is created,
    total duration is accumulated across items, and exactly 1 buffered marker follows
    all queued PCM.
    """
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session_id)
        collector = FrameCollector()
        bridge.push_frame = collector.push_frame  # type: ignore[assignment]

        try:
            await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            assert bridge.current_identity is not None
            gen_id = bridge.current_identity.generation_id
            stream_id = bridge.current_identity.audio_stream_id

            await bridge.coordinator.dispatch_event(
                ResponseStartedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-multi",
                )
            )

            # Item 1: 2 chunks of 10ms (240 samples each)
            pcm_chunk = b"\x15\x00" * 240
            await bridge.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_id,
                        sequence=1,
                        pts_ms=0,
                        sample_rate=24_000,
                        channels=1,
                        audio=pcm_chunk,
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
                        audio=pcm_chunk,
                        is_final=True,
                    )
                )
            )

            # Item 2: 2 more chunks of 10ms
            await bridge.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_id,
                        sequence=3,
                        pts_ms=20,
                        sample_rate=24_000,
                        channels=1,
                        audio=pcm_chunk,
                        is_final=False,
                    )
                )
            )
            await bridge.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_id,
                        sequence=4,
                        pts_ms=30,
                        sample_rate=24_000,
                        channels=1,
                        audio=pcm_chunk,
                        is_final=True,
                    )
                )
            )

            # Response completion
            await bridge.coordinator.dispatch_event(
                AssistantTranscriptEvent(
                    candidate=RealtimeTranscriptCandidate(
                        session_id=session_id,
                        generation_id=gen_id,
                        role="assistant",
                        phase="final",
                        text="Multi-item speech combined.",
                        provider_response_id="resp-multi",
                    )
                )
            )
            await bridge.coordinator.dispatch_event(
                ResponseCompletedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-multi",
                    final_text="Multi-item speech combined.",
                )
            )

            # Exactly 2 transport message frames: 1 started, 1 buffered
            msg_frames = [
                f.message
                for f in collector.frames
                if isinstance(f, OutputTransportMessageFrame) and isinstance(f.message, dict)
            ]
            assert len(msg_frames) == 2
            started_marker = msg_frames[0]
            assert started_marker["phase"] == "started"
            buffered_marker = msg_frames[1]
            assert buffered_marker["phase"] == "buffered"
            assert buffered_marker["duration_ms"] == 40  # 4 chunks * 10ms = 40ms
            segment_id = UUID(str(buffered_marker["segment_id"]))

            # Exactly 1 segment in database
            segments = await container.database.fetchall(
                "SELECT * FROM playback_segments WHERE generation_id = ?",
                (str(gen_id),),
            )
            assert len(segments) == 1
            assert segments[0]["duration_ms"] == 40
            assert segments[0]["segment_index"] == 0

            # Playout ACK completes generation
            ack_res = await container.playback.acknowledge(
                create_ack_command(
                    session_id=session_id,
                    generation_id=gen_id,
                    stream_id=stream_id,
                    segment_id=segment_id,
                    phase="stopped",
                    played_pts_ms=40,
                    reason="ended",
                )
            )
            assert ack_res.completed is True
            assert ack_res.spoken_text == "Multi-item speech combined."
        finally:
            await bridge.cleanup()
    finally:
        await container.stop()


# ==============================================================================
# 9. Stale late chunk after barge-in/cancellation cannot commit
# ==============================================================================


@pytest.mark.asyncio
async def test_09_stale_late_chunk_after_bargein_cannot_commit(tmp_path: Path) -> None:
    """Acceptance 9: Late chunks arriving after barge-in/cancellation cannot commit."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session_id)
        collector = FrameCollector()
        bridge.push_frame = collector.push_frame  # type: ignore[assignment]

        try:
            await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            assert bridge.current_identity is not None
            gen_id = bridge.current_identity.generation_id
            stream_id = bridge.current_identity.audio_stream_id

            await bridge.coordinator.dispatch_event(
                ResponseStartedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-stale",
                )
            )
            pcm_chunk = b"\x30\x00" * 240
            await bridge.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_id,
                        sequence=1,
                        pts_ms=0,
                        sample_rate=24_000,
                        channels=1,
                        audio=pcm_chunk,
                        is_final=False,
                    )
                )
            )

            # User barges in -> generation cancelled
            await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            assert bridge.coordinator.mirror.is_tombstoned(gen_id)

            # Late audio chunk arrives from provider after cancellation
            await bridge.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_id,
                        sequence=2,
                        pts_ms=10,
                        sample_rate=24_000,
                        channels=1,
                        audio=pcm_chunk,
                        is_final=True,
                    )
                )
            )
            # Late transcript and completed events arrive
            await bridge.coordinator.dispatch_event(
                AssistantTranscriptEvent(
                    candidate=RealtimeTranscriptCandidate(
                        session_id=session_id,
                        generation_id=gen_id,
                        role="assistant",
                        phase="final",
                        text="Late text after cancellation.",
                        provider_response_id="resp-stale",
                    )
                )
            )
            await bridge.coordinator.dispatch_event(
                ResponseCompletedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-stale",
                    final_text="Late text after cancellation.",
                )
            )

            # Generation in DB is cancelled
            gen_row = await container.database.fetchone(
                "SELECT state, spoken_text FROM generations WHERE generation_id = ?",
                (str(gen_id),),
            )
            assert gen_row is not None
            assert gen_row["state"] == "cancelled"
            assert gen_row["spoken_text"] == ""

            # Even if client sends an ended ACK claiming full playback, CAS rejects commit
            msg_frames = [
                f.message
                for f in collector.frames
                if isinstance(f, OutputTransportMessageFrame) and isinstance(f.message, dict)
            ]
            seg_id = UUID(str(msg_frames[0]["segment_id"]))
            ack_res = await container.playback.acknowledge(
                create_ack_command(
                    session_id=session_id,
                    generation_id=gen_id,
                    stream_id=stream_id,
                    segment_id=seg_id,
                    phase="stopped",
                    played_pts_ms=10,
                    reason="ended",
                )
            )
            assert ack_res.completed is False
            assert ack_res.spoken_text == ""
        finally:
            await bridge.cleanup()
    finally:
        await container.stop()


# ==============================================================================
# 10. Memory observation after transcript-after-ACK (Subtest 2A order)
# ==============================================================================


@pytest.mark.asyncio
async def test_10_memory_observation_after_transcript_after_ack(tmp_path: Path) -> None:
    """Acceptance 10: Spoken memory observer extracts memory when transcript arrives after ACK."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        gen_id = uuid4()
        turn_id = uuid4()
        stream_id = uuid4()
        seg_id = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=gen_id,
            audio_stream_id=stream_id,
        )
        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            segment_index=0,
            text="",
            duration_ms=1000,
            duration_finalized=True,
            transcript_finalized=False,
        )

        # 1. Playout ACK arrives first
        ack_res = await container.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_id,
                stream_id=stream_id,
                segment_id=seg_id,
                phase="stopped",
                played_pts_ms=1000,
                reason="ended",
            )
        )
        assert ack_res.completed is False

        # 2. Transcript arrives later -> commits segment and triggers SpokenMemoryObserver
        commit_res = await container.playback.attach_generation_transcript(
            gen_id, "We shared an unforgettable afternoon tea."
        )
        assert commit_res is not None
        assert commit_res.completed is True
        assert commit_res.spoken_text == "We shared an unforgettable afternoon tea."

        # Wait deterministically for observer async task
        await container.spoken_memory_observer.wait_until_idle()

        # Spoken text is committed in DB
        gen_row = await container.database.fetchone(
            "SELECT spoken_text FROM generations WHERE generation_id = ?",
            (str(gen_id),),
        )
        assert gen_row is not None
        assert gen_row["spoken_text"] == "We shared an unforgettable afternoon tea."

        # Verify AssistantSpokenTextCommittedEvent in event store
        events = await container.event_store.read_stream(session_id)
        assert any(e.get("event_type") == "assistant.spoken_text_committed" for e in events)
    finally:
        await container.stop()


# ==============================================================================
# 11. Durable memory observation survives container crash and restart
# ==============================================================================


@pytest.mark.asyncio
async def test_11_durable_memory_observation_on_restart(tmp_path: Path) -> None:
    """Verify SpokenMemoryObserver survives crash and extracts memory on restart.

    Ensures that when an assistant.spoken_text_committed event is recorded in the events
    table (even if outbox was marked published), a newly started container's
    SpokenMemoryObserver discovers the unprocessed event via backfill, records it into
    spoken_memory_facts, invokes extraction, and completes it.
    """
    settings = create_cloud_settings(tmp_path)
    c1 = RuntimeContainer(settings)
    await c1.start()
    session_id = uuid4()
    turn_id = uuid4()
    gen_id = uuid4()
    stream_id = uuid4()
    seg_id = uuid4()
    try:
        session = await c1.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        await c1.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=gen_id,
            audio_stream_id=stream_id,
        )
        await c1.playback.register_segment(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            segment_index=0,
            text="",
            duration_ms=500,
            duration_finalized=True,
            transcript_finalized=False,
        )
        await c1.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_id,
                stream_id=stream_id,
                segment_id=seg_id,
                phase="stopped",
                played_pts_ms=500,
                reason="ended",
            )
        )
        await c1.playback.attach_generation_transcript(
            gen_id, "I love hanging out in the botanical garden."
        )
    finally:
        await c1.stop()

    # Start a brand new container pointing at the exact same database
    c2 = RuntimeContainer(settings)

    class MockExtractor:
        async def extract(self, *args: object, **kwargs: object) -> list[ExtractedMemoryCandidate]:
            return [
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="I love hanging out in the botanical garden.",
                        observed_at=datetime.now(UTC),
                        confidence=0.95,
                        importance=0.8,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=True,
                    rationale="extracted spoken memory",
                )
            ]

    c2.memory._inference = MockExtractor()  # type: ignore[assignment]
    await c2.start()
    try:
        # Observer should process backfilled facts deterministically
        await c2.spoken_memory_observer.wait_until_idle()

        # Verify fact was recorded and marked completed in spoken_memory_facts
        facts = await c2.database.fetchall(
            "SELECT * FROM spoken_memory_facts WHERE session_id = ?",
            (str(session_id),),
        )
        assert len(facts) == 1
        assert facts[0]["state"] == "completed"
        assert facts[0]["completed_at"] is not None
        assert facts[0]["spoken_text"] == "I love hanging out in the botanical garden."

        # Verify memory records were extracted and stored in memory_records
        records = await c2.database.fetchall("SELECT * FROM memory_records")
        assert len(records) > 0
    finally:
        await c2.stop()


# ==============================================================================
# 12. Duration-aware deadline and empty-audio handling in coordinator
# ==============================================================================


@pytest.mark.asyncio
async def test_12_coordinator_duration_aware_ack_deadline(tmp_path: Path) -> None:
    """Verify duration-aware ACK deadline calculation and empty audio guard."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session_id)
        collector = FrameCollector()
        bridge.push_frame = collector.push_frame  # type: ignore[assignment]

        try:
            await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await bridge.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            assert bridge.current_identity is not None
            gen_id = bridge.current_identity.generation_id

            deadlines_captured: list[float] = []
            deadline_event = asyncio.Event()
            waiter_entered = asyncio.Event()

            async def mock_waiter(deadline: float) -> None:
                deadlines_captured.append(deadline)
                waiter_entered.set()
                await deadline_event.wait()

            bridge.coordinator.inject_ack_deadline_waiter(mock_waiter)

            # Response with 8,000 ms (8.0s) audio
            await bridge.coordinator.dispatch_event(
                ResponseStartedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-duration",
                )
            )
            # 8 seconds of 24kHz 16-bit mono PCM = 8 * 24000 * 2 = 384,000 bytes
            pcm_8s = b"\x10\x00" * (24_000 * 8)
            await bridge.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_id,
                        sequence=1,
                        pts_ms=0,
                        sample_rate=24_000,
                        channels=1,
                        audio=pcm_8s,
                        is_final=True,
                    )
                )
            )
            await bridge.coordinator.dispatch_event(
                AssistantTranscriptEvent(
                    candidate=RealtimeTranscriptCandidate(
                        session_id=session_id,
                        generation_id=gen_id,
                        role="assistant",
                        phase="final",
                        text="Long duration response.",
                        provider_response_id="resp-duration",
                    )
                )
            )
            await bridge.coordinator.dispatch_event(
                ResponseCompletedEvent(
                    session_id=session_id,
                    generation_id=gen_id,
                    provider_response_id="resp-duration",
                    final_text="Long duration response.",
                )
            )

            # Await the deadline waiter deterministically
            await waiter_entered.wait()
            # Deadline must be 8.0s audio + 5.0s ACK grace = 13.0s (not old hardcoded 5.0s)
            assert len(deadlines_captured) == 1
            assert deadlines_captured[0] == pytest.approx(13.0, rel=1e-2)

            deadline_event.set()
        finally:
            await bridge.cleanup()

        # Subtest: Empty-audio frames (b"") must NOT mark has_audio or start missing-ACK timer
        bridge_empty = await container.cloud_realtime_factory.create_bridge(session_id)
        collector_empty = FrameCollector()
        bridge_empty.push_frame = collector_empty.push_frame  # type: ignore[assignment]
        try:
            await bridge_empty.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
            await bridge_empty.process_frame(
                VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            await bridge_empty.process_frame(
                VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
            )
            assert bridge_empty.current_identity is not None
            gen_empty = bridge_empty.current_identity.generation_id

            await bridge_empty.coordinator.dispatch_event(
                ResponseStartedEvent(
                    session_id=session_id,
                    generation_id=gen_empty,
                    provider_response_id="resp-empty",
                )
            )
            # Empty audio chunk
            await bridge_empty.coordinator.dispatch_event(
                OutputAudioEvent(
                    frame=RealtimeOutputAudioFrame(
                        session_id=session_id,
                        generation_id=gen_empty,
                        sequence=1,
                        pts_ms=0,
                        sample_rate=24_000,
                        channels=1,
                        audio=b"",
                        is_final=True,
                    )
                )
            )
            assert not bridge_empty.coordinator.mirror.has_audio(gen_empty)

            await bridge_empty.coordinator.dispatch_event(
                ResponseCompletedEvent(
                    session_id=session_id,
                    generation_id=gen_empty,
                    provider_response_id="resp-empty",
                    final_text="Silent completion.",
                )
            )
            # Completed immediately as text-only response, no missing ACK task created
            assert gen_empty not in bridge_empty.coordinator._missing_ack_tasks
            gen_row = await container.database.fetchone(
                "SELECT state, spoken_text FROM generations WHERE generation_id = ?",
                (str(gen_empty),),
            )
            assert gen_row is not None
            assert gen_row["state"] == "completed"
        finally:
            await bridge_empty.cleanup()
    finally:
        await container.stop()


# ==============================================================================
# 13. Listener registration ownership prevents cross-bridge unregistration
# ==============================================================================


@pytest.mark.asyncio
async def test_13_listener_registration_ownership(tmp_path: Path) -> None:
    """Verify listener registration returns an owned token preventing cross-bridge clobbering."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        calls_1: list[UUID] = []
        calls_2: list[UUID] = []

        async def listener_1(gen_id: UUID, turn_id: UUID | None, text: str) -> None:
            calls_1.append(gen_id)

        async def listener_2(gen_id: UUID, turn_id: UUID | None, text: str) -> None:
            calls_2.append(gen_id)

        token_1 = container.playback.register_completion_listener(session_id, listener_1)
        token_2 = container.playback.register_completion_listener(session_id, listener_2)

        assert token_1 != token_2

        # Old bridge unregisters using its token_1: must return False and NOT unregister bridge 2
        unregistered = container.playback.unregister_completion_listener(session_id, token_1)
        assert unregistered is False

        # Attempt to unregister with random mismatched token
        assert container.playback.unregister_completion_listener(session_id, uuid4()) is False

        # Complete a segment via playback ACK -> listener 2 must be invoked, listener 1 must not
        turn_id = uuid4()
        gen_test = uuid4()
        stream_id = uuid4()
        seg_id = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=gen_test,
            audio_stream_id=stream_id,
        )
        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_test,
            stream_id=stream_id,
            segment_id=seg_id,
            segment_index=0,
            text="test text",
            duration_ms=500,
            duration_finalized=True,
            transcript_finalized=True,
        )
        await container.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_test,
                stream_id=stream_id,
                segment_id=seg_id,
                phase="stopped",
                played_pts_ms=500,
                reason="ended",
            )
        )

        assert len(calls_1) == 0
        assert len(calls_2) == 1
        assert calls_2[0] == gen_test

        # Cleanly unregister listener 2 with its matching token_2
        assert container.playback.unregister_completion_listener(session_id, token_2) is True
    finally:
        await container.stop()


# ==============================================================================
# 14. Segment immutability and no synthetic turn IDs
# ==============================================================================


@pytest.mark.asyncio
async def test_14_segment_immutability_and_no_synthetic_turn(tmp_path: Path) -> None:
    """Verify segment duration and transcript are immutable; turn_id is not synthetic."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id
        admitted_turn_id = uuid4()
        gen_id = uuid4()
        stream_id = uuid4()
        seg_id = uuid4()

        # Turn admission with admitted turn_id
        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=admitted_turn_id,
            generation_id=gen_id,
            audio_stream_id=stream_id,
        )

        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            segment_index=0,
            text="",
            duration_ms=1000,
            duration_finalized=False,
            transcript_finalized=False,
        )

        # 1. Finalize segment duration to 1000ms
        await container.playback.finalize_segment(seg_id, 1000)
        # Idempotent same-duration call succeeds
        await container.playback.finalize_segment(seg_id, 1000)
        # Conflicting duration is ignored (immutability)
        await container.playback.finalize_segment(seg_id, 9999)

        seg_row = await container.database.fetchone(
            "SELECT duration_ms FROM playback_segments WHERE segment_id = ?",
            (str(seg_id),),
        )
        assert seg_row is not None
        assert seg_row["duration_ms"] == 1000

        # 2. Attach transcript "Original text"
        await container.playback.attach_generation_transcript(gen_id, "Original text")
        # Idempotent same-transcript call succeeds
        await container.playback.attach_generation_transcript(gen_id, "Original text")
        # Conflicting transcript is ignored
        await container.playback.attach_generation_transcript(gen_id, "Conflicting text")

        seg_row2 = await container.database.fetchone(
            "SELECT text FROM playback_segments WHERE segment_id = ?",
            (str(seg_id),),
        )
        assert seg_row2 is not None
        assert seg_row2["text"] == "Original text"

        # 3. Playout ACK completes the segment and records event
        await container.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_id,
                stream_id=stream_id,
                segment_id=seg_id,
                phase="stopped",
                played_pts_ms=1000,
                reason="ended",
            )
        )

        # Verify assistant.spoken_text_committed event preserves admitted turn_id exactly
        events = await container.event_store.read_stream(session_id)
        spoken_events = [
            e for e in events if e.get("event_type") == "assistant.spoken_text_committed"
        ]
        assert len(spoken_events) == 1
        assert spoken_events[0].get("turn_id") == str(admitted_turn_id)
    finally:
        await container.stop()


# ==============================================================================
# 15. Crash after apply before mark_completed idempotency
# ==============================================================================


@pytest.mark.asyncio
async def test_15_crash_after_apply_before_mark_completed_idempotency(
    tmp_path: Path,
) -> None:
    """Acceptance 15: Crash after candidate apply before mark_completed.

    Ensures that when a crash occurs after proposals, records, and sources are saved,
    the resumed worker on container restart uses the durably staged candidates, does not
    re-invoke the model extractor, and deterministic IDs prevent duplicate proposals/records.
    """
    settings = create_cloud_settings(tmp_path)
    c1 = RuntimeContainer(settings)

    applied_event = asyncio.Event()

    class C1MockExtractor:
        async def extract(self, *args: object, **kwargs: object) -> list[ExtractedMemoryCandidate]:
            return [
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="We enjoyed iced matcha by the fountain.",
                        observed_at=datetime.now(UTC),
                        confidence=0.95,
                        importance=0.8,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=True,
                    rationale="spoken memory",
                )
            ]

    c1.memory._inference = C1MockExtractor()  # type: ignore[assignment]

    async def hook_after_apply(fact: SpokenMemoryFact) -> None:
        applied_event.set()
        await asyncio.Event().wait()  # Block until c1.stop() cancels the worker

    c1.spoken_memory_observer.hook_after_apply = hook_after_apply

    await c1.start()
    session_id: UUID
    try:
        session = await c1.sessions.create_session("ayachi_nene")
        session_id = session.session_id
        gen_id = uuid4()
        turn_id = uuid4()
        stream_id = uuid4()
        seg_id = uuid4()

        await c1.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=gen_id,
            audio_stream_id=stream_id,
        )
        await c1.playback.register_segment(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            segment_index=0,
            text="",
            duration_ms=600,
            duration_finalized=True,
            transcript_finalized=False,
        )
        await c1.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_id,
                stream_id=stream_id,
                segment_id=seg_id,
                phase="stopped",
                played_pts_ms=600,
                reason="ended",
            )
        )
        await c1.playback.attach_generation_transcript(
            gen_id, "We enjoyed iced matcha by the fountain."
        )

        # Wait until candidate has been applied in c1
        await applied_event.wait()
    finally:
        await c1.stop()

    # Verify state after crash with brand new container c2
    c2 = RuntimeContainer(settings)

    class C2MockExtractor:
        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, *args: object, **kwargs: object) -> list[ExtractedMemoryCandidate]:
            self.calls += 1
            return [
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="Different wording that must not be created.",
                        observed_at=datetime.now(UTC),
                        confidence=0.95,
                        importance=0.8,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=True,
                    rationale="different wording",
                )
            ]

    c2_extractor = C2MockExtractor()
    c2.memory._inference = c2_extractor  # type: ignore[assignment]
    await c2.start()
    try:
        await c2.spoken_memory_observer.wait_until_idle()

        # 1. Extractor was NEVER called on c2 because candidates were durably staged
        assert c2_extractor.calls == 0

        # 2. Proposals, records, and sources are strictly 1 (no duplicates)
        proposals = await c2.database.fetchall(
            "SELECT * FROM memory_proposals WHERE candidate_json LIKE '%fountain%'"
        )
        assert len(proposals) == 1

        records = await c2.database.fetchall("SELECT * FROM memory_records")
        assert len(records) == 1
        assert records[0]["text"] == "We enjoyed iced matcha by the fountain."

        sources = await c2.database.fetchall(
            "SELECT * FROM memory_sources WHERE session_id = ?",
            (str(session_id),),
        )
        assert len(sources) == 1

        # 3. Spoken fact state is marked completed
        facts = await c2.database.fetchall(
            "SELECT * FROM spoken_memory_facts WHERE session_id = ?",
            (str(session_id),),
        )
        assert len(facts) == 1
        assert facts[0]["state"] == "completed"
        assert facts[0]["completed_at"] is not None
    finally:
        await c2.stop()


# ==============================================================================
# 16. Crash midway through multi-candidate apply resumes remainder
# ==============================================================================


@pytest.mark.asyncio
async def test_16_crash_midway_multi_candidate_apply_resumes_remainder(
    tmp_path: Path,
) -> None:
    """Acceptance 16: Crash midway through multi-candidate apply resumes remainder.

    Verifies that when a fact contains multiple candidates and crashes after candidate 0
    is applied, restart resumes from checkpoint_index=1, applies candidate 1, and
    does not duplicate candidate 0.
    """
    settings = create_cloud_settings(tmp_path)
    c1 = RuntimeContainer(settings)

    class MultiCandidateExtractor:
        async def extract(self, *args: object, **kwargs: object) -> list[ExtractedMemoryCandidate]:
            now = datetime.now(UTC)
            return [
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="Candidate zero: Nene loves strawberries.",
                        observed_at=now,
                        confidence=0.95,
                        importance=0.8,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=True,
                    rationale="cand 0",
                ),
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="Candidate one: Nene visited the botanical garden.",
                        observed_at=now,
                        confidence=0.92,
                        importance=0.85,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=True,
                    rationale="cand 1",
                ),
            ]

    c1.memory._inference = MultiCandidateExtractor()  # type: ignore[assignment]

    c0_applied_event = asyncio.Event()

    async def hook_candidate_applied(fact: SpokenMemoryFact, applied_index: int) -> None:
        if applied_index == 0:
            c0_applied_event.set()
            await asyncio.Event().wait()  # Crash after candidate 0 applied and checkpointed

    c1.spoken_memory_observer.hook_after_candidate_applied = hook_candidate_applied

    await c1.start()
    session_id: UUID
    try:
        session = await c1.sessions.create_session("ayachi_nene")
        session_id = session.session_id
        gen_id = uuid4()
        turn_id = uuid4()
        stream_id = uuid4()
        seg_id = uuid4()

        await c1.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=gen_id,
            audio_stream_id=stream_id,
        )
        await c1.playback.register_segment(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            segment_index=0,
            text="",
            duration_ms=800,
            duration_finalized=True,
            transcript_finalized=False,
        )
        await c1.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_id,
                stream_id=stream_id,
                segment_id=seg_id,
                phase="stopped",
                played_pts_ms=800,
                reason="ended",
            )
        )
        await c1.playback.attach_generation_transcript(
            gen_id, "Nene loves strawberries and visited the garden."
        )

        await c0_applied_event.wait()

        # In c1 right after candidate 0 is applied:
        fact_c1 = await c1.database.fetchone(
            "SELECT checkpoint_index, state FROM spoken_memory_facts WHERE session_id = ?",
            (str(session_id),),
        )
        assert fact_c1 is not None
        assert fact_c1["checkpoint_index"] == 1

        proposals_c1 = await c1.database.fetchall("SELECT * FROM memory_proposals")
        assert len(proposals_c1) == 1

        records_c1 = await c1.database.fetchall("SELECT * FROM memory_records")
        assert len(records_c1) == 1
        assert "Candidate zero" in records_c1[0]["text"]
    finally:
        await c1.stop()

    # Verify DB state after restart with container c2
    c2 = RuntimeContainer(settings)
    await c2.start()
    try:
        # Resume processing
        await c2.spoken_memory_observer.wait_until_idle()

        # Both candidates are now applied, exactly 2 proposals and 2 records
        fact_post = await c2.database.fetchone(
            "SELECT checkpoint_index, state, completed_at FROM spoken_memory_facts "
            "WHERE session_id = ?",
            (str(session_id),),
        )
        assert fact_post is not None
        assert fact_post["checkpoint_index"] == 2
        assert fact_post["state"] == "completed"
        assert fact_post["completed_at"] is not None

        proposals_post = await c2.database.fetchall("SELECT * FROM memory_proposals")
        assert len(proposals_post) == 2

        records_post = await c2.database.fetchall(
            "SELECT * FROM memory_records ORDER BY created_at ASC"
        )
        assert len(records_post) == 2
        assert "Candidate zero" in records_post[0]["text"]
        assert "Candidate one" in records_post[1]["text"]
    finally:
        await c2.stop()


# ==============================================================================
# 17. Pending-review proposals duplicate prevention on crash
# ==============================================================================


@pytest.mark.asyncio
async def test_17_pending_review_proposals_duplicate_prevention_on_crash(
    tmp_path: Path,
) -> None:
    """Acceptance 17: Low-confidence/unconfirmed proposal with status 'pending'

    Ensures that if a crash occurs after a pending-review proposal is persisted,
    re-running does not duplicate the pending proposal.
    """
    settings = create_cloud_settings(tmp_path)
    c1 = RuntimeContainer(settings)

    class ReviewCandidateExtractor:
        async def extract(self, *args: object, **kwargs: object) -> list[ExtractedMemoryCandidate]:
            return [
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="The user might be interested in stargazing.",
                        observed_at=datetime.now(UTC),
                        confidence=0.85,
                        importance=0.5,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=False,  # Triggers MemoryWriteDecision.REVIEW -> status 'pending'
                    rationale="uncertain observation",
                )
            ]

    c1.memory._inference = ReviewCandidateExtractor()  # type: ignore[assignment]

    hook_entered = asyncio.Event()

    async def hook_after_apply(fact: SpokenMemoryFact) -> None:
        hook_entered.set()
        await asyncio.Event().wait()  # Crash before mark_completed

    c1.spoken_memory_observer.hook_after_apply = hook_after_apply

    await c1.start()
    session_id: UUID
    try:
        session = await c1.sessions.create_session("ayachi_nene")
        session_id = session.session_id
        gen_id = uuid4()
        turn_id = uuid4()
        stream_id = uuid4()
        seg_id = uuid4()

        await c1.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=gen_id,
            audio_stream_id=stream_id,
        )
        await c1.playback.register_segment(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            segment_index=0,
            text="",
            duration_ms=500,
            duration_finalized=True,
            transcript_finalized=False,
        )
        await c1.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_id,
                stream_id=stream_id,
                segment_id=seg_id,
                phase="stopped",
                played_pts_ms=500,
                reason="ended",
            )
        )
        await c1.playback.attach_generation_transcript(gen_id, "Stargazing tonight?")

        await hook_entered.wait()
    finally:
        await c1.stop()

    # Restart c2
    c2 = RuntimeContainer(settings)
    await c2.start()
    try:
        await c2.spoken_memory_observer.wait_until_idle()

        # Exactly 1 proposal with status 'pending'
        proposals = await c2.database.fetchall(
            "SELECT * FROM memory_proposals WHERE candidate_json LIKE '%stargazing%'"
        )
        assert len(proposals) == 1
        assert proposals[0]["status"] == "pending"

        # 0 active records
        records = await c2.database.fetchall("SELECT * FROM memory_records")
        assert len(records) == 0

        # Spoken fact is completed
        facts = await c2.database.fetchall(
            "SELECT * FROM spoken_memory_facts WHERE session_id = ?",
            (str(session_id),),
        )
        assert len(facts) == 1
        assert facts[0]["state"] == "completed"
    finally:
        await c2.stop()


# ==============================================================================
# 18. Bounded retry backoff and non-starvation
# ==============================================================================


@pytest.mark.asyncio
async def test_18_bounded_retry_backoff_and_non_starvation(tmp_path: Path) -> None:
    """Acceptance 18: Exponential backoff schedule, non-starvation of newer facts,

    and dead-letter transition after max_retries.
    """
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)

    class SimulatedClock:
        def __init__(self, start: datetime) -> None:
            self.now = start

        def __call__(self) -> datetime:
            return self.now

        def advance(self, seconds: float) -> None:
            self.now += timedelta(seconds=seconds)

    sim_clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))
    container.spoken_memory_observer._clock = sim_clock
    container.spoken_memory_observer._max_retries = 3
    container.spoken_memory_observer._initial_retry_delay_s = 10.0
    container.spoken_memory_observer._backoff_factor = 2.0

    class ConditionalFailingExtractor:
        async def extract(
            self, text: str, *args: object, **kwargs: object
        ) -> list[ExtractedMemoryCandidate]:
            if "fail_fact" in text:
                raise RuntimeError("Simulated transient LLM rate limit")
            return [
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="Successful memory extracted.",
                        observed_at=sim_clock(),
                        confidence=0.95,
                        importance=0.8,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=True,
                    rationale="success",
                )
            ]

    container.memory._inference = ConditionalFailingExtractor()  # type: ignore[assignment]

    retry_event = asyncio.Event()
    orig_record_retry_failure = container.spoken_memory_repository.record_retry_failure

    async def hook_record_retry(
        source_event_id: UUID, last_error: str, retry_count: int, next_retry_at: datetime
    ) -> None:
        await orig_record_retry_failure(source_event_id, last_error, retry_count, next_retry_at)
        retry_event.set()

    container.spoken_memory_repository.record_retry_failure = hook_record_retry  # type: ignore[assignment]

    fact2_completed_event = asyncio.Event()
    orig_mark_completed = container.spoken_memory_repository.mark_completed

    async def hook_mark_completed(source_event_id: UUID, completed_at: datetime) -> None:
        await orig_mark_completed(source_event_id, completed_at)
        fact2_completed_event.set()

    container.spoken_memory_repository.mark_completed = hook_mark_completed  # type: ignore[assignment]

    dead_letter_event = asyncio.Event()
    orig_record_dead_letter = container.spoken_memory_repository.record_dead_letter

    async def hook_record_dead_letter(
        source_event_id: UUID, last_error: str, retry_count: int
    ) -> None:
        await orig_record_dead_letter(source_event_id, last_error, retry_count)
        dead_letter_event.set()

    container.spoken_memory_repository.record_dead_letter = hook_record_dead_letter  # type: ignore[assignment]

    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        # 1. Commit Fact 1 (which fails)
        gen_1 = uuid4()
        turn_1 = uuid4()
        stream_1 = uuid4()
        seg_1 = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_1,
            generation_id=gen_1,
            audio_stream_id=stream_1,
        )
        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_1,
            stream_id=stream_1,
            segment_id=seg_1,
            segment_index=0,
            text="fail_fact 1",
            duration_ms=400,
            duration_finalized=True,
            transcript_finalized=True,
        )
        await container.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_1,
                stream_id=stream_1,
                segment_id=seg_1,
                phase="stopped",
                played_pts_ms=400,
                reason="ended",
            )
        )

        # Await attempt 1 retry failure event deterministically
        await retry_event.wait()
        retry_event.clear()

        fact_1 = await container.database.fetchone(
            "SELECT retry_count, next_retry_at, state FROM spoken_memory_facts "
            "WHERE spoken_text = 'fail_fact 1'"
        )
        assert fact_1 is not None
        assert fact_1["state"] == "pending"
        assert fact_1["retry_count"] == 1
        assert fact_1["next_retry_at"] is not None
        next_retry_1 = datetime.fromisoformat(fact_1["next_retry_at"])
        assert next_retry_1 > sim_clock()

        # 2. Fact 2 arrives while Fact 1 is backed off -> must NOT be starved
        gen_2 = uuid4()
        turn_2 = uuid4()
        stream_2 = uuid4()
        seg_2 = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_2,
            generation_id=gen_2,
            audio_stream_id=stream_2,
        )
        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_2,
            stream_id=stream_2,
            segment_id=seg_2,
            segment_index=0,
            text="success_fact 2",
            duration_ms=500,
            duration_finalized=True,
            transcript_finalized=True,
        )
        await container.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_2,
                stream_id=stream_2,
                segment_id=seg_2,
                phase="stopped",
                played_pts_ms=500,
                reason="ended",
            )
        )

        # Fact 2 is processed and completed immediately
        await fact2_completed_event.wait()

        fact_2 = await container.database.fetchone(
            "SELECT state FROM spoken_memory_facts WHERE spoken_text = 'success_fact 2'"
        )
        assert fact_2 is not None
        assert fact_2["state"] == "completed"

        # Fact 1 is STILL in backoff delay (non-starvation confirmed)
        fact_1_check = await container.database.fetchone(
            "SELECT retry_count, state FROM spoken_memory_facts WHERE spoken_text = 'fail_fact 1'"
        )
        assert fact_1_check is not None
        assert fact_1_check["state"] == "pending"
        assert fact_1_check["retry_count"] == 1

        # 3. Advance clock past next_retry_1 to trigger retry attempt 2
        sim_clock.advance(15.0)
        container.spoken_memory_observer._wakeup_event.set()
        await retry_event.wait()
        retry_event.clear()

        fact_1_r2 = await container.database.fetchone(
            "SELECT retry_count, next_retry_at FROM spoken_memory_facts "
            "WHERE spoken_text = 'fail_fact 1'"
        )
        assert fact_1_r2 is not None
        assert fact_1_r2["retry_count"] == 2

        # 4. Advance clock past next_retry_2 to trigger attempt 3 (max_retries -> dead letter)
        sim_clock.advance(25.0)
        container.spoken_memory_observer._wakeup_event.set()
        await dead_letter_event.wait()

        fact_1_r3 = await container.database.fetchone(
            "SELECT state, retry_count, last_error FROM spoken_memory_facts "
            "WHERE spoken_text = 'fail_fact 1'"
        )
        assert fact_1_r3 is not None
        assert fact_1_r3["state"] == "failed"
        assert fact_1_r3["retry_count"] == 3
        assert fact_1_r3["last_error"] == "RuntimeError"
        assert "Simulated transient LLM rate limit" not in str(fact_1_r3["last_error"])
    finally:
        await container.stop()


# ==============================================================================
# 19. Privacy reset and tombstone fences
# ==============================================================================


@pytest.mark.asyncio
async def test_19_privacy_reset_and_tombstone_fences(tmp_path: Path) -> None:
    """Acceptance 19: Character experience resets and tombstones prevent resurrection

    of deleted or forgotten memories.
    """
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)

    class GuitarMemoryExtractor:
        async def extract(self, *args: object, **kwargs: object) -> list[ExtractedMemoryCandidate]:
            return [
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="Ayachi loves playing guitar.",
                        observed_at=datetime.now(UTC),
                        confidence=0.95,
                        importance=0.8,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=True,
                    rationale="guitar memory",
                )
            ]

    container.memory._inference = GuitarMemoryExtractor()  # type: ignore[assignment]
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        # --- Subtest 19A: Tombstone Forget Fence ---
        # 1. First spoken turn creates active memory
        gen_1 = uuid4()
        turn_1 = uuid4()
        stream_1 = uuid4()
        seg_1 = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_1,
            generation_id=gen_1,
            audio_stream_id=stream_1,
        )
        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_1,
            stream_id=stream_1,
            segment_id=seg_1,
            segment_index=0,
            text="Ayachi loves playing guitar.",
            duration_ms=600,
            duration_finalized=True,
            transcript_finalized=True,
        )
        await container.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_1,
                stream_id=stream_1,
                segment_id=seg_1,
                phase="stopped",
                played_pts_ms=600,
                reason="ended",
            )
        )

        await container.spoken_memory_observer.wait_until_idle()

        # Active record exists in DB
        active_records = await container.database.fetchall(
            "SELECT * FROM memory_records WHERE state = 'active'"
        )
        assert len(active_records) == 1
        record_id = UUID(active_records[0]["memory_id"])

        # 2. User forgets this memory (creates tombstone)
        forgotten = await container.memory.forget(session_id, record_id)
        assert forgotten is True

        tombstoned_records = await container.database.fetchall(
            "SELECT * FROM memory_records WHERE state = 'tombstoned'"
        )
        assert len(tombstoned_records) == 1

        # 3. Second spoken turn with the exact same fact
        gen_2 = uuid4()
        turn_2 = uuid4()
        stream_2 = uuid4()
        seg_2 = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_2,
            generation_id=gen_2,
            audio_stream_id=stream_2,
        )
        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_2,
            stream_id=stream_2,
            segment_id=seg_2,
            segment_index=0,
            text="Ayachi loves playing guitar.",
            duration_ms=600,
            duration_finalized=True,
            transcript_finalized=True,
        )
        await container.playback.acknowledge(
            create_ack_command(
                session_id=session_id,
                generation_id=gen_2,
                stream_id=stream_2,
                segment_id=seg_2,
                phase="stopped",
                played_pts_ms=600,
                reason="ended",
            )
        )

        await container.spoken_memory_observer.wait_until_idle()

        # Proposal was marked 'ignored' due to tombstone fence
        proposals = await container.database.fetchall(
            "SELECT * FROM memory_proposals WHERE status = 'ignored'"
        )
        assert len(proposals) >= 1
        assert any("tombstoned" in str(p["rationale"]) for p in proposals)

        # Still 0 active records
        active_records_after = await container.database.fetchall(
            "SELECT * FROM memory_records WHERE state = 'active'"
        )
        assert len(active_records_after) == 0

        # --- Subtest 19B: Scope Reset Privacy Fence ---
        # Clear scope for ayachi_nene
        await container.memory.clear_scope("ayachi_nene")

        # Verify memory_scope_resets has character reset recorded
        reset_rows = await container.database.fetchall(
            "SELECT * FROM memory_scope_resets WHERE character_id = 'ayachi_nene'"
        )
        assert len(reset_rows) >= 1
        reset_at = datetime.fromisoformat(reset_rows[0]["reset_at"])

        # Facts preceding reset_at are filtered out from backfill
        backfilled = await container.spoken_memory_repository.backfill_unprocessed_spoken_events(
            limit=50
        )
        assert backfilled == 0

        # is_scope_reset correctly identifies past events as fenced
        past_time = reset_at - timedelta(seconds=10)
        assert (
            await container.spoken_memory_repository.is_scope_reset("ayachi_nene", past_time)
            is True
        )
    finally:
        await container.stop()


# ==============================================================================
# 20. Transactional spoken commit and bounded periodic reconcile
# ==============================================================================


@pytest.mark.asyncio
async def test_20_transactional_spoken_commit_and_bounded_reconcile(
    tmp_path: Path,
) -> None:
    """Acceptance 20: Transactional spoken commit and bounded periodic reconcile.

    1. PlaybackService._try_commit_segment inserts assistant.spoken_text_committed
       and spoken_memory_facts atomically.
    2. Bounded backfill reconciles unprocessed events when EventHub delivery is missed.
    """
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)

    class MockExtractor:
        async def extract(self, *args: object, **kwargs: object) -> list[ExtractedMemoryCandidate]:
            return [
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="Sunset on the coast.",
                        observed_at=datetime.now(UTC),
                        confidence=0.95,
                        importance=0.8,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=True,
                    rationale="reconcile test",
                )
            ]

    container.memory._inference = MockExtractor()  # type: ignore[assignment]
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        session_id = session.session_id

        # --- Part 1: Transactional spoken commit ---
        gen_id = uuid4()
        turn_id = uuid4()
        stream_id = uuid4()
        seg_id = uuid4()

        await container.conversation.begin_realtime_generation(
            session_id=session_id,
            turn_id=turn_id,
            generation_id=gen_id,
            audio_stream_id=stream_id,
        )
        await container.playback.register_segment(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            segment_index=0,
            text="Sunset on the coast.",
            duration_ms=500,
            duration_finalized=True,
            transcript_finalized=True,
        )

        ack_cmd = create_ack_command(
            session_id=session_id,
            generation_id=gen_id,
            stream_id=stream_id,
            segment_id=seg_id,
            phase="stopped",
            played_pts_ms=500,
            reason="ended",
        )
        ack_res = await container.playback.acknowledge(ack_cmd)
        assert ack_res.completed is True
        committed_event_id = ack_res.committed_event_id
        assert committed_event_id is not None

        # Verify fact exists in spoken_memory_facts
        fact_row = await container.database.fetchone(
            "SELECT * FROM spoken_memory_facts WHERE source_event_id = ?",
            (str(committed_event_id),),
        )
        assert fact_row is not None
        assert fact_row["spoken_text"] == "Sunset on the coast."

        await container.spoken_memory_observer.wait_until_idle()

        # --- Part 2: Bounded reconcile for dropped EventHub event ---
        dropped_event_id = uuid4()
        dropped_turn_id = uuid4()
        now = datetime.now(UTC)

        # Bypass EventHub and insert event directly into events table
        await container.database.execute(
            """
            INSERT INTO events(
                event_id, session_id, sequence, event_type, schema_version,
                occurred_at, source, correlation_id, causation_id, payload_json, envelope_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(dropped_event_id),
                str(session_id),
                999,
                "assistant.spoken_text_committed",
                "1.0",
                now.isoformat(),
                "runtime.playback",
                None,
                None,
                json.dumps(
                    {
                        "stream_id": str(uuid4()),
                        "segment_id": str(uuid4()),
                        "turn_id": str(dropped_turn_id),
                        "text": "Dropped event that missed EventHub.",
                        "spoken_text": "Dropped event that missed EventHub.",
                    }
                ),
                json.dumps({"turn_id": str(dropped_turn_id)}),
            ),
        )

        # Confirm not yet in spoken_memory_facts
        fact_dropped_pre = await container.database.fetchone(
            "SELECT * FROM spoken_memory_facts WHERE source_event_id = ?",
            (str(dropped_event_id),),
        )
        assert fact_dropped_pre is None

        # Existing dead letters must not occupy the bounded scan ahead of missing facts.
        await container.database.execute("UPDATE spoken_memory_facts SET state = 'failed'")
        backfilled_count = (
            await container.spoken_memory_repository.backfill_unprocessed_spoken_events(limit=1)
        )
        assert backfilled_count >= 1

        # Wake up observer and wait for idle
        container.spoken_memory_observer._wakeup_event.set()
        await container.spoken_memory_observer.wait_until_idle()

        fact_dropped_post = await container.database.fetchone(
            "SELECT state, completed_at FROM spoken_memory_facts WHERE source_event_id = ?",
            (str(dropped_event_id),),
        )
        assert fact_dropped_post is not None
        assert fact_dropped_post["state"] == "completed"
        assert fact_dropped_post["completed_at"] is not None
    finally:
        await container.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_scope", ["character", "all"])
async def test_reset_while_spoken_candidates_are_staged_does_not_restore_memory(
    tmp_path: Path, reset_scope: str
) -> None:
    container = RuntimeContainer(create_cloud_settings(tmp_path))
    staged = asyncio.Event()
    release = asyncio.Event()
    applied = asyncio.Event()

    class Extractor:
        async def extract(self, *args: object, **kwargs: object) -> list[ExtractedMemoryCandidate]:
            return [
                ExtractedMemoryCandidate(
                    draft=MemoryRecordDraft(
                        namespace="character/ayachi_nene/user/local",
                        kind="episodic.shared_event",
                        text="We visited the garden.",
                        observed_at=datetime.now(UTC),
                        confidence=0.95,
                        importance=0.8,
                        sensitivity=PrivacyLevel.PRIVATE,
                    ),
                    explicit=True,
                    rationale="heard shared event",
                )
            ]

    async def pause_after_stage(fact: object) -> None:
        staged.set()
        await release.wait()

    async def finished_apply(fact: object) -> None:
        applied.set()

    container.memory._inference = Extractor()  # type: ignore[assignment]
    container.spoken_memory_observer.hook_after_stage = pause_after_stage
    container.spoken_memory_observer.hook_after_apply = finished_apply
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        generation_id, turn_id, stream_id, segment_id = uuid4(), uuid4(), uuid4(), uuid4()
        await container.conversation.begin_realtime_generation(
            session_id=session.session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            audio_stream_id=stream_id,
        )
        await container.playback.register_segment(
            session_id=session.session_id,
            generation_id=generation_id,
            stream_id=stream_id,
            segment_id=segment_id,
            segment_index=0,
            text="We visited the garden.",
            duration_ms=500,
        )
        await container.playback.acknowledge(
            create_ack_command(
                session_id=session.session_id,
                generation_id=generation_id,
                stream_id=stream_id,
                segment_id=segment_id,
                phase="stopped",
                played_pts_ms=500,
                reason="ended",
            )
        )
        await asyncio.wait_for(staged.wait(), timeout=2)
        if reset_scope == "all":
            await container.memory.clear_all()
        else:
            await container.memory.clear_scope("ayachi_nene")
        release.set()
        await asyncio.wait_for(applied.wait(), timeout=2)
        assert await container.database.fetchall("SELECT * FROM memory_records") == []
        assert await container.database.fetchall("SELECT * FROM memory_proposals") == []
    finally:
        release.set()
        await container.stop()


@pytest.mark.asyncio
async def test_spoken_candidate_retains_every_evidence_source_on_replay(tmp_path: Path) -> None:
    container = RuntimeContainer(create_cloud_settings(tmp_path))
    await container.start()
    try:
        session = await container.sessions.create_session("ayachi_nene")
        turn_id, generation_id = uuid4(), uuid4()
        await container.conversation.begin_realtime_generation(
            session_id=session.session_id,
            turn_id=turn_id,
            generation_id=generation_id,
            audio_stream_id=uuid4(),
        )
        rows = await container.database.fetchall(
            "SELECT event_id FROM events WHERE session_id = ? ORDER BY sequence",
            (str(session.session_id),),
        )
        evidence_ids = tuple(UUID(str(row["event_id"])) for row in rows)
        assert len(evidence_ids) >= 2
        candidate = ExtractedMemoryCandidate(
            draft=MemoryRecordDraft(
                namespace="character/ayachi_nene/user/local",
                kind="episodic.shared_event",
                text="We visited the garden together.",
                observed_at=datetime.now(UTC),
                confidence=0.95,
                importance=0.8,
                sensitivity=PrivacyLevel.PRIVATE,
            ),
            explicit=True,
            rationale="shared event",
            evidence_event_ids=evidence_ids,
        )
        for _ in range(2):
            await container.memory.apply_spoken_candidates(
                session_id=session.session_id,
                turn_id=turn_id,
                source_event_id=evidence_ids[-1],
                character_id="ayachi_nene",
                candidates=[candidate],
            )
        sources = await container.database.fetchall(
            "SELECT source_id, source_event_id FROM memory_sources"
        )
        assert {UUID(str(row["source_event_id"])) for row in sources} == set(evidence_ids)
        assert len({row["source_id"] for row in sources}) == len(evidence_ids)
        assert len(await container.database.fetchall("SELECT * FROM memory_records")) == 1
        assert len(await container.database.fetchall("SELECT * FROM memory_proposals")) == 1
    finally:
        await container.stop()
