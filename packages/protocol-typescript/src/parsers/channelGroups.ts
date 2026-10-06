import { z } from "zod";
import type {
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
  ChannelTurnSnapshot,
} from "../generated/domain";
import { channelTurnSnapshotSchema } from "./protocol";
import { channelGroupDeliveryTargetSchema } from "./channelGroupTarget";

export { channelGroupDeliveryTargetSchema } from "./channelGroupTarget";

const version = z.literal("1.0").default("1.0");
const uuid = z.string().uuid();
const dateTime = z.string().datetime({ offset: true });
const revision = z.number().int().min(1);
const qqId = z.string().regex(/^[1-9][0-9]{0,19}$/);
const identifier = z
  .string()
  .min(1)
  .max(128)
  .refine((value) => value === value.trim());
const name = z
  .string()
  .min(1)
  .max(80)
  .refine((value) => value === value.trim());
const fingerprint = z.string().regex(/^[0-9a-f]{64}$/);
const cursor = z.string().min(1).max(256).nullish();
const speakers = z.array(qqId).max(32).refine(unique);

export const channelGroupPauseReasonSchema = z.enum([
  "operator_disabled",
  "reconnect",
  "membership_changed",
  "account_changed",
  "connection_disabled",
  "connection_deleted",
  "configuration_changed",
  "link_revoked",
  "scene_reset",
  "route_deleted",
]);
export const channelGroupAudienceRequestSchema = z
  .object({ schema_version: version, group_id: qqId })
  .strict();
export const channelParticipantLinkCreateSchema = z
  .object({
    schema_version: version,
    observation_id: uuid,
    sender_key: qqId,
    participant_id: identifier,
  })
  .strict();
export const channelParticipantLinkUpdateSchema = z
  .object({
    schema_version: version,
    enabled: z.boolean(),
    expected_revision: revision,
  })
  .strict();
export const channelParticipantLinkSnapshotSchema = z
  .object({
    schema_version: version,
    link_id: uuid,
    provider_id: z.literal("qq_napcat").default("qq_napcat"),
    account_key: qqId,
    sender_key: qqId,
    participant_id: identifier,
    enabled: z.boolean(),
    revision,
    created_at: dateTime,
    updated_at: dateTime,
  })
  .passthrough();
export const channelGroupAudienceSnapshotSchema = z
  .object({
    schema_version: version,
    observation_id: uuid,
    connection_id: uuid,
    connection_revision: revision,
    account_key: qqId,
    group_id: qqId,
    // JSON Schema minItems=2 generates a TS tuple; these runtime arrays retain
    // the Python list AST and enforce the same length constraints before use.
    member_ids: z
      .array(qqId)
      .min(2)
      .max(32)
      .refine(unique) as unknown as z.ZodType<
      ChannelGroupAudienceSnapshot["member_ids"]
    >,
    member_fingerprint: fingerprint,
    observed_at: dateTime,
    expires_at: dateTime,
  })
  .passthrough()
  .refine(
    (value) => {
      const ttl = Date.parse(value.expires_at) - Date.parse(value.observed_at);
      return ttl > 0 && ttl <= 60_000;
    },
    {
      path: ["expires_at"],
      message: "observation must expire within 60 seconds",
    },
  );
export const channelGroupRouteCreateSchema = z
  .object({
    schema_version: version,
    observation_id: uuid,
    display_name: name,
    speaker_sender_keys: speakers.default([]),
  })
  .strict();
export const channelGroupRouteUpdateSchema = z
  .object({
    schema_version: version,
    enabled: z.boolean(),
    expected_revision: revision,
    observation_id: uuid.nullish(),
    speaker_sender_keys: speakers,
  })
  .strict()
  .refine(
    (value) =>
      !value.enabled ||
      (value.observation_id != null && value.speaker_sender_keys.length > 0),
    {
      path: ["observation_id"],
      message: "enabling requires an observation and at least one speaker",
    },
  );
export const channelGroupRouteMemberSnapshotSchema = z
  .object({
    schema_version: version,
    link_id: uuid,
    sender_key: qqId,
    participant_id: identifier,
    can_speak: z.boolean(),
  })
  .passthrough();
export const channelGroupRouteSnapshotSchema = z
  .object({
    schema_version: version,
    route_id: uuid,
    connection_id: uuid,
    account_key: qqId,
    group_id: qqId,
    character_id: identifier,
    scene_id: identifier,
    display_name: name,
    revision,
    enabled: z.boolean().default(false),
    pause_reason: channelGroupPauseReasonSchema.nullish(),
    observation_id: uuid,
    audience_fingerprint: fingerprint,
    members: z
      .array(channelGroupRouteMemberSnapshotSchema)
      .min(2)
      .max(32)
      .refine(
        (value) =>
          unique(value.map((member) => member.sender_key)) &&
          unique(value.map((member) => member.participant_id)) &&
          unique(value.map((member) => member.link_id)),
      ) as unknown as z.ZodType<ChannelGroupRouteSnapshot["members"]>,
    created_at: dateTime,
    updated_at: dateTime,
    deleted_at: dateTime.nullish(),
  })
  .passthrough();
export const channelGroupRoutePageSchema = z
  .object({
    schema_version: version,
    items: z.array(channelGroupRouteSnapshotSchema).max(50).default([]),
    next_cursor: cursor,
  })
  .passthrough();
export const channelParticipantLinkPageSchema = z
  .object({
    schema_version: version,
    items: z.array(channelParticipantLinkSnapshotSchema).max(50).default([]),
    next_cursor: cursor,
  })
  .passthrough();
export const channelGroupTurnCancelRequestSchema = z
  .object({
    schema_version: version,
    expected_revision: z.number().int().nonnegative(),
  })
  .strict();
export const channelGroupTurnSnapshotSchema = z
  .object({
    schema_version: version,
    route_id: uuid,
    route_revision: revision,
    scene_id: identifier,
    participant_id: identifier,
    turn: channelTurnSnapshotSchema as z.ZodType<ChannelTurnSnapshot>,
    provider_receipt_present: z.boolean().default(false),
    cancelable: z.boolean().default(false),
  })
  .passthrough();
export const channelGroupTurnPageSchema = z
  .object({
    schema_version: version,
    items: z.array(channelGroupTurnSnapshotSchema).max(50).default([]),
    next_cursor: cursor,
  })
  .passthrough();

export function parseChannelGroupAudienceRequest(input: unknown) {
  return channelGroupAudienceRequestSchema.parse(
    input,
  ) satisfies ChannelGroupAudienceRequest;
}
export function parseChannelGroupAudienceSnapshot(input: unknown) {
  return channelGroupAudienceSnapshotSchema.parse(
    input,
  ) satisfies ChannelGroupAudienceSnapshot;
}
export function parseChannelGroupDeliveryTarget(input: unknown) {
  return channelGroupDeliveryTargetSchema.parse(
    input,
  ) satisfies ChannelGroupDeliveryTarget;
}
export function parseChannelParticipantLinkCreate(input: unknown) {
  return channelParticipantLinkCreateSchema.parse(
    input,
  ) satisfies ChannelParticipantLinkCreate;
}
export function parseChannelParticipantLinkUpdate(input: unknown) {
  return channelParticipantLinkUpdateSchema.parse(
    input,
  ) satisfies ChannelParticipantLinkUpdate;
}
export function parseChannelParticipantLinkSnapshot(input: unknown) {
  return channelParticipantLinkSnapshotSchema.parse(
    input,
  ) satisfies ChannelParticipantLinkSnapshot;
}
export function parseChannelParticipantLinkPage(input: unknown) {
  return channelParticipantLinkPageSchema.parse(
    input,
  ) satisfies ChannelParticipantLinkPage;
}
export function parseChannelGroupRouteCreate(input: unknown) {
  return channelGroupRouteCreateSchema.parse(
    input,
  ) satisfies ChannelGroupRouteCreate;
}
export function parseChannelGroupRouteUpdate(input: unknown) {
  return channelGroupRouteUpdateSchema.parse(
    input,
  ) satisfies ChannelGroupRouteUpdate;
}
export function parseChannelGroupRouteMemberSnapshot(input: unknown) {
  return channelGroupRouteMemberSnapshotSchema.parse(
    input,
  ) satisfies ChannelGroupRouteMemberSnapshot;
}
export function parseChannelGroupRouteSnapshot(input: unknown) {
  return channelGroupRouteSnapshotSchema.parse(
    input,
  ) satisfies ChannelGroupRouteSnapshot;
}
export function parseChannelGroupRoutePage(input: unknown) {
  return channelGroupRoutePageSchema.parse(
    input,
  ) satisfies ChannelGroupRoutePage;
}
export function parseChannelGroupTurnCancelRequest(input: unknown) {
  return channelGroupTurnCancelRequestSchema.parse(
    input,
  ) satisfies ChannelGroupTurnCancelRequest;
}
export function parseChannelGroupTurnSnapshot(input: unknown) {
  return channelGroupTurnSnapshotSchema.parse(
    input,
  ) satisfies ChannelGroupTurnSnapshot;
}
export function parseChannelGroupTurnPage(input: unknown) {
  return channelGroupTurnPageSchema.parse(input) satisfies ChannelGroupTurnPage;
}

function unique(value: readonly string[]): boolean {
  return new Set(value).size === value.length;
}
