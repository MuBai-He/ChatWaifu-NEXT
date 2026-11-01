import { z } from "zod";
import {
  parseActivityEntry,
  parseStandingIntent,
  parseWorkItem,
  type StandingIntentInput,
  type StandingIntentUpdate,
} from "@chatwaifu/protocol";
import { requestRuntime, runtimeParser } from "./http";

const page =
  <T>(parse: (input: unknown) => T) =>
  (value: unknown) =>
    z
      .object({ items: z.array(z.unknown()) })
      .parse(value)
      .items.map(parse);
export const getStandingIntents = (session: string) =>
  requestRuntime(
    `/v1/agent/intents?session_id=${encodeURIComponent(session)}`,
    runtimeParser(page(parseStandingIntent)),
  );
export const getHarnessWork = (session: string) =>
  requestRuntime(
    `/v1/personal-assistant/work-items?session_id=${encodeURIComponent(session)}`,
    runtimeParser(page(parseWorkItem)),
  );
export const getHarnessActivity = (session: string, before?: number) =>
  requestRuntime(
    `/v1/agent/activity?session_id=${encodeURIComponent(session)}${before ? `&before=${before}` : ""}`,
    runtimeParser(page(parseActivityEntry)),
  );
export const createStandingIntent = (body: StandingIntentInput) =>
  requestRuntime("/v1/agent/intents", runtimeParser(parseStandingIntent), {
    method: "POST",
    body: JSON.stringify(body),
  });
export const updateStandingIntent = (id: string, body: StandingIntentUpdate) =>
  requestRuntime(
    `/v1/agent/intents/${id}`,
    runtimeParser(parseStandingIntent),
    { method: "PUT", body: JSON.stringify(body) },
  );
export const linkHarnessWork = (
  session: string,
  proposal: import("@chatwaifu/protocol").WorkItem,
  target: import("@chatwaifu/protocol").WorkItem,
) =>
  requestRuntime(
    `/v1/personal-assistant/work-items/${encodeURIComponent(proposal.work_item_id)}`,
    runtimeParser(parseWorkItem),
    {
      method: "POST",
      body: JSON.stringify({
        session_id: session,
        operation_id: crypto.randomUUID(),
        expected_revision: proposal.revision,
        action: "link",
        target_id: target.work_item_id,
        expected_target_revision: target.revision,
      }),
    },
  );
export const actStandingIntent = (
  session: string,
  id: string,
  revision: number,
  action: "pause" | "resume" | "revoke" | "enable",
) =>
  requestRuntime(
    `/v1/agent/intents/${id}`,
    runtimeParser(parseStandingIntent),
    {
      method: "POST",
      body: JSON.stringify({
        session_id: session,
        expected_revision: revision,
        action,
      }),
    },
  );
export const reviewHarnessWork = (
  session: string,
  id: string,
  revision: number,
  action: "confirm" | "reject" | "complete" | "cancel" | "edit",
  changes?: {
    title: string;
    details: string;
    start_date?: string | null;
    end_date?: string | null;
    due_date?: string | null;
    starts_at?: string | null;
    ends_at?: string | null;
    remind_at?: string | null;
    primary_device_id?: string | null;
    involvement?: "self" | "other" | "unclear";
  },
) =>
  requestRuntime(
    `/v1/personal-assistant/work-items/${encodeURIComponent(id)}`,
    runtimeParser(parseWorkItem),
    {
      method: "POST",
      body: JSON.stringify({
        session_id: session,
        expected_revision: revision,
        action,
        changes,
        operation_id: crypto.randomUUID(),
      }),
    },
  );
const sourcesSchema = z.object({
  devices: z
    .array(z.object({ device_id: z.string(), label: z.string() }))
    .default([]),
  rooms: z.array(z.object({ room_id: z.string(), label: z.string() })),
  skills: z.array(z.object({ skill_id: z.string(), name: z.string() })),
  calendar_ids: z.array(z.string()),
  resources: z
    .array(
      z.object({
        id: z.string(),
        label: z.string(),
        kind: z.enum(["calendar", "reminder"]),
      }),
    )
    .default([]),
  tasklist_ids: z.array(z.string()).default([]),
});
export const getHarnessSources = (session: string) =>
  requestRuntime(
    `/v1/agent/sources?session_id=${encodeURIComponent(session)}`,
    sourcesSchema,
  );

export const readHarnessEvidence = (
  session: string,
  reference: string,
  revision: number,
) =>
  requestRuntime(
    `/v1/knowledge/records/${encodeURIComponent(reference)}?session_id=${encodeURIComponent(session)}&revision=${revision}`,
    z.object({
      title: z.string(),
      text: z.string(),
      sender_name: z.string().default(""),
      occurred_at: z.number(),
    }),
  );

const reviewEntry = z.object({
  current_text: z.string().optional(),
  kind: z.enum(["memory", "sync"]),
  reference: z.string(),
  title: z.string(),
  text: z.string(),
  reason: z.string(),
  revision: z.number().int(),
  evidence: z
    .array(z.object({ text: z.string(), occurred_at: z.string() }))
    .optional(),
});
export const getHarnessReview = (session: string) =>
  requestRuntime(
    `/v1/agent/review?session_id=${encodeURIComponent(session)}`,
    z.object({ items: z.array(reviewEntry) }),
  );
export const decideHarnessReview = (
  session: string,
  item: z.infer<typeof reviewEntry>,
  decision: "accept" | "reject" | "keep_local" | "keep_server",
) =>
  requestRuntime(
    `/v1/agent/review/${item.kind}/${encodeURIComponent(item.reference)}`,
    z.record(z.string(), z.unknown()),
    {
      method: "POST",
      body: JSON.stringify({
        session_id: session,
        decision,
        expected_revision: item.revision,
        operation_id: crypto.randomUUID(),
      }),
    },
  );

const activityPage = z.object({
  items: z.array(z.unknown()),
  quiet: z.boolean(),
  context_epoch: z.string(),
  next_cursor: z.number().int(),
});
export const getHarnessActivitySnapshot = (session: string) =>
  requestRuntime(
    `/v1/agent/activity?session_id=${encodeURIComponent(session)}`,
    runtimeParser((value) => {
      const page = activityPage.parse(value);
      return { ...page, items: page.items.map(parseActivityEntry) };
    }),
  );
