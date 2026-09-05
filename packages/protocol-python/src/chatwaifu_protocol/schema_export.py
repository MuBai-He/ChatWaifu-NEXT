"""Deterministic JSON Schema export catalog."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from chatwaifu_protocol.avatar import AvatarCapabilityManifest, AvatarCue, AvatarInteractionEvent
from chatwaifu_protocol.base import ProtocolModel
from chatwaifu_protocol.channels import (
    ChannelAuthorizationSnapshot,
    ChannelAuthorizationStartRequest,
    ChannelAuthorizationVerificationRequest,
    ChannelConnectionConfiguration,
    ChannelConnectionSnapshot,
    ChannelDeliveryAcknowledgement,
    ChannelDeliveryClaimRequest,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartSnapshot,
    ChannelDeliveryPlanSnapshot,
    ChannelDeliverySnapshot,
    ChannelErrorResponse,
    ChannelGatewayStatusSnapshot,
    ChannelInboundTextMessage,
    ChannelPresentationPolicy,
    ChannelProviderRegistration,
    ChannelTurnCancelReceipt,
    ChannelTurnCancelRequest,
    ChannelTurnReceipt,
    ChannelTurnSnapshot,
)
from chatwaifu_protocol.character import (
    CharacterKernelSnapshot,
    PromptBudgetReport,
    ResponsePlan,
)
from chatwaifu_protocol.commands import CommandModel
from chatwaifu_protocol.conversation import ConversationInterruption
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import EgressBlockedPayload, EgressReceiptPayload, EventModel
from chatwaifu_protocol.media import AudioFrameHeader, VideoFrameHeader
from chatwaifu_protocol.memory import (
    MemoryChannelAttribution,
    MemoryContextPacket,
    MemoryProposal,
    MemoryRecord,
    MemorySource,
)
from chatwaifu_protocol.models import ModelManifest, RouteDecision
from chatwaifu_protocol.permissions import PermissionDecision, PermissionGrant, PermissionRequest
from chatwaifu_protocol.photo_memory import (
    PhotoMemoryDeleteResult,
    PhotoMemorySettings,
    PhotoMemorySettingsUpdate,
    PhotoMemorySnapshot,
    SavedPhoto,
)
from chatwaifu_protocol.session import GenerationSnapshot, SessionSnapshot, TurnSnapshot
from chatwaifu_protocol.skills import (
    McpCapabilitySnapshot,
    McpConnectionConfiguration,
    McpConnectionSnapshot,
    PluginManifest,
    PluginSnapshot,
    SkillDefinition,
    SkillInvocation,
    SkillResult,
    SkillRunSnapshot,
)
from chatwaifu_protocol.sticker_library import (
    LearnedSticker,
    StickerLibraryDeleteResult,
    StickerLibrarySettings,
    StickerLibrarySettingsUpdate,
    StickerLibrarySnapshot,
)


class ProtocolCatalog(ProtocolModel):
    """Schema-only catalog used to generate a single conflict-free TypeScript module."""

    event: EventModel
    command: CommandModel
    cloud_egress_receipt: EgressReceiptPayload
    cloud_egress_blocked: EgressBlockedPayload
    conversation_interruption: ConversationInterruption
    audio_frame: AudioFrameHeader
    video_frame: VideoFrameHeader
    session: SessionSnapshot
    turn: TurnSnapshot
    generation: GenerationSnapshot
    avatar_cue: AvatarCue
    avatar_capabilities: AvatarCapabilityManifest
    avatar_interaction: AvatarInteractionEvent
    character_kernel: CharacterKernelSnapshot
    response_plan: ResponsePlan
    prompt_budget: PromptBudgetReport
    channel_authorization_start_request: ChannelAuthorizationStartRequest
    channel_authorization_verification_request: ChannelAuthorizationVerificationRequest
    channel_authorization: ChannelAuthorizationSnapshot
    channel_provider: ChannelProviderRegistration
    channel_presentation_policy: ChannelPresentationPolicy
    channel_connection_configuration: ChannelConnectionConfiguration
    channel_connection: ChannelConnectionSnapshot
    channel_gateway_status: ChannelGatewayStatusSnapshot
    channel_inbound_text: ChannelInboundTextMessage
    channel_turn_receipt: ChannelTurnReceipt
    channel_turn: ChannelTurnSnapshot
    channel_delivery_acknowledgement: ChannelDeliveryAcknowledgement
    channel_delivery_claim_request: ChannelDeliveryClaimRequest
    channel_delivery_part_acknowledgement: ChannelDeliveryPartAcknowledgement
    channel_delivery_part_claim_request: ChannelDeliveryPartClaimRequest
    channel_delivery_part: ChannelDeliveryPartSnapshot
    channel_delivery_plan: ChannelDeliveryPlanSnapshot
    channel_delivery: ChannelDeliverySnapshot
    channel_turn_cancel_request: ChannelTurnCancelRequest
    channel_turn_cancel_receipt: ChannelTurnCancelReceipt
    channel_error: ChannelErrorResponse
    skill: SkillDefinition
    skill_run: SkillRunSnapshot
    skill_result: SkillResult
    memory: MemoryRecord
    memory_channel_attribution: MemoryChannelAttribution
    memory_proposal: MemoryProposal
    memory_context: MemoryContextPacket
    memory_source: MemorySource
    model: ModelManifest
    route: RouteDecision
    permission_request: PermissionRequest
    permission_decision: PermissionDecision
    permission_grant: PermissionGrant
    plugin_manifest: PluginManifest
    plugin: PluginSnapshot
    mcp_connection_configuration: McpConnectionConfiguration
    mcp_connection: McpConnectionSnapshot
    mcp_capabilities: McpCapabilitySnapshot
    skill_invocation: SkillInvocation
    error: StructuredError
    learned_sticker: LearnedSticker
    sticker_library_settings: StickerLibrarySettings
    sticker_library_settings_update: StickerLibrarySettingsUpdate
    sticker_library_snapshot: StickerLibrarySnapshot
    sticker_library_delete_result: StickerLibraryDeleteResult
    photo_memory_settings: PhotoMemorySettings
    photo_memory_settings_update: PhotoMemorySettingsUpdate
    saved_photo: SavedPhoto
    photo_memory_snapshot: PhotoMemorySnapshot
    photo_memory_delete_result: PhotoMemoryDeleteResult


SCHEMAS: dict[str, type[BaseModel] | TypeAdapter[Any]] = {
    "audio-frame-header": AudioFrameHeader,
    "avatar-capability-manifest": AvatarCapabilityManifest,
    "avatar-cue": AvatarCue,
    "avatar-interaction-event": AvatarInteractionEvent,
    "character-kernel-snapshot": CharacterKernelSnapshot,
    "channel-authorization-snapshot": ChannelAuthorizationSnapshot,
    "channel-authorization-start-request": ChannelAuthorizationStartRequest,
    "channel-authorization-verification-request": ChannelAuthorizationVerificationRequest,
    "channel-connection-configuration": ChannelConnectionConfiguration,
    "channel-connection-snapshot": ChannelConnectionSnapshot,
    "channel-delivery-acknowledgement": ChannelDeliveryAcknowledgement,
    "channel-delivery-claim-request": ChannelDeliveryClaimRequest,
    "channel-delivery-part-acknowledgement": ChannelDeliveryPartAcknowledgement,
    "channel-delivery-part-claim-request": ChannelDeliveryPartClaimRequest,
    "channel-delivery-part-snapshot": ChannelDeliveryPartSnapshot,
    "channel-delivery-plan-snapshot": ChannelDeliveryPlanSnapshot,
    "channel-delivery-snapshot": ChannelDeliverySnapshot,
    "channel-error-response": ChannelErrorResponse,
    "channel-gateway-status-snapshot": ChannelGatewayStatusSnapshot,
    "channel-inbound-text-message": ChannelInboundTextMessage,
    "channel-provider-registration": ChannelProviderRegistration,
    "channel-turn-cancel-receipt": ChannelTurnCancelReceipt,
    "channel-turn-cancel-request": ChannelTurnCancelRequest,
    "channel-turn-receipt": ChannelTurnReceipt,
    "channel-turn-snapshot": ChannelTurnSnapshot,
    "cloud-egress-receipt": EgressReceiptPayload,
    "cloud-egress-blocked": EgressBlockedPayload,
    "command-envelope": TypeAdapter(CommandModel),
    "conversation-interruption": ConversationInterruption,
    "event-envelope": TypeAdapter(EventModel),
    "generation-snapshot": GenerationSnapshot,
    "learned-sticker": LearnedSticker,
    "memory-context-packet": MemoryContextPacket,
    "memory-channel-attribution": MemoryChannelAttribution,
    "memory-proposal": MemoryProposal,
    "memory-record": MemoryRecord,
    "memory-source": MemorySource,
    "mcp-capability-snapshot": McpCapabilitySnapshot,
    "mcp-connection-configuration": McpConnectionConfiguration,
    "mcp-connection-snapshot": McpConnectionSnapshot,
    "model-manifest": ModelManifest,
    "permission-decision": PermissionDecision,
    "permission-grant": PermissionGrant,
    "permission-request": PermissionRequest,
    "prompt-budget-report": PromptBudgetReport,
    "photo-memory-settings": PhotoMemorySettings,
    "photo-memory-settings-update": PhotoMemorySettingsUpdate,
    "photo-memory-snapshot": PhotoMemorySnapshot,
    "photo-memory-delete-result": PhotoMemoryDeleteResult,
    "plugin-manifest": PluginManifest,
    "plugin-snapshot": PluginSnapshot,
    "protocol-catalog": ProtocolCatalog,
    "route-decision": RouteDecision,
    "response-plan": ResponsePlan,
    "saved-photo": SavedPhoto,
    "session-snapshot": SessionSnapshot,
    "skill-definition": SkillDefinition,
    "skill-invocation": SkillInvocation,
    "skill-result": SkillResult,
    "skill-run-snapshot": SkillRunSnapshot,
    "sticker-library-delete-result": StickerLibraryDeleteResult,
    "sticker-library-settings": StickerLibrarySettings,
    "sticker-library-settings-update": StickerLibrarySettingsUpdate,
    "sticker-library-snapshot": StickerLibrarySnapshot,
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
