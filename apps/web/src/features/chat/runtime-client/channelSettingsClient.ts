import {
  parseChannelRuntimeSettingsResponse,
  parseChannelRuntimeSettingsUpdate,
  type ChannelRuntimePolicy,
} from "@chatwaifu/protocol";
import { requestRuntime, runtimeParser } from "./http";
import type { RuntimeRequestContext } from "../runtimeEndpoint";

export type ChannelSettingsRequestOptions = {
  expectedContext: RuntimeRequestContext;
  signal: AbortSignal;
};

export function getChannelRuntimeSettings(
  options: ChannelSettingsRequestOptions,
) {
  return requestRuntime(
    "/v1/channels/settings",
    runtimeParser(parseChannelRuntimeSettingsResponse),
    options,
  );
}
export function updateChannelRuntimeSettings(
  policy: ChannelRuntimePolicy,
  expectedRevision: number,
  options: ChannelSettingsRequestOptions,
) {
  const body = parseChannelRuntimeSettingsUpdate({
    policy,
    expected_revision: expectedRevision,
  });
  return requestRuntime(
    "/v1/channels/settings",
    runtimeParser(parseChannelRuntimeSettingsResponse),
    { ...options, method: "PUT", body: JSON.stringify(body) },
  );
}
