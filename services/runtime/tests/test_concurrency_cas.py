# pyright: reportPrivateUsage=false
"""Tests for State Machine CAS and Concurrency Invariants (Phase 12.5 Track B)."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.character import AffectState, RelationshipState
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import (
    ErrorRaisedEvent,
    ErrorRaisedPayload,
    GenericCoreEvent,
)
from chatwaifu_protocol.memory import MemoryProposal, MemoryRecord, MemoryRecordDraft, MemorySource
from chatwaifu_protocol.session import GenerationState, SessionState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.character_kernel.service import CharacterKernelService
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
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
        turn_id=turn_id,
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
        turn_id=turn_id,
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
        turn_id=turn_id,
        generation_id=gen_id,
        occurred_at=now,
        set_session_idle=True,
        cancel_events=(cancel_event,),
    )
    assert cancelled is None

    # Verify state in DB remains 'completed'
    async with database.transaction() as conn:
        cursor = await conn.execute(
            """
            SELECT state, output_text, error_code FROM generations
            WHERE generation_id = ?
            """,
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
async def test_generation_cas_mismatched_session_or_turn_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    database = Database(db_path, StorageConfig(database_path=db_path))
    await database.open()
    event_store = EventStore(database)
    repo = SQLiteConversationRepository(database, event_store)

    session_id = uuid4()
    wrong_session_id = uuid4()
    turn_id = uuid4()
    wrong_turn_id = uuid4()
    gen_id = uuid4()
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
        payload={"text": "Mismatched reply"},
    )

    # 1. Complete with wrong session_id rejected
    rejected_sess = await repo.complete_generation(
        session_id=wrong_session_id,
        turn_id=turn_id,
        generation_id=gen_id,
        assistant_turn_id=uuid4(),
        output="Mismatched reply",
        source_context=None,
        occurred_at=now,
        set_session_idle=True,
        complete_event=complete_ev,
    )
    assert rejected_sess is None

    # 2. Complete with wrong turn_id rejected
    rejected_turn = await repo.complete_generation(
        session_id=session_id,
        turn_id=wrong_turn_id,
        generation_id=gen_id,
        assistant_turn_id=uuid4(),
        output="Mismatched reply",
        source_context=None,
        occurred_at=now,
        set_session_idle=True,
        complete_event=complete_ev,
    )
    assert rejected_turn is None

    # 3. Fail with wrong turn_id rejected
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
                code="ERR",
                message="msg",
                retryable=False,
                component="conversation",
            )
        ),
    )
    rejected_fail = await repo.fail_generation(
        session_id=session_id,
        turn_id=wrong_turn_id,
        generation_id=gen_id,
        error_code="ERR",
        occurred_at=now,
        set_session_idle=True,
        fail_event=fail_ev,
    )
    assert rejected_fail is None

    # 4. Cancel with wrong turn_id rejected
    cancel_ev = GenericCoreEvent(
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
    rejected_cancel = await repo.cancel_generation(
        session_id=session_id,
        turn_id=wrong_turn_id,
        generation_id=gen_id,
        occurred_at=now,
        set_session_idle=True,
        cancel_events=(cancel_ev,),
    )
    assert rejected_cancel is None

    # Verify generation remains running, and zero assistant turns or events were inserted
    async with database.transaction() as conn:
        cursor = await conn.execute(
            "SELECT state FROM generations WHERE generation_id = ?",
            (str(gen_id),),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == GenerationState.RUNNING.value
        await cursor.close()

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = ? AND role = 'assistant'",
            (str(session_id),),
        )
        turns_count = await cursor.fetchone()
        assert turns_count is not None and turns_count[0] == 0
        await cursor.close()

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ?",
            (str(session_id),),
        )
        events_count = await cursor.fetchone()
        assert events_count is not None and events_count[0] == 0
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
        turn_id=turn_id,
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
        turn_id=turn_id,
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
        turn_id=turn_id,
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
        turn_id=turn_id,
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
        turn_id=turn_id,
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
        turn_id=turn_id,
        generation_id=gen_id,
        error_code="RACE_ERROR",
        occurred_at=now,
        set_session_idle=True,
        fail_event=fail_ev,
    )
    t_cancel = repo.cancel_generation(
        session_id=session_id,
        turn_id=turn_id,
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


@pytest.mark.asyncio
async def test_character_kernel_partial_failure_rolls_back_atomically(
    tmp_path: Path, runtime_settings: Settings
) -> None:
    db_path = tmp_path / "runtime.db"
    database = Database(db_path, StorageConfig(database_path=db_path))
    await database.open()
    event_store = EventStore(database)
    hub = EventHub()
    publisher = EventPublisher(event_store, hub)
    characters = CharacterService(runtime_settings.characters_dir)
    characters.start()
    service = CharacterKernelService(database, characters, publisher)

    # Initialize character
    await service.snapshot("default")
    now = datetime.now(UTC)

    # Corrupt only relationship revision to 5, leaving character_states at revision 0
    async with database.transaction() as conn:
        await conn.execute(
            "UPDATE relationship_states SET revision = 5 WHERE character_id = 'default'"
        )

    # Attempt CAS expecting revision 0.
    # character_states matches (rev 0), but relationship_states fails (rev 5 != 0).
    new_affect = AffectState(valence=0.8, updated_at=now)
    new_rel = RelationshipState(familiarity=0.8, updated_at=now)
    cas_result = await service._persist_cas(
        character_id="default",
        affect=new_affect,
        relationship=new_rel,
        expected_revision=0,
        new_revision=1,
    )
    assert cas_result is False

    # Assert character_states was rolled back and NOT updated to revision 1 or valence 0.8
    async with database.transaction() as conn:
        cursor = await conn.execute(
            "SELECT revision, valence FROM character_states WHERE character_id = 'default'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 0
        assert row[1] != 0.8
        await cursor.close()

    await database.close()
