"""Deterministic JSON Schema export catalog."""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from chatwaifu_protocol.avatar import AvatarCapabilityManifest, AvatarCue, AvatarInteractionEvent
from chatwaifu_protocol.base import ProtocolModel
from chatwaifu_protocol.channel_groups import (
    ChannelGroupAudienceRequest,
    ChannelGroupAudienceSnapshot,
    ChannelGroupDeliveryTarget,
    ChannelGroupRouteCreate,
    ChannelGroupRouteMemberSnapshot,
    ChannelGroupRoutePage,
    ChannelGroupRouteSnapshot,
    ChannelGroupRouteUpdate,
    ChannelGroupTurnCancelRequest,
    ChannelGroupTurnPage,
    ChannelGroupTurnSnapshot,
    ChannelParticipantLinkCreate,
    ChannelParticipantLinkPage,
    ChannelParticipantLinkSnapshot,
    ChannelParticipantLinkUpdate,
)
from chatwaifu_protocol.channel_proactive import (
    ChannelOutboundIntentCancelRequest,
    ChannelOutboundIntentPage,
    ChannelOutboundIntentSnapshot,
    ChannelProactivePolicy,
    ChannelProactivePolicySnapshot,
    ChannelProactivePolicyUpdate,
    ChannelProactivePreview,
)
from chatwaifu_protocol.channel_settings import (
    ChannelRuntimePolicy,
    ChannelRuntimeSettingsResponse,
    ChannelRuntimeSettingsSnapshot,
    ChannelRuntimeSettingsUpdate,
    GroupDiscussionPolicy,
)
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
    ChannelPairingSnapshot,
    ChannelPairingStartRequest,
    ChannelPresentationPolicy,
    ChannelProviderRegistration,
    ChannelTurnCancelReceipt,
    ChannelTurnCancelRequest,
    ChannelTurnReceipt,
    ChannelTurnSnapshot,
)
from chatwaifu_protocol.character import (
    CharacterKernelSnapshot,
    CharacterPromptCompiledPayload,
    PromptBudgetReport,
    PromptContextIdentity,
    ResponsePlan,
)
from chatwaifu_protocol.commands import CommandModel
from chatwaifu_protocol.conversation import ConversationInterruption
from chatwaifu_protocol.diagnostics import InteractionTraceDetail, InteractionTracePage
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
from chatwaifu_protocol.session import (
    GenerationSnapshot,
    ParticipantSnapshot,
    SceneSnapshot,
    SessionSnapshot,
    TurnSnapshot,
)
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
    StickerUsageHistory,
    StickerUsageRecord,
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
    participant: ParticipantSnapshot
    scene: SceneSnapshot
    session: SessionSnapshot
    turn: TurnSnapshot
    generation: GenerationSnapshot
    avatar_cue: AvatarCue
    avatar_capabilities: AvatarCapabilityManifest
    avatar_interaction: AvatarInteractionEvent
    character_kernel: CharacterKernelSnapshot
    response_plan: ResponsePlan
    prompt_budget: PromptBudgetReport
    prompt_context_identity: PromptContextIdentity
    character_prompt_compiled: CharacterPromptCompiledPayload
    interaction_trace_page: InteractionTracePage
    interaction_trace_detail: InteractionTraceDetail
    channel_authorization_start_request: ChannelAuthorizationStartRequest
    channel_authorization_verification_request: ChannelAuthorizationVerificationRequest
    channel_authorization: ChannelAuthorizationSnapshot
    channel_pairing_start_request: ChannelPairingStartRequest
    channel_pairing: ChannelPairingSnapshot
    channel_provider: ChannelProviderRegistration
    channel_runtime_policy: ChannelRuntimePolicy
    channel_runtime_settings: ChannelRuntimeSettingsResponse
    channel_runtime_settings_snapshot: ChannelRuntimeSettingsSnapshot
    channel_runtime_settings_update: ChannelRuntimeSettingsUpdate
    group_discussion_policy: GroupDiscussionPolicy
    channel_presentation_policy: ChannelPresentationPolicy
    channel_connection_configuration: ChannelConnectionConfiguration
    channel_connection: ChannelConnectionSnapshot
    channel_gateway_status: ChannelGatewayStatusSnapshot
    channel_group_audience_request: ChannelGroupAudienceRequest
    channel_group_audience_snapshot: ChannelGroupAudienceSnapshot
    channel_group_turn_snapshot: ChannelGroupTurnSnapshot
    channel_group_turn_page: ChannelGroupTurnPage
    channel_group_delivery_target: ChannelGroupDeliveryTarget
    channel_group_route_create: ChannelGroupRouteCreate
    channel_group_route_member_snapshot: ChannelGroupRouteMemberSnapshot
    channel_group_route_page: ChannelGroupRoutePage
    channel_group_route_snapshot: ChannelGroupRouteSnapshot
    channel_group_route_update: ChannelGroupRouteUpdate
    channel_group_turn_cancel_request: ChannelGroupTurnCancelRequest
    channel_participant_link_create: ChannelParticipantLinkCreate
    channel_participant_link_page: ChannelParticipantLinkPage
    channel_participant_link_snapshot: ChannelParticipantLinkSnapshot
    channel_participant_link_update: ChannelParticipantLinkUpdate
    channel_proactive_policy: ChannelProactivePolicy
    channel_proactive_policy_update: ChannelProactivePolicyUpdate
    channel_proactive_policy_snapshot: ChannelProactivePolicySnapshot
    channel_proactive_preview: ChannelProactivePreview
    channel_outbound_intent: ChannelOutboundIntentSnapshot
    channel_outbound_intent_page: ChannelOutboundIntentPage
    channel_outbound_intent_cancel_request: ChannelOutboundIntentCancelRequest
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
    sticker_usage_record: StickerUsageRecord
    sticker_usage_history: StickerUsageHistory
    sticker_library_snapshot: StickerLibrarySnapshot
    sticker_library_delete_result: StickerLibraryDeleteResult
    photo_memory_settings: PhotoMemorySettings
    photo_memory_settings_update: PhotoMemorySettingsUpdate
    saved_photo: SavedPhoto
    photo_memory_snapshot: PhotoMemorySnapshot
    photo_memory_delete_result: PhotoMemoryDeleteResult


SCHEMAS: dict[str, type[BaseModel] | TypeAdapter[Any]] = {
    "channel-runtime-policy": ChannelRuntimePolicy,
    "channel-runtime-settings-response": ChannelRuntimeSettingsResponse,
    "channel-runtime-settings-snapshot": ChannelRuntimeSettingsSnapshot,
    "channel-runtime-settings-update": ChannelRuntimeSettingsUpdate,
    "group-discussion-policy": GroupDiscussionPolicy,
    "channel-group-turn-snapshot": ChannelGroupTurnSnapshot,
    "channel-group-turn-page": ChannelGroupTurnPage,
    "channel-group-audience-request": ChannelGroupAudienceRequest,
    "channel-group-audience-snapshot": ChannelGroupAudienceSnapshot,
    "channel-group-delivery-target": ChannelGroupDeliveryTarget,
    "channel-group-route-create": ChannelGroupRouteCreate,
    "channel-group-route-member-snapshot": ChannelGroupRouteMemberSnapshot,
    "channel-group-route-page": ChannelGroupRoutePage,
    "channel-group-route-snapshot": ChannelGroupRouteSnapshot,
    "channel-group-route-update": ChannelGroupRouteUpdate,
    "channel-group-turn-cancel-request": ChannelGroupTurnCancelRequest,
    "channel-participant-link-create": ChannelParticipantLinkCreate,
    "channel-participant-link-page": ChannelParticipantLinkPage,
    "channel-participant-link-snapshot": ChannelParticipantLinkSnapshot,
    "channel-participant-link-update": ChannelParticipantLinkUpdate,
    "audio-frame-header": AudioFrameHeader,
    "avatar-capability-manifest": AvatarCapabilityManifest,
    "avatar-cue": AvatarCue,
    "avatar-interaction-event": AvatarInteractionEvent,
    "character-kernel-snapshot": CharacterKernelSnapshot,
    "channel-pairing-snapshot": ChannelPairingSnapshot,
    "channel-pairing-start-request": ChannelPairingStartRequest,
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
    "channel-proactive-policy": ChannelProactivePolicy,
    "channel-proactive-policy-update": ChannelProactivePolicyUpdate,
    "channel-proactive-policy-snapshot": ChannelProactivePolicySnapshot,
    "channel-proactive-preview": ChannelProactivePreview,
    "channel-outbound-intent-snapshot": ChannelOutboundIntentSnapshot,
    "channel-outbound-intent-page": ChannelOutboundIntentPage,
    "channel-outbound-intent-cancel-request": ChannelOutboundIntentCancelRequest,
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
    "interaction-trace-page": InteractionTracePage,
    "interaction-trace-detail": InteractionTraceDetail,
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
    "prompt-context-identity": PromptContextIdentity,
    "character-prompt-compiled": CharacterPromptCompiledPayload,
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
    "participant-snapshot": ParticipantSnapshot,
    "scene-snapshot": SceneSnapshot,
    "skill-definition": SkillDefinition,
    "skill-invocation": SkillInvocation,
    "skill-result": SkillResult,
    "skill-run-snapshot": SkillRunSnapshot,
    "sticker-library-delete-result": StickerLibraryDeleteResult,
    "sticker-library-settings": StickerLibrarySettings,
    "sticker-library-settings-update": StickerLibrarySettingsUpdate,
    "sticker-library-snapshot": StickerLibrarySnapshot,
    "sticker-usage-record": StickerUsageRecord,
    "sticker-usage-history": StickerUsageHistory,
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
