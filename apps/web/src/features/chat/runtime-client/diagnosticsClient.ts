import type {
  InteractionTraceDetail,
  InteractionTracePage,
} from "@chatwaifu/protocol";

import { runtimeParser, requestRuntime } from "./http";
import {
  parseInteractionTraceDetail,
  parseInteractionTracePage,
} from "./diagnosticsParsers";

export async function getInteractionTraces(
  sessionId: string,
  options: {
    cursor?: string | null;
    includeNonparticipation?: boolean;
    signal?: AbortSignal;
  } = {},
): Promise<InteractionTracePage> {
  const query = new URLSearchParams();
  if (options.cursor) query.set("cursor", options.cursor);
  if (options.includeNonparticipation)
    query.set("include_nonparticipation", "true");
  const suffix = query.size ? `?${query.toString()}` : "";
  return requestRuntime(
    `/v1/sessions/${encodeURIComponent(sessionId)}/interactions${suffix}`,
    runtimeParser(parseInteractionTracePage),
    { signal: options.signal },
  );
}

export async function getInteractionTraceDetail(
  sessionId: string,
  interactionId: string,
  options: { afterSequence?: number; signal?: AbortSignal } = {},
): Promise<InteractionTraceDetail> {
  const suffix = options.afterSequence
    ? `?after_sequence=${options.afterSequence}`
    : "";
  return requestRuntime(
    `/v1/sessions/${encodeURIComponent(sessionId)}/interactions/${encodeURIComponent(interactionId)}${suffix}`,
    runtimeParser(parseInteractionTraceDetail),
    { signal: options.signal },
  );
}
