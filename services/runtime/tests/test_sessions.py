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
