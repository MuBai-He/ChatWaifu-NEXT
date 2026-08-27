import { z } from "zod";
import type {
  AudioFrameHeader,
  AvatarCapabilityManifest,
  AvatarCue,
  AvatarInteractionEvent,
  ProtocolCatalog,
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
  "assistant.text_delta",
  "assistant.text_segment_committed",
  "assistant.generation_cancelled",
  "assistant.generation_completed",
  "assistant.audio_stream_started",
  "assistant.audio_chunk_queued",
  "conversation.interruption_requested",
  "conversation.interrupted",
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
