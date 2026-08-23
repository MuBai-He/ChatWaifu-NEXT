import type {
  CharacterProfile,
  MemoryItem,
  RuntimeHealth,
  SessionResetResult,
  SessionSnapshot,
} from "./types";

export const RUNTIME_URL = "http://127.0.0.1:8765";
export const RUNTIME_WS_URL = "ws://127.0.0.1:8765";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${RUNTIME_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(
      detail?.detail ?? `Runtime request failed (${response.status})`,
    );
  }
  return (await response.json()) as T;
}

export async function getHealth(): Promise<RuntimeHealth> {
  return request<RuntimeHealth>("/v1/runtime/health");
}

export async function getCharacters(): Promise<CharacterProfile[]> {
  const response = await request<{ items: CharacterProfile[] }>(
    "/v1/characters",
  );
  return response.items;
}

export async function createSession(
  characterId: string,
): Promise<SessionSnapshot> {
  return request<SessionSnapshot>("/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ character_id: characterId }),
  });
}

export async function getSession(sessionId: string): Promise<SessionSnapshot> {
  return request<SessionSnapshot>(`/v1/sessions/${sessionId}`);
}

export async function getMessages(
  sessionId: string,
): Promise<
  Array<{ turn_id: string; role: "user" | "assistant"; committed_text: string }>
> {
  const response = await request<{
    items: Array<{
      turn_id: string;
      role: "user" | "assistant";
      committed_text: string;
    }>;
  }>(`/v1/sessions/${sessionId}/messages`);
  return response.items;
}

export async function submitText(
  sessionId: string,
  text: string,
): Promise<void> {
  await request(`/v1/sessions/${sessionId}/turns`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export async function interrupt(sessionId: string): Promise<void> {
  await request(`/v1/sessions/${sessionId}/interrupt`, {
    method: "POST",
    body: JSON.stringify({ reason: "user_interruption" }),
  });
}

export async function resetSession(
  sessionId: string,
): Promise<SessionResetResult> {
  return request<SessionResetResult>(`/v1/sessions/${sessionId}/reset`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
}

export async function getMemory(): Promise<MemoryItem[]> {
  const response = await request<{ items: MemoryItem[] }>("/v1/memory");
  return response.items;
}

export async function runStatusSkill(sessionId: string): Promise<string> {
  const response = await request<{ spoken_summary?: string }>(
    `/v1/sessions/${sessionId}/skills/runtime.status`,
    { method: "POST", body: "{}" },
  );
  return response.spoken_summary ?? "Runtime status is available.";
}
