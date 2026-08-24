"""Public ChatWaifu NEXT protocol surface."""

from chatwaifu_protocol.avatar import AvatarCapabilityManifest, AvatarCue, AvatarInteractionEvent
from chatwaifu_protocol.commands import CommandEnvelope
from chatwaifu_protocol.conversation import ConversationInterruption
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import EventEnvelope
from chatwaifu_protocol.media import (
    AudioFrameHeader,
    VideoFrameHeader,
    decode_audio_frame_header,
    encode_audio_frame_header,
)
from chatwaifu_protocol.memory import (
    MemoryContextPacket,
    MemoryProposal,
    MemoryRecord,
    MemorySource,
)
from chatwaifu_protocol.models import ModelManifest, RouteDecision
from chatwaifu_protocol.permissions import PermissionDecision, PermissionGrant, PermissionRequest
from chatwaifu_protocol.registry import SchemaRegistry, create_default_registry
from chatwaifu_protocol.session import GenerationSnapshot, SessionSnapshot, TurnSnapshot
from chatwaifu_protocol.skills import (
    PluginManifest,
    PluginSnapshot,
    SkillDefinition,
    SkillInvocation,
    SkillResult,
    SkillRunSnapshot,
)
from chatwaifu_protocol.version import PACKAGE_VERSION, SCHEMA_VERSION

__all__ = [
    "PACKAGE_VERSION",
    "SCHEMA_VERSION",
    "AudioFrameHeader",
    "AvatarCapabilityManifest",
    "AvatarCue",
    "AvatarInteractionEvent",
    "CommandEnvelope",
    "ConversationInterruption",
    "EventEnvelope",
    "GenerationSnapshot",
    "MemoryContextPacket",
    "MemoryProposal",
    "MemoryRecord",
    "MemorySource",
    "ModelManifest",
    "PermissionDecision",
    "PermissionGrant",
    "PermissionRequest",
    "PluginManifest",
    "PluginSnapshot",
    "RouteDecision",
    "SchemaRegistry",
    "SessionSnapshot",
    "SkillDefinition",
    "SkillInvocation",
    "SkillResult",
    "SkillRunSnapshot",
    "StructuredError",
    "TurnSnapshot",
    "VideoFrameHeader",
    "create_default_registry",
    "decode_audio_frame_header",
    "encode_audio_frame_header",
]
