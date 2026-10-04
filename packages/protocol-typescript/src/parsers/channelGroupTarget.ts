import { z } from "zod";

// Independent of protocol.ts so the legacy plan can validate its target without
// creating a cycle with group turn snapshots, which embed ChannelTurnSnapshot.
export const channelGroupDeliveryTargetSchema = z
  .object({
    schema_version: z.literal("1.0").default("1.0"),
    kind: z.literal("group").default("group"),
    connection_id: z.string().uuid(),
    account_key: z.string().regex(/^[1-9][0-9]{0,19}$/),
    group_id: z.string().regex(/^[1-9][0-9]{0,19}$/),
    route_id: z.string().uuid(),
    route_revision: z.number().int().min(1),
    channel_turn_id: z.string().uuid(),
    scene_id: z.string().min(1).max(128),
    audience_fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  })
  .strict();
