import { z } from "zod";
import type {
  ChannelRuntimePolicy,
  ChannelRuntimeSettingsResponse,
  ChannelRuntimeSettingsUpdate,
  GroupDiscussionPolicy,
} from "../generated/domain";

const integer = (value: number, min: number, max: number) =>
  z.number().int().min(min).max(max).default(value);

export const groupDiscussionPolicySchema = z
  .object({
    enabled: z.boolean().default(true),
    max_groups: integer(32, 1, 128),
    message_characters: integer(800, 80, 2000),
    cache_messages: integer(96, 32, 512),
    cache_characters: integer(24000, 3200, 128000),
    retention_seconds: integer(900, 30, 3600),
    member_messages: integer(24, 1, 128),
    member_characters: integer(6000, 80, 32000),
    member_messages_per_window: integer(6, 1, 30),
    frequency_window_seconds: integer(30, 1, 300),
    duplicate_window_seconds: integer(60, 1, 900),
    input_tokens: integer(1536, 128, 8192),
    summary_input_tokens: integer(3072, 256, 16384),
    summary_output_tokens: integer(256, 64, 1024),
    summary_timeout_seconds: z.number().min(0.1).max(30).default(8),
  })
  .strict()
  .refine((p) => p.member_messages <= p.cache_messages, {
    message: "每人条数不能大于群缓存条数",
  })
  .refine((p) => p.member_characters <= p.cache_characters, {
    message: "每人字符数不能大于群缓存字符数",
  });

export const channelRuntimePolicySchema = z
  .object({
    qq_owner_public_web_enabled: z.boolean().default(false),
    qq_owner_agent_enabled: z.boolean().default(false),
    qq_owner_voice_reply_enabled: z.boolean().default(true),
    qq_owner_voice_input_enabled: z.boolean().default(true),
    qq_native_favorites_enabled: z.boolean().default(true),
    group_discussion: groupDiscussionPolicySchema.default(() =>
      groupDiscussionPolicySchema.parse({}),
    ),
  })
  .strict();
const version = z.literal("1.0").default("1.0");
export const channelRuntimeSettingsResponseSchema = z.object({
  schema_version: version,
  revision: z.number().int().nonnegative(),
  policy: channelRuntimePolicySchema,
  updated_at: z.string().datetime({ offset: true }).nullish(),
  search_provider: z.string().min(1),
  reader_provider: z.string().min(1),
  stt_provider: z.string().min(1),
});
export const channelRuntimeSettingsUpdateSchema = z
  .object({
    schema_version: version,
    expected_revision: z.number().int().nonnegative(),
    policy: channelRuntimePolicySchema,
  })
  .strict();

export function parseGroupDiscussionPolicy(
  input: unknown,
): GroupDiscussionPolicy {
  return groupDiscussionPolicySchema.parse(input);
}
export function parseChannelRuntimePolicy(
  input: unknown,
): ChannelRuntimePolicy {
  return channelRuntimePolicySchema.parse(input);
}
export function parseChannelRuntimeSettingsResponse(
  input: unknown,
): ChannelRuntimeSettingsResponse {
  return channelRuntimeSettingsResponseSchema.parse(input);
}
export function parseChannelRuntimeSettingsUpdate(
  input: unknown,
): ChannelRuntimeSettingsUpdate {
  return channelRuntimeSettingsUpdateSchema.parse(input);
}
