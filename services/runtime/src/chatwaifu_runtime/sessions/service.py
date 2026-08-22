"""Table-driven session lifecycle backed by SQLite and domain events."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent, SessionCreatedEvent, SessionCreatedPayload
from chatwaifu_protocol.session import ConversationState, SessionSnapshot, SessionState

from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore

ALLOWED_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset(
        {SessionState.CONNECTING, SessionState.READY, SessionState.CLOSING}
    ),
    SessionState.CONNECTING: frozenset(
        {SessionState.READY, SessionState.DEGRADED, SessionState.CLOSING}
    ),
    SessionState.READY: frozenset(
        {SessionState.DEGRADED, SessionState.RECOVERING, SessionState.CLOSING}
    ),
    SessionState.DEGRADED: frozenset(
        {SessionState.RECOVERING, SessionState.READY, SessionState.CLOSING}
    ),
    SessionState.RECOVERING: frozenset(
        {SessionState.READY, SessionState.DEGRADED, SessionState.CLOSING}
    ),
    SessionState.CLOSING: frozenset({SessionState.CLOSED}),
    SessionState.CLOSED: frozenset(),
}


class InvalidSessionTransition(ValueError):
    pass


class SessionService:
    def __init__(self, database: Database, event_store: EventStore, event_hub: EventHub) -> None:
        self._database = database
        self._event_store = event_store
        self._event_hub = event_hub

    async def create_session(self, character_id: str) -> SessionSnapshot:
        session_id = uuid4()
        now = datetime.now(UTC)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO sessions(
                    session_id, character_id, state, conversation_state,
                    revision, next_sequence, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 1, ?, ?)
                """,
                (
                    str(session_id),
                    character_id,
                    SessionState.READY.value,
                    ConversationState.IDLE.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            event = await self._event_store.append_in_transaction(
                connection,
                SessionCreatedEvent(
                    event_id=uuid4(),
                    session_id=session_id,
                    occurred_at=now,
                    source="runtime.sessions",
                    privacy=PrivacyLevel.LOCAL,
                    payload=SessionCreatedPayload(character_id=character_id),
                ),
            )
        await self._publish(event.model_dump(mode="json"))
        return SessionSnapshot(
            session_id=session_id,
            character_id=character_id,
            state=SessionState.READY,
            conversation_state=ConversationState.IDLE,
            revision=0,
            created_at=now,
            updated_at=now,
        )

    async def get_session(self, session_id: UUID) -> SessionSnapshot | None:
        row = await self._database.fetchone(
            "SELECT * FROM sessions WHERE session_id = ?", (str(session_id),)
        )
        if row is None:
            return None
        return SessionSnapshot.model_validate(dict(row))

    async def transition_session(self, session_id: UUID, target: SessionState) -> SessionSnapshot:
        current = await self.get_session(session_id)
        if current is None:
            raise KeyError(f"unknown session {session_id}")
        if target not in ALLOWED_TRANSITIONS[current.state]:
            raise InvalidSessionTransition(f"cannot transition {current.state} -> {target}")
        now = datetime.now(UTC)
        revision = current.revision + 1
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE sessions SET state = ?, revision = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (target.value, revision, now.isoformat(), str(session_id)),
            )
            event = await self._event_store.append_in_transaction(
                connection,
                GenericCoreEvent(
                    event_id=uuid4(),
                    event_type="session.state_changed",
                    session_id=session_id,
                    occurred_at=now,
                    source="runtime.sessions",
                    privacy=PrivacyLevel.LOCAL,
                    payload={"from": current.state.value, "to": target.value, "revision": revision},
                ),
            )
        await self._publish(event.model_dump(mode="json"))
        updated = await self.get_session(session_id)
        if updated is None:
            raise RuntimeError("session disappeared after transition")
        return updated

    async def close_session(self, session_id: UUID) -> SessionSnapshot:
        current = await self.get_session(session_id)
        if current is None:
            raise KeyError(f"unknown session {session_id}")
        if current.state is SessionState.CLOSED:
            return current
        if current.state is not SessionState.CLOSING:
            await self.transition_session(session_id, SessionState.CLOSING)
        return await self.transition_session(session_id, SessionState.CLOSED)

    async def _publish(self, event: dict[str, object]) -> None:
        await self._event_hub.publish(event)
        event_id = event.get("event_id")
        if event_id is not None:
            await self._event_store.mark_published(str(event_id))
