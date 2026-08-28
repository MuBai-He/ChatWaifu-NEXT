import type {
  MemoryProposal,
  MemoryRecord,
  MemorySource,
  PluginSnapshot,
  SkillDefinition,
  SkillRunSnapshot,
} from "@chatwaifu/protocol";

import type {
  CharacterKernelSnapshot,
  AliyunCloudTtsConfiguration,
  AliyunCloudTtsProviderId,
  CharacterProfile,
  CompanionSettings,
  CompanionStatus,
  MemoryItem,
  ModelRole,
  ModelRoleConfiguration,
  RuntimeHealth,
  ResourceStatus,
  SessionResetResult,
  SessionSnapshot,
  TtsProviderSnapshot,
} from "./types";
import { resolveRuntimeUrl } from "./runtimeEndpoint";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const runtimeUrl = await resolveRuntimeUrl();
  const response = await fetch(`${runtimeUrl}${path}`, {
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

export async function getCompanionStatus(): Promise<CompanionStatus> {
  return request<CompanionStatus>("/v1/companion/status");
}

export async function updateCompanionSettings(
  settings: Omit<CompanionSettings, "schema_version" | "updated_at">,
): Promise<CompanionSettings> {
  return request<CompanionSettings>("/v1/companion/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export async function sleepCompanionResources(): Promise<ResourceStatus> {
  return request<ResourceStatus>("/v1/companion/resources/sleep", {
    method: "POST",
    body: "{}",
  });
}

export async function wakeCompanionResources(): Promise<ResourceStatus> {
  return request<ResourceStatus>("/v1/companion/resources/wake", {
    method: "POST",
    body: "{}",
  });
}

export async function getCharacters(): Promise<CharacterProfile[]> {
  const response = await request<{ items: CharacterProfile[] }>(
    "/v1/characters",
  );
  return response.items;
}

export async function getModelConfigurations(): Promise<
  ModelRoleConfiguration[]
> {
  return (
    await request<{ items: ModelRoleConfiguration[] }>(
      "/v1/model-configurations",
    )
  ).items;
}

export async function updateModelConfiguration(
  role: ModelRole,
  configuration: Omit<
    ModelRoleConfiguration,
    "role" | "api_key_configured" | "updated_at"
  > & { api_key?: string; clear_api_key?: boolean },
): Promise<ModelRoleConfiguration> {
  return request<ModelRoleConfiguration>(`/v1/model-configurations/${role}`, {
    method: "PUT",
    body: JSON.stringify(configuration),
  });
}

export async function testModelConfiguration(
  role: ModelRole,
): Promise<{ status: string; characters?: number; dimensions?: number }> {
  return request(`/v1/model-configurations/${role}/test`, {
    method: "POST",
    body: "{}",
  });
}

export async function getCharacterState(
  sessionId: string,
): Promise<CharacterKernelSnapshot> {
  return request<CharacterKernelSnapshot>(
    `/v1/sessions/${sessionId}/character-state`,
  );
}

export async function sendCharacterInteraction(
  sessionId: string,
  kind: "avatar_touch",
  region = "body",
): Promise<CharacterKernelSnapshot> {
  return request<CharacterKernelSnapshot>(
    `/v1/sessions/${sessionId}/character-interactions`,
    {
      method: "POST",
      body: JSON.stringify({ kind, region }),
    },
  );
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
  await request(`/v1/sessions/${sessionId}/playback/ack`, {
    method: "POST",
    body: JSON.stringify({
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
    }),
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

export async function getAliyunTtsConfiguration(
  providerId: AliyunCloudTtsProviderId,
): Promise<AliyunCloudTtsConfiguration> {
  return request<AliyunCloudTtsConfiguration>(
    `/v1/tts/configurations/${providerId}`,
  );
}

export async function updateAliyunTtsConfiguration(
  configuration: Omit<
    AliyunCloudTtsConfiguration,
    "provider_id" | "api_key_configured" | "updated_at"
  > & {
    provider_id: AliyunCloudTtsProviderId;
    api_key?: string;
    clear_api_key?: boolean;
  },
): Promise<AliyunCloudTtsConfiguration> {
  const { provider_id: providerId, ...payload } = configuration;
  return request<AliyunCloudTtsConfiguration>(
    `/v1/tts/configurations/${providerId}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
}

export async function testAliyunTtsConfiguration(
  providerId: AliyunCloudTtsProviderId,
): Promise<{
  status: string;
  duration_ms?: number;
}> {
  return request(`/v1/tts/configurations/${providerId}/test`, {
    method: "POST",
    body: "{}",
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

export type McpTransport = "stdio" | "streamable_http" | "sse";
export type McpSandboxMode = "required" | "preferred" | "disabled";
export type McpTrustLevel = "trusted" | "untrusted";
export type McpNetworkPolicy = "deny" | "loopback" | "allow";

export interface McpConnectionInput {
  name: string;
  transport: McpTransport;
  command?: string[];
  url?: string;
  bearer_token?: string;
  clear_bearer_token?: boolean;
  enabled: boolean;
  allow_remote: boolean;
  timeout_seconds: number;
  trust_level: McpTrustLevel;
  sandbox_mode: McpSandboxMode;
  network_policy: McpNetworkPolicy;
}

export interface McpConnectionSnapshot
  extends Omit<McpConnectionInput, "bearer_token" | "clear_bearer_token"> {
  connection_id: string;
  bearer_token_configured: boolean;
  status?: "ready" | "disabled" | "untested" | "error";
  sandbox_backend?: string | null;
  capabilities?: McpCapabilitiesSnapshot;
  last_error?: string | null;
  last_tested_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface McpToolCapability {
  name: string;
  description?: string;
  input_schema?: Record<string, unknown>;
}

export interface McpResourceCapability {
  uri: string;
  name?: string;
  title?: string;
  description?: string;
  mime_type?: string;
}

export interface McpResourceTemplateCapability {
  uri_template: string;
  name: string;
  title?: string;
  description?: string;
  mime_type?: string;
}

export interface McpPromptArgument {
  name: string;
  description?: string;
  required?: boolean;
}

export interface McpPromptCapability {
  name: string;
  description?: string;
  arguments?: McpPromptArgument[];
}

export interface McpCapabilitiesSnapshot {
  connection_id?: string;
  protocol_version?: string | null;
  server_name?: string | null;
  server_version?: string | null;
  tools: McpToolCapability[];
  resources: McpResourceCapability[];
  resource_templates: McpResourceTemplateCapability[];
  prompts: McpPromptCapability[];
  discovered_at?: string | null;
}

export interface McpConnectionProbeResult {
  status: string;
  latency_ms?: number;
  protocol_version?: string;
  detail?: string;
}

export type McpConnectionTestResult =
  | McpConnectionSnapshot
  | McpConnectionProbeResult;

export async function getMcpConnections(): Promise<McpConnectionSnapshot[]> {
  return (
    await request<{ items: McpConnectionSnapshot[] }>("/v1/mcp/connections")
  ).items;
}

export async function createMcpConnection(
  connection: McpConnectionInput,
): Promise<McpConnectionSnapshot> {
  return request<McpConnectionSnapshot>("/v1/mcp/connections", {
    method: "POST",
    body: JSON.stringify(connection),
  });
}

export async function updateMcpConnection(
  connectionId: string,
  connection: Partial<McpConnectionInput>,
): Promise<McpConnectionSnapshot> {
  return request<McpConnectionSnapshot>(
    `/v1/mcp/connections/${encodeURIComponent(connectionId)}`,
    {
      method: "PUT",
      body: JSON.stringify(connection),
    },
  );
}

export async function deleteMcpConnection(
  connectionId: string,
): Promise<void> {
  await request(`/v1/mcp/connections/${encodeURIComponent(connectionId)}`, {
    method: "DELETE",
  });
}

export async function testMcpConnection(
  connectionId: string,
): Promise<McpConnectionTestResult> {
  return request<McpConnectionTestResult>(
    `/v1/mcp/connections/${encodeURIComponent(connectionId)}/test`,
    { method: "POST", body: "{}" },
  );
}

export async function getMcpCapabilities(
  connectionId: string,
): Promise<McpCapabilitiesSnapshot> {
  return request<McpCapabilitiesSnapshot>(
    `/v1/mcp/connections/${encodeURIComponent(connectionId)}/capabilities`,
  );
}

export async function readMcpResource(
  connectionId: string,
  uri: string,
): Promise<unknown> {
  return request(
    `/v1/mcp/connections/${encodeURIComponent(connectionId)}/resources/read`,
    { method: "POST", body: JSON.stringify({ uri }) },
  );
}

export async function getMcpPrompt(
  connectionId: string,
  name: string,
  args: Record<string, string>,
): Promise<unknown> {
  return request(
    `/v1/mcp/connections/${encodeURIComponent(connectionId)}/prompts/get`,
    {
      method: "POST",
      body: JSON.stringify({ name, arguments: args }),
    },
  );
}
