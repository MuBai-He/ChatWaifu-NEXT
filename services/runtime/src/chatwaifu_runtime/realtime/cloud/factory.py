"""Runtime factory for Cloud Realtime media bridges and sessions.

Assembles CloudRealtimeMediaBridge instances for Pipecat by:
1. Resolving active Session and Character details;
2. Capturing Character Kernel and Memory snapshots;
3. Extracting allowed Skill capabilities;
4. Enforcing Cloud Egress Policy through CloudEgressGateway;
5. Opening an authorized CloudRealtimeSession;
6. Setting up RealtimeSessionMirror and RuntimeRealtimeDomainSink;
7. Constructing and returning CloudRealtimeMediaBridge.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast
from uuid import UUID

from chatwaifu_runtime.realtime.admission import RealtimeTurnAdmissionPort
from chatwaifu_runtime.realtime.cloud.context import CloudEgressGateway, RealtimeSessionIntent
from chatwaifu_runtime.realtime.cloud.contracts import (
    CloudRealtimeBackend,
    RealtimeSkillCapability,
)
from chatwaifu_runtime.realtime.cloud.domain import RuntimeRealtimeDomainSink
from chatwaifu_runtime.realtime.cloud.media import CloudRealtimeMediaBridge

if TYPE_CHECKING:
    from chatwaifu_protocol.skills import SkillDefinition

    from chatwaifu_runtime.character_kernel.service import CharacterKernelService
    from chatwaifu_runtime.characters.service import CharacterService
    from chatwaifu_runtime.conversation.service import ConversationService
    from chatwaifu_runtime.eventing.hub import EventHub
    from chatwaifu_runtime.memory.service import MemoryService
    from chatwaifu_runtime.runtime_skills.registry import SkillRegistry
    from chatwaifu_runtime.runtime_skills.service import RuntimeSkillService
    from chatwaifu_runtime.sessions.service import SessionService

_LOGGER = logging.getLogger(__name__)


def extract_realtime_skills(
    skills_source: SkillRegistry | RuntimeSkillService | Sequence[SkillDefinition] | None,
) -> list[RealtimeSkillCapability]:
    """Extract a sanitized, typed allowlist of skill capabilities for cloud realtime context."""
    if skills_source is None:
        return []
    definitions: Sequence[SkillDefinition]
    if isinstance(skills_source, Sequence):
        definitions = skills_source
    else:
        definitions = skills_source.list()
    capabilities: list[RealtimeSkillCapability] = []
    for defn in definitions:
        if not getattr(defn, "enabled", True):
            continue
        arg_names: list[str] = []
        caps: Sequence[object] = getattr(defn, "capabilities", ())
        for cap in caps:
            schema: object = getattr(cap, "input_schema", {})
            if isinstance(schema, dict):
                schema_dict = cast(dict[str, object], schema)
                raw_props = schema_dict.get("properties")
                if isinstance(raw_props, dict):
                    props_dict = cast(dict[str, object], raw_props)
                    for k in props_dict:
                        arg_names.append(k)
        capabilities.append(
            RealtimeSkillCapability(
                skill_id=defn.skill_id,
                display_name=defn.name,
                description=defn.description,
                allowed_argument_names=tuple(sorted(set(arg_names))),
            )
        )
    return sorted(capabilities, key=lambda c: c.skill_id)


class RuntimeCloudRealtimeFactory:
    """Assembles authorized CloudRealtimeMediaBridge instances for Pipecat pipelines."""

    def __init__(
        self,
        *,
        backend: CloudRealtimeBackend,
        egress_gateway: CloudEgressGateway,
        conversation: ConversationService,
        sessions: SessionService,
        admission: RealtimeTurnAdmissionPort,
        characters: CharacterService,
        character_kernel: CharacterKernelService,
        memory: MemoryService,
        skills_source: (
            SkillRegistry | RuntimeSkillService | Sequence[SkillDefinition] | None
        ) = None,
        event_hub: EventHub | None = None,
    ) -> None:
        self._backend = backend
        self._egress_gateway = egress_gateway
        self._conversation = conversation
        self._sessions = sessions
        self._admission = admission
        self._characters = characters
        self._character_kernel = character_kernel
        self._memory = memory
        self._skills_source = skills_source
        self._event_hub = event_hub

    async def create_bridge(self, session_id: UUID) -> CloudRealtimeMediaBridge:
        """Create and wire an authorized CloudRealtimeMediaBridge for session_id."""
        session = await self._sessions.get_session(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} does not exist")

        character_id = session.character_id
        character_profile = self._characters.get(character_id)

        kernel_snapshot = None
        try:
            kernel_snapshot = await self._character_kernel.snapshot(character_id)
        except Exception:
            _LOGGER.debug(
                "Could not obtain character kernel snapshot for %s",
                character_id,
                exc_info=True,
            )

        memories = None
        try:
            memories = await self._memory.list(include_tombstoned=False)
        except Exception:
            _LOGGER.debug(
                "Could not obtain memory records for session %s",
                session_id,
                exc_info=True,
            )

        skills = extract_realtime_skills(self._skills_source)

        intent = RealtimeSessionIntent(
            session_id=session_id,
            character_id=character_id,
        )

        cloud_session = await self._egress_gateway.open_session(
            self._backend,
            intent,
            character_profile=character_profile,
            kernel_snapshot=kernel_snapshot,
            memories=memories,
            skills=skills,
        )

        domain_sink = RuntimeRealtimeDomainSink(
            self._conversation,
            event_hub=self._event_hub,
        )

        return CloudRealtimeMediaBridge.create(
            session_id=session_id,
            backend_id=self._backend.backend_id,
            session=cloud_session,
            admission=self._admission,
            domain_sink=domain_sink,
        )
