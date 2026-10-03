"""Persisted scene speaker authority, member state, and legacy migration proofs."""

# pyright: reportPrivateUsage=false
import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from chatwaifu_protocol.character import ResponsePlan
from chatwaifu_protocol.events import (
    AssistantSpokenTextCommittedEvent,
    AssistantSpokenTextCommittedPayload,
    UserTurnCommittedEvent,
    UserTurnCommittedPayload,
)
from chatwaifu_protocol.memory import MemoryRecord, MemoryRecordDraft, MemorySource
from chatwaifu_protocol.session import SessionSnapshot
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.character_kernel.prompt import _memory_text, _tokens
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.memory.extractor import ExtractedMemoryCandidate
from chatwaifu_runtime.memory.inference import LlmMemoryCandidateExtractor
from chatwaifu_runtime.memory.service import UserTurnMemoryObservation
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.migrations import MIGRATIONS
from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository
from chatwaifu_runtime.providers.model_config import ModelConfigurationService


@pytest.fixture
async def runtime(runtime_settings: Settings) -> AsyncIterator[RuntimeContainer]:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        yield container
    finally:
        await container.stop()


async def _members(runtime: RuntimeContainer) -> tuple[SessionSnapshot, SessionSnapshot]:
    alice = await runtime.sessions.create_participant("Alice")
    bob = await runtime.sessions.create_participant("Bob")
    scene = await runtime.sessions.create_scene(
        "Shared", [alice.participant_id, bob.participant_id]
    )
    return (
        await runtime.sessions.create_session(
            "default", participant_id=alice.participant_id, scene_id=scene.scene_id
        ),
        await runtime.sessions.create_session(
            "default", participant_id=bob.participant_id, scene_id=scene.scene_id
        ),
    )


async def _source(
    runtime: RuntimeContainer, session: SessionSnapshot, text: str
) -> UserTurnCommittedEvent:
    now, turn_id = datetime.now(UTC), uuid4()
    async with runtime.database.transaction() as connection:
        await connection.execute(
            "INSERT INTO turns(turn_id,session_id,role,committed_text,committed_at,created_at) "
            "VALUES (?,?,'user',?,?,?)",
            (str(turn_id), str(session.session_id), text, now.isoformat(), now.isoformat()),
        )
    return await runtime.event_store.append(
        UserTurnCommittedEvent(
            event_id=uuid4(),
            session_id=session.session_id,
            turn_id=turn_id,
            sequence=0,
            occurred_at=now,
            source="test",
            payload=UserTurnCommittedPayload(text=text),
        )
    )


async def _observe(runtime: RuntimeContainer, session: SessionSnapshot, text: str) -> None:
    event = await _source(runtime, session, text)
    assert event.turn_id is not None
    await runtime.memory.observe_user_turn(
        session.session_id, event.turn_id, event.event_id, "default", text
    )


@pytest.mark.asyncio
async def test_same_scene_identical_and_conflicting_facts_are_subject_isolated(
    runtime: RuntimeContainer,
) -> None:
    alice, bob = await _members(runtime)
    assert alice.user_scope == bob.user_scope
    assert alice.state_scope != bob.state_scope
    for member in (alice, bob):
        await _observe(runtime, member, "请记住我喜欢蓝色")
    records = await runtime.memory.list(session_id=alice.session_id)
    assert len(records) == 2
    assert {record.subject_id for record in records} == {
        f"participant:{alice.participant_id}",
        f"participant:{bob.participant_id}",
    }
    assert len({record.memory_id for record in records}) == 2
    await _observe(runtime, alice, "请记住我不喜欢蓝色")
    records = await runtime.memory.list(session_id=bob.session_id)
    by_subject = {record.subject_id: record for record in records}
    assert by_subject[f"participant:{alice.participant_id}"].text == "我不喜欢蓝色"
    assert by_subject[f"participant:{bob.participant_id}"].text == "我喜欢蓝色"
    await _observe(runtime, alice, "请忘记蓝色")
    records = await runtime.memory.list(session_id=alice.session_id)
    assert len(records) == 1 and records[0].subject_id == f"participant:{bob.participant_id}"
    # Alice's tombstone cannot suppress Bob's reaffirmation or a third subject.
    await _observe(runtime, bob, "请记住我喜欢蓝色")
    assert len(await runtime.memory.list(session_id=bob.session_id)) == 1
    namespace = records[0].namespace
    assert (
        await runtime.memory_repository.find_tombstone(
            namespace, "我不喜欢蓝色", subject_id=f"participant:{bob.participant_id}"
        )
        is None
    )
    assert (
        await runtime.memory_repository.find_tombstone(
            namespace, "我不喜欢蓝色", subject_id=f"participant:{alice.participant_id}"
        )
        is not None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["请记住:我们喜欢爬山", "请记住:Bob喜欢蓝色"])
async def test_collective_or_third_person_facts_require_review_without_member_assignment(
    runtime: RuntimeContainer,
    text: str,
) -> None:
    alice, bob = await _members(runtime)
    await _observe(runtime, alice, text)
    assert await runtime.memory.list(session_id=alice.session_id) == []
    proposals = await runtime.memory.list_proposals(status="pending", session_id=bob.session_id)
    assert len(proposals) == 1
    assert proposals[0].candidate is not None and proposals[0].candidate.subject_id is None
    assert proposals[0].candidate.subject_id != f"participant:{bob.participant_id}"


@pytest.mark.asyncio
async def test_projection_and_model_subject_spoof_use_persisted_speaker(
    runtime: RuntimeContainer,
) -> None:
    alice, bob = await _members(runtime)
    event = await _source(runtime, alice, "请记住我喜欢绿色")
    assert event.turn_id is not None
    evidence = await runtime.memory_repository.event_evidence(event.event_id)
    assert evidence is not None
    assert evidence.identity == await runtime.sessions.conversation_identity(alice.session_id)
    assert await runtime.memory.enqueue_user_turn(
        UserTurnMemoryObservation(
            session_id=alice.session_id,
            turn_id=event.turn_id,
            source_event_id=event.event_id,
            character_id="default",
            text="请记住我喜欢绿色",
            user_scope="local",
        )
    )
    await asyncio.wait_for(runtime.memory._projection_queue.join(), timeout=2)
    assert (await runtime.memory.list(session_id=bob.session_id))[0].subject_id == (
        f"participant:{alice.participant_id}"
    )
    other = await _source(runtime, alice, "我喜欢红色")
    assert other.turn_id is not None
    draft = MemoryRecordDraft(
        namespace=f"character/default/user/{alice.user_scope}",
        kind="semantic.preference",
        subject_id=f"participant:{bob.participant_id}",
        predicate="preference.like.红色",
        value=True,
        text="我喜欢红色",
        observed_at=other.occurred_at,
        confidence=0.95,
        importance=0.8,
    )
    proposal = await runtime.memory._process_candidate(
        alice.session_id,
        other.turn_id,
        other.event_id,
        ExtractedMemoryCandidate(draft, explicit=True, rationale="untrusted model subject"),
    )
    assert proposal.status == "pending"
    assert proposal.candidate is not None and proposal.candidate.subject_id is None
    with pytest.raises(ValueError, match="source session mismatch"):
        await runtime.memory._process_candidate(
            bob.session_id,
            other.turn_id,
            other.event_id,
            ExtractedMemoryCandidate(draft, explicit=True, rationale="mismatched source"),
        )
    await _observe(runtime, bob, "请记住我喜欢红色")
    target = next(
        item
        for item in await runtime.memory.list(session_id=bob.session_id)
        if item.subject_id == f"participant:{bob.participant_id}"
    )
    with pytest.raises(ValueError, match="target subject mismatch"):
        await runtime.memory._process_candidate(
            alice.session_id,
            other.turn_id,
            other.event_id,
            ExtractedMemoryCandidate(
                draft.model_copy(update={"subject_id": "user"}),
                explicit=True,
                rationale="forged target",
            ),
            target_override=target.memory_id,
        )
    assert (await runtime.memory_repository.get(target.memory_id)) == target
    # A separately authorized management action still may correct the shared record.
    corrected = await runtime.memory.correct(alice.session_id, target.memory_id, "我喜欢紫色")
    assert corrected.subject_id == target.subject_id


@pytest.mark.asyncio
async def test_inference_projection_retains_related_subjects_and_canonical_speaker(
    runtime: RuntimeContainer,
) -> None:
    alice, bob = await _members(runtime)
    for member in (alice, bob):
        await _observe(runtime, member, "请记住我喜欢蓝色")

    class Models:
        def __init__(self) -> None:
            self.inputs: list[dict[str, object]] = []

        def get(self, role: str) -> SimpleNamespace:
            assert role == "memory_extraction"
            return SimpleNamespace(enabled=True, provider="openai_compatible")

        async def complete(self, role: str, system: str, user: str) -> str:
            assert role == "memory_extraction"
            self.inputs.append(json.loads(user))
            return json.dumps(
                {
                    "memories": [
                        {
                            "kind": "semantic.preference",
                            "subject_id": "user",
                            "predicate": "preference.like.蓝色",
                            "value": False,
                            "text": "我不喜欢蓝色",
                            "confidence": 0.95,
                            "importance": 0.8,
                            "sensitivity": "private",
                            "rationale": "changed preference",
                        }
                    ]
                }
            )

    models = Models()
    runtime.memory._inference = LlmMemoryCandidateExtractor(cast(ModelConfigurationService, models))
    event = await _source(runtime, alice, "我不喜欢蓝色")
    assert event.turn_id is not None
    await runtime.memory.enqueue_user_turn(
        UserTurnMemoryObservation(
            session_id=alice.session_id,
            turn_id=event.turn_id,
            source_event_id=event.event_id,
            character_id="default",
            text="我不喜欢蓝色",
        )
    )
    await asyncio.wait_for(runtime.memory._projection_queue.join(), timeout=2)
    assert len(models.inputs) == 1
    related = cast(list[dict[str, object]], models.inputs[0]["related_existing_memories"])
    assert {item["subject_id"] for item in related} == {
        f"participant:{alice.participant_id}",
        f"participant:{bob.participant_id}",
    }
    proposals = await runtime.memory.list_proposals(status="pending", session_id=alice.session_id)
    assert len(proposals) == 1 and proposals[0].candidate is not None
    assert proposals[0].candidate.subject_id == f"participant:{alice.participant_id}"
    assert proposals[0].target_memory_id is not None
    target = await runtime.memory_repository.get(proposals[0].target_memory_id)
    assert target is not None and target.subject_id == f"participant:{alice.participant_id}"


@pytest.mark.asyncio
async def test_private_facts_stay_out_of_scene_prompt_and_subjects_survive_budget(
    runtime: RuntimeContainer,
) -> None:
    alice, bob = await _members(runtime)
    private = await runtime.sessions.create_session("default", participant_id=alice.participant_id)
    await _observe(runtime, private, "请记住我喜欢PRIVATE_SENTINEL")
    for member in (alice, bob):
        await _observe(runtime, member, "请记住我喜欢蓝色")
    packet = await runtime.memory.retrieve_context(alice.session_id, uuid4(), "default", "蓝色")
    excerpts = packet.relevant_memories + packet.pinned_facts
    assert len(excerpts) == 2
    assert {item.subject_id for item in excerpts} == {
        f"participant:{alice.participant_id}",
        f"participant:{bob.participant_id}",
    }
    character = runtime.characters.get("default")
    assert character is not None
    compiled = await runtime.prompt_compiler.compile(
        character=character,
        kernel=await runtime.character_kernel.snapshot("default", user_scope=alice.state_scope),
        plan=ResponsePlan(intent="answer", tone="gentle", expression="neutral", rationale="test"),
        memory=packet,
        history=(),
        user_text="谁喜欢蓝色？",
    )
    combined = compiled.system_prompt + str(compiled.context)
    assert "PRIVATE_SENTINEL" not in combined
    for member in (alice, bob):
        assert f'[subject="participant:{member.participant_id}"]' in combined
    text, recalled, ids = _memory_text(packet, 1)
    assert (text, recalled, ids) == ("", (), ())
    line = f'- [relevant] [subject="{excerpts[0].subject_id}"] {excerpts[0].text}'
    trimmed, recalled, ids = _memory_text(packet, _tokens(line))
    assert len(ids) == 1 and len(recalled) == 1 and "[subject=" in trimmed


@pytest.mark.asyncio
async def test_member_state_identity_reopen_and_scope_reset(runtime: RuntimeContainer) -> None:
    alice, bob = await _members(runtime)
    owner = await runtime.sessions.create_session("default")
    identity = await runtime.sessions.conversation_identity(alice.session_id)
    assert identity.participant_id == alice.participant_id
    assert identity.memory_scope == alice.user_scope
    assert identity.state_scope == f"scene_member:{alice.scene_id}:{alice.participant_id}"
    assert set(identity.audience_ids) == {alice.participant_id, bob.participant_id}
    with pytest.raises(FrozenInstanceError):
        identity.participant_id = "forged"  # type: ignore[misc]
    with pytest.raises(KeyError):
        await runtime.sessions.conversation_identity(uuid4())
    for member, text in ((alice, "谢谢喜欢你"), (bob, "讨厌你闭嘴"), (owner, "你好")):
        await runtime.character_kernel.observe_user_turn(
            session_id=member.session_id,
            turn_id=uuid4(),
            generation_id=uuid4(),
            character_id="default",
            text=text,
        )
    a_state = await runtime.character_kernel.snapshot("default", user_scope=alice.state_scope)
    b_state = await runtime.character_kernel.snapshot("default", user_scope=bob.state_scope)
    assert a_state.relationship.affinity > b_state.relationship.affinity
    assert a_state.relationship.interaction_count == b_state.relationship.interaction_count == 1
    continuation = await runtime.sessions.create_session(
        "default",
        participant_id=alice.participant_id,
        scene_id=alice.scene_id,
    )
    assert continuation.state_scope == alice.state_scope
    assert (
        await runtime.character_kernel.snapshot("default", user_scope=continuation.state_scope)
    ).relationship.interaction_count == 1
    for member in (alice, bob):
        await _observe(runtime, member, "请记住我喜欢蓝色")
    await _observe(runtime, owner, "请记住我喜欢咖啡")
    await runtime.conversation.reset(alice.session_id)
    assert await runtime.memory.list(session_id=bob.session_id) == []
    assert await runtime.memory.list(session_id=owner.session_id)
    assert (
        await runtime.character_kernel.snapshot("default", user_scope=bob.state_scope)
    ).relationship.interaction_count == 0
    assert (
        await runtime.character_kernel.snapshot("default", user_scope=owner.state_scope)
    ).relationship.interaction_count == 1


@pytest.mark.asyncio
async def test_scene_spoken_candidate_never_authorizes_model_selected_member(
    runtime: RuntimeContainer,
) -> None:
    alice, bob = await _members(runtime)
    user = await _source(runtime, alice, "你好")
    event = await runtime.event_store.append(
        AssistantSpokenTextCommittedEvent(
            event_id=uuid4(),
            session_id=alice.session_id,
            turn_id=user.turn_id,
            sequence=0,
            occurred_at=datetime.now(UTC),
            source="test",
            generation_id=uuid4(),
            payload=AssistantSpokenTextCommittedPayload(
                spoken_text="Bob喜欢蓝色",
                text="Bob喜欢蓝色",
                stream_id=uuid4(),
                segment_id=uuid4(),
            ),
        )
    )
    assert user.turn_id is not None
    draft = MemoryRecordDraft(
        namespace=f"character/default/user/{alice.user_scope}",
        kind="relationship.signal",
        subject_id=f"participant:{bob.participant_id}",
        text="Bob喜欢蓝色",
        observed_at=event.occurred_at,
        confidence=0.95,
        importance=0.8,
    )
    proposals = await runtime.memory.apply_spoken_candidates(
        session_id=alice.session_id,
        turn_id=user.turn_id,
        source_event_id=event.event_id,
        character_id="default",
        candidates=[
            ExtractedMemoryCandidate(
                draft,
                explicit=True,
                rationale="model selected another member",
                auto_commit=True,
            )
        ],
    )
    assert len(proposals) == 1 and proposals[0].status == "pending"
    assert proposals[0].candidate is not None and proposals[0].candidate.subject_id is None
    assert await runtime.memory.list(session_id=bob.session_id) == []


@pytest.mark.asyncio
async def test_populated_migration_preserves_legacy_state_and_immutable_scope(
    runtime_settings: Settings,
) -> None:
    legacy = Database(
        runtime_settings.database_path,
        runtime_settings.storage,
        migrations=tuple(item for item in MIGRATIONS if item[0] < 39),
    )
    await legacy.open()
    sid, now = uuid4(), datetime.now(UTC).isoformat()
    async with legacy.transaction() as connection:
        await connection.execute("INSERT INTO participants VALUES ('alice','Alice',?)", (now,))
        await connection.execute(
            "INSERT INTO conversation_scenes VALUES ('scene','Scene','[\"alice\",\"local\"]',?)",
            (now,),
        )
        await connection.execute(
            "INSERT INTO sessions(session_id,character_id,state,conversation_state,created_at,"
            "updated_at,participant_id,scene_id,scene_kind,audience_json,user_scope) "
            "VALUES (?,'default','ready','idle',?,?,'alice','scene','shared','[\"alice\"]',"
            "'scene:scene')",
            (str(sid), now, now),
        )
        await connection.execute(
            "INSERT INTO relationship_states VALUES ('default','scene:scene',.8,.7,.6,.5,.1,"
            "17,'trusted','Legacy',9,?)",
            (now,),
        )
        await connection.execute(
            "INSERT INTO character_states VALUES ('default','scene:scene',.15,.5,.5,.5,0,0,9,?)",
            (now,),
        )
    occurred = datetime.fromisoformat(now)
    event = await EventStore(legacy).append(
        UserTurnCommittedEvent(
            event_id=uuid4(),
            session_id=sid,
            sequence=0,
            occurred_at=occurred,
            source="legacy",
            payload=UserTurnCommittedPayload(text="请记住我喜欢LEGACY_AMBIGUOUS"),
        )
    )
    record = MemoryRecord(
        memory_id=uuid4(),
        namespace="character/default/user/scene:scene",
        subject_id="user",
        kind="semantic.preference",
        text="我喜欢LEGACY_AMBIGUOUS",
        observed_at=occurred,
        source_event_ids=[event.event_id],
        confidence=0.95,
        importance=0.8,
        created_at=occurred,
        updated_at=occurred,
    )
    await SQLiteMemoryRepository(legacy).create_record(
        record,
        [
            MemorySource(
                source_id=uuid4(),
                memory_id=record.memory_id,
                source_event_id=event.event_id,
                session_id=sid,
                source_kind="user_turn",
                created_at=occurred,
            )
        ],
    )
    legacy_relationship = await legacy.fetchone(
        "SELECT * FROM relationship_states WHERE user_scope='scene:scene'"
    )
    legacy_affect = await legacy.fetchone(
        "SELECT * FROM character_states WHERE user_scope='scene:scene'"
    )
    assert legacy_relationship is not None and legacy_affect is not None
    await legacy.close()
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.get_session(sid)
        assert session is not None and session.state_scope == session.user_scope == "scene:scene"
        migrated_relationship = await container.database.fetchone(
            "SELECT * FROM relationship_states WHERE user_scope='scene:scene'"
        )
        migrated_affect = await container.database.fetchone(
            "SELECT * FROM character_states WHERE user_scope='scene:scene'"
        )
        assert migrated_relationship is not None and migrated_affect is not None
        assert dict(migrated_relationship) == dict(legacy_relationship)
        assert dict(migrated_affect) == dict(legacy_affect)
        old = await container.character_kernel.snapshot("default", user_scope=session.state_scope)
        assert (
            old.relationship.interaction_count == 17
            and old.relationship.preferred_address == "Legacy"
        )
        old_facts = await container.memory.list(session_id=sid)
        assert len(old_facts) == 1 and old_facts[0].subject_id == "user"
        new_scene = await container.sessions.create_scene("New group", ["alice", "local"])
        new_group = await container.sessions.create_session(
            "default",
            participant_id="alice",
            scene_id=new_scene.scene_id,
        )
        assert await container.memory.list(session_id=new_group.session_id) == []
        fresh = await container.sessions.create_session(
            "default", participant_id="alice", scene_id="scene"
        )
        assert fresh.state_scope == "scene_member:scene:alice"
        assert (
            await container.character_kernel.snapshot("default", user_scope=fresh.state_scope)
        ).relationship.interaction_count == 0
        for column in ("state_scope", "user_scope", "participant_id"):
            with pytest.raises(Exception, match="scope is immutable"):
                async with container.database.transaction() as connection:
                    await connection.execute(
                        f"UPDATE sessions SET {column}='forged' WHERE session_id=?", (str(sid),)
                    )
        payload = session.model_dump(mode="json")
        payload.pop("state_scope")
        assert SessionSnapshot.model_validate(payload).state_scope == session.user_scope
    finally:
        await container.stop()
    reopened = Database(runtime_settings.database_path, runtime_settings.storage)
    await reopened.open()
    try:
        row = await reopened.fetchone(
            "SELECT state_scope FROM sessions WHERE session_id=?", (str(sid),)
        )
        assert row is not None and row["state_scope"] == "scene:scene"
        row = await reopened.fetchone(
            "SELECT interaction_count FROM relationship_states WHERE user_scope='scene:scene'"
        )
        assert row is not None and row["interaction_count"] == 17
        row = await reopened.fetchone(
            "SELECT subject_id,text FROM memory_records WHERE memory_id=?", (str(record.memory_id),)
        )
        assert row is not None and row["subject_id"] == "user"
        assert row["text"] == "我喜欢LEGACY_AMBIGUOUS"
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_migration_39_failure_rolls_back_backfill_trigger_and_index(
    runtime_settings: Settings,
) -> None:
    previous = tuple(item for item in MIGRATIONS if item[0] < 39)
    legacy = Database(runtime_settings.database_path, runtime_settings.storage, migrations=previous)
    await legacy.open()
    sid, now = str(uuid4()), datetime.now(UTC).isoformat()
    async with legacy.transaction() as connection:
        await connection.execute(
            "INSERT INTO sessions(session_id,character_id,state,conversation_state,created_at,"
            "updated_at,user_scope) VALUES (?,'default','ready','idle',?,?,'custom:legacy')",
            (sid, now, now),
        )
    await legacy.close()
    version, script = MIGRATIONS[-1]
    assert version == 39
    broken = Database(
        runtime_settings.database_path,
        runtime_settings.storage,
        migrations=(*previous, (39, script + "\nSELECT * FROM missing_table;")),
    )
    with pytest.raises(Exception, match="missing_table"):
        await broken.open()
    reopened = Database(
        runtime_settings.database_path, runtime_settings.storage, migrations=previous
    )
    await reopened.open()
    try:
        columns = await reopened.fetchall("PRAGMA table_info(sessions)")
        assert "state_scope" not in {row["name"] for row in columns}
        row = await reopened.fetchone("SELECT user_scope FROM sessions WHERE session_id=?", (sid,))
        assert row is not None and row["user_scope"] == "custom:legacy"
        index = await reopened.fetchone(
            "SELECT sql FROM sqlite_master WHERE name='memory_records_active_text_unique_idx'"
        )
        assert index is not None and "subject_id" not in index["sql"]
        with pytest.raises(Exception, match="scope is immutable"):
            async with reopened.transaction() as connection:
                await connection.execute(
                    "UPDATE sessions SET user_scope='forged' WHERE session_id=?", (sid,)
                )
    finally:
        await reopened.close()
