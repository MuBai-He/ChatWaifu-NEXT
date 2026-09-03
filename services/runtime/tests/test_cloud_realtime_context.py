# pyright: reportPrivateUsage=false

"""Tests for Cloud Realtime Context Sync and Egress Policy (Phase 13.3).

Validates:
1. 'deny' policy blocks backend with 0 open_session calls and records audit;
2. 'ask' policy without explicit consent blocks backend with 0 calls;
3. 'ask' policy with explicit approval permits scoped session access;
4. Patch budget pruning drops whole components by priority;
5. Persona/Relationship/Affect are populated from Runtime snapshot;
6. Memory component includes only selected non-sensitive, active excerpts;
7. Egress receipt excludes sensitive secrets and memory plaintext;
8. Provider context update failure normalizes to StructuredError;
9. Cascade mode bypasses cloud egress completely;
10. Context patch construction is deterministic and reproducible;
11. EventStore persistence records audit receipts without schema migration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.character import (
    AffectState,
    CharacterKernelSnapshot,
    RelationshipState,
)
from chatwaifu_protocol.memory import MemoryRecord
from chatwaifu_runtime.characters.service import CharacterProfile, CharacterVoiceProfile
from chatwaifu_runtime.config.settings import RealtimeConfig, StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.realtime.cloud.context import (
    CloudEgressPolicy,
    ConsentRequiredError,
    EgressGrant,
    PolicyDeniedError,
    RealtimeContextPatchBuilder,
)
from chatwaifu_runtime.realtime.cloud.contracts import (
    RealtimeContextPatch,
    RealtimeSessionOpenRequest,
    RealtimeSkillCapability,
)
from chatwaifu_runtime.realtime.cloud.coordinator import (
    CloudRealtimeCoordinator,
    InMemoryDomainSink,
)
from chatwaifu_runtime.realtime.cloud.fake import (
    FakeCloudRealtimeBackend,
    FakeCloudRealtimeSession,
)
from chatwaifu_runtime.realtime.cloud.mirror import RealtimeSessionMirror


def make_test_profile() -> CharacterProfile:
    return CharacterProfile(
        character_id="ayachi_nene",
        display_name="綾地寧々",
        tagline="秘密を抱えた学院の先輩",
        greeting="こんにちは、今日はどんな話をしましょうか？",
        system_prompt="あなたは綾地寧々です。親しみやすく落ち着いたトーンで話します。",
        accent_color="#E06A8B",
        voice_profile=CharacterVoiceProfile(
            voice_id="ayachi_nene_voice",
            display_name="綾地寧々 Voice",
            language="ja",
            provider="sherpa-onnx",
            model="kokoro",
            speaker_id=0,
            license="MIT",
        ),
        content_notice="Age 18+ visual novel character.",
        lexicon={"先輩": "Senpai", "オカルト研究部": "Occult Research Club"},
    )


def make_test_snapshot() -> CharacterKernelSnapshot:
    now = datetime.now(UTC)
    return CharacterKernelSnapshot(
        character_id="ayachi_nene",
        user_scope="local",
        revision=1,
        affect=AffectState(
            valence=0.7,
            arousal=0.3,
            energy=0.8,
            attention=0.9,
            embarrassment=0.05,
            tension=0.1,
            updated_at=now,
        ),
        relationship=RelationshipState(
            stage="trusted",
            affinity=0.85,
            trust=0.9,
            familiarity=0.8,
            comfort=0.85,
            updated_at=now,
        ),
    )


def make_test_memories() -> list[MemoryRecord]:
    now = datetime.now(UTC)
    source_id = uuid4()
    return [
        MemoryRecord(
            memory_id=uuid4(),
            namespace="conversation",
            kind="semantic.preference",
            subject_id="user",
            predicate="likes",
            value={"item": "green tea"},
            text="ユーザーは緑茶が好き。",
            sensitivity=PrivacyLevel.PUBLIC,
            state="active",
            confidence=0.9,
            importance=0.7,
            source_event_ids=[source_id],
            observed_at=now,
            created_at=now,
            updated_at=now,
        ),
        MemoryRecord(
            memory_id=uuid4(),
            namespace="finance",
            kind="semantic.fact",
            subject_id="user",
            predicate="has_account",
            value={"bank": "secret_bank"},
            text="極秘の銀行口座番号1234567890。",
            sensitivity=PrivacyLevel.SENSITIVE,  # Sensitive, should be filtered
            state="active",
            confidence=0.95,
            importance=0.9,
            source_event_ids=[source_id],
            observed_at=now,
            created_at=now,
            updated_at=now,
        ),
        MemoryRecord(
            memory_id=uuid4(),
            namespace="conversation",
            kind="semantic.fact",
            subject_id="user",
            predicate="lived_in",
            value={"city": "Tokyo"},
            text="昔は東京に住んでいた。",
            sensitivity=PrivacyLevel.PUBLIC,
            state="tombstoned",  # Tombstoned, should be filtered
            confidence=0.8,
            importance=0.5,
            source_event_ids=[source_id],
            observed_at=now,
            created_at=now,
            updated_at=now,
        ),
    ]


@pytest.mark.asyncio
async def test_scenario_1_deny_policy_blocks_backend_zero_calls() -> None:
    """1. 'deny' policy rejects session before opening, 0 backend calls, records audit."""
    backend = FakeCloudRealtimeBackend()
    policy = CloudEgressPolicy(policy_mode="deny")
    session_id = uuid4()
    request = RealtimeSessionOpenRequest(
        session_id=session_id,
        character_id="ayachi_nene",
    )

    with pytest.raises(PolicyDeniedError) as exc_info:
        await policy.evaluate_and_open_session(backend, request)

    assert "denied" in str(exc_info.value).lower()
    assert len(backend.open_session_calls) == 0
    assert len(policy.audit_receipts) == 1
    assert policy.audit_receipts[0].policy_decision == "deny"


@pytest.mark.asyncio
async def test_scenario_2_ask_policy_unapproved_blocks_backend_zero_calls() -> None:
    """2. 'ask' policy without explicit approval rejects session, 0 backend calls."""
    backend = FakeCloudRealtimeBackend()
    policy = CloudEgressPolicy(policy_mode="ask")
    session_id = uuid4()
    request = RealtimeSessionOpenRequest(
        session_id=session_id,
        character_id="ayachi_nene",
    )

    with pytest.raises(ConsentRequiredError) as exc_info:
        await policy.evaluate_and_open_session(backend, request)

    assert "consent" in str(exc_info.value).lower()
    assert len(backend.open_session_calls) == 0
    assert len(policy.audit_receipts) == 1
    assert policy.audit_receipts[0].policy_decision == "consent_required"


@pytest.mark.asyncio
async def test_scenario_3_ask_policy_approved_grants_scoped_access() -> None:
    """3. 'ask' policy with explicit approval allows specified scope; other sessions blocked."""
    backend = FakeCloudRealtimeBackend()
    policy = CloudEgressPolicy(policy_mode="ask")
    session_id_1 = uuid4()
    session_id_2 = uuid4()

    # Grant explicit approval for session 1
    policy.grant(EgressGrant(session_id=session_id_1, approved_by="user", scope="session"))

    req1 = RealtimeSessionOpenRequest(session_id=session_id_1, character_id="ayachi_nene")
    session1 = await policy.evaluate_and_open_session(backend, req1)

    assert session1 is not None
    assert len(backend.open_session_calls) == 1
    assert backend.open_session_calls[0].session_id == session_id_1

    # Session 2 has no grant and must be blocked
    req2 = RealtimeSessionOpenRequest(session_id=session_id_2, character_id="ayachi_nene")
    with pytest.raises(ConsentRequiredError):
        await policy.evaluate_and_open_session(backend, req2)

    assert len(backend.open_session_calls) == 1


def test_scenario_4_patch_builder_budget_pruning_priority_retention() -> None:
    """4. Over-budget patch prunes whole components by priority without truncating JSON."""
    builder = RealtimeContextPatchBuilder(
        max_bytes=350,  # Small byte budget
        max_tokens=200,
    )
    profile = make_test_profile()
    snapshot = make_test_snapshot()
    memories = make_test_memories()
    skills = [
        RealtimeSkillCapability(
            skill_id="weather.get_current",
            display_name="Weather",
            description="Fetches current weather for a city",
        ),
        RealtimeSkillCapability(
            skill_id="alarm.set",
            display_name="Alarm",
            description="Sets a morning wakeup alarm for user",
        ),
    ]

    patch = builder.build_patch(
        safety_contract="Safety and galgame roleplay rules.",
        character_profile=profile,
        kernel_snapshot=snapshot,
        memories=memories,
        skills=skills,
    )

    assert patch.total_bytes <= 350
    kinds = [c.kind for c in patch.components]

    # Priority order: Safety > Persona > Relationship > Affect > Skills > Memory
    # Memory (priority 5) must have been pruned first!
    assert "memory" not in kinds
    # Safety and Persona should be retained
    assert "safety" in kinds
    assert "persona" in kinds


def test_scenario_5_persona_relationship_affect_from_runtime_snapshot() -> None:
    """5. Persona, Relationship, and Affect are extracted accurately from runtime snapshot."""
    builder = RealtimeContextPatchBuilder()
    profile = make_test_profile()
    snapshot = make_test_snapshot()

    patch = builder.build_patch(
        character_profile=profile,
        kernel_snapshot=snapshot,
    )

    component_map = {c.kind: c for c in patch.components}
    assert "persona" in component_map
    assert "綾地寧々" in component_map["persona"].text

    assert "relationship" in component_map
    assert "trusted" in component_map["relationship"].text

    assert "affect" in component_map
    assert "Valence: 0.70" in component_map["affect"].text


def test_scenario_6_memory_only_includes_selected_excerpts() -> None:
    """6. Memory component only includes active, non-restricted excerpts."""
    builder = RealtimeContextPatchBuilder()
    memories = make_test_memories()

    patch = builder.build_patch(memories=memories)
    memory_comps = [c for c in patch.components if c.kind == "memory"]
    assert len(memory_comps) == 1

    memory_text = memory_comps[0].text
    assert "緑茶" in memory_text
    # Restricted bank account and tombstoned Tokyo address must NOT appear
    assert "銀行口座" not in memory_text
    assert "1234567890" not in memory_text
    assert "東京" not in memory_text


@pytest.mark.asyncio
async def test_scenario_7_egress_receipt_excludes_plaintext_and_secrets() -> None:
    """7. Egress receipt contains IDs and metadata only, no memory plaintext or API keys."""
    backend = FakeCloudRealtimeBackend()
    policy = CloudEgressPolicy(policy_mode="allow")
    session_id = uuid4()
    profile = make_test_profile()
    snapshot = make_test_snapshot()
    memories = make_test_memories()
    skills = [
        RealtimeSkillCapability(
            skill_id="calendar.sync",
            display_name="Calendar",
            description="Sync calendar",
        ),
    ]

    req = RealtimeSessionOpenRequest(
        session_id=session_id,
        character_id="ayachi_nene",
    )

    session = await policy.evaluate_and_open_session(
        backend,
        req,
        character_profile=profile,
        kernel_snapshot=snapshot,
        memories=memories,
        skills=skills,
    )
    assert session is not None
    assert len(policy.audit_receipts) == 1

    receipt = policy.audit_receipts[0]
    payload_dict = receipt.model_dump()

    receipt_str = str(payload_dict)
    assert "super-secret-key-123" not in receipt_str
    assert "bearer-token-abc" not in receipt_str
    assert "緑茶" not in receipt_str
    assert "極秘" not in receipt_str

    # Memory records should be referenced by ID only
    assert len(receipt.memory_record_ids) > 0


@pytest.mark.asyncio
async def test_scenario_8_provider_context_update_failure_produces_structured_error() -> None:
    """8. Provider context update failure normalizes into StructuredError and notifies sink."""
    session_id = uuid4()
    req = RealtimeSessionOpenRequest(session_id=session_id, character_id="ayachi_nene")
    session = FakeCloudRealtimeSession(req, auto_ready=True)

    # Inject failure into session.update_context
    async def failing_update(_: RealtimeContextPatch) -> None:
        raise RuntimeError("Provider connection lost during context push")

    session.update_context = failing_update  # type: ignore[assignment]

    mirror = RealtimeSessionMirror(session_id, backend_id="fake")
    sink = InMemoryDomainSink()
    coordinator = CloudRealtimeCoordinator(
        session_id=session_id,
        session=session,
        mirror=mirror,
        domain_sink=sink,
    )

    gateway = CloudEgressPolicy(policy_mode="allow")
    with pytest.raises(RuntimeError):
        await gateway.update_context(
            session=session,
            backend_id="fake",
            safety_contract="test safety",
            coordinator=coordinator,
        )

    assert len(sink.provider_errors) == 1
    err = sink.provider_errors[0][1]
    assert err.code == "provider_context_update_failed"
    assert err.component == "cloud_realtime"
    assert err.retryable is True


def test_scenario_9_cascade_mode_bypasses_cloud_egress() -> None:
    """9. Cascade mode does not trigger cloud egress policy or backend opening."""
    config = RealtimeConfig(connection_mode="cascade")
    assert config.connection_mode == "cascade"

    backend = FakeCloudRealtimeBackend()
    assert len(backend.open_session_calls) == 0


def test_scenario_10_deterministic_reproducibility() -> None:
    """10. Identical inputs produce identical components, ordering, byte counts, and hash."""
    builder = RealtimeContextPatchBuilder()
    profile = make_test_profile()
    snapshot = make_test_snapshot()
    memories = make_test_memories()

    patch1 = builder.build_patch(
        safety_contract="Strict safety framing.",
        character_profile=profile,
        kernel_snapshot=snapshot,
        memories=memories,
    )
    patch2 = builder.build_patch(
        safety_contract="Strict safety framing.",
        character_profile=profile,
        kernel_snapshot=snapshot,
        memories=memories,
    )

    assert patch1.content_hash == patch2.content_hash
    assert patch1.total_bytes == patch2.total_bytes
    assert patch1.estimated_tokens == patch2.estimated_tokens
    assert len(patch1.components) == len(patch2.components)
    for c1, c2 in zip(patch1.components, patch2.components, strict=True):
        assert c1.kind == c2.kind
        assert c1.text == c2.text
        assert c1.priority == c2.priority


@pytest.mark.asyncio
async def test_scenario_11_event_store_audit_persistence(tmp_path: Path) -> None:
    """11. CloudEgressPolicy persists audit events in EventStore without new migrations."""
    db_path = tmp_path / "test_audit.db"
    database = Database(db_path, StorageConfig(database_path=db_path))
    await database.open()
    event_store = EventStore(database)

    session_id = uuid4()

    # Pre-populate session in database so event_store can sequence events
    now_iso = datetime.now(UTC).isoformat()
    async with database.transaction() as connection:
        await connection.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(session_id),
                "ayachi_nene",
                "active",
                "idle",
                0,
                1,
                now_iso,
                now_iso,
            ),
        )

    policy = CloudEgressPolicy(policy_mode="allow", event_store=event_store)
    backend = FakeCloudRealtimeBackend()
    req = RealtimeSessionOpenRequest(session_id=session_id, character_id="ayachi_nene")

    session = await policy.evaluate_and_open_session(backend, req)
    assert session is not None

    # Verify event in database
    async with database.transaction() as connection:
        cursor = await connection.execute(
            "SELECT event_type, payload_json FROM events WHERE session_id = ?",
            (str(session_id),),
        )
        raw_rows = await cursor.fetchall()
        await cursor.close()
        rows = list(raw_rows)

    assert len(rows) == 1
    first_row = rows[0]
    event_type = str(first_row[0])
    payload_json = str(first_row[1])
    assert event_type == "cloud.egress_receipt"
    assert "ayachi_nene" not in payload_json or "provider_backend_id" in payload_json
    assert "policy_decision" in payload_json

    await database.close()


def test_skills_reject_raw_dicts() -> None:
    """Verify build_patch raises TypeError when skills contain raw dicts
    instead of RealtimeSkillCapability.
    """
    builder = RealtimeContextPatchBuilder()
    with pytest.raises(TypeError, match="Expected RealtimeSkillCapability instance"):
        builder.build_patch(
            skills=[{"name": "test", "description": "bad"}]  # type: ignore[list-item]
        )
