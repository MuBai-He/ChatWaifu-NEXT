import { z } from "zod";
import type {
  InteractionTraceDetail,
  InteractionTracePage,
} from "@chatwaifu/protocol";

const uuid = z.string().uuid();
const timestamp = z.string().datetime({ offset: true });
const version = z.literal("1.0");
const code = z.string().max(64);
const nullableCode = code.nullable();

const summary = z.object({
  schema_version: version,
  interaction_id: uuid,
  session_id: uuid,
  turn_id: uuid.nullable(),
  generation_id: uuid.nullable(),
  occurred_at: timestamp,
  trigger: z.enum(["user", "proactive", "ignored_voice", "proactive_deferred"]),
  reason: nullableCode,
  generation_state: nullableCode,
});

const route = z.object({
  role: code,
  provider: code,
  model: z.string().min(1).max(256),
  endpoint_digest: z.string().nullable(),
  context_window: z.number().int().nullable(),
});

const identity = z.object({
  schema_version: version,
  identity_hash: z.string().length(64),
  character_id: z.string(),
  character_package_hash: z.string().length(64),
  prompt_template_version: z.string(),
  presentation_profile: z.string(),
  chat_route: route,
  memory_summary_route: route,
  tools_digest: z.string().min(16).max(64),
});

const budget = z.object({
  model_role: code,
  budget: z.number().int().nonnegative(),
  used: z.number().int().nonnegative(),
  safety_tokens: z.number().int().nonnegative(),
  persona_tokens: z.number().int().nonnegative(),
  state_tokens: z.number().int().nonnegative(),
  relationship_tokens: z.number().int().nonnegative(),
  memory_tokens: z.number().int().nonnegative(),
  scene_tokens: z.number().int().nonnegative(),
  conversation_tokens: z.number().int().nonnegative(),
  dropped_history_turns: z.number().int().nonnegative(),
});

const memoryReference = z.object({
  schema_version: version,
  memory_id: uuid,
  score: z.number().nullable(),
  selected_for_prompt: z.boolean(),
  currently_visible: z.boolean().nullable(),
});

const timelineItem = z.object({
  schema_version: version,
  sequence: z.number().int().nonnegative(),
  event_type: code,
  occurred_at: timestamp,
  reason: nullableCode,
  status: nullableCode,
});

const deliveryPart = z.object({
  schema_version: version,
  part_id: uuid,
  ordinal: z.number().int().nonnegative(),
  kind: code,
  required: z.boolean(),
  status: code,
  attempt: z.number().int().nonnegative(),
  delivered_at: timestamp.nullable(),
});

const playbackSegment = z.object({
  schema_version: version,
  segment_id: uuid,
  segment_index: z.number().int().nonnegative(),
  state: code,
  played_pts_ms: z.number().int().nonnegative(),
  transport: nullableCode,
});

const toolCall = z.object({
  schema_version: version,
  tool_call_id: uuid.nullable(),
  status: code,
  duration_ms: z.number().int().nonnegative().nullable(),
  error_code: nullableCode,
});

const page = z.object({
  schema_version: version,
  items: z.array(summary).max(50),
  has_more: z.boolean(),
  next_cursor: z.string().nullable(),
});

const detail = z.object({
  schema_version: version,
  summary,
  prompt_identity: identity.nullable(),
  response_plan: z
    .object({
      schema_version: version,
      intent: nullableCode,
      tone: nullableCode,
      expression: nullableCode,
      motion: nullableCode,
      response_length: nullableCode,
    })
    .nullable(),
  prompt_budget: budget.nullable(),
  memory_candidates: z.array(memoryReference).max(200),
  selected_memory_ids: z.array(uuid).max(200).nullable(),
  tool_calls: z.array(toolCall).max(200),
  delivery_status: nullableCode,
  delivery_parts: z.array(deliveryPart).max(200),
  playback_segments: z.array(playbackSegment).max(200),
  timeline: z.array(timelineItem).max(200),
  truncated: z.boolean(),
  next_cursor: z.number().int().nonnegative().nullable(),
});

export function parseInteractionTracePage(
  input: unknown,
): InteractionTracePage {
  return page.parse(input);
}

export function parseInteractionTraceDetail(
  input: unknown,
): InteractionTraceDetail {
  return detail.parse(input) as InteractionTraceDetail;
}
