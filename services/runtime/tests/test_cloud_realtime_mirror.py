"""Tests for Cloud Realtime session mirror, event deduplication, and generation fences.

Covers all 10 required scenarios from Section 6.3 of the Phase 13 task specification:
1. Provider response id mapped to Runtime generation id;
2. Event deduplication;
3. Cancelled generation drops late audio;
4. Cancelled generation drops late transcript final;
5. Completed generation drops late deltas;
6. Provider error normalized to StructuredError;
7. No raw provider payload in EventStore;
8. Multi-session isolation;
9. Multi-generation isolation;
10. Completion barrier causal ordering.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from chatwaifu_protocol.errors import StructuredError
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
)
from chatwaifu_runtime.realtime.cloud.coordinator import (
    CloudRealtimeCoordinator,
    InMemoryDomainSink,
    InMemoryMediaSink,
)
from chatwaifu_runtime.realtime.cloud.fake import FakeCloudRealtimeSession
from chatwaifu_runtime.realtime.cloud.mirror import RealtimeSessionMirror


@dataclass(slots=True)
class SessionHarness:
    session_id: UUID
    turn_id: UUID
    gen_id: UUID
    session: FakeCloudRealtimeSession
    mirror: RealtimeSessionMirror
    domain_sink: InMemoryDomainSink
    media_sink: InMemoryMediaSink
    coordinator: CloudRealtimeCoordinator


def build_harness() -> SessionHarness:
    session_id = uuid4()
    turn_id = uuid4()
    gen_id = uuid4()
    request = RealtimeSessionOpenRequest(
        session_id=session_id,
        character_id="ayachi_nene",
        turn_id=turn_id,
        generation_id=gen_id,
    )
    fake_session = FakeCloudRealtimeSession(request, auto_ready=True)
    mirror = RealtimeSessionMirror(session_id, backend_id=fake_session.backend_id)
    domain_sink = InMemoryDomainSink()
    media_sink = InMemoryMediaSink()
    coordinator = CloudRealtimeCoordinator(
        session_id,
        session=fake_session,
        mirror=mirror,
        domain_sink=domain_sink,
        media_sink=media_sink,
    )
    return SessionHarness(
        session_id=session_id,
        turn_id=turn_id,
        gen_id=gen_id,
        session=fake_session,
        mirror=mirror,
        domain_sink=domain_sink,
        media_sink=media_sink,
        coordinator=coordinator,
    )


async def test_scenario_1_provider_response_id_mapped_to_generation_id() -> None:
    """1. Provider response id is properly mapped to Runtime generation id and turn id."""
    h = build_harness()
    h.mirror.register_generation(h.gen_id, h.turn_id)
    provider_resp_id = "provider_resp_999"

    # Emit response started with provider_response_id
    await h.coordinator.dispatch_event(
        ResponseStartedEvent(
            session_id=h.session_id,
            generation_id=h.gen_id,
            provider_response_id=provider_resp_id,
        )
    )

    assert len(h.domain_sink.responses_started) == 1
    assert h.domain_sink.responses_started[0] == (h.session_id, h.turn_id, h.gen_id)

    # Lineage lookup works via opaque provider response id
    assert h.mirror.resolve_generation_id(provider_response_id=provider_resp_id) == h.gen_id

    # Emit transcript delta with provider item reference
    await h.coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=None,
                provider_item_id=provider_resp_id,
                role="assistant",
                phase="delta",
                text="こんにちは",
                source="provider",
            )
        )
    )

    assert len(h.domain_sink.transcript_deltas) == 1
    assert h.domain_sink.transcript_deltas[0] == (h.session_id, h.turn_id, h.gen_id, "こんにちは")


async def test_scenario_2_event_deduplication() -> None:
    """2. Duplicate events with same event_id are processed only once."""
    h = build_harness()
    h.mirror.register_generation(h.gen_id, h.turn_id)
    event = ResponseStartedEvent(
        session_id=h.session_id,
        generation_id=h.gen_id,
        provider_response_id="resp_dup_1",
        event_id="evt_unique_123",
    )

    await h.coordinator.dispatch_event(event)
    await h.coordinator.dispatch_event(event)  # Duplicate injection

    assert len(h.domain_sink.responses_started) == 1


async def test_scenario_3_cancelled_generation_drops_late_audio() -> None:
    """3. Cancelled generation drops all late audio frames."""
    h = build_harness()
    h.mirror.register_generation(h.gen_id, h.turn_id)

    # Frame 1 while active
    await h.coordinator.dispatch_event(
        OutputAudioEvent(
            frame=RealtimeOutputAudioFrame(
                session_id=h.session_id,
                generation_id=h.gen_id,
                sequence=0,
                pts_ms=0,
                sample_rate=24_000,
                channels=1,
                audio=b"\x01\x02",
            )
        )
    )
    assert len(h.media_sink.received_frames) == 1

    # Cancel generation
    await h.coordinator.cancel_generation(h.gen_id, reason="barge_in")
    assert h.mirror.is_tombstoned(h.gen_id)

    # Late frame 2 arrives after cancel
    await h.coordinator.dispatch_event(
        OutputAudioEvent(
            frame=RealtimeOutputAudioFrame(
                session_id=h.session_id,
                generation_id=h.gen_id,
                sequence=1,
                pts_ms=20,
                sample_rate=24_000,
                channels=1,
                audio=b"\x03\x04",
            )
        )
    )
    # Must still only be 1 frame! Late frame was dropped
    assert len(h.media_sink.received_frames) == 1


async def test_scenario_4_cancelled_generation_drops_late_transcript_final() -> None:
    """4. Cancelled generation drops late assistant transcript final and does not commit turn."""
    h = build_harness()
    h.mirror.register_generation(h.gen_id, h.turn_id)
    await h.coordinator.cancel_generation(h.gen_id, reason="user_interrupt")

    # Late transcript final arrives after cancellation
    await h.coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=h.gen_id,
                role="assistant",
                phase="final",
                text="Late transcript after cancel",
                source="provider",
            )
        )
    )

    # No transcript final should have reached the domain sink
    assert len(h.domain_sink.transcript_finals) == 0


async def test_scenario_5_completed_generation_drops_late_deltas() -> None:
    """5. Completed generation drops late deltas, accumulated text remains intact."""
    h = build_harness()
    h.mirror.register_generation(h.gen_id, h.turn_id)

    # Delta 1
    await h.coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=h.gen_id,
                role="assistant",
                phase="delta",
                text="Valid text",
                source="provider",
            )
        )
    )

    # Completed
    await h.coordinator.dispatch_event(
        ResponseCompletedEvent(
            session_id=h.session_id,
            generation_id=h.gen_id,
            provider_response_id="resp_done",
        )
    )

    assert len(h.domain_sink.responses_completed) == 1
    assert h.domain_sink.responses_completed[0][3] == "Valid text"

    # Late delta arrives after completion
    await h.coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=h.gen_id,
                role="assistant",
                phase="delta",
                text=" Late unwanted delta",
                source="provider",
            )
        )
    )

    # Still only 1 delta received, accumulated text unmodified
    assert len(h.domain_sink.transcript_deltas) == 1
    assert h.mirror.get_accumulated_text(h.gen_id) == "Valid text"


async def test_scenario_6_provider_error_normalized_to_structured_error() -> None:
    """6. Provider errors are normalized into StructuredError without raw payloads."""
    h = build_harness()

    await h.coordinator.dispatch_event(
        ProviderErrorEvent(
            error=RealtimeProviderError(
                session_id=h.session_id,
                backend_id="fake_cloud_realtime",
                code="rate_limit_exceeded",
                message="Quota exhausted for model",
                retryable=True,
                details={"retry_after_ms": "500"},
            )
        )
    )

    assert len(h.domain_sink.provider_errors) == 1
    sess, err = h.domain_sink.provider_errors[0]
    assert sess == h.session_id
    assert isinstance(err, StructuredError)
    assert err.code == "provider_error.rate_limit_exceeded"
    assert err.message == "Quota exhausted for model"
    assert err.retryable is True
    assert err.component == "realtime.cloud"
    assert err.details.get("provider_code") == "rate_limit_exceeded"


async def test_scenario_7_no_raw_provider_payload_in_domain_sink() -> None:
    """7. Domain sink and persisted representations do not contain raw provider JSON."""
    h = build_harness()
    h.mirror.register_generation(h.gen_id, h.turn_id)

    # Dispatch sequence
    await h.coordinator.dispatch_event(
        ResponseStartedEvent(
            session_id=h.session_id,
            generation_id=h.gen_id,
            provider_response_id="raw_provider_resp_xyz_secret",
        )
    )
    await h.coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=h.gen_id,
                role="assistant",
                phase="delta",
                text="Hello world",
                source="provider",
            )
        )
    )
    await h.coordinator.dispatch_event(
        ResponseCompletedEvent(
            session_id=h.session_id,
            generation_id=h.gen_id,
            provider_response_id="raw_provider_resp_xyz_secret",
            usage=RealtimeUsage(
                session_id=h.session_id,
                generation_id=h.gen_id,
                backend_id="fake",
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
            ),
        )
    )

    # Inspect all sink payloads
    dumped = json.dumps(
        {
            "started": [str(x) for x in h.domain_sink.responses_started],
            "deltas": [
                (str(s), str(t), str(g), txt) for s, t, g, txt in h.domain_sink.transcript_deltas
            ],
            "completed": [str(x) for x in h.domain_sink.responses_completed],
        }
    )
    # The opaque internal provider id should NOT be in the domain sink messages
    assert "raw_provider_resp_xyz_secret" not in dumped


async def test_scenario_8_multi_session_isolation() -> None:
    """8. Multiple concurrent sessions do not cross-talk."""
    sess1_id, sess2_id = uuid4(), uuid4()
    req1 = RealtimeSessionOpenRequest(session_id=sess1_id, character_id="nene")
    req2 = RealtimeSessionOpenRequest(session_id=sess2_id, character_id="nene")

    fake1 = FakeCloudRealtimeSession(req1)
    fake2 = FakeCloudRealtimeSession(req2)

    sink1, sink2 = InMemoryDomainSink(), InMemoryDomainSink()
    coord1 = CloudRealtimeCoordinator(
        sess1_id,
        session=fake1,
        mirror=RealtimeSessionMirror(sess1_id, backend_id="b1"),
        domain_sink=sink1,
    )
    coord2 = CloudRealtimeCoordinator(
        sess2_id,
        session=fake2,
        mirror=RealtimeSessionMirror(sess2_id, backend_id="b1"),
        domain_sink=sink2,
    )

    gen1 = uuid4()
    coord1.mirror.register_generation(gen1, uuid4())

    await coord1.dispatch_event(
        ResponseStartedEvent(
            session_id=sess1_id,
            generation_id=gen1,
            provider_response_id="p1",
        )
    )

    gen2 = uuid4()
    coord2.mirror.register_generation(gen2, uuid4())
    await coord2.dispatch_event(
        ResponseStartedEvent(
            session_id=sess2_id,
            generation_id=gen2,
            provider_response_id="p2",
        )
    )

    assert len(sink1.responses_started) == 1
    assert len(sink2.responses_started) == 1
    assert sink1.responses_started[0][2] == gen1
    assert sink2.responses_started[0][2] == gen2


async def test_scenario_9_multi_generation_isolation() -> None:
    """9. Multiple generations within the same session do not cross-talk."""
    h = build_harness()
    gen1_id = uuid4()
    gen2_id = uuid4()

    # Gen 1 starts, writes delta, completes
    h.mirror.register_generation(gen1_id, uuid4())
    await h.coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=gen1_id,
                role="assistant",
                phase="delta",
                text="Gen1 text",
                source="provider",
            )
        )
    )
    await h.coordinator.dispatch_event(
        ResponseCompletedEvent(
            session_id=h.session_id,
            generation_id=gen1_id,
            provider_response_id="resp_1",
        )
    )

    # Gen 2 starts
    h.mirror.register_generation(gen2_id, uuid4())
    await h.coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=gen2_id,
                role="assistant",
                phase="delta",
                text="Gen2 text",
                source="provider",
            )
        )
    )

    # Late delta for Gen 1 arrives
    await h.coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=gen1_id,
                role="assistant",
                phase="delta",
                text="Late Gen1 delta",
                source="provider",
            )
        )
    )

    # Verify Gen 1 text is unchanged, Gen 2 received its text
    assert h.mirror.get_accumulated_text(gen1_id) == "Gen1 text"
    assert h.mirror.get_accumulated_text(gen2_id) == "Gen2 text"
    assert len(h.domain_sink.transcript_deltas) == 2  # Only the two valid deltas


async def test_scenario_10_completion_barrier_causal_order() -> None:
    """10. Completion acts as an ordered barrier preserving causal event flow."""
    h = build_harness()
    h.mirror.register_generation(h.gen_id, h.turn_id)

    events = [
        ResponseStartedEvent(
            session_id=h.session_id,
            generation_id=h.gen_id,
            provider_response_id="resp_causal",
        ),
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=h.gen_id,
                role="assistant",
                phase="delta",
                text="Step 1, ",
                source="provider",
            )
        ),
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=h.session_id,
                generation_id=h.gen_id,
                role="assistant",
                phase="delta",
                text="Step 2.",
                source="provider",
            )
        ),
        ResponseCompletedEvent(
            session_id=h.session_id,
            generation_id=h.gen_id,
            provider_response_id="resp_causal",
        ),
    ]

    for event in events:
        await h.coordinator.dispatch_event(event)

    assert len(h.domain_sink.responses_started) == 1
    assert len(h.domain_sink.transcript_deltas) == 2
    assert len(h.domain_sink.responses_completed) == 1
    assert h.domain_sink.responses_completed[0][3] == "Step 1, Step 2."
    assert h.mirror.is_tombstoned(h.gen_id)
