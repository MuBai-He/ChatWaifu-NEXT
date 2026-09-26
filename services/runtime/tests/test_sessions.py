"""Session state machine regression tests."""

import pytest
from chatwaifu_protocol.session import SessionState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.sessions.service import InvalidSessionTransition


@pytest.mark.asyncio
async def test_invalid_transition_is_rejected(runtime_settings: Settings) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        with pytest.raises(InvalidSessionTransition):
            await container.sessions.transition_session(session.session_id, SessionState.CREATED)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_session_transition_expected_revision_cas(runtime_settings: Settings) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        assert session.revision == 0
        assert session.state == SessionState.READY

        # Valid transition with matching expected revision: READY -> DEGRADED
        degraded = await container.sessions.transition_session(
            session.session_id, SessionState.DEGRADED, expected_revision=0
        )
        assert degraded.revision == 1
        assert degraded.state == SessionState.DEGRADED

        # Transition with stale expected revision (0 instead of 1) is rejected
        with pytest.raises(InvalidSessionTransition, match="revision mismatch"):
            await container.sessions.transition_session(
                session.session_id, SessionState.RECOVERING, expected_revision=0
            )

        # Transition with correct revision (1) succeeds: DEGRADED -> RECOVERING
        recovering = await container.sessions.transition_session(
            session.session_id, SessionState.RECOVERING, expected_revision=1
        )
        assert recovering.revision == 2
        assert recovering.state == SessionState.RECOVERING
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_scope_migration_preserves_populated_owner_database(
    runtime_settings: Settings,
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from chatwaifu_runtime.persistence.database import Database
    from chatwaifu_runtime.persistence.migrations import MIGRATIONS

    legacy = Database(
        runtime_settings.database_path,
        runtime_settings.storage,
        migrations=tuple(item for item in MIGRATIONS if item[0] <= 31),
    )
    await legacy.open()
    session_id, now = str(uuid4()), datetime.now(UTC).isoformat()
    async with legacy.transaction() as connection:
        await connection.execute(
            "INSERT INTO sessions(session_id, character_id, state, conversation_state, "
            "created_at, updated_at) VALUES (?, 'default', 'ready', 'idle', ?, ?)",
            (session_id, now, now),
        )
        await connection.execute(
            "INSERT INTO memory_scope_resets(character_id, reset_at) VALUES ('default', ?)", (now,)
        )
    await legacy.close()
    database = Database(runtime_settings.database_path, runtime_settings.storage)
    await database.open()
    try:
        row = await database.fetchone("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        assert row is not None and row["participant_id"] == row["user_scope"] == "local"
        assert row["audience_json"] == '["local"]'
        reset = await database.fetchone(
            "SELECT * FROM memory_scope_resets WHERE character_id = 'default'"
        )
        assert reset is not None and reset["user_scope"] == "local" and reset["reset_at"] == now
        with pytest.raises(Exception, match="scope is immutable"):
            async with database.transaction() as connection:
                await connection.execute(
                    "UPDATE sessions SET user_scope = 'forged' WHERE session_id = ?", (session_id,)
                )
    finally:
        await database.close()
