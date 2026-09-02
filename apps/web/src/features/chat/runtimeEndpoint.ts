export type DesktopRuntimeStatus = {
  state: "starting" | "ready" | "backoff" | "stopped" | "circuit_open";
  runtime_url?: string | null;
  pid?: number | null;
  workers: string[];
  token?: string | null;
  restart_count: number;
  detail?: string | null;
};

export interface RuntimeConnection {
  baseUrl: string;
  token?: string | null;
  restartCount?: number;
}

const browserRuntimeUrl = "http://127.0.0.1:8765";
const statusEvent = "desktop-runtime-status-changed";
// Keep this beyond the native supervisor's complete bounded startup window:
// 300s for selected Worker Packs, 120s for the Runtime server, and 30s of
// supervisor grace. A shorter Web timer abandons healthy CUDA cold starts and
// leaves the desktop session permanently offline even when native startup later
// reaches ready. The final 5s is only for delivery of the native ready event.
export const DESKTOP_RUNTIME_RESOLUTION_TIMEOUT_MS = 455_000;
let cachedConnection: RuntimeConnection | null = null;
let pendingConnectionResolution: Promise<RuntimeConnection> | null = null;

export function isDesktopHost(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function resolveBrowserToken(): string | null {
  if (typeof window === "undefined") return null;
  const globalToken = (
    window as unknown as { __CHATWAIFU_RUNTIME_TOKEN__?: string }
  ).__CHATWAIFU_RUNTIME_TOKEN__;
  if (globalToken) return globalToken;
  try {
    const viteToken = (
      import.meta as unknown as { env?: { VITE_RUNTIME_TOKEN?: string } }
    ).env?.VITE_RUNTIME_TOKEN;
    if (viteToken) return viteToken;
  } catch {
    // import.meta may be undefined in some environments
  }
  return null;
}

export async function resolveRuntimeConnection(
  forceRefresh = false,
): Promise<RuntimeConnection> {
  if (!isDesktopHost()) {
    return {
      baseUrl: browserRuntimeUrl,
      token: resolveBrowserToken(),
      restartCount: 0,
    };
  }
  if (!forceRefresh && cachedConnection) return cachedConnection;
  if (!forceRefresh && pendingConnectionResolution)
    return pendingConnectionResolution;
  const resolution = resolveDesktopRuntimeConnection();
  pendingConnectionResolution = resolution;
  try {
    cachedConnection = await resolution;
    return cachedConnection;
  } finally {
    if (pendingConnectionResolution === resolution)
      pendingConnectionResolution = null;
  }
}

export async function resolveRuntimeUrl(forceRefresh = false): Promise<string> {
  const connection = await resolveRuntimeConnection(forceRefresh);
  return connection.baseUrl;
}

export function runtimeAssetUrl(path: string): string {
  const baseUrl = cachedConnection?.baseUrl ?? browserRuntimeUrl;
  return `${baseUrl}${path}`;
}

export async function runtimeWebSocketUrl(
  forceRefresh = false,
): Promise<string> {
  const url = new URL(await resolveRuntimeUrl(forceRefresh));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString().replace(/\/$/, "");
}

export async function runtimeFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const connection = await resolveRuntimeConnection();
  const url =
    path.startsWith("http://") || path.startsWith("https://")
      ? path
      : `${connection.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
  const headers = new Headers(init?.headers);
  if (connection.token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${connection.token}`);
  }
  return fetch(url, {
    ...init,
    headers,
  });
}

export async function acquireWsTicket(
  connection?: RuntimeConnection,
): Promise<string | null> {
  try {
    const conn = connection ?? (await resolveRuntimeConnection());
    const response = await runtimeFetch(
      `${conn.baseUrl}/v1/runtime/ws-ticket`,
      { method: "POST" },
    );
    if (!response.ok) return null;
    const data = (await response.json()) as { ticket?: string };
    return typeof data.ticket === "string" ? data.ticket : null;
  } catch {
    return null;
  }
}

export async function readDesktopRuntimeStatus(): Promise<DesktopRuntimeStatus | null> {
  if (!isDesktopHost()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<DesktopRuntimeStatus>("get_runtime_status");
}

export async function restartDesktopRuntime(): Promise<DesktopRuntimeStatus | null> {
  if (!isDesktopHost()) return null;
  cachedConnection = null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<DesktopRuntimeStatus>("restart_runtime");
}

async function resolveDesktopRuntimeConnection(): Promise<RuntimeConnection> {
  const [{ invoke }, { listen }] = await Promise.all([
    import("@tauri-apps/api/core"),
    import("@tauri-apps/api/event"),
  ]);
  const current = await invoke<DesktopRuntimeStatus>("get_runtime_status");
  const ready = connectionFrom(current);
  if (ready) return ready;

  return new Promise<RuntimeConnection>((resolve, reject) => {
    let settled = false;
    let unlisten: (() => void) | undefined;
    const timer = window.setTimeout(() => {
      finish(() =>
        reject(new Error("本地 Runtime 启动超时，请在设置中重启本地服务。")),
      );
    }, DESKTOP_RUNTIME_RESOLUTION_TIMEOUT_MS);
    const finish = (complete: () => void) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      unlisten?.();
      complete();
    };
    void listen<DesktopRuntimeStatus>(statusEvent, (event) => {
      const conn = connectionFrom(event.payload);
      if (conn) {
        finish(() => resolve(conn));
      } else if (event.payload.state === "circuit_open") {
        finish(() =>
          reject(
            new Error(
              event.payload.detail ??
                "本地 Runtime 自动恢复已暂停，请手动重启。",
            ),
          ),
        );
      }
    })
      .then((stop) => {
        if (settled) {
          stop();
          return null;
        }
        unlisten = stop;
        return invoke<DesktopRuntimeStatus>("start_runtime");
      })
      .then((status) => {
        if (!status) return;
        const conn = connectionFrom(status);
        if (conn) finish(() => resolve(conn));
      })
      .catch((error: unknown) =>
        finish(() =>
          reject(
            error instanceof Error
              ? error
              : new Error("无法启动本地 Runtime。"),
          ),
        ),
      );
  });
}

function connectionFrom(
  status: DesktopRuntimeStatus,
): RuntimeConnection | null {
  if (status.state !== "ready" || !status.runtime_url) return null;
  const url = new URL(status.runtime_url);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "[::1]", "::1"].includes(url.hostname)
  )
    throw new Error("桌面宿主返回了不安全的 Runtime 地址。将在本地阻止连接。");
  return {
    baseUrl: url.toString().replace(/\/$/, ""),
    token: status.token ?? null,
    restartCount: status.restart_count,
  };
}
