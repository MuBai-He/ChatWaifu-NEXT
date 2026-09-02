import {
  parseChannelAuthorizationSnapshot,
  parseChannelConnectionSnapshot,
  type ChannelAuthorizationSnapshot as ProtocolChannelAuthorizationSnapshot,
  type ChannelConnectionSnapshot as ProtocolChannelConnectionSnapshot,
} from "@chatwaifu/protocol";
import { z } from "zod";

import { mutationReceiptSchema, requestRuntime, runtimeParser } from "./http";

const channelAuthorizationSnapshotParser = runtimeParser(
  parseChannelAuthorizationSnapshot,
);

const channelConnectionsResponseSchema = z.object({
  items: z.array(z.unknown()),
});

export type ChannelAuthorizationSnapshot = ProtocolChannelAuthorizationSnapshot;
export type ChannelAuthorizationStatus =
  ProtocolChannelAuthorizationSnapshot["status"];
export type ChannelConnectionSnapshot = ProtocolChannelConnectionSnapshot;

export async function getChannelConnections(
  signal?: AbortSignal,
): Promise<ChannelConnectionSnapshot[]> {
  const response = await requestRuntime(
    "/v1/channel-connections",
    channelConnectionsResponseSchema,
    { signal },
  );
  return response.items.map(parseChannelConnectionSnapshot);
}

export async function startChannelAuthorization(
  providerId: string,
  characterId: string,
  signal?: AbortSignal,
): Promise<ChannelAuthorizationSnapshot> {
  return requestRuntime(
    "/v1/channel-auth-sessions",
    channelAuthorizationSnapshotParser,
    {
      method: "POST",
      body: JSON.stringify({
        provider_id: providerId,
        character_id: characterId,
      }),
      signal,
    },
  );
}

export async function getChannelAuthorization(
  authSessionId: string,
  waitSeconds = 20,
  signal?: AbortSignal,
): Promise<ChannelAuthorizationSnapshot> {
  const boundedWait = Math.max(0, Math.min(30, Math.floor(waitSeconds)));
  return requestRuntime(
    `/v1/channel-auth-sessions/${encodeURIComponent(authSessionId)}?wait_seconds=${boundedWait}`,
    channelAuthorizationSnapshotParser,
    { signal, timeoutMs: (boundedWait + 8) * 1_000 },
  );
}

export async function submitChannelAuthorizationVerification(
  authSessionId: string,
  verificationCode: string,
  signal?: AbortSignal,
): Promise<ChannelAuthorizationSnapshot> {
  return requestRuntime(
    `/v1/channel-auth-sessions/${encodeURIComponent(authSessionId)}/verification`,
    channelAuthorizationSnapshotParser,
    {
      method: "POST",
      body: JSON.stringify({ verification_code: verificationCode }),
      signal,
    },
  );
}

export async function cancelChannelAuthorization(
  authSessionId: string,
): Promise<void> {
  await requestRuntime(
    `/v1/channel-auth-sessions/${encodeURIComponent(authSessionId)}`,
    mutationReceiptSchema,
    { method: "DELETE" },
  );
}

export async function deleteChannelConnection(
  connectionId: string,
): Promise<void> {
  await requestRuntime(
    `/v1/channel-connections/${encodeURIComponent(connectionId)}`,
    mutationReceiptSchema,
    { method: "DELETE" },
  );
}
