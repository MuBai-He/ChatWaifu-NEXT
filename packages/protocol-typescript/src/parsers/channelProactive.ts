import { z } from "zod";
import type {
  ChannelOutboundIntentCancelRequest,
  ChannelOutboundIntentPage,
  ChannelOutboundIntentSnapshot,
  ChannelProactivePolicy,
  ChannelProactivePolicySnapshot,
  ChannelProactivePolicyUpdate,
  ChannelProactivePreview,
  StructuredError,
} from "../generated/domain";
import { channelDeliveryStatusSchema, structuredErrorSchema } from "./protocol";

const errorSchema = structuredErrorSchema as z.ZodType<StructuredError>;

const version = z.literal("1.0").default("1.0");
const uuid = z.string().uuid();
const dateTime = z.string().datetime({ offset: true });
const revision = z.number().int().nonnegative();
const quietTime = z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d$/);

export const channelProactiveReasonSchema = z.enum([
  "eligible",
  "disabled",
  "unsupported_provider",
  "connection_unavailable",
  "owner_binding_required",
  "no_owner_activity",
  "idle_threshold_not_reached",
  "idle_window_expired",
  "quiet_hours",
  "conversation_busy",
  "cooldown_active",
  "daily_budget_exhausted",
  "episode_already_reserved",
  "capacity_reached",
]);
export const channelOutboundIntentStatusSchema = z.enum([
  "pending",
  "generating",
  "planned",
  "settled",
]);

export const channelProactivePolicySchema = z
  .object({
    schema_version: version,
    enabled: z.boolean().default(false),
    source: z.literal("idle_check_in").default("idle_check_in"),
    timezone: z
      .string()
      .min(1)
      .max(128)
      .refine(isTimeZone)
      .default("Asia/Shanghai"),
    idle_minutes: z.number().int().min(1).max(1440).default(45),
    cooldown_minutes: z.number().int().min(1).max(10080).default(60),
    daily_budget: z.number().int().min(1).max(20).default(3),
    quiet_hours_enabled: z.boolean().default(true),
    quiet_start: quietTime.default("23:00"),
    quiet_end: quietTime.default("08:00"),
    ttl_minutes: z.number().int().min(1).max(60).default(15),
  })
  .strict();
export const channelProactivePolicyUpdateSchema = z
  .object({
    schema_version: version,
    expected_revision: revision,
    policy: channelProactivePolicySchema,
  })
  .strict();
export const channelProactivePolicySnapshotSchema = z.object({
  schema_version: version,
  connection_id: uuid,
  binding_id: uuid.nullish(),
  policy: channelProactivePolicySchema.default(() =>
    channelProactivePolicySchema.parse({}),
  ),
  revision: revision.default(0),
  updated_at: dateTime.nullish(),
});
export const channelProactivePreviewSchema = z.object({
  schema_version: version,
  connection_id: uuid,
  binding_id: uuid.nullish(),
  policy_revision: revision,
  eligible: z.boolean(),
  reason: channelProactiveReasonSchema,
  evaluated_at: dateTime,
  last_owner_at: dateTime.nullish(),
  next_eligible_at: dateTime.nullish(),
  expires_at: dateTime.nullish(),
  reserved_today: z.number().int().nonnegative().default(0),
  remaining_daily_budget: z.number().int().nonnegative().default(0),
  last_reserved_at: dateTime.nullish(),
  pending_request_id: uuid.nullish(),
});
export const channelOutboundIntentSnapshotSchema = z.object({
  schema_version: version,
  request_id: uuid,
  connection_id: uuid,
  binding_id: uuid,
  source: z.literal("idle_check_in").default("idle_check_in"),
  session_id: uuid,
  turn_id: uuid,
  generation_id: uuid,
  status: channelOutboundIntentStatusSchema,
  policy_revision: revision,
  route_revision: revision,
  revision,
  not_before_at: dateTime,
  expires_at: dateTime,
  created_at: dateTime,
  updated_at: dateTime,
  settled_at: dateTime.nullish(),
  settled_reason: z.string().min(1).max(128).nullish(),
  cancel_requested_at: dateTime.nullish(),
  cancel_reason: z.string().min(1).max(128).nullish(),
  reply_text: z.string().min(1).max(2000).nullish(),
  delivery_id: uuid.nullish(),
  delivery_status: channelDeliveryStatusSchema.nullish(),
  provider_receipt_present: z.boolean().default(false),
  cancelable: z.boolean().default(false),
  error: errorSchema.nullish(),
});
export const channelOutboundIntentPageSchema = z.object({
  schema_version: version,
  items: z.array(channelOutboundIntentSnapshotSchema).max(50).default([]),
  next_cursor: z.string().min(1).max(256).nullish(),
});
export const channelOutboundIntentCancelRequestSchema = z
  .object({
    schema_version: version,
    expected_revision: revision,
  })
  .strict();

export function parseChannelProactivePolicy(input: unknown) {
  return channelProactivePolicySchema.parse(
    input,
  ) satisfies ChannelProactivePolicy;
}
export function parseChannelProactivePolicyUpdate(input: unknown) {
  return channelProactivePolicyUpdateSchema.parse(
    input,
  ) satisfies ChannelProactivePolicyUpdate;
}
export function parseChannelProactivePolicySnapshot(input: unknown) {
  return channelProactivePolicySnapshotSchema.parse(
    input,
  ) satisfies ChannelProactivePolicySnapshot;
}
export function parseChannelProactivePreview(input: unknown) {
  return channelProactivePreviewSchema.parse(
    input,
  ) satisfies ChannelProactivePreview;
}
export function parseChannelOutboundIntentSnapshot(input: unknown) {
  return channelOutboundIntentSnapshotSchema.parse(
    input,
  ) satisfies ChannelOutboundIntentSnapshot;
}
export function parseChannelOutboundIntentPage(input: unknown) {
  return channelOutboundIntentPageSchema.parse(
    input,
  ) satisfies ChannelOutboundIntentPage;
}
export function parseChannelOutboundIntentCancelRequest(input: unknown) {
  return channelOutboundIntentCancelRequestSchema.parse(
    input,
  ) satisfies ChannelOutboundIntentCancelRequest;
}

function isTimeZone(value: string): boolean {
  if (value !== value.trim() || /^[+-]/u.test(value)) return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}
