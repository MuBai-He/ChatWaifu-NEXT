import { z } from "zod";
import type {
  AgentTask,
  AgentTaskPage,
  ArtifactRef,
  CapabilityDetail,
  CapabilityPage,
  GroupAutonomyPolicy,
  AgentDevelopmentPolicy,
  CandidateFeature,
  DecisionRecord,
  AgentEvent,
} from "../generated/domain";

const version = z.literal("1.0").default("1.0");
const date = z.string().datetime({ offset: true });
const id = z.string().uuid();
const json = z.record(z.string(), z.json());
export const capabilityDescriptorSchema = z
  .object({
    schema_version: version,
    capability_id: z.string(),
    skill_id: z.string(),
    skill_version: z.string(),
    name: z.string(),
    description: z.string(),
    category: z.string(),
    source: z.enum(["builtin", "plugin", "mcp_connection"]),
    status: z.enum([
      "available",
      "authorization_required",
      "disabled",
      "not_configured",
      "adapter_required",
      "unsupported",
    ]),
    availability_reason: z.string().max(400).nullable().default(null),
    side_effect: z.enum([
      "read",
      "write",
      "external_communication",
      "device_control",
      "destructive",
    ]),
    required_permissions: z.array(z.string()).default([]),
    confirmation_required: z.boolean().default(false),
    execution_location: z
      .enum(["runtime", "paired_device", "plugin"])
      .default("runtime"),
    fingerprint: z.string(),
  })
  .strict();
export const capabilityPageSchema = z
  .object({
    schema_version: version,
    items: z.array(capabilityDescriptorSchema).max(32).default([]),
    categories: z.array(z.string()).max(128).default([]),
    next_cursor: z.string().nullable().default(null),
  })
  .strict();
export const capabilityDetailSchema = z
  .object({
    schema_version: version,
    descriptor: capabilityDescriptorSchema,
    input_schema: json,
    output_schema: json,
    instructions: z.string(),
  })
  .strict();
export const taskAuthorizationSchema = z
  .object({
    schema_version: version,
    allowed_skill_ids: z.array(z.string()).max(64).default([]),
    resource_roots: z.array(z.string()).max(16).default([]),
    calendar_ids: z.array(z.string()).max(32).default([]),
    source_ref: z.string().min(1).max(256),
    allow_writes: z.boolean().default(false),
    capability_fingerprints: z.record(z.string(), z.string()).default({}),
    expires_at: date,
  })
  .strict();
export const taskChannelBindingSchema = z
  .object({
    schema_version: version,
    connection_id: id,
    account_key: z.string(),
    conversation_key: z.string(),
    sender_key: z.string(),
    source_ref: z.string(),
    route_id: id.nullish(),
    route_revision: z.number().int().min(1).nullish(),
    policy_revision: z.number().int().nonnegative().nullish(),
    scene_id: z.string().nullish(),
    audience_fingerprint: z
      .string()
      .regex(/^[0-9a-f]{64}$/)
      .nullish(),
    link_id: id.nullish(),
    link_revision: z.number().int().min(1).nullish(),
  })
  .strict();
export const agentTaskSchema = z
  .object({
    schema_version: version,
    task_id: id,
    session_id: id,
    scope: z.string(),
    goal: z.string(),
    completion_criteria: z.array(z.string()),
    authorization: taskAuthorizationSchema,
    channel_binding: taskChannelBindingSchema.nullish(),
    state: z.enum([
      "queued",
      "running",
      "waiting_input",
      "waiting_authorization",
      "waiting_event",
      "paused",
      "succeeded",
      "failed",
      "cancelled",
    ]),
    revision: z.number().int().nonnegative().default(0),
    max_tool_calls: z.number().int(),
    max_active_seconds: z.number().int(),
    tool_calls: z.number().int().default(0),
    active_seconds: z.number().default(0),
    result_text: z.string().nullable().default(null),
    blocked_reason: z.string().nullable().default(null),
    wake_at: date.nullable().default(null),
    created_at: date,
    updated_at: date,
    continuation: z.string().max(8000).nullable().default(null),
    candidate_id: id.nullable().default(null),
    delivery_id: id.nullable().default(null),
  })
  .strict();
export const agentTaskPageSchema = z
  .object({
    schema_version: version,
    items: z.array(agentTaskSchema).max(50).default([]),
    next_cursor: z.string().nullable().default(null),
  })
  .strict();
export const artifactRefSchema = z
  .object({
    schema_version: version,
    artifact_id: id,
    task_id: id.nullable().default(null),
    session_id: id,
    name: z.string(),
    media_type: z.string(),
    byte_length: z.number().int().nonnegative(),
    sha256: z.string(),
    version: z.number().int().min(1).default(1),
    created_at: date,
    validation_status: z
      .enum(["pending", "structural", "rendered", "failed"])
      .default("pending"),
  })
  .strict();
export const groupAutonomyPolicySchema = z
  .object({
    schema_version: version,
    route_id: id,
    route_revision: z.number().int().min(1),
    revision: z.number().int().nonnegative().default(0),
    mode: z.enum(["off", "shadow", "member"]).default("off"),
    merge_seconds: z.number().int().min(1).max(10).default(3),
    decision_interval_seconds: z.number().int().min(10).max(60).default(10),
    observations_per_hour: z.number().int().min(1).max(240).default(240),
    messages_per_hour: z.number().int().min(0).max(20).default(20),
    message_interval_seconds: z.number().int().min(30).max(3600).default(30),
    quiet_start: z.number().int().min(0).max(23).default(23),
    quiet_end: z.number().int().min(0).max(23).default(8),
    timezone: z.string().max(64).default("Asia/Shanghai"),
    memory_enabled: z.boolean().default(false),
  })
  .strict();

export const parseCapabilityPage = (value: unknown): CapabilityPage =>
  capabilityPageSchema.parse(value);
export const parseCapabilityDetail = (value: unknown): CapabilityDetail =>
  capabilityDetailSchema.parse(value);
export const parseAgentTask = (value: unknown): AgentTask =>
  agentTaskSchema.parse(value) as AgentTask;
export const parseAgentTaskPage = (value: unknown): AgentTaskPage =>
  agentTaskPageSchema.parse(value) as AgentTaskPage;
export const parseArtifactRef = (value: unknown): ArtifactRef =>
  artifactRefSchema.parse(value);
export const parseGroupAutonomyPolicy = (value: unknown): GroupAutonomyPolicy =>
  groupAutonomyPolicySchema.parse(value);

export const developmentPolicySchema = z
  .object({
    schema_version: version,
    revision: z.number().int().nonnegative().default(0),
    enabled: z.boolean().default(false),
  })
  .strict();
export const candidateFeatureSchema = z
  .object({
    schema_version: version,
    candidate_id: id,
    session_id: id,
    goal: z.string(),
    source_ref: z.string(),
    state: z.enum([
      "queued",
      "developing",
      "tested",
      "blocked",
      "failed",
      "approved",
    ]),
    revision: z.number().int().nonnegative().default(0),
    plugin_id: z.string().nullable().default(null),
    package_sha256: z.string().nullable().default(null),
    artifact: artifactRefSchema.nullable().default(null),
    test_summary: z.string().nullable().default(null),
    created_at: date,
    updated_at: date,
  })
  .strict();
export const decisionRecordSchema = z
  .object({
    schema_version: version,
    action: z.enum([
      "wait",
      "respond",
      "clarify",
      "task",
      "defer",
      "capability_gap",
    ]),
    reason: z.string().max(400),
    source_refs: z.array(z.string()).max(16),
    goal: z.string().max(2000).nullable().default(null),
    wake_after_seconds: z
      .number()
      .int()
      .min(30)
      .max(86400)
      .nullable()
      .default(null),
    quiet_seconds: z.number().int().min(0).max(86400).nullable().default(null),
    memory_source_refs: z.array(z.string()).max(8).default([]),
  })
  .strict();
export const agentEventSchema = z
  .object({
    schema_version: version,
    event_id: id,
    session_id: id,
    scope: z.string(),
    task_id: id.nullable().default(null),
    kind: z.enum(["input", "work", "wake"]),
    source_refs: z.array(z.string()).max(96).default([]),
    occurred_at: date,
    expires_at: date,
  })
  .strict();
export const parseDevelopmentPolicy = (
  value: unknown,
): AgentDevelopmentPolicy => developmentPolicySchema.parse(value);
export const parseCandidateFeature = (value: unknown): CandidateFeature =>
  candidateFeatureSchema.parse(value);
export const parseDecisionRecord = (value: unknown): DecisionRecord =>
  decisionRecordSchema.parse(value) as DecisionRecord;
export const parseAgentEvent = (value: unknown): AgentEvent =>
  agentEventSchema.parse(value) as AgentEvent;
