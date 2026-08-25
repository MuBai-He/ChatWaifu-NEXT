import type {
  MemoryProposal,
  MemoryRecord,
  MemorySource,
  PluginSnapshot,
  SkillDefinition,
  SkillRunSnapshot,
} from "@chatwaifu/protocol";

import type {
  CharacterProfile,
  MemoryItem,
  RuntimeHealth,
  SessionResetResult,
  SessionSnapshot,
  TtsProviderSnapshot,
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

export async function getTtsProviders(
  sessionId: string,
): Promise<TtsProviderSnapshot[]> {
  const response = await request<{ items: TtsProviderSnapshot[] }>(
    `/v1/tts/providers?session_id=${encodeURIComponent(sessionId)}`,
  );
  return response.items;
}

export async function selectTtsProvider(
  sessionId: string,
  providerId: string,
): Promise<{ session_id: string; provider_id: string }> {
  return request(`/v1/sessions/${sessionId}/tts/provider`, {
    method: "PUT",
    body: JSON.stringify({ provider_id: providerId }),
  });
}

export async function getMemory(): Promise<MemoryItem[]> {
  const response = await request<{ items: MemoryItem[] }>("/v1/memory");
  return response.items;
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
  return (await request<{ items: MemoryItem[] }>(`/v1/memory${suffix}`)).items;
}

export async function getMemoryProposals(
  status = "pending",
): Promise<MemoryProposal[]> {
  return (
    await request<{ items: MemoryProposal[] }>(
      `/v1/memory/proposals?status=${encodeURIComponent(status)}`,
    )
  ).items;
}

export async function decideMemoryProposal(
  sessionId: string,
  proposalId: string,
  decision: "accept" | "reject",
): Promise<MemoryProposal> {
  return request<MemoryProposal>(
    `/v1/sessions/${sessionId}/memory/proposals/${proposalId}/decision`,
    { method: "POST", body: JSON.stringify({ decision }) },
  );
}

export async function getMemorySources(
  memoryId: string,
): Promise<MemorySource[]> {
  return (
    await request<{ items: MemorySource[] }>(`/v1/memory/${memoryId}/sources`)
  ).items;
}

export async function correctMemory(
  sessionId: string,
  memoryId: string,
  text: string,
): Promise<MemoryRecord> {
  return request<MemoryRecord>(`/v1/sessions/${sessionId}/memory/${memoryId}`, {
    method: "PATCH",
    body: JSON.stringify({ text }),
  });
}

export async function setMemoryPinned(
  sessionId: string,
  memoryId: string,
  pinned: boolean,
): Promise<MemoryRecord> {
  return request<MemoryRecord>(
    `/v1/sessions/${sessionId}/memory/${memoryId}/pinned`,
    { method: "PUT", body: JSON.stringify({ pinned }) },
  );
}

export async function forgetMemory(
  sessionId: string,
  memoryId: string,
): Promise<void> {
  await request(`/v1/sessions/${sessionId}/memory/${memoryId}`, {
    method: "DELETE",
  });
}

export interface SkillConfirmation {
  request_id: string;
  skill_run_id: string;
  skill_id: string;
  capability: string;
  permissions: string[];
  side_effect: string;
  reason: string;
  requested_at: string;
}

export async function getSkills(): Promise<SkillDefinition[]> {
  return (await request<{ items: SkillDefinition[] }>("/v1/skills")).items;
}

export async function getSkillInstructions(skillId: string): Promise<string> {
  return (
    await request<{ instructions: string }>(
      `/v1/skills/${encodeURIComponent(skillId)}/instructions`,
    )
  ).instructions;
}

export async function getPlugins(): Promise<PluginSnapshot[]> {
  return (await request<{ items: PluginSnapshot[] }>("/v1/plugins")).items;
}

export async function installExamplePlugin(): Promise<PluginSnapshot> {
  return request<PluginSnapshot>("/v1/plugins/install-example", {
    method: "POST",
    body: JSON.stringify({ example_id: "local-echo" }),
  });
}

export async function installLocalPlugin(
  sourcePath: string,
): Promise<PluginSnapshot> {
  return request<PluginSnapshot>("/v1/plugins/install", {
    method: "POST",
    body: JSON.stringify({ source_path: sourcePath }),
  });
}

export async function setPluginEnabled(
  pluginId: string,
  enabled: boolean,
): Promise<PluginSnapshot> {
  return request<PluginSnapshot>(
    `/v1/plugins/${encodeURIComponent(pluginId)}/enabled`,
    {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    },
  );
}

export async function uninstallPlugin(pluginId: string): Promise<void> {
  await request(`/v1/plugins/${encodeURIComponent(pluginId)}`, {
    method: "DELETE",
  });
}

export async function invokeSkill(
  sessionId: string,
  skillId: string,
  capability: string,
  args: Record<string, unknown>,
): Promise<SkillRunSnapshot> {
  return request<SkillRunSnapshot>(`/v1/sessions/${sessionId}/skill-runs`, {
    method: "POST",
    body: JSON.stringify({
      skill_id: skillId,
      capability,
      arguments: args,
    }),
  });
}

export async function getSkillRuns(
  sessionId: string,
): Promise<SkillRunSnapshot[]> {
  return (
    await request<{ items: SkillRunSnapshot[] }>(
      `/v1/sessions/${sessionId}/skill-runs`,
    )
  ).items;
}

export async function getSkillConfirmations(
  sessionId: string,
): Promise<SkillConfirmation[]> {
  return (
    await request<{ items: SkillConfirmation[] }>(
      `/v1/sessions/${sessionId}/skill-confirmations`,
    )
  ).items;
}

export async function decideSkillConfirmation(
  requestId: string,
  decision: "allow_once" | "allow_session" | "allow_always" | "deny",
): Promise<SkillRunSnapshot> {
  return request<SkillRunSnapshot>(`/v1/skill-confirmations/${requestId}`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
}

export async function cancelSkillRun(
  skillRunId: string,
): Promise<SkillRunSnapshot> {
  return request<SkillRunSnapshot>(`/v1/skill-runs/${skillRunId}/cancel`, {
    method: "POST",
    body: "{}",
  });
}
