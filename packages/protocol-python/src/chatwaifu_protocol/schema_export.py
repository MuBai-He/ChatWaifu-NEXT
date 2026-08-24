"""Deterministic JSON Schema export catalog."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from chatwaifu_protocol.avatar import AvatarCapabilityManifest, AvatarCue, AvatarInteractionEvent
from chatwaifu_protocol.base import ProtocolModel
from chatwaifu_protocol.commands import CommandModel
from chatwaifu_protocol.conversation import ConversationInterruption
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import EventModel
from chatwaifu_protocol.media import AudioFrameHeader, VideoFrameHeader
from chatwaifu_protocol.memory import MemoryContextPacket, MemoryProposal, MemoryRecord
from chatwaifu_protocol.models import ModelManifest, RouteDecision
from chatwaifu_protocol.permissions import PermissionDecision, PermissionGrant, PermissionRequest
from chatwaifu_protocol.session import GenerationSnapshot, SessionSnapshot, TurnSnapshot
from chatwaifu_protocol.skills import (
    PluginManifest,
    PluginSnapshot,
    SkillDefinition,
    SkillInvocation,
    SkillResult,
    SkillRunSnapshot,
)


class ProtocolCatalog(ProtocolModel):
    """Schema-only catalog used to generate a single conflict-free TypeScript module."""

    event: EventModel
    command: CommandModel
    conversation_interruption: ConversationInterruption
    audio_frame: AudioFrameHeader
    video_frame: VideoFrameHeader
    session: SessionSnapshot
    turn: TurnSnapshot
    generation: GenerationSnapshot
    avatar_cue: AvatarCue
    avatar_capabilities: AvatarCapabilityManifest
    avatar_interaction: AvatarInteractionEvent
    skill: SkillDefinition
    skill_run: SkillRunSnapshot
    skill_result: SkillResult
    memory: MemoryRecord
    memory_proposal: MemoryProposal
    memory_context: MemoryContextPacket
    model: ModelManifest
    route: RouteDecision
    permission_request: PermissionRequest
    permission_decision: PermissionDecision
    permission_grant: PermissionGrant
    plugin_manifest: PluginManifest
    plugin: PluginSnapshot
    skill_invocation: SkillInvocation
    error: StructuredError


SCHEMAS: dict[str, type[BaseModel] | TypeAdapter[Any]] = {
    "audio-frame-header": AudioFrameHeader,
    "avatar-capability-manifest": AvatarCapabilityManifest,
    "avatar-cue": AvatarCue,
    "avatar-interaction-event": AvatarInteractionEvent,
    "command-envelope": TypeAdapter(CommandModel),
    "conversation-interruption": ConversationInterruption,
    "event-envelope": TypeAdapter(EventModel),
    "generation-snapshot": GenerationSnapshot,
    "memory-context-packet": MemoryContextPacket,
    "memory-proposal": MemoryProposal,
    "memory-record": MemoryRecord,
    "model-manifest": ModelManifest,
    "permission-decision": PermissionDecision,
    "permission-grant": PermissionGrant,
    "permission-request": PermissionRequest,
    "plugin-manifest": PluginManifest,
    "plugin-snapshot": PluginSnapshot,
    "protocol-catalog": ProtocolCatalog,
    "route-decision": RouteDecision,
    "session-snapshot": SessionSnapshot,
    "skill-definition": SkillDefinition,
    "skill-invocation": SkillInvocation,
    "skill-result": SkillResult,
    "skill-run-snapshot": SkillRunSnapshot,
    "structured-error": StructuredError,
    "turn-snapshot": TurnSnapshot,
    "video-frame-header": VideoFrameHeader,
}


def export_schemas(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for slug, model in sorted(SCHEMAS.items()):
        schema = (
            model.json_schema() if isinstance(model, TypeAdapter) else model.model_json_schema()
        )
        schema["$id"] = f"https://chatwaifu.local/schemas/domain/v1/{slug}.schema.json"
        schema["title"] = "".join(part.title() for part in slug.split("-"))
        schema["x-schema-version"] = "1.0"
        target = output_dir / f"{slug}.schema.json"
        target.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
