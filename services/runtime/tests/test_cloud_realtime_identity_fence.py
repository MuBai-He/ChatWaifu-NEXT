# pyright: reportPrivateUsage=false

"""Regression tests for the Phase 13 cloud realtime identity review fixes.

Covers:
A. Unknown generation user final transcripts are dropped with a structured
   ``unmapped_realtime_event`` and cause no Turn commit, no Character Kernel
   observation, and no Memory write (P0-1).
B. Identical user final text across two generations commits both turns when no
   provider event id is present (P0-2).
C. A repeated provider event is still deduplicated exactly once (P0-2).
D. ``user.transcript_partial`` carries the admitted ``utterance_id``, which is
   distinct from ``turn_id`` (P1-1).
E. Completion, cancel, failure, and runtime stop all reuse the admission-time
   ``audio_stream_id`` and fail closed when it is unrecoverable (P1-2).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import UserTurnCommittedEvent, UserTurnCommittedPayload
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.conversation.models import GenerationAccepted
from chatwaifu_runtime.conversation.service import _ActiveGeneration
from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    OutputAudioEvent,
    ProviderErrorEvent,
    RealtimeOutputAudioFrame,
    RealtimeProviderError,
    RealtimeSessionOpenRequest,
    RealtimeTranscriptCandidate,
    RealtimeUsage,
    ResponseCompletedEvent,
    ResponseStartedEvent,
    UsageRecordedEvent,
    UserTranscriptEvent,
)
from chatwaifu_runtime.realtime.cloud.coordinator import (
    CloudRealtimeCoordinator,
    InMemoryDomainSink,
)
from chatwaifu_runtime.realtime.cloud.fake import FakeCloudRealtimeSession
from chatwaifu_runtime.realtime.cloud.mirror import RealtimeSessionMirror
from pipecat.frames.frames import (
    InputAudioRawFrame,
    StartFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection


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


def _build_coordinator(
    session_id: UUID,
) -> tuple[CloudRealtimeCoordinator, InMemoryDomainSink, RealtimeSessionMirror]:
    fake_session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=session_id, character_id="nene"),
        auto_ready=False,
    )
    mirror = RealtimeSessionMirror(session_id, backend_id="fake_cloud_realtime")
    sink = InMemoryDomainSink()
    coordinator = CloudRealtimeCoordinator(
        session_id,
        session=fake_session,
        mirror=mirror,
        domain_sink=sink,
    )
    return coordinator, sink, mirror


def _user_candidate(
    session_id: UUID,
    generation_id: UUID | None,
    text: str,
    *,
    phase: str = "final",
    utterance_id: UUID | None = None,
    provider_item_id: str | None = None,
    provider_response_id: str | None = None,
) -> RealtimeTranscriptCandidate:
    return RealtimeTranscriptCandidate(
        session_id=session_id,
        generation_id=generation_id,
        role="user",
        phase=phase,  # type: ignore[arg-type]
        text=text,
        source="provider",
        utterance_id=utterance_id,
        provider_item_id=provider_item_id,
        provider_response_id=provider_response_id,
    )


def _user_final(
    session_id: UUID, generation_id: UUID | None, text: str, **kwargs: object
) -> UserTranscriptEvent:
    return UserTranscriptEvent(
        candidate=_user_candidate(session_id, generation_id, text),
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_a1_unknown_generation_user_final_is_dropped_with_unmapped_error() -> None:
    """A (coordinator): explicit unknown generation never resolves or commits."""
    session_id = uuid4()
    coordinator, sink, mirror = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())

    unknown_gen = uuid4()
    assert not mirror.has_binding(unknown_gen)
    await coordinator.dispatch_event(_user_final(session_id, unknown_gen, "未知代笔的文本"))

    assert sink.transcript_finals == []
    assert sink.transcript_deltas == []
    assert len(sink.provider_errors) == 1
    _, error = sink.provider_errors[0]
    assert error.code == "unmapped_realtime_event"


@pytest.mark.asyncio
async def test_a2_unknown_generation_final_commits_nothing_container(
    tmp_path: Path,
) -> None:
    """A (container): unknown generation final leaves Turn/Memory/Character untouched."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)
        await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        identity = bridge.current_identity
        assert identity is not None

        memory_before = await container.database.fetchone(
            "SELECT COUNT(*) AS n FROM memory_records"
        )
        char_before = await container.database.fetchone(
            "SELECT COUNT(*) AS n FROM character_states"
        )
        rel_before = await container.database.fetchone(
            "SELECT COUNT(*) AS n FROM relationship_states"
        )
        assert memory_before is not None
        assert char_before is not None
        assert rel_before is not None

        unknown_gen = uuid4()
        await bridge.coordinator.dispatch_event(
            _user_final(session.session_id, unknown_gen, "未知代笔的文本")
        )

        # Turn A keeps its uncommitted placeholder; nothing durable was written.
        turn_row = await container.database.fetchone(
            "SELECT committed_text FROM turns WHERE turn_id = ?",
            (str(identity.turn_id),),
        )
        assert turn_row is not None
        assert turn_row["committed_text"] is None
        assert await container.conversation.list_messages(session.session_id) == []
        user_events = await container.database.fetchall(
            "SELECT event_id FROM events "
            "WHERE session_id = ? AND event_type = 'user.turn_committed'",
            (str(session.session_id),),
        )
        assert user_events == []

        memory_after = await container.database.fetchone("SELECT COUNT(*) AS n FROM memory_records")
        char_after = await container.database.fetchone("SELECT COUNT(*) AS n FROM character_states")
        rel_after = await container.database.fetchone(
            "SELECT COUNT(*) AS n FROM relationship_states"
        )
        assert memory_after is not None
        assert char_after is not None
        assert rel_after is not None
        assert memory_after["n"] == memory_before["n"]
        assert char_after["n"] == char_before["n"]
        assert rel_after["n"] == rel_before["n"]

        # The active generation survives: unmapped errors must not fail it collaterally.
        assert (
            container.conversation.active_generation_id(session.session_id)
            == identity.generation_id
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_b_identical_text_across_generations_both_commit() -> None:
    """B: two generations saying '嗯' without provider event ids both commit."""
    session_id = uuid4()
    coordinator, sink, _ = _build_coordinator(session_id)

    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())
    await coordinator.dispatch_event(_user_final(session_id, gen_a, "嗯"))

    turn_b, gen_b = uuid4(), uuid4()
    coordinator.admit_turn(turn_b, gen_b, uuid4())
    await coordinator.dispatch_event(_user_final(session_id, gen_b, "嗯"))

    assert len(sink.transcript_finals) == 2
    assert sink.transcript_finals[0][:3] == (session_id, turn_a, gen_a)
    assert sink.transcript_finals[1][:3] == (session_id, turn_b, gen_b)
    assert sink.provider_errors == []


@pytest.mark.asyncio
async def test_c_duplicate_provider_event_processed_once() -> None:
    """C: replaying the same provider event (stable id or content key) dedupes."""
    session_id = uuid4()

    # Stable provider event id path.
    coordinator, sink, _ = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())
    replay = _user_final(session_id, gen_a, "请重复这句话", event_id="provider-evt-1")
    await coordinator.dispatch_event(replay)
    await coordinator.dispatch_event(replay)
    assert len(sink.transcript_finals) == 1

    # Content-key fallback path (no provider event id, same generation).
    coordinator2, sink2, _ = _build_coordinator(session_id)
    coordinator2.admit_turn(uuid4(), gen_a, uuid4())
    await coordinator2.dispatch_event(_user_final(session_id, gen_a, "请重复这句话"))
    await coordinator2.dispatch_event(_user_final(session_id, gen_a, "请重复这句话"))
    assert len(sink2.transcript_finals) == 1


@pytest.mark.asyncio
async def test_d_partial_carries_admitted_utterance_id(tmp_path: Path) -> None:
    """D: user.transcript_partial uses the admitted utterance_id, not turn_id."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    subscription = container.event_hub.subscribe(
        lambda event: event.get("event_type") == "user.transcript_partial"
    )
    try:
        session = await container.sessions.create_session("default")
        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)
        await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        identity = bridge.current_identity
        assert identity is not None
        assert identity.utterance_id != identity.turn_id

        # The provider candidate echoes no utterance: the coordinator must fill
        # the admitted one instead of deriving it from turn/generation ids.
        await bridge.coordinator.dispatch_event(
            UserTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=session.session_id,
                    generation_id=identity.generation_id,
                    role="user",
                    phase="delta",
                    text="先輩、",
                )
            )
        )
        event = await asyncio.wait_for(subscription.receive(), timeout=2.0)
        payload = event["payload"]
        assert isinstance(payload, dict)
        assert payload["utterance_id"] == str(identity.utterance_id)
        assert payload["utterance_id"] != str(identity.turn_id)
    finally:
        subscription.close()
        await container.stop()


@pytest.mark.asyncio
async def test_e_terminal_paths_reuse_admission_audio_stream_id(tmp_path: Path) -> None:
    """E: complete/cancel/fail/runtime-stop keep the original audio_stream_id."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        conversation = container.conversation
        assert container.realtime_admission is not None

        # Complete path.
        identity = await container.realtime_admission.begin_utterance(session.session_id)
        with patch.object(
            conversation, "_complete", autospec=True, wraps=conversation._complete
        ) as mock_complete:
            await conversation.complete_realtime_generation(
                session_id=session.session_id,
                turn_id=identity.turn_id,
                generation_id=identity.generation_id,
                text="完成",
            )
            assert mock_complete.call_args is not None
            accepted = mock_complete.call_args[0][0]
            assert isinstance(accepted, GenerationAccepted)
            assert accepted.audio_stream_id == identity.audio_stream_id

        # Cancel path.
        identity = await container.realtime_admission.begin_utterance(session.session_id)
        with patch.object(
            conversation, "_cancelled", autospec=True, wraps=conversation._cancelled
        ) as mock_cancelled:
            await conversation.cancel_realtime_generation(
                session_id=session.session_id,
                turn_id=identity.turn_id,
                generation_id=identity.generation_id,
                reason="test_cancel",
            )
            assert mock_cancelled.call_args is not None
            accepted = mock_cancelled.call_args[0][0]
            assert isinstance(accepted, GenerationAccepted)
            assert accepted.audio_stream_id == identity.audio_stream_id

        # Failure path.
        identity = await container.realtime_admission.begin_utterance(session.session_id)
        with patch.object(
            conversation, "_failed", autospec=True, wraps=conversation._failed
        ) as mock_failed:
            await conversation.fail_realtime_generation(
                session_id=session.session_id,
                turn_id=identity.turn_id,
                generation_id=identity.generation_id,
                error=RuntimeError("test_failure"),
            )
            assert mock_failed.call_args is not None
            accepted = mock_failed.call_args[0][0]
            assert isinstance(accepted, GenerationAccepted)
            assert accepted.audio_stream_id == identity.audio_stream_id

        # Runtime-stop path terminates the active generation with the same id.
        stop_identity = await container.realtime_admission.begin_utterance(session.session_id)
        with patch.object(
            conversation, "_cancelled", autospec=True, wraps=conversation._cancelled
        ) as mock_stop_cancelled:
            await container.stop()
            assert mock_stop_cancelled.call_args is not None
            accepted = mock_stop_cancelled.call_args[0][0]
            assert isinstance(accepted, GenerationAccepted)
            assert accepted.audio_stream_id == stop_identity.audio_stream_id
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_e_terminal_path_fails_closed_without_audio_stream_id(tmp_path: Path) -> None:
    """E (fail-closed): unrecoverable audio_stream_id aborts terminal transitions."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        conversation = container.conversation

        ghost_gen, ghost_turn = uuid4(), uuid4()
        conversation._active[session.session_id] = _ActiveGeneration(
            generation_id=ghost_gen,
            task=None,
            turn_id=ghost_turn,
            audio_stream_id=None,
        )

        # No active audio id and no persisted generation row: both must abort
        # instead of minting a fresh random stream id.
        with (
            patch.object(
                conversation, "_complete", autospec=True, wraps=conversation._complete
            ) as mock_complete,
            patch.object(
                conversation, "_cancelled", autospec=True, wraps=conversation._cancelled
            ) as mock_cancelled,
        ):
            await conversation.complete_realtime_generation(
                session_id=session.session_id,
                turn_id=ghost_turn,
                generation_id=ghost_gen,
                text="不应完成",
            )
            await conversation.cancel_realtime_generation(
                session_id=session.session_id,
                turn_id=ghost_turn,
                generation_id=ghost_gen,
                reason="ghost",
            )
            mock_complete.assert_not_called()
            mock_cancelled.assert_not_called()
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_late_provider_error_for_old_generation_spares_new_generation() -> None:
    """Late ProviderErrorEvent for a finished generation must not kill the new one."""
    session_id = uuid4()
    coordinator, sink, mirror = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())
    await coordinator.dispatch_event(
        ResponseCompletedEvent(
            session_id=session_id, generation_id=gen_a, provider_response_id="resp_a"
        )
    )
    assert mirror.is_tombstoned(gen_a)

    turn_b, gen_b = uuid4(), uuid4()
    coordinator.admit_turn(turn_b, gen_b, uuid4())

    await coordinator.dispatch_event(
        ProviderErrorEvent(
            error=RealtimeProviderError(
                session_id=session_id,
                backend_id="fake_cloud_realtime",
                code="rate_limit_exceeded",
                message="Quota exhausted",
                generation_id=gen_a,
                retryable=True,
            )
        )
    )

    assert sink.responses_failed == []
    assert mirror.active_generation_id == gen_b
    assert not mirror.is_tombstoned(gen_b)
    assert len(sink.provider_errors) == 1
    assert sink.provider_errors[0][1].code == "unmapped_realtime_event"


@pytest.mark.asyncio
async def test_same_provider_error_code_across_generations_not_deduped() -> None:
    """Identical provider errors for different generations are handled independently."""
    session_id = uuid4()
    coordinator, sink, _ = _build_coordinator(session_id)

    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())
    await coordinator.dispatch_event(
        ProviderErrorEvent(
            error=RealtimeProviderError(
                session_id=session_id,
                backend_id="fake_cloud_realtime",
                code="rate_limit_exceeded",
                message="Quota exhausted",
                generation_id=gen_a,
            )
        )
    )

    turn_b, gen_b = uuid4(), uuid4()
    coordinator.admit_turn(turn_b, gen_b, uuid4())
    await coordinator.dispatch_event(
        ProviderErrorEvent(
            error=RealtimeProviderError(
                session_id=session_id,
                backend_id="fake_cloud_realtime",
                code="rate_limit_exceeded",
                message="Quota exhausted",
                generation_id=gen_b,
            )
        )
    )

    assert len(sink.responses_failed) == 2
    assert sink.responses_failed[0][:3] == (session_id, turn_a, gen_a)
    assert sink.responses_failed[1][:3] == (session_id, turn_b, gen_b)
    assert sink.provider_errors == []


@pytest.mark.asyncio
async def test_session_level_provider_error_degrades_without_failing_generation() -> None:
    """Session-level errors are recorded and degrade the session, not the generation."""
    session_id = uuid4()
    coordinator, sink, mirror = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())

    await coordinator.dispatch_event(
        ProviderErrorEvent(
            error=RealtimeProviderError(
                session_id=session_id,
                backend_id="fake_cloud_realtime",
                code="rate_limit_exceeded",
                message="Quota exhausted",
                retryable=True,
            )
        )
    )

    assert len(sink.provider_errors) == 1
    assert sink.provider_errors[0][1].code == "provider_error.rate_limit_exceeded"
    assert len(sink.session_degradations) == 1
    assert sink.responses_failed == []
    assert mirror.active_generation_id == gen_a


@pytest.mark.asyncio
async def test_identity_less_user_final_never_lands_on_active_generation() -> None:
    """A gen-less late user final is dropped even when a newer generation is active."""
    session_id = uuid4()
    coordinator, sink, mirror = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())
    await coordinator.dispatch_event(
        ResponseCompletedEvent(
            session_id=session_id, generation_id=gen_a, provider_response_id="resp_a"
        )
    )
    turn_b, gen_b = uuid4(), uuid4()
    coordinator.admit_turn(turn_b, gen_b, uuid4())

    await coordinator.dispatch_event(
        UserTranscriptEvent(candidate=_user_candidate(session_id, None, "迟到的A"))
    )

    assert sink.transcript_finals == []
    assert len(sink.provider_errors) == 1
    assert sink.provider_errors[0][1].code == "unmapped_realtime_event"
    assert mirror.active_generation_id == gen_b


@pytest.mark.asyncio
async def test_identity_less_user_final_commits_nothing_container(tmp_path: Path) -> None:
    """Container: gen-less user final leaves the active turn uncommitted."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)
        await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        identity_b = bridge.current_identity
        assert identity_b is not None

        await bridge.coordinator.dispatch_event(
            UserTranscriptEvent(candidate=_user_candidate(session.session_id, None, "迟到的A"))
        )

        turn_row = await container.database.fetchone(
            "SELECT committed_text FROM turns WHERE turn_id = ?",
            (str(identity_b.turn_id),),
        )
        assert turn_row is not None
        assert turn_row["committed_text"] is None
        assert await container.conversation.list_messages(session.session_id) == []
        assert (
            container.conversation.active_generation_id(session.session_id)
            == identity_b.generation_id
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_identity_less_usage_never_lands_on_last_generation() -> None:
    """Gen-less usage after completion is dropped instead of attributed to last gen."""
    session_id = uuid4()
    coordinator, sink, mirror = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())
    await coordinator.dispatch_event(
        ResponseCompletedEvent(
            session_id=session_id, generation_id=gen_a, provider_response_id="resp_a"
        )
    )
    assert mirror.active_generation_id is None

    await coordinator.dispatch_event(
        UsageRecordedEvent(
            usage=RealtimeUsage(session_id=session_id, backend_id="fake", total_tokens=10)
        )
    )
    assert sink.usages_recorded == []
    assert sink.provider_errors == []

    await coordinator.dispatch_event(
        UsageRecordedEvent(
            usage=RealtimeUsage(
                session_id=session_id,
                backend_id="fake",
                generation_id=uuid4(),
                total_tokens=10,
            )
        )
    )
    assert sink.usages_recorded == []
    assert len(sink.provider_errors) == 1
    assert sink.provider_errors[0][1].code == "unmapped_realtime_event"


@pytest.mark.asyncio
async def test_foreign_session_events_rejected() -> None:
    """Events carrying another session id are dropped with a session_mismatch."""
    session_id = uuid4()
    foreign_id = uuid4()
    coordinator, sink, mirror = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())

    await coordinator.dispatch_event(
        UserTranscriptEvent(candidate=_user_candidate(foreign_id, gen_a, "跨会话文本"))
    )
    await coordinator.dispatch_event(
        ResponseStartedEvent(
            session_id=foreign_id, generation_id=gen_a, provider_response_id="resp_x"
        )
    )
    await coordinator.dispatch_event(
        OutputAudioEvent(
            frame=RealtimeOutputAudioFrame(
                session_id=foreign_id,
                generation_id=gen_a,
                sequence=0,
                pts_ms=0,
                sample_rate=24_000,
                channels=1,
                audio=b"\x01\x02",
            )
        )
    )

    assert sink.transcript_finals == []
    assert sink.responses_started == []
    mismatch = [e for _, e in sink.provider_errors if e.code == "session_mismatch"]
    assert len(mismatch) == 2
    assert mirror.active_generation_id == gen_a


@pytest.mark.asyncio
async def test_provider_utterance_mismatch_rejected() -> None:
    """A provider-echoed utterance id may only confirm the admitted identity."""
    session_id = uuid4()
    coordinator, sink, _ = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    admitted_utterance = uuid4()
    coordinator.admit_turn(turn_a, gen_a, admitted_utterance)

    await coordinator.dispatch_event(
        UserTranscriptEvent(
            candidate=_user_candidate(session_id, gen_a, "伪造的utterance", utterance_id=uuid4())
        )
    )
    assert sink.transcript_finals == []
    assert len(sink.provider_errors) == 1
    assert sink.provider_errors[0][1].code == "lineage_mismatch"

    await coordinator.dispatch_event(
        UserTranscriptEvent(
            candidate=_user_candidate(
                session_id, gen_a, "确认的utterance", utterance_id=admitted_utterance
            )
        )
    )
    assert len(sink.transcript_finals) == 1


@pytest.mark.asyncio
async def test_repository_rejects_generation_turn_mismatch(tmp_path: Path) -> None:
    """Repository triple check: a foreign generation cannot commit another turn."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        assert container.realtime_admission is not None
        identity_a = await container.realtime_admission.begin_utterance(session.session_id)
        identity_b = await container.realtime_admission.begin_utterance(session.session_id)
        repo = container.conversation_repository

        mismatched = await repo.commit_realtime_user_transcript(
            session_id=session.session_id,
            turn_id=identity_a.turn_id,
            generation_id=identity_b.generation_id,
            text="错配提交",
            occurred_at=datetime.now(UTC),
            user_event=UserTurnCommittedEvent(
                event_id=uuid4(),
                session_id=session.session_id,
                turn_id=identity_a.turn_id,
                generation_id=identity_b.generation_id,
                occurred_at=datetime.now(UTC),
                source="test",
                privacy=PrivacyLevel.LOCAL,
                payload=UserTurnCommittedPayload(text="错配提交"),
            ),
        )
        assert mismatched is None
        turn_row = await container.database.fetchone(
            "SELECT committed_text FROM turns WHERE turn_id = ?",
            (str(identity_a.turn_id),),
        )
        assert turn_row is not None
        assert turn_row["committed_text"] is None
        user_events = await container.database.fetchall(
            "SELECT event_id FROM events "
            "WHERE session_id = ? AND event_type = 'user.turn_committed'",
            (str(session.session_id),),
        )
        assert user_events == []
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_identical_assistant_deltas_without_sequence_both_processed() -> None:
    """Blocking 1: two identical '哈' deltas are stream output, not replays."""
    session_id = uuid4()
    coordinator, sink, mirror = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())

    for _ in range(2):
        await coordinator.dispatch_event(
            AssistantTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=session_id,
                    generation_id=gen_a,
                    role="assistant",
                    phase="delta",
                    text="哈",
                    source="provider",
                )
            )
        )

    assert len(sink.transcript_deltas) == 2
    assert mirror.get_accumulated_text(gen_a) == "哈哈"


@pytest.mark.asyncio
async def test_replayed_sequence_delta_processed_once() -> None:
    """Blocking 1: same provider_sequence replays dedupe; next sequence applies."""
    session_id = uuid4()
    coordinator, sink, mirror = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())

    async def send_delta(text: str, sequence: int) -> None:
        await coordinator.dispatch_event(
            AssistantTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=session_id,
                    generation_id=gen_a,
                    role="assistant",
                    phase="delta",
                    text=text,
                    source="provider",
                    provider_sequence=sequence,
                )
            )
        )

    await send_delta("哈", 7)
    await send_delta("哈", 7)
    assert len(sink.transcript_deltas) == 1
    await send_delta("哈", 8)
    assert len(sink.transcript_deltas) == 2
    assert mirror.get_accumulated_text(gen_a) == "哈哈"


@pytest.mark.asyncio
async def test_conflicting_provider_identities_rejected() -> None:
    """Blocking 3: a response id bound to A must not reroute to B silently."""
    session_id = uuid4()
    coordinator, sink, mirror = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    turn_b, gen_b = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())

    await coordinator.dispatch_event(
        ResponseStartedEvent(
            session_id=session_id, generation_id=gen_a, provider_response_id="resp-1"
        )
    )
    assert len(sink.responses_started) == 1

    # Item-level learning happens while A is still the active generation.
    await coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=session_id,
                generation_id=gen_a,
                role="assistant",
                phase="delta",
                text="首句",
                source="provider",
                provider_item_id="item-1",
            )
        )
    )
    assert len(sink.transcript_deltas) == 1

    coordinator.admit_turn(turn_b, gen_b, uuid4())
    await coordinator.dispatch_event(
        ResponseStartedEvent(
            session_id=session_id, generation_id=gen_b, provider_response_id="resp-1"
        )
    )
    assert len(sink.responses_started) == 1
    mismatch = [e for _, e in sink.provider_errors if e.code == "lineage_mismatch"]
    assert len(mismatch) == 1
    assert mirror.lookup_response_generation("resp-1") == gen_a

    # Item-level conflict behaves the same way.
    await coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=session_id,
                generation_id=gen_b,
                role="assistant",
                phase="delta",
                text="错位句",
                source="provider",
                provider_item_id="item-1",
            )
        )
    )
    assert len(sink.transcript_deltas) == 1
    assert len([e for _, e in sink.provider_errors if e.code == "lineage_mismatch"]) == 2


@pytest.mark.asyncio
async def test_transcript_role_mismatch_rejected() -> None:
    """Blocking 3: candidate role must match the wrapping transcript event."""
    session_id = uuid4()
    coordinator, sink, _ = _build_coordinator(session_id)
    turn_a, gen_a = uuid4(), uuid4()
    coordinator.admit_turn(turn_a, gen_a, uuid4())

    await coordinator.dispatch_event(
        UserTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=session_id,
                generation_id=gen_a,
                role="assistant",
                phase="final",
                text="角色错位",
                source="provider",
            )
        )
    )
    await coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=session_id,
                generation_id=gen_a,
                role="user",
                phase="delta",
                text="角色错位",
                source="provider",
            )
        )
    )

    assert sink.transcript_finals == []
    assert sink.transcript_deltas == []
    assert len([e for _, e in sink.provider_errors if e.code == "lineage_mismatch"]) == 2


def test_provider_bindings_refuse_silent_rebind() -> None:
    """Blocking 3: response/item bindings are immutable once set."""
    session_id = uuid4()
    mirror = RealtimeSessionMirror(session_id, backend_id="fake")
    gen_a, gen_b = uuid4(), uuid4()
    mirror.register_generation(gen_a, uuid4())
    mirror.register_generation(gen_b, uuid4())

    assert mirror.bind_provider_response("resp-x", gen_a) is not None
    assert mirror.bind_provider_response("resp-x", gen_b) is None
    assert mirror.lookup_response_generation("resp-x") == gen_a
    assert mirror.bind_provider_response("resp-x", gen_a) is not None

    assert mirror.bind_provider_item("item-x", gen_a) is not None
    assert mirror.bind_provider_item("item-x", gen_b) is None
    assert mirror.lookup_item_generation("item-x") == gen_a


@pytest.mark.asyncio
async def test_send_audio_failure_fails_generation_and_idles_session(
    tmp_path: Path,
) -> None:
    """Blocking 2: a send_audio fault must not leave a RUNNING generation behind."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)
        await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        identity = bridge.current_identity
        assert identity is not None

        fake_session = bridge.coordinator.session
        with patch.object(
            fake_session, "send_audio", new=AsyncMock(side_effect=RuntimeError("uplink down"))
        ):
            await bridge.process_frame(
                InputAudioRawFrame(audio=b"\x01\x00" * 160, sample_rate=16_000, num_channels=1),
                FrameDirection.DOWNSTREAM,
            )
            await asyncio.wait_for(bridge._input_queue.join(), timeout=5)

        gen_row = await container.database.fetchone(
            "SELECT state FROM generations WHERE generation_id = ?",
            (str(identity.generation_id),),
        )
        assert gen_row is not None
        assert gen_row["state"] == "failed"
        sess_row = await container.database.fetchone(
            "SELECT conversation_state FROM sessions WHERE session_id = ?",
            (str(session.session_id),),
        )
        assert sess_row is not None
        assert sess_row["conversation_state"] == "idle"
        fail_events = await container.database.fetchall(
            "SELECT envelope_json FROM events "
            "WHERE session_id = ? AND event_type = 'system.error_raised'",
            (str(session.session_id),),
        )
        assert len(fail_events) == 1
        fail_envelope = json.loads(str(fail_events[0]["envelope_json"]))
        assert fail_envelope.get("generation_id") == str(identity.generation_id)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_commit_input_failure_fails_generation_and_idles_session(
    tmp_path: Path,
) -> None:
    """Blocking 2: a commit_input fault fails fast instead of awaiting completion."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)
        await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        identity = bridge.current_identity
        assert identity is not None

        fake_session = bridge.coordinator.session
        with patch.object(
            fake_session,
            "commit_input",
            new=AsyncMock(side_effect=RuntimeError("commit down")),
        ):
            await bridge.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

        gen_row = await container.database.fetchone(
            "SELECT state FROM generations WHERE generation_id = ?",
            (str(identity.generation_id),),
        )
        assert gen_row is not None
        assert gen_row["state"] == "failed"
        sess_row = await container.database.fetchone(
            "SELECT conversation_state FROM sessions WHERE session_id = ?",
            (str(session.session_id),),
        )
        assert sess_row is not None
        assert sess_row["conversation_state"] == "idle"
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_p01_evicted_binding_clears_provider_response_mapping() -> None:
    """P0-1.6: capacity eviction must not leave dangling response mappings."""
    session_id = uuid4()
    mirror = RealtimeSessionMirror(session_id, backend_id="fake", max_bindings=2, max_responses=10)
    gen_old, gen_mid, gen_new = uuid4(), uuid4(), uuid4()
    mirror.register_generation(gen_old, uuid4(), provider_response_id="resp_old")
    mirror.register_generation(gen_mid, uuid4())
    assert mirror.resolve_generation_id(provider_response_id="resp_old") == gen_old

    mirror.register_generation(gen_new, uuid4())
    assert not mirror.has_binding(gen_old)
    assert mirror.resolve_generation_id(provider_response_id="resp_old") is None
    assert mirror.resolve_generation_id(generation_id=gen_old) is None
