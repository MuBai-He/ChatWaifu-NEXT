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
