"""Comprehensive vertical-slice tests for Phase 17.4A bounded shared jokes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import JsonValue, PrivacyLevel
from chatwaifu_protocol.character import (
    AffectState,
    CharacterKernelSnapshot,
    RelationshipState,
    ResponsePlan,
)
from chatwaifu_protocol.events import (
    AssistantSpokenTextCommittedEvent,
    AssistantSpokenTextCommittedPayload,
    GenericCoreEvent,
    UserTurnCommittedEvent,
    UserTurnCommittedPayload,
)
from chatwaifu_protocol.memory import (
    MemoryChannelAttribution,
    MemoryContextPacket,
    MemoryExcerpt,
    MemoryRecord,
    MemoryRecordDraft,
    MemorySource,
)
from chatwaifu_runtime.character_kernel.prompt import PromptCompiler
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.memory.extractor import ExtractedMemoryCandidate
from chatwaifu_runtime.memory.policy import MemoryPolicy, MemoryWriteDecision
from chatwaifu_runtime.memory.repository import PrecedingAssistantEvidence
from chatwaifu_runtime.memory.service import MemoryService, UserTurnMemoryObservation
from chatwaifu_runtime.memory.shared_joke import (
    classify_uptake,
    is_cue_grounded,
    is_shared_joke_draft,
    validate_and_transform_shared_joke,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository
from chatwaifu_runtime.providers.model_config import ModelConfigurationService

CHARACTERS_ROOT = Path(__file__).resolve().parents[3] / "characters"


class _MockExtractionModels:
    def __init__(self, response_json: str | None = None) -> None:
        self.response_json = response_json or '{"memories": []}'
        self.calls: list[dict[str, str]] = []

    def get(self, role: str) -> SimpleNamespace:
        assert role in {"memory_extraction", "chat", "memory_summary"}
        return SimpleNamespace(
            enabled=True,
            provider="openai_compatible",
            context_window=4096,
        )

    async def complete(self, role: str, system: str, user: str) -> str:
        self.calls.append({"role": role, "system": system, "user": user})
        return self.response_json


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db_path = tmp_path / "runtime.db"
    db = Database(db_path, StorageConfig(database_path=db_path))
    await db.open()
    try:
        yield db
    finally:
        await db.close()


def _make_source(
    memory_id: UUID,
    source_event_id: UUID,
    session_id: UUID,
    source_kind: Literal[
        "user_turn", "assistant_spoken", "assistant_delivered", "memory_management", "migration"
    ] = "user_turn",
    turn_id: UUID | None = None,
    channel_attribution: MemoryChannelAttribution | None = None,
) -> MemorySource:
    return MemorySource(
        source_id=uuid4(),
        memory_id=memory_id,
        source_event_id=source_event_id,
        session_id=session_id,
        turn_id=turn_id,
        source_kind=source_kind,
        channel_attribution=channel_attribution,
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def event_store(database: Database) -> EventStore:
    return EventStore(database)


@pytest.fixture
def repository(database: Database) -> SQLiteMemoryRepository:
    return SQLiteMemoryRepository(database)


async def _create_session(
    database: Database,
    session_id: UUID,
    character_id: str = "default-character",
) -> None:
    now = datetime.now(UTC).isoformat()
    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, ?, 'active', 'idle', 0, 1, ?, ?)
            """,
            (str(session_id), character_id, now, now),
        )


async def _create_turn(
    database: Database,
    session_id: UUID,
    turn_id: UUID,
    role: str = "user",
    source_context: Mapping[str, object] | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    source_json = json.dumps(source_context, ensure_ascii=False) if source_context else None
    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO turns (
                turn_id, session_id, role, committed_text, committed_at, created_at,
                source_context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(turn_id), str(session_id), role, "test", now, now, source_json),
        )


async def _create_generation(
    database: Database,
    session_id: UUID,
    turn_id: UUID,
    generation_id: UUID,
    output_text: str = "test output",
    state: str = "completed",
) -> None:
    now = datetime.now(UTC).isoformat()
    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind,
                output_text, started_at, completed_at
            ) VALUES (?, ?, ?, ?, 'mock', ?, ?, ?)
            """,
            (str(generation_id), str(session_id), str(turn_id), state, output_text, now, now),
        )


async def _create_event(
    database: Database,
    session_id: UUID,
    event_id: UUID | None = None,
) -> UUID:
    eid = event_id or uuid4()
    now = datetime.now(UTC)
    evt = GenericCoreEvent.model_validate(
        {
            "event_id": eid,
            "event_type": "system.runtime_started",
            "session_id": session_id,
            "occurred_at": now,
            "source": "runtime.lifecycle",
            "payload": {},
        }
    )
    store = EventStore(database)
    async with database.transaction() as conn:
        await store.append_in_transaction(conn, evt)
    return eid


@pytest.mark.asyncio
async def test_positive_voice_spoken_exchange(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    turn_id = uuid4()
    generation_id = uuid4()
    await _create_session(database, session_id)
    await _create_turn(database, session_id, turn_id)
    await _create_generation(
        database,
        session_id,
        turn_id,
        generation_id,
        output_text="我们要定个暗号:芝麻开门！",
    )

    t0 = datetime.now(UTC)
    spoken_event = AssistantSpokenTextCommittedEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=generation_id,
        occurred_at=t0,
        source="runtime.playback",
        payload=AssistantSpokenTextCommittedPayload(
            stream_id=uuid4(),
            segment_id=uuid4(),
            text="我们要定个暗号:芝麻开门！",
            spoken_text="我们要定个暗号:芝麻开门！",
        ),
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, spoken_event)

    user_turn_id = uuid4()
    user_event_id = uuid4()
    t1 = t0 + timedelta(seconds=5)
    await _create_turn(database, session_id, user_turn_id)
    user_event = UserTurnCommittedEvent(
        event_id=user_event_id,
        session_id=session_id,
        turn_id=user_turn_id,
        occurred_at=t1,
        source="runtime.conversation",
        payload=UserTurnCommittedPayload(text="这是我们的暗号:芝麻开门"),
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, user_event)

    # Repository port verification
    evidence = await repository.get_preceding_presented_assistant(user_event_id)
    assert evidence is not None
    assert evidence.event_type == "assistant.spoken_text_committed"
    assert evidence.presented_text == "我们要定个暗号:芝麻开门！"
    assert evidence.event_id == spoken_event.event_id

    # MemoryService end-to-end extraction and commit
    model_response = json.dumps(
        {
            "memories": [
                {
                    "kind": "episodic.shared_event",
                    "subject_id": "user",
                    "predicate": "shared_joke.芝麻开门",
                    "value": {
                        "cue": "芝麻开门",
                        "context": "双方约定作为暗号",
                        "kind": "shared_joke",
                    },
                    "text": "两人把'芝麻开门'当作了一个共同的梗 (双方约定作为暗号)。",
                    "confidence": 0.85,
                    "importance": 0.7,
                    "sensitivity": "private",
                    "rationale": "用户明确表示这是暗号",
                }
            ]
        },
        ensure_ascii=False,
    )
    models = _MockExtractionModels(model_response)
    service = MemoryService(
        repository=repository,
        publisher=EventPublisher(event_store, EventHub()),
        models=cast(ModelConfigurationService, models),
    )
    proposals = await service.observe_user_turn(
        session_id=session_id,
        turn_id=user_turn_id,
        source_event_id=user_event_id,
        character_id="default-character",
        text="这是我们的暗号:芝麻开门",
    )
    assert len(proposals) == 1
    assert proposals[0].status == "accepted"
    assert proposals[0].operation == "add"
    assert len(proposals[0].evidence_event_ids) == 2
    inference_payload = json.loads(models.calls[0]["user"])
    assert inference_payload["preceding_presented_assistant_text"] == "我们要定个暗号:芝麻开门！"

    # Verify active record and sources in persistence
    records = await repository.list_records(kind="episodic.shared_event")
    assert len(records) == 1
    assert records[0].predicate == "shared_joke.芝麻开门"
    assert isinstance(records[0].value, dict)
    assert records[0].value.get("cue") == "芝麻开门"

    sources = await repository.list_sources(records[0].memory_id)
    assert len(sources) == 2
    source_kinds = {s.source_kind for s in sources}
    assert source_kinds == {"user_turn", "assistant_spoken"}


@pytest.mark.asyncio
async def test_positive_external_delivered_exchange(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    originating_user_turn_id = uuid4()
    generation_id = uuid4()
    await _create_session(database, session_id)

    channel_context = {
        "provider_id": "weixin_ilink",
        "connection_id": str(uuid4()),
        "chat_type": "direct",
        "conversation_key": "conv-1",
        "sender_key": "user-1",
        "principal_scope": "local",
        "conversation_label": "微信私聊",
        "sender_display_name": "木白",
    }
    await _create_turn(
        database, session_id, originating_user_turn_id, source_context=channel_context
    )
    await _create_generation(
        database,
        session_id,
        originating_user_turn_id,
        generation_id,
        output_text="今天就吃火星土豆吧！",
        state="completed",
    )

    t0 = datetime.now(UTC)
    delivery_event = GenericCoreEvent.model_validate(
        {
            "event_id": uuid4(),
            "event_type": "channel.delivery_plan_completed",
            "session_id": session_id,
            # Production delivery events retain the originating inbound user turn ID.
            "turn_id": originating_user_turn_id,
            "generation_id": generation_id,
            "occurred_at": t0,
            "source": "runtime.external_channels",
            "payload": {
                "connection_id": channel_context["connection_id"],
                "channel_turn_id": str(uuid4()),
                "delivery_id": str(uuid4()),
                "part_count": 1,
            },
        }
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, delivery_event)

    user_turn_id = uuid4()
    user_event_id = uuid4()
    t1 = t0 + timedelta(seconds=10)
    await _create_turn(database, session_id, user_turn_id, source_context=channel_context)
    user_event = UserTurnCommittedEvent(
        event_id=user_event_id,
        session_id=session_id,
        turn_id=user_turn_id,
        occurred_at=t1,
        source="runtime.external_channels",
        payload=UserTurnCommittedPayload(text="哈哈哈哈笑死我了，火星土豆太搞笑了"),
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, user_event)

    # Repository port verification
    evidence = await repository.get_preceding_presented_assistant(user_event_id)
    assert evidence is not None
    assert evidence.event_type == "channel.delivery_plan_completed"
    assert evidence.turn_id == originating_user_turn_id
    assert evidence.presented_text == "今天就吃火星土豆吧！"
    assert evidence.channel_attribution is not None
    assert evidence.channel_attribution.provider_id == "weixin_ilink"

    # MemoryService end-to-end
    model_response = json.dumps(
        {
            "memories": [
                {
                    "kind": "episodic.shared_event",
                    "subject_id": "user",
                    "predicate": "shared_joke.火星土豆",
                    "value": {
                        "cue": "火星土豆",
                        "context": "关于火星上种土豆的玩笑",
                        "kind": "shared_joke",
                    },
                    "text": "两人把'火星土豆'当作了一个共同的梗 (关于火星上种土豆的玩笑)。",
                    "confidence": 0.95,
                    "importance": 0.75,
                    "sensitivity": "private",
                    "rationale": "用户强烈笑声和呼应",
                }
            ]
        },
        ensure_ascii=False,
    )
    models = _MockExtractionModels(model_response)
    service = MemoryService(
        repository=repository,
        publisher=EventPublisher(event_store, EventHub()),
        models=cast(ModelConfigurationService, models),
    )
    proposals = await service.observe_user_turn(
        session_id=session_id,
        turn_id=user_turn_id,
        source_event_id=user_event_id,
        character_id="default-character",
        text="哈哈哈哈笑死我了，火星土豆太搞笑了",
    )
    assert len(proposals) == 1
    assert proposals[0].status == "accepted"

    records = await repository.list_records(kind="episodic.shared_event")
    assert len(records) == 1
    sources = await repository.list_sources(records[0].memory_id)
    assert len(sources) == 2
    source_map = {s.source_kind: s for s in sources}
    assert "user_turn" in source_map
    assert "assistant_delivered" in source_map
    assert source_map["assistant_delivered"].channel_attribution is not None
    assert source_map["assistant_delivered"].channel_attribution.provider_id == "weixin_ilink"


@pytest.mark.asyncio
async def test_external_delivery_requires_same_owner_direct_route(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    base_context = {
        "provider_id": "future_qq",
        "connection_id": str(uuid4()),
        "account_key": "owner-account",
        "chat_type": "direct",
        "conversation_key": "owner-direct",
        "sender_key": "owner-sender",
        "principal_scope": "local",
    }

    async def scenario(user_context: Mapping[str, object]) -> PrecedingAssistantEvidence | None:
        session_id = uuid4()
        originating_user_turn_id = uuid4()
        generation_id = uuid4()
        await _create_session(database, session_id)
        await _create_turn(
            database,
            session_id,
            originating_user_turn_id,
            source_context=base_context,
        )
        await _create_generation(
            database,
            session_id,
            originating_user_turn_id,
            generation_id,
            output_text="火星土豆又来了",
        )
        delivery = GenericCoreEvent.model_validate(
            {
                "event_id": uuid4(),
                "event_type": "channel.delivery_plan_completed",
                "session_id": session_id,
                "turn_id": originating_user_turn_id,
                "generation_id": generation_id,
                "occurred_at": datetime.now(UTC),
                "source": "runtime.external_channels",
                "payload": {},
            }
        )
        user_turn_id = uuid4()
        user_event_id = uuid4()
        await _create_turn(database, session_id, user_turn_id, source_context=user_context)
        user_event = UserTurnCommittedEvent(
            event_id=user_event_id,
            session_id=session_id,
            turn_id=user_turn_id,
            occurred_at=delivery.occurred_at + timedelta(seconds=1),
            source="runtime.external_channels",
            payload=UserTurnCommittedPayload(text="哈哈，火星土豆"),
        )
        async with database.transaction() as connection:
            await event_store.append_in_transaction(connection, delivery)
            await event_store.append_in_transaction(connection, user_event)
        return await repository.get_preceding_presented_assistant(user_event_id)

    # Provider-neutral evidence is accepted when the stable route is the same.
    assert await scenario(base_context) is not None
    assert await scenario({**base_context, "conversation_key": "another-chat"}) is None
    assert await scenario({**base_context, "chat_type": "group"}) is None
    assert await scenario({**base_context, "principal_scope": "someone-else"}) is None


@pytest.mark.asyncio
async def test_voice_evidence_does_not_cross_into_external_group_turn(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    spoken_turn_id = uuid4()
    await _create_session(database, session_id)
    await _create_turn(database, session_id, spoken_turn_id)
    occurred_at = datetime.now(UTC)
    spoken = AssistantSpokenTextCommittedEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=spoken_turn_id,
        generation_id=uuid4(),
        occurred_at=occurred_at,
        source="runtime.playback",
        payload=AssistantSpokenTextCommittedPayload(
            stream_id=uuid4(),
            segment_id=uuid4(),
            text="大白兔真可爱",
            spoken_text="大白兔真可爱",
        ),
    )
    group_context = {
        "provider_id": "future_group",
        "connection_id": str(uuid4()),
        "account_key": "group-account",
        "principal_scope": "someone-else",
        "chat_type": "group",
        "conversation_key": "group-1",
        "sender_key": "member-1",
    }
    group_turn_id = uuid4()
    group_event_id = uuid4()
    await _create_turn(database, session_id, group_turn_id, source_context=group_context)
    group_user = UserTurnCommittedEvent(
        event_id=group_event_id,
        session_id=session_id,
        turn_id=group_turn_id,
        occurred_at=occurred_at + timedelta(seconds=1),
        source="runtime.external_channels",
        payload=UserTurnCommittedPayload(text="这是我们的暗号:大白兔"),
    )
    async with database.transaction() as connection:
        await event_store.append_in_transaction(connection, spoken)
        await event_store.append_in_transaction(connection, group_user)

    assert await repository.get_preceding_presented_assistant(group_event_id) is None


@pytest.mark.asyncio
async def test_assistant_spoken_observation_drops_one_sided_shared_joke_candidate(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    turn_id = uuid4()
    await _create_session(database, session_id)
    await _create_turn(database, session_id, turn_id)
    spoken = AssistantSpokenTextCommittedEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=uuid4(),
        occurred_at=datetime.now(UTC),
        source="runtime.playback",
        payload=AssistantSpokenTextCommittedPayload(
            stream_id=uuid4(),
            segment_id=uuid4(),
            text="banana",
            spoken_text="banana",
        ),
    )
    async with database.transaction() as connection:
        await event_store.append_in_transaction(connection, spoken)
    models = _MockExtractionModels(
        json.dumps(
            {
                "memories": [
                    {
                        "kind": "episodic.shared_event",
                        "subject_id": "relationship",
                        "predicate": "shared_joke.banana",
                        "value": {"cue": "banana", "kind": "shared_joke"},
                        "text": "banana became a shared joke",
                        "confidence": 0.99,
                        "importance": 0.8,
                        "sensitivity": "private",
                        "rationale": "one-sided assistant joke",
                    }
                ]
            }
        )
    )
    service = MemoryService(
        repository=repository,
        publisher=EventPublisher(event_store, EventHub()),
        models=cast(ModelConfigurationService, models),
    )

    proposals = await service.observe_assistant_spoken(
        session_id, turn_id, spoken.event_id, "default-character", "banana"
    )

    assert proposals == []
    assert await repository.list_records(kind="episodic.shared_event") == []


@pytest.mark.asyncio
async def test_no_record_for_generated_but_undelivered_assistant(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    turn_id = uuid4()
    generation_id = uuid4()
    await _create_session(database, session_id)
    await _create_turn(database, session_id, turn_id)
    await _create_generation(
        database,
        session_id,
        turn_id,
        generation_id,
        output_text="未送达的笑话",
        state="running",
    )

    t0 = datetime.now(UTC)
    # Only assistant.generation_completed was emitted, NOT delivery_plan_completed or spoken_text
    gen_completed = GenericCoreEvent.model_validate(
        {
            "event_id": uuid4(),
            "event_type": "assistant.generation_completed",
            "session_id": session_id,
            "turn_id": turn_id,
            "generation_id": generation_id,
            "occurred_at": t0,
            "source": "runtime.conversation",
            "payload": {"text": "未送达的笑话"},
        }
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, gen_completed)

    user_turn_id = uuid4()
    user_event_id = uuid4()
    t1 = t0 + timedelta(seconds=2)
    await _create_turn(database, session_id, user_turn_id)
    user_event = UserTurnCommittedEvent(
        event_id=user_event_id,
        session_id=session_id,
        turn_id=user_turn_id,
        occurred_at=t1,
        source="runtime.conversation",
        payload=UserTurnCommittedPayload(text="哈哈笑死未送达的笑话"),
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, user_event)

    evidence = await repository.get_preceding_presented_assistant(user_event_id)
    assert evidence is None

    model_response = json.dumps(
        {
            "memories": [
                {
                    "kind": "episodic.shared_event",
                    "subject_id": "user",
                    "predicate": "shared_joke.未送达的笑话",
                    "value": {"cue": "未送达的笑话", "kind": "shared_joke"},
                    "text": "两人把'未送达的笑话'当作了梗。",
                    "confidence": 0.95,
                    "importance": 0.5,
                    "sensitivity": "private",
                    "rationale": "笑声",
                }
            ]
        },
        ensure_ascii=False,
    )
    models = _MockExtractionModels(model_response)
    service = MemoryService(
        repository=repository,
        publisher=EventPublisher(event_store, EventHub()),
        models=cast(ModelConfigurationService, models),
    )
    proposals = await service.observe_user_turn(
        session_id=session_id,
        turn_id=user_turn_id,
        source_event_id=user_event_id,
        character_id="default-character",
        text="哈哈笑死未送达的笑话",
    )
    # Fails closed because preceding assistant evidence does not exist
    assert len(proposals) == 0
    records = await repository.list_records(kind="episodic.shared_event")
    assert len(records) == 0


@pytest.mark.asyncio
async def test_no_record_for_one_sided_joke_or_unrelated_next_turn(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    turn_id = uuid4()
    generation_id = uuid4()
    await _create_session(database, session_id)
    await _create_turn(database, session_id, turn_id)
    await _create_generation(database, session_id, turn_id, generation_id, output_text="幽默回复")

    t0 = datetime.now(UTC)
    spoken_event = AssistantSpokenTextCommittedEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=generation_id,
        occurred_at=t0,
        source="runtime.playback",
        payload=AssistantSpokenTextCommittedPayload(
            stream_id=uuid4(),
            segment_id=uuid4(),
            text="幽默回复",
            spoken_text="幽默回复",
        ),
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, spoken_event)

    user_turn_id = uuid4()
    user_event_id = uuid4()
    t1 = t0 + timedelta(seconds=2)
    await _create_turn(database, session_id, user_turn_id)
    user_event = UserTurnCommittedEvent(
        event_id=user_event_id,
        session_id=session_id,
        turn_id=user_turn_id,
        occurred_at=t1,
        source="runtime.conversation",
        payload=UserTurnCommittedPayload(text="明天几点下雨？帮我查一下"),
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, user_event)

    models = _MockExtractionModels(
        json.dumps(
            {
                "memories": [
                    {
                        "kind": "episodic.shared_event",
                        "subject_id": "user",
                        "predicate": "shared_joke.幽默回复",
                        "value": {"cue": "幽默回复", "kind": "shared_joke"},
                        "text": "两人把'幽默回复'当成了梗",
                        "confidence": 0.95,
                        "importance": 0.5,
                        "sensitivity": "private",
                        "rationale": "试探",
                    }
                ]
            }
        )
    )
    service = MemoryService(
        repository=repository,
        publisher=EventPublisher(event_store, EventHub()),
        models=cast(ModelConfigurationService, models),
    )
    proposals = await service.observe_user_turn(
        session_id=session_id,
        turn_id=user_turn_id,
        source_event_id=user_event_id,
        character_id="default-character",
        text="明天几点下雨？帮我查一下",
    )
    # User had no uptake marker -> fails closed
    assert len(proposals) == 0
    inference_payload = json.loads(models.calls[0]["user"])
    assert "preceding_presented_assistant_text" not in inference_payload


@pytest.mark.asyncio
async def test_reject_ungrounded_cue_malformed_sensitive_stale_or_intervening(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    turn_id = uuid4()
    generation_id = uuid4()
    await _create_session(database, session_id)
    await _create_turn(database, session_id, turn_id)
    await _create_generation(
        database,
        session_id,
        turn_id,
        generation_id,
        output_text="今天天气真好，阳光很明媚",
    )

    t0 = datetime.now(UTC) - timedelta(minutes=40)  # Stale: > 30 minutes
    stale_event = AssistantSpokenTextCommittedEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=generation_id,
        occurred_at=t0,
        source="runtime.playback",
        payload=AssistantSpokenTextCommittedPayload(
            stream_id=uuid4(),
            segment_id=uuid4(),
            text="今天天气真好，阳光很明媚",
            spoken_text="今天天气真好，阳光很明媚",
        ),
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, stale_event)

    user_event_id = uuid4()
    user_turn_id = uuid4()
    now = datetime.now(UTC)
    await _create_turn(database, session_id, user_turn_id)
    user_event = UserTurnCommittedEvent(
        event_id=user_event_id,
        session_id=session_id,
        turn_id=user_turn_id,
        occurred_at=now,
        source="runtime.conversation",
        payload=UserTurnCommittedPayload(text="哈哈太搞笑了阳光很明媚"),
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, user_event)

    # 1. Stale exchange rejected
    evidence = await repository.get_preceding_presented_assistant(user_event_id)
    assert evidence is None

    # 2. Intervening user turn rejected
    t_fresh = datetime.now(UTC)
    fresh_asst_turn = uuid4()
    fresh_gen = uuid4()
    await _create_turn(database, session_id, fresh_asst_turn)
    await _create_generation(
        database,
        session_id,
        fresh_asst_turn,
        fresh_gen,
        output_text="这是新鲜的笑话",
    )
    fresh_asst_event = AssistantSpokenTextCommittedEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=fresh_asst_turn,
        generation_id=fresh_gen,
        occurred_at=t_fresh,
        source="runtime.playback",
        payload=AssistantSpokenTextCommittedPayload(
            stream_id=uuid4(),
            segment_id=uuid4(),
            text="这是新鲜的笑话",
            spoken_text="这是新鲜的笑话",
        ),
    )
    intervening_turn_id = uuid4()
    intervening_event_id = uuid4()
    intervening_event = UserTurnCommittedEvent(
        event_id=intervening_event_id,
        session_id=session_id,
        turn_id=intervening_turn_id,
        occurred_at=t_fresh + timedelta(seconds=2),
        source="runtime.conversation",
        payload=UserTurnCommittedPayload(text="先不管这个"),
    )
    third_turn_id = uuid4()
    third_event_id = uuid4()
    third_event = UserTurnCommittedEvent(
        event_id=third_event_id,
        session_id=session_id,
        turn_id=third_turn_id,
        occurred_at=t_fresh + timedelta(seconds=5),
        source="runtime.conversation",
        payload=UserTurnCommittedPayload(text="哈哈新鲜的笑话"),
    )
    async with database.transaction() as conn:
        await event_store.append_in_transaction(conn, fresh_asst_event)
        await event_store.append_in_transaction(conn, intervening_event)
        await event_store.append_in_transaction(conn, third_event)

    # Intervening user turn breaks immediate adjacency
    intervened = await repository.get_preceding_presented_assistant(third_event_id)
    assert intervened is None

    # 3. Ungrounded cue helper check
    assert not is_cue_grounded("不存在的词", "哈哈笑死", "今天吃烤鸭")
    assert is_cue_grounded("烤鸭", "哈哈笑死", "今天吃烤鸭")

    # 4. Sensitive content rejection
    mock_evidence = PrecedingAssistantEvidence(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=fresh_asst_turn,
        generation_id=fresh_gen,
        event_type="assistant.spoken_text_committed",
        presented_text="这是你的密码123456",
        occurred_at=datetime.now(UTC),
    )
    candidate_draft = MemoryRecordDraft(
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        predicate="shared_joke.密码",
        value={"cue": "密码", "kind": "shared_joke"},
        text="两人把密码当成梗",
        observed_at=datetime.now(UTC),
        confidence=0.95,
        importance=0.5,
    )
    candidate = ExtractedMemoryCandidate(draft=candidate_draft, explicit=False, rationale="")
    result = validate_and_transform_shared_joke(
        candidate,
        user_text="哈哈太逗了密码123456",
        preceding_assistant=mock_evidence,
        source_event_id=uuid4(),
    )
    assert result is None  # Sensitive content failed closed!


def test_uptake_classification_and_confidence_thresholds() -> None:
    # Explicit declaration markers: threshold >= 0.80
    kind, thresh = classify_uptake("以后这就是我们的梗了")
    assert kind == "explicit"
    assert thresh == 0.80

    kind, thresh = classify_uptake("这是暗号:天王盖地虎")
    assert kind == "explicit"
    assert thresh == 0.80

    kind, thresh = classify_uptake("this is our inside joke")
    assert kind == "explicit"
    assert thresh == 0.80

    # Implicit laughter / callback markers: threshold >= 0.90
    kind, thresh = classify_uptake("哈哈哈哈太好笑了")
    assert kind == "implicit"
    assert thresh == 0.90

    kind, thresh = classify_uptake("笑死我了，太逗了")
    assert kind == "implicit"
    assert thresh == 0.90

    kind, thresh = classify_uptake("lmao that cracked me up")
    assert kind == "implicit"
    assert thresh == 0.90

    # Unrelated input
    kind, thresh = classify_uptake("今天吃牛肉面")
    assert kind is None
    assert thresh == 1.0

    for rejected in (
        "别把这个当梗",
        "这不是我们的梗",
        "不要记这个暗号",
        "我忘了我们的梗",
        "我不记得我们的暗号",
        "please do not treat this as our inside joke",
        "forget this shared joke",
        "这个梗是什么意思？",
        "我们的暗号是什么？",
        "what is our inside joke?",
        "这是我们的梗？",
        "这是暗号？",
        "This is an inside joke?",
        "Our inside joke?",
        "This isn't an inside joke",
        "There is no inside joke",
        "It wasn't an inside joke",
        "That was not hilarious at all",
        "This is not too funny",
        "太好笑了？",
        "Is this hilarious?",
        "I bought a lollipop",
    ):
        kind, thresh = classify_uptake(rejected)
        assert kind is None
        assert thresh == 1.0


def test_shared_joke_validation_uses_only_grounded_factual_shape() -> None:
    evidence = PrecedingAssistantEvidence(
        event_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        event_type="assistant.spoken_text_committed",
        presented_text="今天就吃火星土豆吧！",
        occurred_at=datetime.now(UTC),
    )
    draft = MemoryRecordDraft(
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        subject_id="user",
        predicate="shared_joke.火星土豆",
        value={
            "cue": "火星土豆",
            "context": "模型声称两人真的去过火星",
            "kind": "shared_joke",
        },
        text="两人真的去过火星，并把火星土豆当成了梗。",
        observed_at=datetime.now(UTC),
        confidence=0.95,
        importance=0.7,
    )
    candidate = ExtractedMemoryCandidate(draft=draft, explicit=False, rationale="model")

    validated = validate_and_transform_shared_joke(
        candidate,
        user_text="哈哈，火星土豆以后就是我们的梗",
        preceding_assistant=evidence,
        source_event_id=uuid4(),
    )

    assert validated is not None
    assert validated.draft.subject_id == "relationship"
    assert validated.draft.text == "用户将“火星土豆”认作了双方的共同梗或暗号。"
    assert validated.draft.value == {
        "cue": "火星土豆",
        "context": "今天就吃火星土豆吧！",
        "kind": "shared_joke",
    }
    assert "去过火星" not in validated.draft.text


@pytest.mark.parametrize(
    ("cue", "assistant_text"),
    (
        ("tan", "cat and dog"),
        ("hot", "who told you"),
        ("sin", "this information"),
        ("her", "another joke"),
    ),
)
def test_grounding_rejects_phantom_or_subword_ascii_cues(cue: str, assistant_text: str) -> None:
    assert not is_cue_grounded(cue, "哈哈", assistant_text)


def test_non_joke_cue_and_untrusted_auto_commit_remain_review_only() -> None:
    ordinary = MemoryRecordDraft(
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        subject_id="user",
        predicate=None,
        value={"cue": "python", "initiated_by": "user"},
        text="用户主动选择过 python 作为聊天话题",
        observed_at=datetime.now(UTC),
        confidence=0.95,
        importance=0.55,
    )
    candidate = ExtractedMemoryCandidate(
        draft=ordinary,
        explicit=False,
        rationale="ordinary episode",
        auto_commit=True,
    )

    assert not is_shared_joke_draft(ordinary)
    assert MemoryPolicy().decide_write(candidate) is MemoryWriteDecision.REVIEW


@pytest.mark.parametrize(
    "value",
    (
        {"cue": ["火星土豆"], "kind": "shared_joke"},
        {"cue": "火星土豆", "context": 42, "kind": "shared_joke"},
        {"cue": "火星土豆", "context": "太" * 241, "kind": "shared_joke"},
    ),
)
def test_shared_joke_rejects_malformed_structured_value(value: JsonValue) -> None:
    evidence = PrecedingAssistantEvidence(
        event_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        event_type="assistant.spoken_text_committed",
        presented_text="火星土豆",
        occurred_at=datetime.now(UTC),
    )
    draft = MemoryRecordDraft(
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        predicate="shared_joke.火星土豆",
        value=value,
        text="共同梗",
        observed_at=datetime.now(UTC),
        confidence=0.95,
        importance=0.7,
    )
    candidate = ExtractedMemoryCandidate(draft=draft, explicit=False, rationale="model")
    assert (
        validate_and_transform_shared_joke(
            candidate,
            user_text="哈哈，火星土豆就是我们的梗",
            preceding_assistant=evidence,
            source_event_id=uuid4(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_two_different_jokes_coexist(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    await _create_session(database, session_id)
    e1 = await _create_event(database, session_id)
    e2 = await _create_event(database, session_id)
    now = datetime.now(UTC)

    # Insert joke 1
    r1 = MemoryRecord(
        memory_id=uuid4(),
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        subject_id="user",
        predicate="shared_joke.火星土豆",
        value={"cue": "火星土豆", "kind": "shared_joke"},
        text="两人把'火星土豆'当作了一个共同的梗。",
        source_event_ids=[e1],
        observed_at=now,
        confidence=0.95,
        importance=0.7,
        sensitivity=PrivacyLevel.PRIVATE,
        state="active",
        pinned=False,
        created_at=now,
        updated_at=now,
    )
    await repository.create_record(r1, [_make_source(r1.memory_id, e1, session_id)])

    # Insert joke 2
    r2 = MemoryRecord(
        memory_id=uuid4(),
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        subject_id="user",
        predicate="shared_joke.芝麻开门",
        value={"cue": "芝麻开门", "kind": "shared_joke"},
        text="两人把'芝麻开门'当作了一个共同的梗。",
        source_event_ids=[e2],
        observed_at=now,
        confidence=0.92,
        importance=0.7,
        sensitivity=PrivacyLevel.PRIVATE,
        state="active",
        pinned=False,
        created_at=now,
        updated_at=now,
    )
    await repository.create_record(r2, [_make_source(r2.memory_id, e2, session_id)])

    active = await repository.list_records(
        namespace="character/default/user/local", kind="episodic.shared_event"
    )
    assert len(active) == 2
    predicates = {r.predicate for r in active}
    assert predicates == {"shared_joke.火星土豆", "shared_joke.芝麻开门"}


@pytest.mark.asyncio
async def test_repeated_same_cue_dedupes_not_supersede(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    await _create_session(database, session_id)
    e1 = await _create_event(database, session_id)
    now = datetime.now(UTC)

    r1 = MemoryRecord(
        memory_id=uuid4(),
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        subject_id="user",
        predicate="shared_joke.火星土豆",
        value={"cue": "火星土豆", "kind": "shared_joke"},
        text="两人把'火星土豆'当作了一个共同的梗。",
        source_event_ids=[e1],
        observed_at=now,
        confidence=0.95,
        importance=0.7,
        sensitivity=PrivacyLevel.PRIVATE,
        state="active",
        pinned=False,
        created_at=now,
        updated_at=now,
    )
    await repository.create_record(r1, [_make_source(r1.memory_id, e1, session_id)])

    service = MemoryService(
        repository=repository,
        publisher=EventPublisher(event_store, EventHub()),
        models=cast(ModelConfigurationService, _MockExtractionModels()),
    )

    # Simulate proposal with same cue arriving
    draft = MemoryRecordDraft(
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        subject_id="user",
        predicate="shared_joke.火星土豆",
        value={"cue": "火星土豆", "kind": "shared_joke"},
        text="两人把'火星土豆'当作了一个共同的梗 (再次提起)。",
        observed_at=now,
        confidence=0.95,
        importance=0.7,
        sensitivity=PrivacyLevel.PRIVATE,
    )
    candidate = ExtractedMemoryCandidate(
        draft=draft,
        explicit=False,
        rationale="重复成梗",
        evidence_event_ids=(uuid4(), uuid4()),
        auto_commit=True,
    )

    proposal = await service._process_candidate(  # pyright: ignore[reportPrivateUsage]
        session_id, None, uuid4(), candidate
    )
    assert proposal.operation == "ignore"
    assert proposal.status == "ignored"
    assert proposal.target_memory_id == r1.memory_id
    assert proposal.rationale == "duplicate active shared joke"

    # Verify original record is unchanged and no second record exists
    records = await repository.list_records(
        namespace="character/default/user/local", kind="episodic.shared_event"
    )
    assert len(records) == 1
    assert records[0].memory_id == r1.memory_id
    assert records[0].supersedes is None


@pytest.mark.asyncio
async def test_forget_and_tombstone_subsequent_recall(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    await _create_session(database, session_id)
    e = await _create_event(database, session_id)
    now = datetime.now(UTC)

    memory_id = uuid4()
    r = MemoryRecord(
        memory_id=memory_id,
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        subject_id="user",
        predicate="shared_joke.火星土豆",
        value={"cue": "火星土豆", "kind": "shared_joke"},
        text="两人把'火星土豆'当作了一个共同的梗。",
        source_event_ids=[e],
        observed_at=now,
        confidence=0.95,
        importance=0.7,
        sensitivity=PrivacyLevel.PRIVATE,
        state="active",
        pinned=False,
        created_at=now,
        updated_at=now,
    )
    await repository.create_record(r, [_make_source(r.memory_id, e, session_id)])

    service = MemoryService(
        repository=repository,
        publisher=EventPublisher(event_store, EventHub()),
        models=cast(ModelConfigurationService, _MockExtractionModels()),
    )

    # Forget the record
    forgotten = await service.forget(session_id, memory_id)
    assert forgotten is True

    # Check that active listing does not return it
    active = await repository.list_records(
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        include_tombstoned=False,
    )
    assert len(active) == 0

    # But tombstoned listing returns it
    all_recs = await repository.list_records(
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        include_tombstoned=True,
    )
    assert len(all_recs) == 1
    assert all_recs[0].state == "tombstoned"

    # Subsequent same-cue formation is now allowed
    identities = await repository.find_identity(
        "character/default/user/local", "user", "shared_joke.火星土豆"
    )
    assert len(identities) == 0  # No active identity matches


@pytest.mark.asyncio
async def test_restart_persistence_and_namespace_isolation(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    db1 = Database(db_path, StorageConfig(database_path=db_path))
    await db1.open()
    repo1 = SQLiteMemoryRepository(db1)

    session_id = uuid4()
    await _create_session(db1, session_id)
    e = await _create_event(db1, session_id)
    now = datetime.now(UTC)
    memory_id = uuid4()
    r = MemoryRecord(
        memory_id=memory_id,
        namespace="character/default/user/local",
        kind="episodic.shared_event",
        subject_id="user",
        predicate="shared_joke.火星土豆",
        value={"cue": "火星土豆", "kind": "shared_joke"},
        text="两人把'火星土豆'当作了一个共同的梗。",
        source_event_ids=[e],
        observed_at=now,
        confidence=0.95,
        importance=0.7,
        sensitivity=PrivacyLevel.PRIVATE,
        state="active",
        pinned=False,
        created_at=now,
        updated_at=now,
    )
    await repo1.create_record(r, [_make_source(r.memory_id, e, session_id)])
    await db1.close()

    # Reopen DB as if restarted
    db2 = Database(db_path, StorageConfig(database_path=db_path))
    await db2.open()
    repo2 = SQLiteMemoryRepository(db2)

    # Active record persisted across restart
    reopened = await repo2.get(memory_id)
    assert reopened is not None
    assert reopened.predicate == "shared_joke.火星土豆"

    # Namespace isolation: another character namespace sees nothing
    other_ns = await repo2.list_records(
        namespace="character/other-character/user/local",
        kind="episodic.shared_event",
    )
    assert len(other_ns) == 0

    await db2.close()


@pytest.mark.asyncio
async def test_prompt_compiler_guidance_and_budget() -> None:
    models = _MockExtractionModels()
    compiler = PromptCompiler(cast(ModelConfigurationService, models))

    now = datetime.now(UTC)
    packet = MemoryContextPacket(
        pinned_facts=[],
        recent_episodes=[
            MemoryExcerpt(
                memory_id=uuid4(),
                text="两人把'火星土豆'当作了一个共同的梗 (关于火星种土豆的玩笑)。",
                source_event_ids=[uuid4()],
                relevance=0.95,
            )
        ],
        relevant_memories=[],
        open_commitments=[],
        relationship_context=[],
        provenance_ids=[],
        token_budget_used=50,
    )

    characters = CharacterService(CHARACTERS_ROOT)
    characters.start()
    profile = characters.get("default")
    assert profile is not None
    snapshot = CharacterKernelSnapshot(
        character_id="default",
        user_scope="local",
        revision=1,
        affect=AffectState(updated_at=now),
        relationship=RelationshipState(updated_at=now),
    )
    plan = ResponsePlan(
        intent="tease",
        tone="playful",
        expression="happy",
        rationale="Shared joke recall",
    )

    compilation = await compiler.compile(
        character=profile,
        kernel=snapshot,
        plan=plan,
        memory=packet,
        history=(),
        user_text="今天的火星土豆好吃吗？",
    )

    assert compilation.context
    context_texts = [msg[1] for msg in compilation.context if msg[0] == "system"]
    combined_system = "\n".join([compilation.system_prompt, *context_texts])

    # Memory text is injected with [episode] label
    assert "火星土豆" in combined_system
    # Guidance is present
    assert "shared joke" in combined_system
    assert "切勿机械解释" in combined_system
    assert "两人把'火星土豆'当作了一个共同的梗" in compilation.recalled_memory_texts[0]


@pytest.mark.asyncio
async def test_projection_cancellation_and_reset(
    database: Database, event_store: EventStore, repository: SQLiteMemoryRepository
) -> None:
    session_id = uuid4()
    await _create_session(database, session_id)
    service = MemoryService(
        repository=repository,
        publisher=EventPublisher(event_store, EventHub()),
        models=cast(ModelConfigurationService, _MockExtractionModels()),
    )
    await service.start()

    # Enqueue a projection
    obs = UserTurnMemoryObservation(
        session_id=session_id,
        turn_id=uuid4(),
        source_event_id=uuid4(),
        character_id="default-character",
        text="普通消息",
        user_scope="local",
    )
    enqueued = await service.enqueue_user_turn(obs)
    assert enqueued is True

    # Invalidate scope before processing finishes
    await service.prepare_scope_reset("default-character", "local")

    # Stop worker cleanly
    await service.stop()
    records = await repository.list_records(kind="episodic.shared_event")
    assert len(records) == 0
