import { z } from "zod";
import type {
  AudioFrameHeader,
  AvatarCapabilityManifest,
  AvatarCue,
  AvatarInteractionEvent,
  CharacterKernelSnapshot,
  McpCapabilitySnapshot,
  McpConnectionSnapshot,
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

export function parseMemoryRecord(input: unknown): MemoryRecord {
  return memoryRecordSchema.parse(input) as MemoryRecord;
}

export function parseMemoryProposal(input: unknown): MemoryProposal {
  return memoryProposalSchema.parse(input) as MemoryProposal;
}

export function parseMemorySource(input: unknown): MemorySource {
  return memorySourceSchema.parse(input) as MemorySource;
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
