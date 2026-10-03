import {
  parseChannelOutboundIntentCancelRequest,
  parseChannelOutboundIntentPage,
  parseChannelOutboundIntentSnapshot,
  parseChannelProactivePolicy,
  parseChannelProactivePolicySnapshot,
  parseChannelProactivePolicyUpdate,
  parseChannelProactivePreview,
} from "@chatwaifu/protocol";
import { requestRuntime, runtimeParser } from "./http";

export type ChannelProactivePolicy = ReturnType<
  typeof parseChannelProactivePolicy
>;
export type ChannelProactivePolicySnapshot = ReturnType<
  typeof parseChannelProactivePolicySnapshot
>;
export type ChannelProactivePreview = ReturnType<
  typeof parseChannelProactivePreview
>;
export type ChannelOutboundIntentSnapshot = ReturnType<
  typeof parseChannelOutboundIntentSnapshot
>;
export type ChannelOutboundIntentPage = ReturnType<
  typeof parseChannelOutboundIntentPage
>;

function connectionPath(connectionId: string): string {
  return `/v1/channel-connections/${encodeURIComponent(connectionId)}`;
}
export function getChannelProactivePolicy(
  connectionId: string,
  signal?: AbortSignal,
) {
  return requestRuntime(
    `${connectionPath(connectionId)}/proactive-policy`,
    runtimeParser(parseChannelProactivePolicySnapshot),
    { signal },
  );
}
export function updateChannelProactivePolicy(
  connectionId: string,
  policy: ChannelProactivePolicy,
  expectedRevision: number,
  signal?: AbortSignal,
) {
  const body = parseChannelProactivePolicyUpdate({
    policy,
    expected_revision: expectedRevision,
  });
  return requestRuntime(
    `${connectionPath(connectionId)}/proactive-policy`,
    runtimeParser(parseChannelProactivePolicySnapshot),
    { method: "PUT", body: JSON.stringify(body), signal },
  );
}
export function previewChannelProactivePolicy(
  connectionId: string,
  signal?: AbortSignal,
) {
  return requestRuntime(
    `${connectionPath(connectionId)}/proactive-preview`,
    runtimeParser(parseChannelProactivePreview),
    { method: "POST", body: JSON.stringify({}), signal },
  );
}
export function getChannelOutboundIntents(
  connectionId: string,
  cursor?: string,
  signal?: AbortSignal,
) {
  const query = new URLSearchParams({ limit: "25" });
  if (cursor) query.set("cursor", cursor);
  return requestRuntime(
    `${connectionPath(connectionId)}/outbound-intents?${query}`,
    runtimeParser(parseChannelOutboundIntentPage),
    { signal },
  );
}
export function cancelChannelOutboundIntent(
  connectionId: string,
  requestId: string,
  expectedRevision: number,
  signal?: AbortSignal,
) {
  const body = parseChannelOutboundIntentCancelRequest({
    expected_revision: expectedRevision,
  });
  return requestRuntime(
    `${connectionPath(connectionId)}/outbound-intents/${encodeURIComponent(requestId)}/cancel`,
    runtimeParser(parseChannelOutboundIntentSnapshot),
    { method: "POST", body: JSON.stringify(body), signal },
  );
}
