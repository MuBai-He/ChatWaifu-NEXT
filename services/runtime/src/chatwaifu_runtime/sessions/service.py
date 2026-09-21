"""Table-driven session lifecycle backed by SQLite and domain events."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent, SessionCreatedEvent, SessionCreatedPayload
from chatwaifu_protocol.session import (
    ConversationState,
    ParticipantSnapshot,
    SceneSnapshot,
    SessionSnapshot,
    SessionState,
)

from chatwaifu_runtime.conversation.models import ConversationSourceContext
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

    async def source_context(self, session_id: UUID) -> ConversationSourceContext | None:
        session = await self.get_session(session_id)
        if session is None:
            raise KeyError("session not found")
        if session.user_scope == "local":
            return None
        names = {p.participant_id: p.display_name for p in await self.list_participants()}
        scene = next((s for s in await self.list_scenes() if s.scene_id == session.scene_id), None)
        return ConversationSourceContext(
            provider_id="runtime",
            connection_id=session.session_id,
            account_key=None,
            principal_scope=session.user_scope,
            chat_type="group" if scene else "direct",
            conversation_key=session.scene_id or session.participant_id,
            sender_key=session.participant_id,
            sender_display_name=names[session.participant_id],
            conversation_label=scene.display_name if scene else names[session.participant_id],
            audience_ids=tuple(session.audience_ids),
        )

    async def list_participants(self) -> list[ParticipantSnapshot]:
        rows = await self._database.fetchall(
            "SELECT * FROM participants ORDER BY created_at, participant_id"
        )
        return [ParticipantSnapshot.model_validate(dict(row)) for row in rows]

    async def create_participant(self, display_name: str) -> ParticipantSnapshot:
        participant = ParticipantSnapshot(
            participant_id=str(uuid4()),
            display_name=display_name.strip(),
            created_at=datetime.now(UTC),
        )
        async with self._database.transaction() as connection:
            await connection.execute(
                "INSERT INTO participants VALUES (?, ?, ?)",
                (
                    participant.participant_id,
                    participant.display_name,
                    participant.created_at.isoformat(),
                ),
            )
        return participant

    async def list_scenes(self) -> list[SceneSnapshot]:
        rows = await self._database.fetchall(
            "SELECT * FROM conversation_scenes ORDER BY created_at, scene_id"
        )
        return [
            SceneSnapshot(
                scene_id=row["scene_id"],
                display_name=row["display_name"],
                participant_ids=json.loads(row["participant_ids_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def create_scene(self, display_name: str, participant_ids: list[str]) -> SceneSnapshot:
        ids = sorted(set(participant_ids))
        known = {p.participant_id for p in await self.list_participants()}
        if not set(ids).issubset(known):
            raise ValueError("unknown scene participant")
        scene = SceneSnapshot(
            scene_id=str(uuid4()),
            display_name=display_name.strip(),
            participant_ids=ids,
            created_at=datetime.now(UTC),
        )
        async with self._database.transaction() as connection:
            await connection.execute(
                "INSERT INTO conversation_scenes VALUES (?, ?, ?, ?)",
                (scene.scene_id, scene.display_name, json.dumps(ids), scene.created_at.isoformat()),
            )
        return scene

    async def create_session(
        self, character_id: str, *, participant_id: str = "local", scene_id: str | None = None
    ) -> SessionSnapshot:
        if participant_id not in {p.participant_id for p in await self.list_participants()}:
            raise ValueError("unknown participant")
        audience = [participant_id]
        scope = "local" if participant_id == "local" else f"participant:{participant_id}"
        kind = "private"
        if scene_id is not None:
            scene = next((s for s in await self.list_scenes() if s.scene_id == scene_id), None)
            if scene is None or participant_id not in scene.participant_ids:
                raise ValueError("participant is not in the selected scene")
            audience = scene.participant_ids
            scope = f"scene:{scene_id}"
            kind = "shared"
        session_id = uuid4()
        now = datetime.now(UTC)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO sessions(
                    session_id, character_id, state, conversation_state,
                    revision, next_sequence, created_at, updated_at,
                    participant_id, scene_id, scene_kind, audience_json, user_scope
                ) VALUES (?, ?, ?, ?, 0, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(session_id),
                    character_id,
                    SessionState.READY.value,
                    ConversationState.IDLE.value,
                    now.isoformat(),
                    now.isoformat(),
                    participant_id,
                    scene_id,
                    kind,
                    json.dumps(audience),
                    scope,
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
            participant_id=participant_id,
            scene_id=scene_id,
            scene_kind="shared" if scene_id else "private",
            audience_ids=audience,
            user_scope=scope,
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
        return _session_snapshot(dict(row))

    async def list_ready_sessions(self) -> tuple[SessionSnapshot, ...]:
        rows = await self._database.fetchall(
            "SELECT * FROM sessions WHERE state = ? ORDER BY updated_at DESC",
            (SessionState.READY.value,),
        )
        return tuple(_session_snapshot(dict(row)) for row in rows)

    async def transition_session(
        self,
        session_id: UUID,
        target: SessionState,
        *,
        expected_revision: int | None = None,
    ) -> SessionSnapshot:
        current = await self.get_session(session_id)
        if current is None:
            raise KeyError(f"unknown session {session_id}")
        if expected_revision is not None and current.revision != expected_revision:
            raise InvalidSessionTransition(
                f"session {session_id} revision mismatch "
                f"(expected {expected_revision}, current {current.revision})"
            )
        if target not in ALLOWED_TRANSITIONS[current.state]:
            raise InvalidSessionTransition(f"cannot transition {current.state} -> {target}")
        now = datetime.now(UTC)
        revision = current.revision + 1
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE sessions SET state = ?, revision = ?, updated_at = ?
                WHERE session_id = ? AND revision = ? AND state = ?
                """,
                (
                    target.value,
                    revision,
                    now.isoformat(),
                    str(session_id),
                    current.revision,
                    current.state.value,
                ),
            )
            updated = cursor.rowcount > 0
            await cursor.close()
            if not updated:
                raise InvalidSessionTransition(
                    f"session {session_id} state/revision changed concurrently "
                    f"(expected revision {current.revision}, state {current.state})"
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


def _session_snapshot(row: dict[str, object]) -> SessionSnapshot:
    row["audience_ids"] = json.loads(str(row.pop("audience_json")))
    return SessionSnapshot.model_validate(row)
