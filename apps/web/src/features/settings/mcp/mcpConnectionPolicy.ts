import type {
  McpConnectionInput,
  McpConnectionSnapshot,
  McpNetworkPolicy,
  McpSandboxMode,
  McpTransport,
  McpTrustLevel,
} from "../../chat/runtimeClient";

export interface ConnectionDraft {
  name: string;
  transport: McpTransport;
  commandText: string;
  url: string;
  bearerToken: string;
  enabled: boolean;
  allowRemote: boolean;
  timeoutSeconds: number;
  trustLevel: McpTrustLevel;
  sandboxMode: McpSandboxMode;
  networkPolicy: McpNetworkPolicy;
}

export type PayloadValidation =
  { ok: true; payload: McpConnectionInput } | { ok: false; error: string };

export function emptyConnectionDraft(): ConnectionDraft {
  return {
    name: "",
    transport: "stdio",
    commandText: "",
    url: "",
    bearerToken: "",
    enabled: true,
    allowRemote: false,
    timeoutSeconds: 30,
    trustLevel: "untrusted",
    sandboxMode: "required",
    networkPolicy: "deny",
  };
}

export function validateConnectionDraft(
  draft: ConnectionDraft,
): PayloadValidation {
  const name = draft.name.trim();
  if (!name) return { ok: false, error: "请填写连接名称。" };
  if (
    !Number.isFinite(draft.timeoutSeconds) ||
    draft.timeoutSeconds < 1 ||
    draft.timeoutSeconds > 600
  )
    return { ok: false, error: "超时必须在 1 到 600 秒之间。" };
  if (
    draft.transport === "stdio" &&
    draft.sandboxMode === "disabled" &&
    draft.trustLevel !== "trusted"
  )
    return {
      ok: false,
      error: "不受信任的 stdio 进程不能关闭沙箱，请启用沙箱或改为受信任。",
    };
  if (
    draft.transport === "stdio" &&
    draft.sandboxMode === "disabled" &&
    draft.networkPolicy !== "allow"
  )
    return {
      ok: false,
      error: "关闭沙箱后无法保证网络隔离，stdio 网络策略必须设为允许网络。",
    };
  if (draft.transport === "stdio" && draft.networkPolicy === "loopback")
    return {
      ok: false,
      error: "本地 stdio 不提供仅回环网络策略，请选择禁止网络或允许网络。",
    };

  const base: McpConnectionInput = {
    name,
    transport: draft.transport,
    enabled: draft.enabled,
    allow_remote: draft.transport === "stdio" ? false : draft.allowRemote,
    timeout_seconds: draft.timeoutSeconds,
    trust_level: draft.trustLevel,
    sandbox_mode: draft.transport === "stdio" ? draft.sandboxMode : "disabled",
    network_policy:
      draft.transport === "stdio"
        ? draft.networkPolicy
        : draft.allowRemote
          ? "allow"
          : "loopback",
    ...(draft.bearerToken.trim()
      ? { bearer_token: draft.bearerToken.trim() }
      : {}),
  };
  if (draft.transport === "stdio") {
    const command = commandFromText(draft.commandText);
    return command.length
      ? { ok: true, payload: { ...base, command } }
      : { ok: false, error: "请填写 stdio 服务启动命令。" };
  }

  const url = draft.url.trim();
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return { ok: false, error: "请输入有效的 MCP 服务 URL。" };
  }
  if (!new Set(["http:", "https:"]).has(parsed.protocol))
    return { ok: false, error: "MCP 服务 URL 仅支持 HTTP 或 HTTPS。" };
  if (!isLoopbackHost(parsed.hostname) && !draft.allowRemote)
    return {
      ok: false,
      error: "非回环地址需要明确开启“允许远程网络连接”。",
    };
  return { ok: true, payload: { ...base, url } };
}

export function draftFromConnection(
  connection: McpConnectionSnapshot,
): ConnectionDraft {
  return {
    name: connection.name,
    transport: connection.transport,
    commandText: (connection.command ?? []).join("\n"),
    url: connection.url ?? "",
    bearerToken: "",
    enabled: connection.enabled,
    allowRemote: connection.allow_remote,
    timeoutSeconds: connection.timeout_seconds,
    trustLevel: connection.trust_level,
    sandboxMode: connection.sandbox_mode,
    networkPolicy: connection.network_policy,
  };
}

export function payloadFromConnection(
  connection: McpConnectionSnapshot,
  enabled: boolean,
): McpConnectionInput {
  return {
    name: connection.name,
    transport: connection.transport,
    ...(connection.command?.length ? { command: [...connection.command] } : {}),
    ...(connection.url ? { url: connection.url } : {}),
    enabled,
    allow_remote: connection.allow_remote,
    timeout_seconds: connection.timeout_seconds,
    trust_level: connection.trust_level,
    sandbox_mode: connection.sandbox_mode,
    network_policy: connection.network_policy,
  };
}

export function upsertConnection(
  connections: McpConnectionSnapshot[],
  saved: McpConnectionSnapshot,
): McpConnectionSnapshot[] {
  const exists = connections.some(
    (connection) => connection.connection_id === saved.connection_id,
  );
  return exists
    ? connections.map((connection) =>
        connection.connection_id === saved.connection_id ? saved : connection,
      )
    : [...connections, saved];
}

export function transportLabel(transport: McpTransport): string {
  return {
    stdio: "stdio",
    streamable_http: "Streamable HTTP",
    sse: "SSE",
  }[transport];
}

export function sandboxStatusLabel(connection: McpConnectionSnapshot): string {
  if (connection.sandbox_mode === "disabled") return "沙箱：已关闭";
  if (connection.sandbox_backend) return `沙箱：${connection.sandbox_backend}`;
  return "沙箱：尚未测试";
}

export function formatMcpResult(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function commandFromText(value: string): string[] {
  return value
    .split(/\r?\n/u)
    .map((part) => part.trim())
    .filter(Boolean);
}

function isLoopbackHost(hostname: string): boolean {
  return new Set(["localhost", "127.0.0.1", "::1", "[::1]"]).has(
    hostname.toLowerCase(),
  );
}
