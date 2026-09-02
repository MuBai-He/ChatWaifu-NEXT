"""Tests for State Machine CAS and Concurrency Invariants (Phase 12.5 Track B)."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import (
    ErrorRaisedEvent,
    ErrorRaisedPayload,
    GenericCoreEvent,
)
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

    complete_event = GenericCoreEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        event_type="assistant.generation_completed",
        payload={"text": "Hello world"},
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
        complete_event=complete_event,
    )
    assert completed is not None
    _pre_evs, complete_ev = completed
    assert complete_ev.event_type == "assistant.generation_completed"

    fail_event = ErrorRaisedEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        payload=ErrorRaisedPayload(
            error=StructuredError(
                code="SOME_ERROR",
                message="some error",
                retryable=True,
                component="conversation",
            )
        ),
    )

    # 2. Subsequent fail_generation cannot overwrite completed state
    failed = await repo.fail_generation(
        session_id=session_id,
        generation_id=gen_id,
        error_code="SOME_ERROR",
        occurred_at=now,
        set_session_idle=True,
        fail_event=fail_event,
    )
    assert failed is None

    cancel_event = GenericCoreEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        event_type="assistant.generation_cancelled",
        payload={"reason": "test"},
    )

    # 3. Subsequent cancel_generation cannot overwrite completed state
    cancelled = await repo.cancel_generation(
        session_id=session_id,
        generation_id=gen_id,
        occurred_at=now,
        set_session_idle=True,
        cancel_events=(cancel_event,),
    )
    assert cancelled is None

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
        namespace="character",
        kind="semantic.fact",
        subject_id="user",
        predicate="likes",
        value={"activity": "reading"},
        text="用户喜欢阅读",
        observed_at=now,
        confidence=0.9,
        importance=0.7,
        sensitivity=PrivacyLevel.LOCAL,
    )
    proposal = MemoryProposal(
        proposal_id=proposal_id,
        operation="add",
        candidate=draft,
        target_memory_id=None,
        evidence_event_ids=[source_event_id],
        confidence=0.9,
        rationale="test",
        status="pending",
        created_at=now,
    )
    await repo.save_proposal(proposal)

    # 1. First accept succeeds
    record = MemoryRecord(
        **draft.model_dump(),
        memory_id=uuid4(),
        source_event_ids=[source_event_id],
        origin_proposal_id=proposal_id,
        valid_from=now,
        state="active",
        supersedes=None,
        pinned=False,
        created_at=now,
        updated_at=now,
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

    accepted = await repo.accept_proposal_atomically(
        proposal_id=proposal_id,
        decided_at=now,
        record=record,
        sources=[source],
    )
    assert accepted is True

    # 2. Duplicate accept returns False (idempotent / CAS rejection on proposal state)
    second_record = MemoryRecord(
        **draft.model_dump(),
        memory_id=uuid4(),
        source_event_ids=[source_event_id],
        origin_proposal_id=proposal_id,
        valid_from=now,
        state="active",
        supersedes=None,
        pinned=False,
        created_at=now,
        updated_at=now,
    )
    second_accepted = await repo.accept_proposal_atomically(
        proposal_id=proposal_id,
        decided_at=now,
        record=second_record,
        sources=[source],
    )
    assert second_accepted is False

    # 3. Direct insert with same origin_proposal_id fails unique constraint
    with pytest.raises(sqlite3.IntegrityError):
        await repo.create_record(second_record, sources=[source])

    await database.close()


@pytest.mark.asyncio
async def test_concurrent_session_transitions_cas_rejection(runtime_settings: Settings) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        assert session.revision == 0

        # Concurrent transition with revision 0 (READY -> DEGRADED and READY -> CLOSING are valid)
        t1 = container.sessions.transition_session(
            session.session_id, SessionState.DEGRADED, expected_revision=0
        )
        t2 = container.sessions.transition_session(
            session.session_id, SessionState.CLOSING, expected_revision=0
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

    complete_event_1 = GenericCoreEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        event_type="assistant.generation_completed",
        payload={"text": "First reply"},
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
        complete_event=complete_event_1,
    )
    assert completed is not None

    # 2. Duplicate complete fails CAS
    second_assistant_turn_id = uuid4()
    complete_event_2 = GenericCoreEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        event_type="assistant.generation_completed",
        payload={"text": "Conflicting second reply"},
    )
    second_completed = await repo.complete_generation(
        session_id=session_id,
        generation_id=gen_id,
        assistant_turn_id=second_assistant_turn_id,
        output="Conflicting second reply",
        source_context=None,
        occurred_at=now,
        set_session_idle=True,
        complete_event=complete_event_2,
    )
    assert second_completed is None

    # 3. Duplicate fail fails CAS
    fail_event = ErrorRaisedEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        payload=ErrorRaisedPayload(
            error=StructuredError(
                code="PROVIDER_ERROR",
                message="error",
                retryable=True,
                component="conversation",
            )
        ),
    )
    second_failed = await repo.fail_generation(
        session_id=session_id,
        generation_id=gen_id,
        error_code="PROVIDER_ERROR",
        occurred_at=now,
        set_session_idle=True,
        fail_event=fail_event,
    )
    assert second_failed is None

    # 4. Duplicate cancel fails CAS
    cancel_event = GenericCoreEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        event_type="assistant.generation_cancelled",
        payload={"reason": "test"},
    )
    second_cancelled = await repo.cancel_generation(
        session_id=session_id,
        generation_id=gen_id,
        occurred_at=now,
        set_session_idle=True,
        cancel_events=(cancel_event,),
    )
    assert second_cancelled is None

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


@pytest.mark.asyncio
async def test_concurrent_generation_cas_race(tmp_path: Path) -> None:
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

    complete_ev = GenericCoreEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        event_type="assistant.generation_completed",
        payload={"text": "Winner"},
    )
    fail_ev = ErrorRaisedEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        payload=ErrorRaisedPayload(
            error=StructuredError(
                code="RACE_ERROR",
                message="race failure",
                retryable=False,
                component="conversation",
            )
        ),
    )
    cancel_ev = GenericCoreEvent(
        event_id=uuid4(),
        session_id=session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        source="runtime.conversation",
        privacy=PrivacyLevel.LOCAL,
        event_type="assistant.generation_cancelled",
        payload={"reason": "race cancel"},
    )

    # Concurrently execute complete, fail, and cancel
    t_complete = repo.complete_generation(
        session_id=session_id,
        generation_id=gen_id,
        assistant_turn_id=assistant_turn_id,
        output="Winner",
        source_context=None,
        occurred_at=now,
        set_session_idle=True,
        complete_event=complete_ev,
    )
    t_fail = repo.fail_generation(
        session_id=session_id,
        generation_id=gen_id,
        error_code="RACE_ERROR",
        occurred_at=now,
        set_session_idle=True,
        fail_event=fail_ev,
    )
    t_cancel = repo.cancel_generation(
        session_id=session_id,
        generation_id=gen_id,
        occurred_at=now,
        set_session_idle=True,
        cancel_events=(cancel_ev,),
    )

    results = await asyncio.gather(t_complete, t_fail, t_cancel, return_exceptions=True)

    non_none_results = [r for r in results if r is not None and not isinstance(r, Exception)]
    assert len(non_none_results) == 1, f"Expected exactly 1 winner, got {results}"

    # Verify event store contains exactly 1 or tuple of terminal events (from the winner only)
    async with database.transaction() as conn:
        cursor = await conn.execute(
            """
            SELECT event_type FROM events
            WHERE session_id = ? AND event_type IN (
                'assistant.generation_completed',
                'core.error_raised',
                'assistant.generation_cancelled'
            )
            """,
            (str(session_id),),
        )
        events_rows = list(await cursor.fetchall())
        assert len(events_rows) == 1
        await cursor.close()

    await database.close()
