import {
  parseCharacterKernelSnapshot,
  parseCommandEnvelope,
  parseEventEnvelope,
  parseSessionSnapshot,
  type CharacterKernelSnapshot,
  type DomainEvent,
  type SessionSnapshot,
} from "@chatwaifu/protocol";
import { z } from "zod";

import {
  sessionMessageSchema,
  sessionRecoverySchema,
  sessionResetResultSchema,
  type SessionMessage,
  type SessionResetResult,
} from "./contracts";
import { mutationReceiptSchema, requestRuntime, runtimeParser } from "./http";

const messagesResponseSchema = z.object({
  items: z.array(sessionMessageSchema),
});
const ttsSelectionSchema = z.object({
  session_id: z.string().uuid(),
  provider_id: z.string().min(1),
});

export async function createSession(
  characterId: string,
): Promise<SessionSnapshot> {
  return requestRuntime("/v1/sessions", runtimeParser(parseSessionSnapshot), {
    method: "POST",
    body: JSON.stringify({ character_id: characterId }),
  });
}

export async function getSession(sessionId: string): Promise<SessionSnapshot> {
  return requestRuntime(
    `/v1/sessions/${sessionId}`,
    runtimeParser(parseSessionSnapshot),
  );
}

export async function getMessages(
  sessionId: string,
): Promise<SessionMessage[]> {
  return (
    await requestRuntime(
      `/v1/sessions/${sessionId}/messages`,
      messagesResponseSchema,
    )
  ).items;
}

export async function getSessionRecovery(
  sessionId: string,
): Promise<z.infer<typeof sessionRecoverySchema>> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/recovery`,
    sessionRecoverySchema,
  );
}

export async function getSessionEvents(
  sessionId: string,
  afterSequence: number,
): Promise<DomainEvent[]> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/events?after_sequence=${afterSequence}&limit=500`,
    {
      parse(input: unknown): DomainEvent[] {
        const response = z.object({ items: z.array(z.unknown()) }).parse(input);
        return response.items.map((item) => parseEventEnvelope(item));
      },
    },
  );
}

export async function submitText(
  sessionId: string,
  text: string,
): Promise<void> {
  await requestRuntime(
    `/v1/sessions/${sessionId}/turns`,
    mutationReceiptSchema,
    {
      method: "POST",
      body: JSON.stringify({ text }),
    },
  );
}

export async function interrupt(sessionId: string): Promise<void> {
  await requestRuntime(
    `/v1/sessions/${sessionId}/interrupt`,
    mutationReceiptSchema,
    {
      method: "POST",
      body: JSON.stringify({ reason: "user_interruption" }),
    },
  );
}

export interface PlaybackAckReceipt {
  phase: "started" | "progress" | "stopped" | "queue_cleared";
  generationId: string;
  streamId: string;
  segmentId: string;
  playedPtsMs: number;
  bufferedMs: number;
  clientClockMs: number;
  transport: "audio_element" | "webrtc";
  reason?: "ended" | "interrupted" | "error" | "queue_cleared";
}

export async function acknowledgePlayback(
  sessionId: string,
  receipt: PlaybackAckReceipt,
): Promise<void> {
  const command = parseCommandEnvelope({
    command_id: crypto.randomUUID(),
    schema_version: "1.0",
    command_type: "cmd.playback.ack",
    issued_at: new Date().toISOString(),
    issuer: "web.chat",
    session_id: sessionId,
    generation_id: receipt.generationId,
    payload: {
      phase: receipt.phase,
      stream_id: receipt.streamId,
      segment_id: receipt.segmentId,
      played_pts_ms: receipt.playedPtsMs,
      buffered_ms: receipt.bufferedMs,
      client_clock_ms: receipt.clientClockMs,
      transport: receipt.transport,
      reason: receipt.reason ?? null,
    },
  });
  await requestRuntime(
    `/v1/sessions/${sessionId}/playback/ack`,
    mutationReceiptSchema,
    { method: "POST", body: JSON.stringify(command), timeoutMs: 2_000 },
  );
}

export async function resetSession(
  sessionId: string,
): Promise<SessionResetResult> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/reset`,
    sessionResetResultSchema,
    { method: "POST", body: JSON.stringify({ confirm: true }) },
  );
}

export async function getCharacterState(
  sessionId: string,
): Promise<CharacterKernelSnapshot> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/character-state`,
    runtimeParser(parseCharacterKernelSnapshot),
  );
}

export async function sendCharacterInteraction(
  sessionId: string,
  kind: "avatar_touch",
  region = "body",
): Promise<CharacterKernelSnapshot> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/character-interactions`,
    runtimeParser(parseCharacterKernelSnapshot),
    { method: "POST", body: JSON.stringify({ kind, region }) },
  );
}

export async function selectTtsProvider(
  sessionId: string,
  providerId: string,
): Promise<z.infer<typeof ttsSelectionSchema>> {
  return requestRuntime(
    `/v1/sessions/${sessionId}/tts/provider`,
    ttsSelectionSchema,
    { method: "PUT", body: JSON.stringify({ provider_id: providerId }) },
  );
}
