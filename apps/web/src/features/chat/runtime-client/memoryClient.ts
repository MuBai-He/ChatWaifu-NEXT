import {
  parseMemoryProposal,
  parseMemoryRecord,
  parseMemorySource,
  type MemoryProposal,
  type MemoryRecord,
  type MemorySource,
} from "@chatwaifu/protocol";
import { z } from "zod";

import type { MemoryItem } from "../types";
import { mutationReceiptSchema, requestRuntime, runtimeParser } from "./http";

const memoryRecordsResponse = runtimeParser((input: unknown): MemoryItem[] => {
  const payload = z.object({ items: z.array(z.unknown()) }).parse(input);
  return payload.items.map((item) => parseMemoryRecord(item) as MemoryItem);
});
const memoryProposalsResponse = runtimeParser(
  (input: unknown): MemoryProposal[] => {
    const payload = z.object({ items: z.array(z.unknown()) }).parse(input);
    return payload.items.map(parseMemoryProposal);
  },
);
const memorySourcesResponse = runtimeParser(
  (input: unknown): MemorySource[] => {
    const payload = z.object({ items: z.array(z.unknown()) }).parse(input);
    return payload.items.map(parseMemorySource);
  },
);

export async function getMemory(): Promise<MemoryItem[]> {
  return requestRuntime(
    "/v1/memory",
    runtimeParser((input) => {
      const payload = z.object({ items: z.array(z.unknown()) }).parse(input);
      return payload.items.map((item) => parseMemoryRecord(item) as MemoryItem);
    }),
  );
}

export async function getMemoryRecords(filters?: {
  includeTombstoned?: boolean;
  kind?: string;
  sensitivity?: string;
}): Promise<MemoryItem[]> {
  const query = new URLSearchParams();
  if (filters?.includeTombstoned) query.set("include_tombstoned", "true");
  if (filters?.kind) query.set("kind", filters.kind);
  if (filters?.sensitivity) query.set("sensitivity", filters.sensitivity);
  const suffix = query.size ? `?${query.toString()}` : "";
  return requestRuntime(`/v1/memory${suffix}`, memoryRecordsResponse);
}

export async function getMemoryProposals(
  status = "pending",
): Promise<MemoryProposal[]> {
  return requestRuntime(
    `/v1/memory/proposals?status=${encodeURIComponent(status)}`,
    memoryProposalsResponse,
  );
}

export async function decideMemoryProposal(
  sessionId: string,
  proposalId: string,
  decision: "accept" | "reject",
): Promise<MemoryProposal> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/memory/proposals/${proposalId}/decision`,
    runtimeParser(parseMemoryProposal),
    { method: "POST", body: JSON.stringify({ decision }) },
  );
}

export async function getMemorySources(
  memoryId: string,
): Promise<MemorySource[]> {
  return requestRuntime(
    `/v1/memory/${memoryId}/sources`,
    memorySourcesResponse,
  );
}

export async function correctMemory(
  sessionId: string,
  memoryId: string,
  text: string,
): Promise<MemoryRecord> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/memory/${memoryId}`,
    runtimeParser(parseMemoryRecord),
    { method: "PATCH", body: JSON.stringify({ text }) },
  );
}

export async function setMemoryPinned(
  sessionId: string,
  memoryId: string,
  pinned: boolean,
): Promise<MemoryRecord> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/memory/${memoryId}/pinned`,
    runtimeParser(parseMemoryRecord),
    { method: "PUT", body: JSON.stringify({ pinned }) },
  );
}

export async function forgetMemory(
  sessionId: string,
  memoryId: string,
): Promise<void> {
  await requestRuntime(
    `/v1/sessions/${sessionId}/memory/${memoryId}`,
    mutationReceiptSchema,
    { method: "DELETE" },
  );
}
