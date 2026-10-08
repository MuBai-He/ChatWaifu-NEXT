"""Only model-selected original evidence enters the existing scene memory pipeline."""

import asyncio
from uuid import UUID, uuid4, uuid5

from chatwaifu_protocol.base import JsonObject, PrivacyLevel
from chatwaifu_protocol.events import GenericCoreEvent

from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.autonomy import GroupObservation
from chatwaifu_runtime.memory.service import MemoryService
from chatwaifu_runtime.sessions.service import SessionService


class GroupMemoryBridge:
    def __init__(
        self, sessions: SessionService, memory: MemoryService, publisher: EventPublisher
    ) -> None:
        self.sessions = sessions
        self.memory = memory
        self.publisher = publisher
        self._lock = asyncio.Lock()

    async def context(self, observation: GroupObservation) -> JsonObject:
        await observation.authorize()
        member = next(
            m
            for m in observation.route.members
            if m.sender_key == observation.descriptor.sender_key
        )
        async with self._lock:
            session = await self.sessions.scene_evidence_session(
                observation.route.character_id, member.participant_id, observation.route.scene_id
            )
        packet = await self.memory.retrieve_context(
            session.session_id, uuid4(), observation.route.character_id, observation.descriptor.text
        )
        return {"scope": session.user_scope, "packet": packet.model_dump(mode="json")}

    async def observe(self, observation: GroupObservation, refs: tuple[str, ...]) -> None:
        async with self._lock:
            for message in observation.discussion.messages:
                if message.message_id not in refs or message.omitted_characters:
                    continue
                await observation.authorize()
                member = next(
                    m
                    for m in observation.route.members
                    if m.participant_id == message.participant_id
                )
                session = await self.sessions.scene_evidence_session(
                    observation.route.character_id,
                    member.participant_id,
                    observation.route.scene_id,
                )
                event_id = uuid5(
                    UUID(observation.route.scene_id),
                    "group-evidence:" + member.participant_id + ":" + message.message_id,
                )
                if await self.memory.source_event_exists(event_id):
                    continue
                source: JsonObject = {
                    "provider_id": "qq_napcat",
                    "connection_id": str(observation.route.connection_id),
                    "account_key": observation.route.account_key,
                    "principal_scope": session.user_scope,
                    "chat_type": "group",
                    "conversation_key": "group:" + observation.route.group_id,
                    "sender_key": member.sender_key,
                    "received_at": message.received_at.isoformat(),
                    "route_revision": observation.route.revision,
                }
                event = GenericCoreEvent(
                    event_id=event_id,
                    event_type="agent.group_evidence",
                    session_id=session.session_id,
                    occurred_at=message.received_at,
                    source="runtime.agent.group_memory",
                    privacy=PrivacyLevel.PRIVATE,
                    payload={
                        "text": message.text,
                        "source_ref": message.message_id,
                        "source_context": source,
                    },
                )
                await observation.authorize()
                await self.publisher.emit(event)
                await self.memory.observe_user_turn(
                    session.session_id,
                    uuid4(),
                    event_id,
                    observation.route.character_id,
                    message.text,
                )
