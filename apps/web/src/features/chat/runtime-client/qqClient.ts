import {
  parseChannelConnectionSnapshot,
  parseChannelPairingSnapshot,
  type ChannelConnectionSnapshot,
  type ChannelPairingSnapshot,
} from "@chatwaifu/protocol";

import { mutationReceiptSchema, requestRuntime, runtimeParser } from "./http";

const pairingSnapshotParser = runtimeParser(parseChannelPairingSnapshot);

export type QQPairingSnapshot = ChannelPairingSnapshot;

export function startQQPairing(
  endpoint: string,
  accessToken: string,
  characterId: string,
): Promise<QQPairingSnapshot> {
  return requestRuntime("/v1/channel-pairing-sessions", pairingSnapshotParser, {
    method: "POST",
    body: JSON.stringify({
      schema_version: "1.0",
      provider_id: "qq_napcat",
      endpoint,
      access_token: accessToken,
      character_id: characterId,
    }),
  });
}

export function getQQPairing(
  pairingId: string,
  waitSeconds = 20,
  signal?: AbortSignal,
): Promise<QQPairingSnapshot> {
  const boundedWait = Number.isFinite(waitSeconds)
    ? Math.max(0, Math.min(25, Math.floor(waitSeconds)))
    : 20;
  return requestRuntime(
    `/v1/channel-pairing-sessions/${encodeURIComponent(pairingId)}?wait_seconds=${boundedWait}`,
    pairingSnapshotParser,
    { signal, timeoutMs: (boundedWait + 8) * 1_000 },
  );
}

export async function cancelQQPairing(pairingId: string): Promise<void> {
  await requestRuntime(
    `/v1/channel-pairing-sessions/${encodeURIComponent(pairingId)}`,
    mutationReceiptSchema,
    { method: "DELETE" },
  );
}

export function testQQChannelConnection(
  connectionId: string,
  signal?: AbortSignal,
): Promise<ChannelConnectionSnapshot> {
  return requestRuntime(
    `/v1/channel-connections/${encodeURIComponent(connectionId)}/test`,
    runtimeParser(parseChannelConnectionSnapshot),
    { method: "POST", signal },
  );
}
