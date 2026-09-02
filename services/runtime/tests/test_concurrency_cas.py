"""Tests for State Machine CAS and Concurrency Invariants (Phase 12.5 Track B)."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.memory import MemoryProposal, MemoryRecord, MemoryRecordDraft, MemorySource
from chatwaifu_protocol.session import GenerationState, SessionState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_conversation import SQLiteConversationRepository
from chatwaifu_runtime.persistence.sqlite_memory_repository import SQLiteMemoryRepository
from chatwaifu_runtime.sessions.service import InvalidSessionTransition


@pytest.mark.asyncio
async def test_generation_terminal_transitions_cas(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    database = Database(db_path, StorageConfig(database_path=db_path))
    await database.open()
    event_store = EventStore(database)
    repo = SQLiteConversationRepository(database, event_store)

    session_id = uuid4()
    turn_id = uuid4()
    gen_id = uuid4()
    now = datetime.now(UTC)

    # Setup session and turn
    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'default', 'active', 'idle', 0, 1, ?, ?)
            """,
            (str(session_id), now.isoformat(), now.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO turns(turn_id, session_id, role, created_at)
            VALUES (?, ?, 'user', ?)
            """,
            (str(turn_id), str(session_id), now.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind, started_at
            ) VALUES (?, ?, ?, 'running', 'demo', ?)
            """,
            (str(gen_id), str(session_id), str(turn_id), now.isoformat()),
        )

    # 1. Complete generation succeeds
    completed = await repo.complete_generation(
        session_id=session_id,
        generation_id=gen_id,
        assistant_turn_id=uuid4(),
        output="Hello world",
        source_context=None,
        occurred_at=now,
        set_session_idle=True,
    )
    assert completed is True

    # 2. Subsequent fail_generation cannot overwrite completed state
    failed = await repo.fail_generation(
        session_id=session_id,
        generation_id=gen_id,
        error_code="SOME_ERROR",
        occurred_at=now,
        set_session_idle=True,
    )
    assert failed is False

    # 3. Subsequent cancel_generation cannot overwrite completed state
    cancelled = await repo.cancel_generation(
        session_id=session_id,
        generation_id=gen_id,
        occurred_at=now,
        set_session_idle=True,
    )
    assert cancelled is False

    # Verify state in DB remains 'completed'
    async with database.transaction() as conn:
        cursor = await conn.execute(
            "SELECT state, output_text, error_code FROM generations WHERE generation_id = ?",
            (str(gen_id),),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == GenerationState.COMPLETED.value
        assert row[1] == "Hello world"
        assert row[2] is None
        await cursor.close()

    await database.close()


@pytest.mark.asyncio
async def test_memory_proposal_atomic_acceptance_and_deduplication(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    database = Database(db_path, StorageConfig(database_path=db_path))
    await database.open()
    repo = SQLiteMemoryRepository(database)

    session_id = uuid4()
    proposal_id = uuid4()
    now = datetime.now(UTC)

    source_event_id = uuid4()
    # Setup session and event
    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'default', 'active', 'idle', 0, 1, ?, ?)
            """,
            (str(session_id), now.isoformat(), now.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO events (
                event_id, session_id, sequence, schema_version,
                event_type, source, occurred_at, payload_json, envelope_json
            ) VALUES (?, ?, 1, '1.0', 'user.turn_committed', 'test', ?, '{}', '{}')
            """,
            (str(source_event_id), str(session_id), now.isoformat()),
        )

    draft = MemoryRecordDraft(
        namespace="character/default/user/local",
        kind="semantic.preference",
        subject_id="user",
        predicate="like.blue",
        value=True,
        text="User likes blue",
        confidence=0.95,
        importance=0.8,
        sensitivity=PrivacyLevel.PRIVATE,
        observed_at=now,
    )
    proposal = MemoryProposal(
        proposal_id=proposal_id,
        operation="add",
        candidate=draft,
        evidence_event_ids=[source_event_id],
        confidence=0.95,
        rationale="User statement",
        status="pending",
        created_at=now,
    )
    await repo.save_proposal(proposal)

    record = MemoryRecord(
        memory_id=uuid4(),
        namespace="character/default/user/local",
        kind="semantic.preference",
        subject_id="user",
        predicate="like.blue",
        value=True,
        text="User likes blue",
        observed_at=now,
        source_event_ids=[source_event_id],
        confidence=0.95,
        importance=0.8,
        sensitivity=PrivacyLevel.PRIVATE,
        state="active",
        created_at=now,
        updated_at=now,
        origin_proposal_id=proposal_id,
    )
    source = MemorySource(
        source_id=uuid4(),
        memory_id=record.memory_id,
        source_event_id=source_event_id,
        session_id=session_id,
        turn_id=None,
        source_kind="user_turn",
        created_at=now,
    )

    # First accept succeeds
    accepted1 = await repo.accept_proposal_atomically(
        proposal_id=proposal_id,
        record=record,
        sources=[source],
        decided_at=now,
    )
    assert accepted1 is True

    # Second accept attempt fails atomically
    record2 = record.model_copy(update={"memory_id": uuid4()})
    accepted2 = await repo.accept_proposal_atomically(
        proposal_id=proposal_id,
        record=record2,
        sources=[source],
        decided_at=now,
    )
    assert accepted2 is False

    # Proposal state is accepted
    prop = await repo.get_proposal(proposal_id)
    assert prop is not None
    assert prop.status == "accepted"

    await database.close()


@pytest.mark.asyncio
async def test_concurrent_session_transitions_cas_rejection(runtime_settings: Settings) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        assert session.revision == 0

        # Concurrent transition with revision 0
        t1 = container.sessions.transition_session(
            session.session_id, SessionState.DEGRADED, expected_revision=0
        )
        t2 = container.sessions.transition_session(
            session.session_id, SessionState.CLOSED, expected_revision=0
        )

        results = await asyncio.gather(t1, t2, return_exceptions=True)
        # Exactly one must succeed, one must fail with InvalidSessionTransition
        successes = [r for r in results if not isinstance(r, Exception)]
        exceptions = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 1
        assert len(exceptions) == 1
        assert isinstance(exceptions[0], InvalidSessionTransition)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_generation_cas_failure_has_no_side_effects(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    database = Database(db_path, StorageConfig(database_path=db_path))
    await database.open()
    event_store = EventStore(database)
    repo = SQLiteConversationRepository(database, event_store)

    session_id = uuid4()
    turn_id = uuid4()
    gen_id = uuid4()
    assistant_turn_id = uuid4()
    now = datetime.now(UTC)

    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'default', 'active', 'idle', 0, 1, ?, ?)
            """,
            (str(session_id), now.isoformat(), now.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO turns(turn_id, session_id, role, created_at)
            VALUES (?, ?, 'user', ?)
            """,
            (str(turn_id), str(session_id), now.isoformat()),
        )
        await conn.execute(
            """
            INSERT INTO generations (
                generation_id, session_id, turn_id, state, backend_kind, started_at
            ) VALUES (?, ?, ?, 'running', 'demo', ?)
            """,
            (str(gen_id), str(session_id), str(turn_id), now.isoformat()),
        )

    # 1. First complete succeeds
    completed = await repo.complete_generation(
        session_id=session_id,
        generation_id=gen_id,
        assistant_turn_id=assistant_turn_id,
        output="First reply",
        source_context=None,
        occurred_at=now,
        set_session_idle=True,
    )
    assert completed is True

    # 2. Duplicate complete fails CAS
    second_assistant_turn_id = uuid4()
    second_completed = await repo.complete_generation(
        session_id=session_id,
        generation_id=gen_id,
        assistant_turn_id=second_assistant_turn_id,
        output="Conflicting second reply",
        source_context=None,
        occurred_at=now,
        set_session_idle=True,
    )
    assert second_completed is False

    # 3. Duplicate fail fails CAS
    second_failed = await repo.fail_generation(
        session_id=session_id,
        generation_id=gen_id,
        error_code="PROVIDER_ERROR",
        occurred_at=now,
        set_session_idle=True,
    )
    assert second_failed is False

    # 4. Duplicate cancel fails CAS
    second_cancelled = await repo.cancel_generation(
        session_id=session_id,
        generation_id=gen_id,
        occurred_at=now,
        set_session_idle=True,
    )
    assert second_cancelled is False

    # Assert invariant: only 1 assistant turn exists with first output
    async with database.transaction() as conn:
        cursor = await conn.execute(
            """
            SELECT turn_id, role, committed_text FROM turns
            WHERE session_id = ? AND role = 'assistant'
            """,
            (str(session_id),),
        )
        turns = list(await cursor.fetchall())
        assert len(turns) == 1
        assert turns[0][0] == str(assistant_turn_id)
        assert turns[0][2] == "First reply"
        await cursor.close()

        # Assert generation record remains strictly COMPLETED with first output
        cursor = await conn.execute(
            """
            SELECT state, output_text, error_code, invalidated_at
            FROM generations WHERE generation_id = ?
            """,
            (str(gen_id),),
        )
        gen = await cursor.fetchone()
        assert gen is not None
        assert gen[0] == GenerationState.COMPLETED.value
        assert gen[1] == "First reply"
        assert gen[2] is None
        assert gen[3] is None
        await cursor.close()

    await database.close()
