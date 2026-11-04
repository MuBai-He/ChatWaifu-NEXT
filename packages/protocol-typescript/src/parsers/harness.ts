import { z } from "zod";
import type {
  ActivityEntry,
  EvidenceRef,
  StandingIntent,
  WorkItem,
} from "../generated/domain";

export const evidenceRefSchema = z.object({
  kind: z.enum(["event", "knowledge"]),
  reference: z.string().min(1).max(256),
  revision: z.number().int().positive(),
});
export const intentSchema = z.object({
  reminder_defaults: z
    .object({
      event_offset_seconds: z
        .number()
        .int()
        .min(0)
        .max(2678400)
        .nullable()
        .default(null),
      todo_offset_seconds: z
        .number()
        .int()
        .min(0)
        .max(2678400)
        .nullable()
        .default(null),
      primary_device_id: z.string().uuid(),
    })
    .nullable()
    .default(null),
  intent_id: z.string().uuid(),
  owner: z.string(),
  character_id: z.string(),
  session_id: z.string().uuid(),
  goal: z.string().min(1),
  room_ids: z.array(z.string()).max(32),
  observe_calendar: z.boolean().default(false),
  resource_roots: z.array(z.string()).max(16).default(["."]),
  schedule: z
    .object({
      first_wake_at: z.string(),
      repeat: z.enum(["none", "daily", "weekly"]),
      timezone: z.string(),
      kind: z.enum(["run", "remind"]),
      title: z.string().nullable(),
    })
    .nullable()
    .optional(),
  allowed_skill_ids: z.array(z.string()).min(1).max(32),
  calendar_ids: z.array(z.string()).default([]),
  tasklist_ids: z.array(z.string()).default([]),
  allowed_operations: z
    .array(z.enum(["create", "update", "complete", "cancel", "delete"]))
    .default(["create", "update", "complete", "cancel"]),
  allow_writes: z.boolean(),
  shadow: z.boolean(),
  expires_at: z.string().nullable().optional(),
  max_tool_calls: z.number().int().positive(),
  max_active_seconds: z.number().int().positive(),
  revision: z.number().int().positive(),
  state: z.enum(["active", "paused", "revoked"]),
  capability_fingerprints: z.record(z.string(), z.string()),
  created_at: z.string(),
});
export const workSchema = z.object({
  owner_decision: z
    .object({
      operation_id: z.string().uuid(),
      request_hash: z.string().regex(/^[0-9a-f]{64}$/),
      received_at: z.string(),
      pending: z.boolean().default(true),
    })
    .nullable()
    .default(null),
  requested_fields: z.array(z.string()).max(16).default([]),
  migration_target: z.record(z.string(), z.json()).default({}),
  work_item_id: z.string(),
  legacy_record_id: z.string().nullable().optional(),
  memory_identity: z.record(z.string(), z.json()).default({}),
  action_scope: z.enum(["single", "series", "following"]).default("single"),
  provider_updated_at: z.string().nullable().optional(),
  target_applied_revision: z.number().int().nullable().optional(),
  reconciled_task_revision: z.number().int().nullable().optional(),
  start_date: z.string().nullable().optional(),
  end_date: z.string().nullable().optional(),
  due_date: z.string().nullable().optional(),
  clear_due: z.boolean().default(false),
  primary_device_id: z.string().uuid().nullable().optional(),
  reminder_rule: z.enum(["absolute", "relative"]).default("absolute"),
  reminder_policy: z.enum(["inherit", "none"]).default("inherit"),
  reminder_offset_seconds: z.number().int().nonnegative().nullable().optional(),
  previous_starts_at: z.string().nullable().optional(),
  previous_ends_at: z.string().nullable().optional(),
  owner: z.string(),
  room_id: z.string(),
  source_room_ids: z.array(z.string()).max(100).default([]),
  ingestion_mode: z
    .enum(["live", "historical", "delayed"])
    .default("historical"),
  kind: z.enum(["todo", "event", "memory", "reference", "decision"]),
  title: z.string().min(1),
  details: z.string(),
  state: z.enum([
    "proposed",
    "open",
    "waiting",
    "blocked",
    "completed",
    "cancelled",
    "rejected",
  ]),
  revision: z.number().int().positive(),
  operation: z
    .enum(["create", "update", "complete", "cancel"])
    .default("create"),
  target_id: z.string().nullable().optional(),
  expected_revision: z.number().int().nonnegative().default(0),
  legacy_review: z.boolean().default(false),
  acceptance: z.enum(["pending", "human", "policy"]),
  temporal_status: z
    .enum(["current", "historical", "uncertain"])
    .default("current"),
  temporal_reason: z.string().max(200).default(""),
  evidence: z.array(evidenceRefSchema).min(1),
  due_at: z.string().nullable(),
  starts_at: z.string().nullable(),
  ends_at: z.string().nullable(),
  remind_at: z.string().nullable(),
  timezone: z.string(),
  involvement: z.enum(["self", "other", "unclear"]),
  task_id: z.string().uuid().nullable(),
  waiting_reason: z.string().nullable().optional(),
  bindings: z.array(z.record(z.string(), z.json())).max(16),
  updated_at: z.string(),
});
export const activitySchema = z.object({
  redacted: z.boolean().default(false),
  delivery_history: z.array(z.record(z.string(), z.json())).default([]),
  details: z.record(z.string(), z.json()).default({}),
  delivery: z.record(z.string(), z.string()).default({}),
  sequence: z.number().int().positive(),
  kind: z.string(),
  reference: z.string(),
  state: z.string(),
  summary: z.string(),
  created_at: z.string(),
  evidence: z.array(evidenceRefSchema),
});
export const parseEvidenceRef = (value: unknown): EvidenceRef =>
  evidenceRefSchema.parse(value);
export const parseStandingIntent = (value: unknown): StandingIntent =>
  intentSchema.parse(value) as unknown as StandingIntent;
export const parseWorkItem = (value: unknown): WorkItem =>
  workSchema.parse(value) as WorkItem;
export const parseActivityEntry = (value: unknown): ActivityEntry =>
  activitySchema.parse(value);

export const harnessMetricsSnapshotSchema = z.object({
  processor:z.record(z.string(),z.json()).default({}),
  schema_version: z.literal("1.0"),
  generated_at: z.number().int().nonnegative(),
  work_item_id: z.string().nullable().default(null),
  scope: z.enum(["owner", "owner_and_selected_work"]),
  observation_states: z.record(z.string(), z.number().int().nonnegative()),
  processing_room_states: z.record(z.string(), z.number().int().nonnegative()),
  work_states: z.record(z.string(), z.number().int().nonnegative()),
  source_coverage: z.record(z.string(), z.json()),
  latency_seconds: z.record(z.string(), z.json()),
  action_event_states: z.record(z.string(), z.number().int().nonnegative()),
  review_decisions: z.record(z.string(), z.number().int().nonnegative()),
  task_cost: z.record(z.string(), z.json()),
  model_cost: z.record(z.string(), z.json()),
  shared_source_model_cost: z.record(z.string(), z.json()).nullable(),
  active_alerts: z.record(z.string(), z.number().int().min(0).max(1)),
  duplicate_action_claims: z.null(),
  wrong_target_writes: z.null(),
});

export function parseHarnessMetricsSnapshot(value: unknown): import("../generated/domain").HarnessMetricsSnapshot {
  return harnessMetricsSnapshotSchema.parse(value);
}
