import { z } from "zod";

import { requestRuntime } from "./http";

export const domainRebuildStateSchema = z.enum([
  "idle",
  "running",
  "completed",
  "failed",
  "cancelled",
]);
export type DomainRebuildState = z.infer<typeof domainRebuildStateSchema>;

export const domainRebuildStatusSchema = z.object({
  domain: z.string(),
  state: domainRebuildStateSchema,
  total_count: z.number().int().nonnegative(),
  indexed_count: z.number().int().nonnegative(),
  failed_count: z.number().int().nonnegative(),
  error: z.string().nullable().optional(),
});
export type DomainRebuildStatus = z.infer<typeof domainRebuildStatusSchema>;

export const overallRebuildStateSchema = z.enum([
  "idle",
  "running",
  "completed",
  "failed",
  "cancelled",
]);
export type OverallRebuildState = z.infer<typeof overallRebuildStateSchema>;

export const indexRebuildStatusSchema = z.object({
  schema_version: z.literal("1.0"),
  job_id: z.string(),
  state: overallRebuildStateSchema,
  domains: z.record(z.string(), domainRebuildStatusSchema),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
});
export type IndexRebuildStatus = z.infer<typeof indexRebuildStatusSchema>;

export async function rebuildIndexes(
  signal?: AbortSignal,
): Promise<IndexRebuildStatus> {
  return requestRuntime("/v1/indexes/rebuild", indexRebuildStatusSchema, {
    signal,
    method: "POST",
    body: "{}",
  });
}

export async function getIndexRebuildStatus(
  signal?: AbortSignal,
): Promise<IndexRebuildStatus> {
  return requestRuntime("/v1/indexes/rebuild", indexRebuildStatusSchema, {
    signal,
  });
}
