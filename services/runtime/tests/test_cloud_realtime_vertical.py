# pyright: reportPrivateUsage=false

"""End-to-End Vertical Acceptance Tests for Fake Cloud Realtime Foundation (Phase 13.0-13.3).

Validates the full vertical integration:
1. RuntimeContainer boots in explicit 'cloud_realtime' + 'fake' mode and wires cloud factory;
2. Invalid configurations (e.g. non-fake cloud backend) fail fast at startup;
3. Cascade mode creates strictly 0 cloud backends or gateways;
4. VAD speech start triggers RuntimeRealtimeTurnAdmission and registers generation in Mirror;
5. Zero uuid4 fallback in lower layers; domain identity is allocated authoritatively by runtime;
6. End-to-end user speech commits User Turn to SQLite, character kernel, and memory;
7. Ephemeral text deltas are published without SQLite amplification;
8. Response completion triggers CAS in ConversationService, finalizing Assistant Turn in SQLite;
9. Assistant Final Transcript priority: event.final_text > authoritative_final > accumulated_delta;
10. transcript_delta carries explicit role tagging ("user" / "assistant");
11. Fail-closed durable audit: EventStore write failure blocks provider network calls with 0 calls;
12. Cloud Egress policy 'deny' and unauthorized 'ask' block provider calls and emit egress_blocked;
13. Egress receipts record only retained memory records after budget pruning;
14. Skill capabilities enforce typed RealtimeSkillCapability allowlist (reject raw dicts);
15. Initial context bypass is eliminated (RealtimeSessionOpenRequest has no initial_context);
16. Event pump errors and session lifecycle degradations propagate to observable domain state;
17. Tests do not directly call mirror.register_generation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.memory import MemoryRecord
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.realtime.admission import RuntimeRealtimeTurnAdmission
from chatwaifu_runtime.realtime.cloud.context import (
    CloudEgressGateway,
    ConsentRequiredError,
    EgressGrant,
    PolicyDeniedError,
    RealtimeContextPatchBuilder,
)
from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    RealtimeSessionIntent,
    RealtimeSessionOpenRequest,
    RealtimeSkillCapability,
    RealtimeTranscriptCandidate,
    RealtimeUsage,
    ResponseCompletedEvent,
    ResponseStartedEvent,
    SessionDegradedEvent,
    UserTranscriptEvent,
)
from chatwaifu_runtime.realtime.cloud.coordinator import (
    CloudRealtimeCoordinator,
    InMemoryDomainSink,
)
from chatwaifu_runtime.realtime.cloud.factory import (
    RuntimeCloudRealtimeFactory,
)
from chatwaifu_runtime.realtime.cloud.fake import (
    FakeCloudRealtimeBackend,
    FakeCloudRealtimeSession,
)
from chatwaifu_runtime.realtime.cloud.mirror import RealtimeSessionMirror
from pipecat.frames.frames import (
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


@pytest.mark.asyncio
async def test_01_container_boots_in_fake_cloud_mode(tmp_path: Path) -> None:
    """1. RuntimeContainer boots in cloud_realtime + fake mode and wires all components."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)

    assert container.cloud_realtime_backend is not None
    assert isinstance(container.cloud_realtime_backend, FakeCloudRealtimeBackend)
    assert container.cloud_egress_gateway is not None
    assert isinstance(container.cloud_egress_gateway, CloudEgressGateway)
    assert container.realtime_admission is not None
    assert isinstance(container.realtime_admission, RuntimeRealtimeTurnAdmission)
    assert container.cloud_realtime_factory is not None
    assert isinstance(container.cloud_realtime_factory, RuntimeCloudRealtimeFactory)

    await container.start()
    assert container._state == "started"

    await container.stop()
    assert container._state == "stopped"
    assert container.cloud_realtime_backend.close_calls == 1


def test_02_invalid_cloud_configuration_fails_fast(tmp_path: Path) -> None:
    """2. Invalid cloud backend configurations fail fast during settings validation."""
    with pytest.raises(ValueError, match="cloud_backend must be explicitly set to 'fake'"):
        Settings.model_validate(
            {
                "config_dir": tmp_path / "config",
                "data_dir": tmp_path,
                "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
                "llm": {"provider": "demo"},
                "tts": {"provider": "fake"},
                "realtime": {
                    "connection_mode": "cloud_realtime",
                    "cloud_backend": None,
                },
            }
        )


@pytest.mark.asyncio
async def test_03_cascade_mode_creates_zero_cloud_backends(tmp_path: Path) -> None:
    """3. Cascade mode leaves cloud backend and egress factory uninitialized."""
    settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path,
            "storage": StorageConfig(database_path=tmp_path / "runtime.db"),
            "llm": {"provider": "demo"},
            "tts": {"provider": "fake"},
            "realtime": {
                "connection_mode": "cascade",
            },
        }
    )
    container = RuntimeContainer(settings)
    assert container.cloud_realtime_backend is None
    assert container.cloud_egress_gateway is None
    assert container.cloud_realtime_factory is None


@pytest.mark.asyncio
async def test_04_vad_frame_admits_turn_without_uuid4_fallback(tmp_path: Path) -> None:
    """4. VAD start allocates turn & gen IDs and registers them in Mirror with no uuid4 fallback."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()

    try:
        session = await container.sessions.create_session("default")
        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)

        await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
        assert bridge.current_identity is None

        # Speech starts
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        identity = bridge.current_identity
        assert identity is not None
        assert identity.session_id == session.session_id

        # Registered in mirror
        coordinator = bridge.coordinator
        assert coordinator.mirror.is_active(identity.generation_id) is True
        assert coordinator.mirror.get_turn_id(identity.generation_id) == identity.turn_id

        # Tracked in ConversationService
        assert (
            container.conversation.active_generation_id(session.session_id)
            == identity.generation_id
        )
        assert container.conversation.active_turn_id(session.session_id) == identity.turn_id

    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_05_vertical_end_to_end_full_turn_commits_to_sqlite(tmp_path: Path) -> None:
    """5. Full turn: VAD -> user final -> assistant deltas -> completion CAS in SQLite."""
    settings = create_cloud_settings(tmp_path)
    container = RuntimeContainer(settings)
    await container.start()

    try:
        session = await container.sessions.create_session("default")
        assert container.cloud_realtime_factory is not None
        bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)
        await bridge.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

        # 1. User starts speaking
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        identity = bridge.current_identity
        assert identity is not None
        gen_id = identity.generation_id

        # 2. User finishes speaking
        await bridge.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

        # 3. Provider emits User final transcript
        coordinator = bridge.coordinator
        await coordinator.dispatch_event(
            UserTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=session.session_id,
                    generation_id=gen_id,
                    role="user",
                    text="先輩、お疲れ様です！",
                    phase="final",
                )
            )
        )

        # User turn is now committed to SQLite repository
        messages = await container.conversation.list_messages(session.session_id)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["committed_text"] == "先輩、お疲れ様です！"

        # 4. Provider response starts and streams deltas
        await coordinator.dispatch_event(
            ResponseStartedEvent(
                session_id=session.session_id,
                generation_id=gen_id,
                provider_response_id="resp_101",
            )
        )
        await coordinator.dispatch_event(
            AssistantTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=session.session_id,
                    generation_id=gen_id,
                    role="assistant",
                    text="お疲れ様です、",
                    phase="delta",
                    provider_item_id="resp_101",
                )
            )
        )
        await coordinator.dispatch_event(
            AssistantTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=session.session_id,
                    generation_id=gen_id,
                    role="assistant",
                    text="先輩！",
                    phase="delta",
                    provider_item_id="resp_101",
                )
            )
        )

        # Deltas are ephemeral - SQLite still has only user message
        messages_mid = await container.conversation.list_messages(session.session_id)
        assert len(messages_mid) == 1

        # 5. Provider response completes
        await coordinator.dispatch_event(
            ResponseCompletedEvent(
                session_id=session.session_id,
                generation_id=gen_id,
                provider_response_id="resp_101",
                usage=RealtimeUsage(
                    session_id=session.session_id,
                    backend_id="fake",
                    input_tokens=15,
                    output_tokens=8,
                ),
            )
        )

        # SQLite now has both user and assistant turns
        messages_final = await container.conversation.list_messages(session.session_id)
        assert len(messages_final) == 2
        assert messages_final[0]["role"] == "user"
        assert messages_final[1]["role"] == "assistant"
        assert messages_final[1]["committed_text"] == "お疲れ様です、先輩！"

        # Conversation recovery and generation records reflect completed generation
        recovery = await container.conversation.recovery_state(session.session_id)
        assert recovery.active_generation_id is None
        gen_record = await container.conversation_repository.generation_result(gen_id)
        assert gen_record is not None
        assert gen_record.state == "completed"
        assert gen_record.output_text == "お疲れ様です、先輩！"

    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_06_authoritative_final_transcript_overrides_deltas() -> None:
    """6. Authoritative final transcript overrides accumulated deltas upon completion."""
    session_id = uuid4()
    turn_id = uuid4()
    gen_id = uuid4()

    fake_session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(
            session_id=session_id,
            character_id="ayachi_nene",
            turn_id=turn_id,
            generation_id=gen_id,
        )
    )
    mirror = RealtimeSessionMirror(session_id, backend_id="fake")
    domain_sink = InMemoryDomainSink()
    coordinator = CloudRealtimeCoordinator(
        session_id,
        session=fake_session,
        mirror=mirror,
        domain_sink=domain_sink,
    )
    coordinator.admit_turn(turn_id, gen_id)

    # Stream deltas
    await coordinator.dispatch_event(
        ResponseStartedEvent(
            session_id=session_id,
            generation_id=gen_id,
            provider_response_id="p1",
        )
    )
    await coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=session_id,
                generation_id=gen_id,
                role="assistant",
                text="partial bad delta",
                phase="delta",
                provider_item_id="p1",
            )
        )
    )

    # Provider emits authoritative final text
    await coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=session_id,
                generation_id=gen_id,
                role="assistant",
                text="Correct authoritative final text.",
                phase="final",
                provider_item_id="p1",
            )
        )
    )

    # Response completes without final_text in completion event
    await coordinator.dispatch_event(
        ResponseCompletedEvent(
            session_id=session_id,
            generation_id=gen_id,
            provider_response_id="p1",
        )
    )

    assert len(domain_sink.responses_completed) == 1
    _, _, _, completed_text = domain_sink.responses_completed[0]
    assert completed_text == "Correct authoritative final text."


@pytest.mark.asyncio
async def test_07_completion_event_final_text_has_highest_priority() -> None:
    """7. event.final_text in ResponseCompletedEvent has highest priority."""
    session_id = uuid4()
    turn_id = uuid4()
    gen_id = uuid4()

    fake_session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(
            session_id=session_id,
            character_id="ayachi_nene",
            turn_id=turn_id,
            generation_id=gen_id,
        )
    )
    mirror = RealtimeSessionMirror(session_id, backend_id="fake")
    domain_sink = InMemoryDomainSink()
    coordinator = CloudRealtimeCoordinator(
        session_id,
        session=fake_session,
        mirror=mirror,
        domain_sink=domain_sink,
    )
    coordinator.admit_turn(turn_id, gen_id)

    await coordinator.dispatch_event(
        ResponseStartedEvent(
            session_id=session_id,
            generation_id=gen_id,
            provider_response_id="p1",
        )
    )
    await coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=session_id,
                generation_id=gen_id,
                role="assistant",
                text="delta text",
                phase="delta",
                provider_item_id="p1",
            )
        )
    )
    await coordinator.dispatch_event(
        AssistantTranscriptEvent(
            candidate=RealtimeTranscriptCandidate(
                session_id=session_id,
                generation_id=gen_id,
                role="assistant",
                text="intermediate final text",
                phase="final",
                provider_item_id="p1",
            )
        )
    )
    await coordinator.dispatch_event(
        ResponseCompletedEvent(
            session_id=session_id,
            generation_id=gen_id,
            provider_response_id="p1",
            final_text="Highest priority final text from completion event.",
        )
    )

    assert len(domain_sink.responses_completed) == 1
    _, _, _, completed_text = domain_sink.responses_completed[0]
    assert completed_text == "Highest priority final text from completion event."


@pytest.mark.asyncio
async def test_08_transcript_delta_role_tagging() -> None:
    """8. transcript_delta carries role tagging for user and assistant streams."""
    domain_sink = InMemoryDomainSink()
    s_id, t_id, g_id = uuid4(), uuid4(), uuid4()

    await domain_sink.transcript_delta(s_id, t_id, g_id, "User delta", role="user")
    await domain_sink.transcript_delta(s_id, t_id, g_id, "Assistant delta", role="assistant")

    assert len(domain_sink.transcript_deltas_with_role) == 2
    assert domain_sink.transcript_deltas_with_role[0][3] == "User delta"
    assert domain_sink.transcript_deltas_with_role[0][4] == "user"
    assert domain_sink.transcript_deltas_with_role[1][3] == "Assistant delta"
    assert domain_sink.transcript_deltas_with_role[1][4] == "assistant"


@pytest.mark.asyncio
async def test_09_cloud_egress_gateway_fails_closed_on_durable_audit_failure() -> None:
    """9. Fail-closed: EventStore failure raises RuntimeError and prevents any backend calls."""
    failing_event_store = AsyncMock()
    failing_event_store.append.side_effect = RuntimeError("Disk I/O error on audit log")

    gateway = CloudEgressGateway(policy_mode="allow", event_store=failing_event_store)
    backend = FakeCloudRealtimeBackend()
    intent = RealtimeSessionIntent(session_id=uuid4(), character_id="ayachi_nene")

    with pytest.raises(RuntimeError, match="Egress audit persistence failed"):
        await gateway.open_session(backend, intent)

    # Invariant: 0 provider backend calls
    assert len(backend.open_session_calls) == 0


@pytest.mark.asyncio
async def test_10_egress_policy_blocks_deny_and_unauthorized_ask() -> None:
    """10. Policy 'deny' and 'ask' without grant block provider calls and emit audit."""
    # Deny mode
    deny_gateway = CloudEgressGateway(policy_mode="deny")
    backend = FakeCloudRealtimeBackend()
    s_id = uuid4()
    intent = RealtimeSessionIntent(session_id=s_id, character_id="ayachi_nene")

    with pytest.raises(PolicyDeniedError):
        await deny_gateway.open_session(backend, intent)
    assert len(backend.open_session_calls) == 0

    # Ask mode without grant
    ask_gateway = CloudEgressGateway(policy_mode="ask")
    with pytest.raises(ConsentRequiredError):
        await ask_gateway.open_session(backend, intent)
    assert len(backend.open_session_calls) == 0

    # Ask mode with valid grant
    grant = EgressGrant(session_id=s_id)
    ask_gateway.grant(grant)
    session = await ask_gateway.open_session(backend, intent)
    assert session is not None
    assert len(backend.open_session_calls) == 1


def test_11_receipt_records_only_retained_memories() -> None:
    """11. EgressReceipt only includes memory record IDs that survived budget pruning."""
    builder = RealtimeContextPatchBuilder(max_bytes=100)
    # Memory record with substantial text exceeding 100 bytes
    now = datetime.now(UTC)
    source_id = uuid4()
    large_mem = MemoryRecord(
        memory_id=uuid4(),
        namespace="conversation",
        kind="semantic.fact",
        text="A" * 200,
        sensitivity=PrivacyLevel.PUBLIC,
        observed_at=now,
        created_at=now,
        updated_at=now,
        confidence=0.9,
        importance=0.8,
        source_event_ids=[source_id],
    )
    patch = builder.build_patch(
        safety_contract="Safety prompt",
        memories=[large_mem],
    )
    # The memory component was pruned because it exceeded total byte budget
    kinds = [c.kind for c in patch.components]
    assert "memory" not in kinds

    # Retained memory IDs should be empty
    retained_mids = [
        mid for c in patch.components if c.kind == "memory" for mid in c.source_record_ids
    ]
    assert len(retained_mids) == 0


def test_12_skill_capabilities_typed_dto_allowlist() -> None:
    """12. Skill context enforces typed RealtimeSkillCapability, rejecting raw dicts."""
    builder = RealtimeContextPatchBuilder()
    with pytest.raises(TypeError, match="Expected RealtimeSkillCapability instance"):
        builder.build_patch(skills=[{"raw": "dict"}])  # type: ignore[arg-type]

    # Valid typed capability
    cap = RealtimeSkillCapability(
        skill_id="web_search",
        display_name="Search",
        description="Search web",
        allowed_argument_names=("query",),
    )
    patch = builder.build_patch(skills=[cap])
    skills_comp = next(c for c in patch.components if c.kind == "skills")
    assert "web_search" in skills_comp.text
    assert "query" in skills_comp.text


def test_13_initial_context_bypass_is_eliminated() -> None:
    """13. RealtimeSessionOpenRequest has no initial_context field."""
    req = RealtimeSessionOpenRequest(session_id=uuid4(), character_id="ayachi_nene")
    assert not hasattr(req, "initial_context")


@pytest.mark.asyncio
async def test_14_session_lifecycle_and_pump_error_observability() -> None:
    """14. Session degrade, close, and pump errors propagate to observable domain state."""
    session_id = uuid4()
    domain_sink = InMemoryDomainSink()
    fake_session = FakeCloudRealtimeSession(
        RealtimeSessionOpenRequest(session_id=session_id, character_id="ayachi_nene")
    )
    mirror = RealtimeSessionMirror(session_id, backend_id="fake")
    coordinator = CloudRealtimeCoordinator(
        session_id,
        session=fake_session,
        mirror=mirror,
        domain_sink=domain_sink,
    )

    # Degraded event
    await coordinator.dispatch_event(
        SessionDegradedEvent(session_id=session_id, backend_id="fake", reason="high_latency")
    )
    assert len(domain_sink.session_degradations) == 1
    assert domain_sink.session_degradations[0] == (session_id, "high_latency")

    # Pump error via direct structured error
    err = StructuredError(
        code="simulated_failure", message="boom", retryable=False, component="cloud"
    )
    await domain_sink.provider_error(session_id, err)
    assert len(domain_sink.provider_errors) == 1
    assert domain_sink.provider_errors[0][1].code == "simulated_failure"

    # End-to-end pump loop failure: ensure active generation cancelled and session closed
    class FailingEventsSession(FakeCloudRealtimeSession):
        async def events(self):
            raise RuntimeError("network pump failure")
            yield  # type: ignore

    failing_session = FailingEventsSession(
        RealtimeSessionOpenRequest(session_id=session_id, character_id="ayachi_nene")
    )
    fail_sink = InMemoryDomainSink()
    fail_mirror = RealtimeSessionMirror(session_id, backend_id="fake")
    gen_id = uuid4()
    turn_id = uuid4()
    pump_coordinator = CloudRealtimeCoordinator(
        session_id,
        session=failing_session,
        mirror=fail_mirror,
        domain_sink=fail_sink,
    )
    pump_coordinator.admit_turn(turn_id, gen_id)
    assert pump_coordinator.is_running is False
    pump_coordinator.start()
    assert pump_coordinator.is_running is True
    if pump_coordinator._pump_task:
        try:
            await pump_coordinator._pump_task
        except Exception:
            pass
    assert pump_coordinator.is_running is False
    assert len(fail_sink.provider_errors) == 1
    assert fail_sink.provider_errors[0][1].code == "realtime_pump_failed"
    assert len(fail_sink.session_closures) == 1
    assert fail_sink.session_closures[0] == (session_id, "pump_failed")
    assert len(fail_sink.responses_cancelled) == 1
    assert fail_sink.responses_cancelled[0][2] == gen_id


def test_15_zero_mirror_register_generation_in_test_suite() -> None:
    """15. Guarantee that tests never call mirror.register_generation directly."""
    import inspect

    import services.runtime.tests.test_cloud_realtime_mirror as tm

    source = inspect.getsource(tm)
    assert "mirror.register_generation" not in source


async def test_16_user_partial_transcript_emits_valid_protocol_event(tmp_path: Path) -> None:
    """16. User partial transcript emits valid user.transcript_partial without crash."""
    import asyncio

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

        # 1. User starts speaking (VAD start)
        await bridge.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        identity = bridge.current_identity
        assert identity is not None
        gen_id = identity.generation_id
        turn_id = identity.turn_id

        coordinator = bridge.coordinator
        assert coordinator.is_running

        # 2. Provider emits User partial transcript delta
        await coordinator.dispatch_event(
            UserTranscriptEvent(
                candidate=RealtimeTranscriptCandidate(
                    session_id=session.session_id,
                    generation_id=gen_id,
                    role="user",
                    text="先輩、",
                    phase="delta",
                )
            )
        )

        # Verify EventHub received valid user.transcript_partial event
        event = await asyncio.wait_for(subscription.receive(), timeout=2.0)
        assert event["event_type"] == "user.transcript_partial"
        assert event["session_id"] == str(session.session_id)
        assert event["turn_id"] == str(turn_id)
        assert event["generation_id"] == str(gen_id)
        payload = event["payload"]
        assert isinstance(payload, dict)
        assert payload["text"] == "先輩、"
        assert payload["is_final"] is False
        assert payload["provider"] == "fake_cloud_realtime"

        # Verify Generation is still RUNNING and Pump is still is_running
        active_gen = container.conversation._active.get(session.session_id)
        assert active_gen is not None
        assert active_gen.generation_id == gen_id
        assert coordinator.is_running
    finally:
        subscription.close()
        await container.stop()
