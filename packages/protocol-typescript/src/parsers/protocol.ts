import { z } from "zod";
import type {
  AudioFrameHeader,
  AvatarCapabilityManifest,
  AvatarCue,
  AvatarInteractionEvent,
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
  ChannelProviderRegistration,
  ChannelTurnCancelReceipt,
  ChannelTurnCancelRequest,
  ChannelTurnReceipt,
  ChannelTurnSnapshot,
  CharacterKernelSnapshot,
  McpCapabilitySnapshot,
  McpConnectionSnapshot,
  MemoryChannelAttribution,
  MemoryProposal,
  MemoryRecord,
  MemorySource,
  PluginSnapshot,
  ProtocolCatalog,
  SessionSnapshot,
  SkillDefinition,
  SkillRunSnapshot,
} from "../generated/domain";
import { SUPPORTED_SCHEMA_MAJOR } from "../version";

export type DomainEvent = ProtocolCatalog["event"];
export type DomainCommand = ProtocolCatalog["command"];

const uuid = z.string().uuid();
const awareDateTime = z.string().datetime({ offset: true });
const schemaVersion = z
  .string()
  .refine(
    (value) => value.split(".", 1)[0] === SUPPORTED_SCHEMA_MAJOR,
    "unsupported schema major version",
  );

const eventBase = {
  event_id: uuid,
  schema_version: schemaVersion,
  session_id: uuid.nullish(),
  turn_id: uuid.nullish(),
  generation_id: uuid.nullish(),
  skill_run_id: uuid.nullish(),
  sequence: z.number().int().nonnegative().nullish(),
  occurred_at: awareDateTime,
  source: z.string().min(1),
  correlation_id: uuid.nullish(),
  causation_id: uuid.nullish(),
  privacy: z
    .enum(["public", "local", "private", "sensitive"])
    .default("private"),
};

const avatarCueSchema = z
  .object({
    cue_id: uuid,
    generation_id: uuid.nullish(),
    kind: z.enum([
      "state",
      "expression",
      "motion",
      "gaze",
      "speech",
      "override",
    ]),
    name: z.string().min(1),
    intensity: z.number().min(0).max(1).default(1),
    start_anchor: z.string().default("immediate"),
    duration_ms: z.number().int().nonnegative().nullish(),
    priority: z.number().int().min(0).max(100).default(50),
    interruptible: z.boolean().default(true),
    metadata: z.record(z.string(), z.unknown()).default({}),
  })
  .passthrough();

const avatarCapabilityManifestSchema = z
  .object({
    avatar_id: z.string().min(1),
    renderer_kind: z.string().min(1),
    states: z.array(z.string()).default([]),
    expressions: z.array(z.string()).default([]),
    motions: z.array(z.string()).default([]),
    gaze_targets: z.array(z.string()).default([]),
    hit_areas: z.array(z.string()).default([]),
    supports_lipsync: z.boolean().default(false),
  })
  .passthrough();

const avatarInteractionEventSchema = z
  .object({
    interaction_id: uuid,
    avatar_id: z.string().min(1),
    kind: z.enum(["pointer", "touch", "gaze", "drag", "system"]),
    target: z.string().nullish(),
    x: z.number().nullish(),
    y: z.number().nullish(),
    metadata: z.record(z.string(), z.unknown()).default({}),
  })
  .passthrough();

const strongEventEnvelopeSchema = z.discriminatedUnion("event_type", [
  z
    .object({
      ...eventBase,
      event_type: z.literal("session.created"),
      payload: z.object({ character_id: z.string().min(1) }).passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("session.data_reset"),
      payload: z
        .object({
          character_id: z.string().min(1),
          user_scope: z.string().min(1),
          conversation: z.literal("current_session"),
          audio: z.literal("current_session"),
          memory: z.literal("current_character_user"),
          character_state: z.literal("current_character_user"),
        })
        .passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("user.turn_committed"),
      payload: z.object({ text: z.string().min(1).max(20_000) }).passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("user.speech_started"),
      payload: z
        .object({
          utterance_id: uuid,
          audio_stream_id: uuid,
          sample_rate: z.number().int().min(8_000).max(48_000),
          channels: z.number().int().min(1).max(2),
        })
        .passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("user.speech_stopped"),
      payload: z
        .object({
          utterance_id: uuid,
          audio_stream_id: uuid,
          duration_ms: z.number().int().nonnegative(),
          audio_bytes: z.number().int().nonnegative(),
        })
        .passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.enum(["user.transcript_partial", "user.transcript_final"]),
      payload: z
        .object({
          utterance_id: uuid,
          text: z.string().min(1).max(20_000),
          language: z.string().min(2).max(32).nullish(),
          provider: z.string().min(1).max(128),
          is_final: z.boolean(),
        })
        .passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("assistant.generation_started"),
      payload: z.object({ backend_kind: z.string().min(1) }).passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("assistant.text_delta"),
      payload: z.object({ text: z.string().min(1).max(20_000) }).passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("assistant.generation_completed"),
      payload: z
        .object({
          text: z.string().max(100_000),
          assistant_turn_id: uuid,
        })
        .passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.enum([
        "assistant.generation_cancelled",
        "conversation.interrupted",
      ]),
      payload: z.object({ reason: z.string().min(1).max(1_000) }).passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.enum([
        "assistant.playback_started",
        "assistant.playback_progress",
      ]),
      payload: z
        .object({
          stream_id: uuid,
          segment_id: uuid,
          played_pts_ms: z.number().int().nonnegative(),
          buffered_ms: z.number().int().nonnegative(),
          client_clock_ms: z.number().int().nonnegative(),
          transport: z.enum(["audio_element", "webrtc"]),
        })
        .passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("assistant.playback_stopped"),
      payload: z
        .object({
          stream_id: uuid,
          segment_id: uuid,
          played_pts_ms: z.number().int().nonnegative(),
          buffered_ms: z.number().int().nonnegative(),
          client_clock_ms: z.number().int().nonnegative(),
          transport: z.enum(["audio_element", "webrtc"]),
          reason: z.enum(["ended", "interrupted", "error", "queue_cleared"]),
          completed: z.boolean(),
        })
        .passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("assistant.spoken_text_committed"),
      payload: z
        .object({
          stream_id: uuid,
          segment_id: uuid,
          text: z.string().min(1).max(20_000),
          spoken_text: z.string().min(1).max(100_000),
        })
        .passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("avatar.cue_emitted"),
      payload: z.object({ cue: avatarCueSchema }).passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...eventBase,
      event_type: z.literal("system.error_raised"),
      payload: z
        .object({
          error: z
            .object({
              code: z.string().min(1),
              message: z.string(),
              retryable: z.boolean(),
              component: z.string().min(1),
              details: z.record(z.string(), z.unknown()).default({}),
              correlation_id: uuid.nullish(),
            })
            .passthrough(),
        })
        .passthrough(),
    })
    .passthrough(),
]);

const genericCoreEventTypes = [
  "system.runtime_started",
  "system.runtime_stopping",
  "system.component_health_changed",
  "session.closed",
  "session.state_changed",
  "user.speech_progress",
  "assistant.text_segment_committed",
  "assistant.audio_stream_started",
  "assistant.audio_chunk_queued",
  "conversation.interruption_requested",
  "conversation.recovered",
  "skill.discovered",
  "skill.activated",
  "skill.run_started",
  "skill.progress",
  "skill.confirmation_requested",
  "skill.run_completed",
  "skill.run_failed",
  "skill.run_cancelled",
  "skill.run_expired",
  "tool.call_started",
  "tool.call_completed",
  "tool.call_failed",
  "memory.proposed",
  "memory.committed",
  "memory.superseded",
  "memory.tombstoned",
  "memory.recalled",
  "memory.extraction_completed",
  "character.state_changed",
  "character.response_planned",
  "character.prompt_compiled",
  "relationship.state_changed",
  "avatar.interaction_received",
  "model.route_selected",
  "model.worker_loaded",
  "model.worker_unloaded",
  "model.fallback_triggered",
  "voice.wake_detected",
  "voice.utterance_ignored",
  "companion.proactive_triggered",
  "companion.proactive_deferred",
  "channel.delivery_acknowledged",
  "channel.delivery_plan_created",
  "channel.delivery_part_claimed",
  "channel.delivery_part_acknowledged",
  "channel.delivery_part_delivered",
  "channel.delivery_part_failed",
  "channel.delivery_plan_completed",
  "channel.delivery_plan_cancel_requested",
  "channel.delivery_plan_cancelled",
  "channel.delivery_plan_failed",
  "channel.turn_failed",
  "channel.turn_cancelled",
  "resource.models_slept",
  "resource.models_woke",
] as const;

const genericCoreEventSchema = z
  .object({
    ...eventBase,
    event_type: z.enum(genericCoreEventTypes),
    payload: z.record(z.string(), z.unknown()),
  })
  .passthrough();

const eventEnvelopeSchema = z.union([
  strongEventEnvelopeSchema,
  genericCoreEventSchema,
]);

const commandBase = {
  command_id: uuid,
  schema_version: schemaVersion,
  issued_at: awareDateTime,
  issuer: z.string().min(1),
  session_id: uuid.nullish(),
  turn_id: uuid.nullish(),
  generation_id: uuid.nullish(),
  correlation_id: uuid.nullish(),
  expected_revision: z.number().int().nonnegative().nullish(),
};

const commandEnvelopeSchema = z.discriminatedUnion("command_type", [
  z
    .object({
      ...commandBase,
      command_type: z.literal("cmd.session.start"),
      payload: z.object({ character_id: z.string().min(1) }).passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...commandBase,
      command_type: z.literal("cmd.text.send"),
      payload: z.object({ text: z.string().min(1).max(20_000) }).passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...commandBase,
      command_type: z.literal("cmd.conversation.interrupt"),
      payload: z.object({ reason: z.string().min(1) }).passthrough(),
    })
    .passthrough(),
  z
    .object({
      ...commandBase,
      command_type: z.literal("cmd.playback.ack"),
      payload: z
        .object({
          phase: z.enum(["started", "progress", "stopped", "queue_cleared"]),
          stream_id: uuid,
          segment_id: uuid,
          played_pts_ms: z.number().int().nonnegative(),
          buffered_ms: z.number().int().nonnegative(),
          client_clock_ms: z.number().int().nonnegative(),
          transport: z.enum(["audio_element", "webrtc"]),
          reason: z
            .enum(["ended", "interrupted", "error", "queue_cleared"])
            .nullish(),
        })
        .passthrough(),
    })
    .passthrough(),
]);

const audioFrameHeaderSchema = z
  .object({
    stream_id: uuid,
    generation_id: uuid.nullish(),
    sequence: z.number().int().nonnegative(),
    pts_ms: z.number().int().nonnegative(),
    duration_ms: z.number().int().positive().max(10_000),
    codec: z.enum(["pcm_s16le", "opus"]),
    sample_rate: z.number().int().positive().max(384_000),
    channels: z.number().int().positive().max(8),
    byte_length: z.number().int().positive().max(16_777_216),
    end_of_stream: z.boolean().default(false),
  })
  .passthrough();

const MAX_MEDIA_HEADER_BYTES = 16_384;

// HTTP control-plane responses reuse the generated domain types, but still
// need a runtime validator at the browser boundary. These schemas deliberately
// accept additive v1 fields while rejecting malformed identity/state fields.
const sessionSnapshotSchema = z
  .object({
    session_id: uuid,
    character_id: z.string().min(1),
    state: z.enum([
      "created",
      "connecting",
      "ready",
      "degraded",
      "recovering",
      "closing",
      "closed",
    ]),
    conversation_state: z.enum([
      "idle",
      "listening",
      "committing_user_turn",
      "planning",
      "generating",
      "speaking",
      "interrupting",
      "recovering",
    ]),
    revision: z.number().int().nonnegative(),
    created_at: awareDateTime,
    updated_at: awareDateTime,
  })
  .passthrough();

const characterKernelSnapshotSchema = z
  .object({
    character_id: z.string().min(1),
    user_scope: z.string().min(1),
    revision: z.number().int().nonnegative(),
    affect: z
      .object({
        valence: z.number().min(-1).max(1).default(0),
        arousal: z.number().min(0).max(1).default(0),
        energy: z.number().min(0).max(1).default(0),
        attention: z.number().min(0).max(1).default(0),
        embarrassment: z.number().min(0).max(1).default(0),
        tension: z.number().min(0).max(1).default(0),
        updated_at: awareDateTime,
      })
      .passthrough(),
    relationship: z
      .object({
        familiarity: z.number().min(0).max(1).default(0),
        trust: z.number().min(0).max(1).default(0),
        affinity: z.number().min(0).max(1).default(0),
        comfort: z.number().min(0).max(1).default(0),
        recent_tension: z.number().min(0).max(1).default(0),
        interaction_count: z.number().int().nonnegative().default(0),
        stage: z
          .enum(["acquaintance", "familiar", "trusted", "close"])
          .default("acquaintance"),
        preferred_address: z.string().nullish(),
        updated_at: awareDateTime,
      })
      .passthrough(),
  })
  .passthrough();

const memoryRecordSchema = z
  .object({
    memory_id: uuid,
    namespace: z.string().min(1),
    kind: z.enum([
      "core",
      "semantic.fact",
      "semantic.preference",
      "episodic.shared_event",
      "procedural.preference",
      "relationship.signal",
      "prospective.commitment",
      "character.self",
    ]),
    text: z.string().min(1),
    subject_id: z.string().min(1).nullish(),
    predicate: z.string().nullish(),
    value: z.json().nullish(),
    confidence: z.number().min(0).max(1),
    importance: z.number().min(0).max(1),
    sensitivity: z
      .enum(["public", "local", "private", "sensitive"])
      .default("private"),
    state: z
      .enum(["active", "superseded", "contradicted", "tombstoned"])
      .default("active"),
    observed_at: awareDateTime,
    valid_from: awareDateTime.nullish(),
    valid_to: awareDateTime.nullish(),
    source_event_ids: z.array(uuid).min(1),
    supersedes: uuid.nullish(),
    pinned: z.boolean().default(false),
    created_at: awareDateTime,
    updated_at: awareDateTime,
  })
  .passthrough();

const memoryDraftSchema = memoryRecordSchema.omit({
  memory_id: true,
  state: true,
  source_event_ids: true,
  supersedes: true,
  pinned: true,
  created_at: true,
  updated_at: true,
});

const memoryProposalSchema = z
  .object({
    proposal_id: uuid,
    operation: z.enum([
      "add",
      "update",
      "supersede",
      "contradict",
      "forget",
      "ignore",
    ]),
    candidate: memoryDraftSchema.nullish(),
    target_memory_id: uuid.nullish(),
    confidence: z.number().min(0).max(1),
    rationale: z.string(),
    evidence_event_ids: z.array(uuid).min(1),
    status: z
      .enum(["pending", "accepted", "rejected", "ignored"])
      .default("pending"),
    created_at: awareDateTime,
    decided_at: awareDateTime.nullish(),
  })
  .passthrough();

const memoryChannelAttributionSchema = z
  .object({
    schema_version: z.literal("1.0").default("1.0"),
    provider_id: z.string().regex(/^[a-z0-9][a-z0-9._-]{1,127}$/),
    connection_id: uuid,
    account_key: z.string().min(1).max(512).nullish(),
    principal_scope: z.string().min(1).max(256),
    chat_type: z.enum(["direct", "group"]),
    conversation_key: z.string().min(1).max(512),
    sender_key: z.string().min(1).max(512),
    received_at: awareDateTime,
    conversation_label: z.string().min(1).max(256).nullish(),
    sender_display_name: z.string().min(1).max(256).nullish(),
  })
  .passthrough();

const memorySourceSchema = z
  .object({
    source_id: uuid,
    memory_id: uuid,
    source_event_id: uuid,
    session_id: uuid,
    turn_id: uuid.nullish(),
    source_kind: z.enum([
      "user_turn",
      "assistant_spoken",
      "memory_management",
      "migration",
    ]),
    created_at: awareDateTime,
    channel_attribution: memoryChannelAttributionSchema.nullish(),
  })
  .passthrough();

const structuredErrorSchema = z
  .object({
    code: z.string().min(1),
    message: z.string(),
    retryable: z.boolean(),
    component: z.string().min(1),
    details: z.record(z.string(), z.unknown()).default({}),
    correlation_id: uuid.nullish(),
  })
  .passthrough();

const channelSchemaVersion = z.literal("1.0");
const channelTurnStatusSchema = z.enum([
  "accepted",
  "processing",
  "completed",
  "cancelling",
  "cancelled",
  "failed",
  "timed_out",
]);
const channelDeliveryStatusSchema = z.enum([
  "pending",
  "sending",
  "delivered",
  "failed",
  "cancelled",
]);
const channelProviderCapabilitiesSchema = z
  .object({
    authorization_methods: z.array(z.literal("qr_code")).max(8).default([]),
    chat_types: z
      .array(z.enum(["direct", "group"]))
      .min(1)
      .default(["direct"]),
    inbound_message_kinds: z.array(z.literal("text")).min(1).default(["text"]),
    outbound_message_kinds: z.array(z.literal("text")).min(1).default(["text"]),
    supports_typing: z.boolean().default(false),
    supports_partial_replies: z.boolean().default(false),
    supports_delivery_ack: z.boolean().default(true),
    supports_cancellation: z.boolean().default(true),
    supports_proactive_messages: z.boolean().default(false),
    max_text_chars: z.number().int().min(1).max(1_000_000).default(20_000),
  })
  .passthrough();

const channelProviderRegistrationSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    provider_id: z.string().regex(/^[a-z0-9][a-z0-9._-]{1,127}$/),
    version: z.string().regex(/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/),
    name: z.string().min(1).max(128),
    description: z.string().min(1).max(2_000),
    capabilities: channelProviderCapabilitiesSchema.default(() =>
      channelProviderCapabilitiesSchema.parse({}),
    ),
  })
  .passthrough();

const channelConnectionConfigurationSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    connection_id: uuid,
    provider_id: z.string().regex(/^[a-z0-9][a-z0-9._-]{1,127}$/),
    name: z.string().min(1).max(128),
    character_id: z.string().min(1).max(256),
    principal_scope: z.string().min(1).max(256),
    account_key: z.string().min(1).max(512).nullish(),
    allowed_sender_keys: z
      .array(z.string().min(1).max(512))
      .max(64)
      .default([]),
    enabled: z.boolean().default(true),
    timeout_seconds: z.number().positive().max(600).default(120),
  })
  .passthrough();

const channelConnectionSnapshotSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    configuration: channelConnectionConfigurationSchema,
    revision: z.number().int().min(1),
    status: z
      .enum(["untested", "ready", "degraded", "error", "disabled"])
      .default("untested"),
    capabilities: channelProviderCapabilitiesSchema.default(() =>
      channelProviderCapabilitiesSchema.parse({}),
    ),
    last_error: structuredErrorSchema.nullish(),
    last_seen_at: awareDateTime.nullish(),
    created_at: awareDateTime,
    updated_at: awareDateTime,
  })
  .passthrough();

const channelAuthorizationStartRequestSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    provider_id: z.string().regex(/^[a-z0-9][a-z0-9._-]{1,127}$/),
    method: z.literal("qr_code").default("qr_code"),
    character_id: z.string().min(1).max(256),
    connection_name: z.string().min(1).max(128).nullish(),
    principal_scope: z.string().min(1).max(256).default("local"),
  })
  .passthrough();

const channelAuthorizationVerificationRequestSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    verification_code: z
      .string()
      .min(1)
      .max(32)
      .regex(/^[0-9A-Za-z-]+$/),
  })
  .passthrough();

const channelAuthorizationSnapshotSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    auth_session_id: uuid,
    provider_id: z.string().regex(/^[a-z0-9][a-z0-9._-]{1,127}$/),
    method: z.literal("qr_code").default("qr_code"),
    status: z.enum([
      "pending",
      "scanned",
      "verification_required",
      "confirmed",
      "expired",
      "cancelled",
      "failed",
    ]),
    qr_code_content: z.string().min(1).max(8_192).nullish(),
    verification_required: z.boolean().default(false),
    connection: channelConnectionSnapshotSchema.nullish(),
    error: structuredErrorSchema.nullish(),
    status_message: z.string().min(1).max(1_000).nullish(),
    poll_after_ms: z.number().int().nonnegative().max(60_000).nullish(),
    expires_at: awareDateTime,
    created_at: awareDateTime,
    updated_at: awareDateTime,
  })
  .passthrough()
  .superRefine((snapshot, context) => {
    const activeStatuses = new Set([
      "pending",
      "scanned",
      "verification_required",
    ]);
    if (activeStatuses.has(snapshot.status) && !snapshot.qr_code_content) {
      context.addIssue({
        code: "custom",
        message: "active QR authorization snapshots require qr_code_content",
        path: ["qr_code_content"],
      });
    }
    if (
      snapshot.verification_required !==
      (snapshot.status === "verification_required")
    ) {
      context.addIssue({
        code: "custom",
        message:
          "verification_required must match the verification_required status",
        path: ["verification_required"],
      });
    }
    if (snapshot.status === "confirmed" && !snapshot.connection) {
      context.addIssue({
        code: "custom",
        message: "confirmed authorization snapshots require a connection",
        path: ["connection"],
      });
    }
    if (snapshot.status === "failed" && !snapshot.error) {
      context.addIssue({
        code: "custom",
        message: "failed authorization snapshots require an error",
        path: ["error"],
      });
    }
    if (snapshot.status !== "failed" && snapshot.error) {
      context.addIssue({
        code: "custom",
        message: "only failed authorization snapshots may include an error",
        path: ["error"],
      });
    }
  });

const channelGatewayStatusSnapshotSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    status: z.enum(["ready", "degraded", "error"]),
    provider_count: z.number().int().nonnegative(),
    enabled_connection_count: z.number().int().nonnegative(),
    checked_at: awareDateTime,
  })
  .passthrough();

const channelInboundTextMessageSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    connection_id: uuid,
    account_key: z.string().min(1).max(512).nullish(),
    external_message_id: z.string().min(1).max(512),
    conversation_key: z.string().min(1).max(512),
    sender_key: z.string().min(1).max(512),
    principal_scope: z.string().min(1).max(256),
    chat_type: z.enum(["direct", "group"]).default("direct"),
    kind: z.literal("text").default("text"),
    text: z.string().min(1).max(20_000),
    conversation_label: z.string().min(1).max(256).nullish(),
    sender_display_name: z.string().min(1).max(256).nullish(),
    received_at: awareDateTime,
    reply_to_external_message_id: z.string().min(1).max(512).nullish(),
  })
  .passthrough();

const channelTurnReceiptSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    channel_turn_id: uuid,
    connection_id: uuid,
    account_key: z.string().min(1).max(512).nullish(),
    external_message_id: z.string().min(1).max(512),
    conversation_key: z.string().min(1).max(512),
    sender_key: z.string().min(1).max(512),
    principal_scope: z.string().min(1).max(256),
    chat_type: z.enum(["direct", "group"]).default("direct"),
    conversation_label: z.string().min(1).max(256).nullish(),
    sender_display_name: z.string().min(1).max(256).nullish(),
    session_id: uuid,
    turn_id: uuid,
    generation_id: uuid,
    status: channelTurnStatusSchema,
    duplicate: z.boolean().default(false),
    revision: z.number().int().nonnegative(),
    accepted_at: awareDateTime,
    poll_after_ms: z.number().int().nonnegative().max(60_000).nullish(),
  })
  .passthrough();

const channelTurnSnapshotSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    channel_turn_id: uuid,
    connection_id: uuid,
    account_key: z.string().min(1).max(512).nullish(),
    external_message_id: z.string().min(1).max(512),
    conversation_key: z.string().min(1).max(512),
    sender_key: z.string().min(1).max(512),
    principal_scope: z.string().min(1).max(256),
    chat_type: z.enum(["direct", "group"]).default("direct"),
    conversation_label: z.string().min(1).max(256).nullish(),
    sender_display_name: z.string().min(1).max(256).nullish(),
    session_id: uuid,
    turn_id: uuid,
    generation_id: uuid,
    status: channelTurnStatusSchema,
    reply_text: z.string().max(100_000).nullish(),
    delivery_id: uuid.nullish(),
    delivery_status: channelDeliveryStatusSchema.nullish(),
    error: structuredErrorSchema.nullish(),
    revision: z.number().int().nonnegative(),
    created_at: awareDateTime,
    updated_at: awareDateTime,
    completed_at: awareDateTime.nullish(),
  })
  .passthrough();

const channelDeliveryAcknowledgementSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    delivery_id: uuid,
    channel_turn_id: uuid,
    lease_id: uuid,
    status: z.enum(["delivered", "failed", "cancelled"]),
    provider_message_id: z.string().min(1).max(512).nullish(),
    error: structuredErrorSchema.nullish(),
    acknowledged_at: awareDateTime,
  })
  .passthrough()
  .superRefine((acknowledgement, context) => {
    if (acknowledgement.status === "delivered" && acknowledgement.error) {
      context.addIssue({
        code: "custom",
        message: "delivered acknowledgements cannot include an error",
        path: ["error"],
      });
    }
    if (acknowledgement.status === "failed" && !acknowledgement.error) {
      context.addIssue({
        code: "custom",
        message: "failed acknowledgements require an error",
        path: ["error"],
      });
    }
  });

const channelDeliveryClaimRequestSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    delivery_id: uuid,
    channel_turn_id: uuid,
    lease_id: uuid,
    lease_seconds: z.number().int().min(5).max(300).default(60),
  })
  .passthrough();

const channelDeliverySnapshotSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    delivery_id: uuid,
    channel_turn_id: uuid,
    connection_id: uuid,
    status: channelDeliveryStatusSchema,
    attempt: z.number().int().min(1).default(1),
    lease_id: uuid.nullish(),
    lease_expires_at: awareDateTime.nullish(),
    provider_message_id: z.string().min(1).max(512).nullish(),
    last_error: structuredErrorSchema.nullish(),
    plan_version: z.number().int().min(1).default(1),
    part_count: z.number().int().min(1).default(1),
    delivered_part_count: z.number().int().nonnegative().default(0),
    cancel_requested_at: awareDateTime.nullish(),
    created_at: awareDateTime,
    updated_at: awareDateTime,
    delivered_at: awareDateTime.nullish(),
  })
  .passthrough()
  .superRefine((snapshot, context) => {
    if (
      snapshot.status === "sending" &&
      (!snapshot.lease_id || !snapshot.lease_expires_at)
    ) {
      context.addIssue({
        code: "custom",
        message: "sending delivery snapshots require an active lease",
        path: ["lease_id"],
      });
    }
  });

const channelDeliveryPartKindSchema = z.enum(["text", "image"]);

const channelDeliveryPartStatusSchema = z.enum([
  "pending",
  "sending",
  "delivered",
  "failed",
  "cancelled",
  "skipped",
]);

const channelTextDeliveryPartPayloadSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    kind: z.literal("text").default("text"),
    text: z.string().min(1).max(20_000),
  })
  .passthrough();

const channelDeliveryPartPayloadSchema = channelTextDeliveryPartPayloadSchema;

const channelDeliveryPartSnapshotSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    part_id: uuid,
    delivery_id: uuid,
    ordinal: z.number().int().nonnegative(),
    kind: channelDeliveryPartKindSchema.default("text"),
    payload: channelDeliveryPartPayloadSchema,
    required: z.boolean().default(true),
    status: channelDeliveryPartStatusSchema,
    delay_after_ms: z.number().int().min(0).max(60_000).default(0),
    not_before_at: awareDateTime.nullish(),
    attempt: z.number().int().nonnegative().default(0),
    lease_id: uuid.nullish(),
    lease_expires_at: awareDateTime.nullish(),
    provider_client_id: z.string().min(1).max(512),
    provider_message_id: z.string().min(1).max(512).nullish(),
    last_error: structuredErrorSchema.nullish(),
    created_at: awareDateTime,
    updated_at: awareDateTime,
    delivered_at: awareDateTime.nullish(),
  })
  .passthrough()
  .superRefine((snapshot, context) => {
    if (
      snapshot.status === "sending" &&
      (!snapshot.lease_id || !snapshot.lease_expires_at)
    ) {
      context.addIssue({
        code: "custom",
        message: "sending delivery part snapshots require an active lease",
        path: ["lease_id"],
      });
    }
  });

const channelDeliveryPartClaimRequestSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    delivery_id: uuid,
    part_id: uuid.nullish(),
    lease_id: uuid,
    lease_seconds: z.number().int().min(5).max(300).default(60),
  })
  .passthrough();

const channelDeliveryPartAcknowledgementSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    delivery_id: uuid,
    part_id: uuid,
    lease_id: uuid,
    status: z.enum(["delivered", "failed", "cancelled"]),
    provider_message_id: z.string().min(1).max(512).nullish(),
    error: structuredErrorSchema.nullish(),
    acknowledged_at: awareDateTime,
  })
  .passthrough()
  .superRefine((acknowledgement, context) => {
    if (acknowledgement.status === "delivered" && acknowledgement.error) {
      context.addIssue({
        code: "custom",
        message: "delivered acknowledgements cannot include an error",
        path: ["error"],
      });
    }
    if (acknowledgement.status === "failed" && !acknowledgement.error) {
      context.addIssue({
        code: "custom",
        message: "failed acknowledgements require an error",
        path: ["error"],
      });
    }
  });

const channelDeliveryPlanSnapshotSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    delivery_id: uuid,
    channel_turn_id: uuid,
    connection_id: uuid,
    status: channelDeliveryStatusSchema,
    plan_version: z.number().int().min(1).default(1),
    part_count: z.number().int().min(1),
    delivered_part_count: z.number().int().nonnegative().default(0),
    next_pending_ordinal: z.number().int().nonnegative().nullish(),
    cancel_requested_at: awareDateTime.nullish(),
    parts: z.array(channelDeliveryPartSnapshotSchema).default([]),
    created_at: awareDateTime,
    updated_at: awareDateTime,
    delivered_at: awareDateTime.nullish(),
  })
  .passthrough();

const channelTurnCancelRequestSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    reason: z.string().min(1).max(1_000),
    requested_at: awareDateTime,
  })
  .passthrough();

const channelTurnCancelReceiptSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    channel_turn_id: uuid,
    accepted: z.boolean(),
    status: channelTurnStatusSchema,
    revision: z.number().int().nonnegative(),
    acknowledged_at: awareDateTime,
  })
  .passthrough();

const channelErrorResponseSchema = z
  .object({
    schema_version: channelSchemaVersion.default("1.0"),
    error: structuredErrorSchema,
    channel_turn_id: uuid.nullish(),
    external_message_id: z.string().min(1).max(512).nullish(),
    retry_after_ms: z.number().int().nonnegative().max(600_000).nullish(),
  })
  .passthrough();

const skillCapabilitySchema = z
  .object({
    name: z.string().min(1),
    description: z.string(),
    input_schema: z.record(z.string(), z.unknown()),
    output_schema: z.record(z.string(), z.unknown()),
    required_permissions: z.array(z.string()).default([]),
    side_effect: z
      .enum([
        "read",
        "write",
        "destructive",
        "external_communication",
        "device_control",
      ])
      .default("read"),
    confirmation_required: z.boolean().default(false),
    timeout_seconds: z.number().positive().max(600).default(30),
    adapter_tool: z.string().nullish(),
  })
  .passthrough();

const skillDefinitionSchema = z
  .object({
    skill_id: z.string().min(1),
    version: z.string().min(1),
    name: z.string().min(1),
    description: z.string(),
    enabled: z.boolean().default(true),
    source: z.enum(["builtin", "plugin", "mcp_connection"]).default("builtin"),
    plugin_id: z.string().nullish(),
    mcp_connection_id: uuid.nullish(),
    capabilities: z.array(skillCapabilitySchema).default([]),
    interruptible: z.boolean().default(true),
    background_allowed: z.boolean().default(false),
  })
  .passthrough();

const skillResultSchema = z
  .object({
    status: z.string(),
    data: z.json().nullish(),
    spoken_summary: z.string().nullish(),
    ui_cards: z.array(z.record(z.string(), z.unknown())).default([]),
    avatar_cues: z.array(avatarCueSchema).default([]),
    memory_proposal_ids: z.array(uuid).default([]),
    prospective_task_ids: z.array(uuid).default([]),
    provenance: z.array(z.string()).default([]),
  })
  .passthrough();

const skillRunSnapshotSchema = z
  .object({
    skill_run_id: uuid,
    session_id: uuid,
    skill_id: z.string().min(1),
    skill_version: z.string().min(1),
    capability: z.string().min(1),
    state: z.enum([
      "created",
      "activating",
      "running",
      "waiting_for_tool",
      "waiting_for_confirmation",
      "paused",
      "succeeded",
      "failed",
      "cancelling",
      "cancelled",
      "expired",
    ]),
    progress: z.number().min(0).max(1).nullish(),
    result: skillResultSchema.nullish(),
    error: structuredErrorSchema.nullish(),
    confirmation_request_id: uuid.nullish(),
    plugin_id: z.string().nullish(),
    mcp_connection_id: uuid.nullish(),
    turn_id: uuid.nullish(),
    generation_id: uuid.nullish(),
    origin: z.enum(["manual", "agent", "external_mcp"]).default("manual"),
    provider_tool_call_id: z.string().max(256).nullish(),
    created_at: awareDateTime,
    updated_at: awareDateTime,
    started_at: awareDateTime.nullish(),
    completed_at: awareDateTime.nullish(),
  })
  .passthrough();

const pluginSnapshotSchema = z
  .object({
    plugin_id: z.string().min(1),
    version: z.string().min(1),
    name: z.string().min(1),
    description: z.string(),
    install_path: z.string().min(1),
    enabled: z.boolean(),
    trust_level: z.enum(["trusted", "untrusted"]).default("untrusted"),
    sandbox_mode: z
      .enum(["required", "preferred", "disabled"])
      .default("required"),
    network_policy: z.enum(["deny", "loopback", "allow"]).default("deny"),
    sandbox_backend: z.string().nullish(),
    installed_at: awareDateTime,
    updated_at: awareDateTime,
  })
  .passthrough();

const mcpToolSchema = z
  .object({
    name: z.string().min(1),
    title: z.string().nullish(),
    description: z.string().nullish(),
    input_schema: z.record(z.string(), z.unknown()).default({}),
    output_schema: z.record(z.string(), z.unknown()).nullish(),
  })
  .passthrough();

const mcpResourceSchema = z
  .object({
    uri: z.string().min(1),
    name: z.string().min(1),
    title: z.string().nullish(),
    description: z.string().nullish(),
    mime_type: z.string().nullish(),
  })
  .passthrough();

const mcpResourceTemplateSchema = z
  .object({
    uri_template: z.string().min(1),
    name: z.string().min(1),
    title: z.string().nullish(),
    description: z.string().nullish(),
    mime_type: z.string().nullish(),
  })
  .passthrough();

const mcpPromptSchema = z
  .object({
    name: z.string().min(1),
    title: z.string().nullish(),
    description: z.string().nullish(),
    arguments: z
      .array(
        z
          .object({
            name: z.string().min(1),
            description: z.string().nullish(),
            required: z.boolean().default(false),
          })
          .passthrough(),
      )
      .default([]),
  })
  .passthrough();

const mcpCapabilitySnapshotSchema = z
  .object({
    connection_id: uuid,
    protocol_version: z.string().nullish(),
    server_name: z.string().nullish(),
    server_version: z.string().nullish(),
    tools: z.array(mcpToolSchema).default([]),
    resources: z.array(mcpResourceSchema).default([]),
    resource_templates: z.array(mcpResourceTemplateSchema).default([]),
    prompts: z.array(mcpPromptSchema).default([]),
    discovered_at: awareDateTime.nullish(),
  })
  .passthrough();

const mcpConnectionSnapshotSchema = z
  .object({
    connection_id: uuid,
    name: z.string().min(1),
    transport: z.enum(["stdio", "streamable_http", "sse"]),
    command: z.array(z.string()).default([]),
    url: z.string().nullish(),
    allow_remote: z.boolean().default(false),
    enabled: z.boolean().default(true),
    timeout_seconds: z.number().positive().max(600).default(30),
    trust_level: z.enum(["trusted", "untrusted"]).default("untrusted"),
    sandbox_mode: z
      .enum(["required", "preferred", "disabled"])
      .default("required"),
    network_policy: z.enum(["deny", "loopback", "allow"]).default("deny"),
    status: z
      .enum(["untested", "ready", "error", "disabled"])
      .default("untested"),
    bearer_token_configured: z.boolean().default(false),
    sandbox_backend: z.string().nullish(),
    capabilities: mcpCapabilitySnapshotSchema,
    last_error: z.string().nullish(),
    last_tested_at: awareDateTime.nullish(),
    created_at: awareDateTime,
    updated_at: awareDateTime,
  })
  .passthrough();

export function parseEventEnvelope(input: unknown): DomainEvent {
  return eventEnvelopeSchema.parse(input) as DomainEvent;
}

export function parseCommandEnvelope(input: unknown): DomainCommand {
  return commandEnvelopeSchema.parse(input) as DomainCommand;
}

export function parseAudioFrameHeader(input: unknown): AudioFrameHeader {
  return audioFrameHeaderSchema.parse(input) as AudioFrameHeader;
}

export function parseAvatarCue(input: unknown): AvatarCue {
  return avatarCueSchema.parse(input) as AvatarCue;
}

export function parseAvatarCapabilityManifest(
  input: unknown,
): AvatarCapabilityManifest {
  return avatarCapabilityManifestSchema.parse(
    input,
  ) as AvatarCapabilityManifest;
}

export function parseAvatarInteractionEvent(
  input: unknown,
): AvatarInteractionEvent {
  return avatarInteractionEventSchema.parse(input) as AvatarInteractionEvent;
}

export function parseSessionSnapshot(input: unknown): SessionSnapshot {
  return sessionSnapshotSchema.parse(input) as SessionSnapshot;
}

export function parseCharacterKernelSnapshot(
  input: unknown,
): CharacterKernelSnapshot {
  return characterKernelSnapshotSchema.parse(input) as CharacterKernelSnapshot;
}

export function parseChannelProviderRegistration(
  input: unknown,
): ChannelProviderRegistration {
  return channelProviderRegistrationSchema.parse(
    input,
  ) as ChannelProviderRegistration;
}

export function parseChannelAuthorizationStartRequest(
  input: unknown,
): ChannelAuthorizationStartRequest {
  return channelAuthorizationStartRequestSchema.parse(
    input,
  ) as ChannelAuthorizationStartRequest;
}

export function parseChannelAuthorizationVerificationRequest(
  input: unknown,
): ChannelAuthorizationVerificationRequest {
  return channelAuthorizationVerificationRequestSchema.parse(
    input,
  ) as ChannelAuthorizationVerificationRequest;
}

export function parseChannelAuthorizationSnapshot(
  input: unknown,
): ChannelAuthorizationSnapshot {
  return channelAuthorizationSnapshotSchema.parse(
    input,
  ) as ChannelAuthorizationSnapshot;
}

export function parseChannelConnectionConfiguration(
  input: unknown,
): ChannelConnectionConfiguration {
  return channelConnectionConfigurationSchema.parse(
    input,
  ) as ChannelConnectionConfiguration;
}

export function parseChannelConnectionSnapshot(
  input: unknown,
): ChannelConnectionSnapshot {
  return channelConnectionSnapshotSchema.parse(
    input,
  ) as ChannelConnectionSnapshot;
}

export function parseChannelGatewayStatusSnapshot(
  input: unknown,
): ChannelGatewayStatusSnapshot {
  return channelGatewayStatusSnapshotSchema.parse(
    input,
  ) as ChannelGatewayStatusSnapshot;
}

export function parseChannelInboundTextMessage(
  input: unknown,
): ChannelInboundTextMessage {
  return channelInboundTextMessageSchema.parse(
    input,
  ) as ChannelInboundTextMessage;
}

export function parseChannelTurnReceipt(input: unknown): ChannelTurnReceipt {
  return channelTurnReceiptSchema.parse(input) as ChannelTurnReceipt;
}

export function parseChannelTurnSnapshot(input: unknown): ChannelTurnSnapshot {
  return channelTurnSnapshotSchema.parse(input) as ChannelTurnSnapshot;
}

export function parseChannelDeliveryAcknowledgement(
  input: unknown,
): ChannelDeliveryAcknowledgement {
  return channelDeliveryAcknowledgementSchema.parse(
    input,
  ) as ChannelDeliveryAcknowledgement;
}

export function parseChannelDeliveryClaimRequest(
  input: unknown,
): ChannelDeliveryClaimRequest {
  return channelDeliveryClaimRequestSchema.parse(
    input,
  ) as ChannelDeliveryClaimRequest;
}

export function parseChannelDeliverySnapshot(
  input: unknown,
): ChannelDeliverySnapshot {
  return channelDeliverySnapshotSchema.parse(input) as ChannelDeliverySnapshot;
}

export function parseChannelDeliveryPartSnapshot(
  input: unknown,
): ChannelDeliveryPartSnapshot {
  return channelDeliveryPartSnapshotSchema.parse(
    input,
  ) as ChannelDeliveryPartSnapshot;
}

export function parseChannelDeliveryPartClaimRequest(
  input: unknown,
): ChannelDeliveryPartClaimRequest {
  return channelDeliveryPartClaimRequestSchema.parse(
    input,
  ) as ChannelDeliveryPartClaimRequest;
}

export function parseChannelDeliveryPartAcknowledgement(
  input: unknown,
): ChannelDeliveryPartAcknowledgement {
  return channelDeliveryPartAcknowledgementSchema.parse(
    input,
  ) as ChannelDeliveryPartAcknowledgement;
}

export function parseChannelDeliveryPlanSnapshot(
  input: unknown,
): ChannelDeliveryPlanSnapshot {
  return channelDeliveryPlanSnapshotSchema.parse(
    input,
  ) as ChannelDeliveryPlanSnapshot;
}

export function parseChannelTurnCancelRequest(
  input: unknown,
): ChannelTurnCancelRequest {
  return channelTurnCancelRequestSchema.parse(
    input,
  ) as ChannelTurnCancelRequest;
}

export function parseChannelTurnCancelReceipt(
  input: unknown,
): ChannelTurnCancelReceipt {
  return channelTurnCancelReceiptSchema.parse(
    input,
  ) as ChannelTurnCancelReceipt;
}

export function parseChannelErrorResponse(
  input: unknown,
): ChannelErrorResponse {
  return channelErrorResponseSchema.parse(input) as ChannelErrorResponse;
}

export function parseMemoryRecord(input: unknown): MemoryRecord {
  return memoryRecordSchema.parse(input) as MemoryRecord;
}

export function parseMemoryProposal(input: unknown): MemoryProposal {
  return memoryProposalSchema.parse(input) as MemoryProposal;
}

export function parseMemorySource(input: unknown): MemorySource {
  return memorySourceSchema.parse(input) as MemorySource;
}

export function parseMemoryChannelAttribution(
  input: unknown,
): MemoryChannelAttribution {
  return memoryChannelAttributionSchema.parse(
    input,
  ) as MemoryChannelAttribution;
}

export function parseSkillDefinition(input: unknown): SkillDefinition {
  return skillDefinitionSchema.parse(input) as SkillDefinition;
}

export function parseSkillRunSnapshot(input: unknown): SkillRunSnapshot {
  return skillRunSnapshotSchema.parse(input) as SkillRunSnapshot;
}

export function parsePluginSnapshot(input: unknown): PluginSnapshot {
  return pluginSnapshotSchema.parse(input) as PluginSnapshot;
}

export function parseMcpCapabilitySnapshot(
  input: unknown,
): McpCapabilitySnapshot {
  return mcpCapabilitySnapshotSchema.parse(input) as McpCapabilitySnapshot;
}

export function parseMcpConnectionSnapshot(
  input: unknown,
): McpConnectionSnapshot {
  return mcpConnectionSnapshotSchema.parse(input) as McpConnectionSnapshot;
}

export function encodeAudioFrameHeader(input: unknown): Uint8Array {
  const header = parseAudioFrameHeader(input);
  const encoded = new TextEncoder().encode(JSON.stringify(header));
  if (encoded.byteLength > MAX_MEDIA_HEADER_BYTES) {
    throw new Error("audio frame header exceeds size limit");
  }
  return encoded;
}

export function decodeAudioFrameHeader(encoded: Uint8Array): AudioFrameHeader {
  if (encoded.byteLength > MAX_MEDIA_HEADER_BYTES) {
    throw new Error("audio frame header exceeds size limit");
  }
  try {
    return parseAudioFrameHeader(JSON.parse(new TextDecoder().decode(encoded)));
  } catch (error: unknown) {
    throw new Error("invalid audio frame header", { cause: error });
  }
}
