export type DesktopRuntimeStatus = {
  state: "starting" | "ready" | "backoff" | "stopped" | "circuit_open";
  runtime_url?: string | null;
  pid?: number | null;
  workers: string[];
  restart_count: number;
  detail?: string | null;
};

const browserRuntimeUrl = "http://127.0.0.1:8765";
const statusEvent = "desktop-runtime-status-changed";
// Keep this beyond the native supervisor's complete bounded startup window:
// 300s for selected Worker Packs, 120s for the Runtime server, and 30s of
// supervisor grace. A shorter Web timer abandons healthy CUDA cold starts and
// leaves the desktop session permanently offline even when native startup later
// reaches ready. The final 5s is only for delivery of the native ready event.
export const DESKTOP_RUNTIME_RESOLUTION_TIMEOUT_MS = 455_000;
let cachedRuntimeUrl: string | null = null;
let pendingResolution: Promise<string> | null = null;

export function isDesktopHost(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export async function resolveRuntimeUrl(forceRefresh = false): Promise<string> {
  if (!isDesktopHost()) return browserRuntimeUrl;
  if (!forceRefresh && cachedRuntimeUrl) return cachedRuntimeUrl;
  if (!forceRefresh && pendingResolution) return pendingResolution;
  const resolution = resolveDesktopRuntimeUrl();
  pendingResolution = resolution;
  try {
    cachedRuntimeUrl = await resolution;
    return cachedRuntimeUrl;
  } finally {
    if (pendingResolution === resolution) pendingResolution = null;
  }
}

export function runtimeAssetUrl(path: string): string {
  return `${cachedRuntimeUrl ?? browserRuntimeUrl}${path}`;
}

export async function runtimeWebSocketUrl(
  forceRefresh = false,
): Promise<string> {
  const url = new URL(await resolveRuntimeUrl(forceRefresh));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString().replace(/\/$/, "");
}

export async function readDesktopRuntimeStatus(): Promise<DesktopRuntimeStatus | null> {
  if (!isDesktopHost()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<DesktopRuntimeStatus>("get_runtime_status");
}

export async function restartDesktopRuntime(): Promise<DesktopRuntimeStatus | null> {
  if (!isDesktopHost()) return null;
  cachedRuntimeUrl = null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<DesktopRuntimeStatus>("restart_runtime");
}

async function resolveDesktopRuntimeUrl(): Promise<string> {
  const [{ invoke }, { listen }] = await Promise.all([
    import("@tauri-apps/api/core"),
    import("@tauri-apps/api/event"),
  ]);
  const current = await invoke<DesktopRuntimeStatus>("get_runtime_status");
  const ready = endpointFrom(current);
  if (ready) return ready;

  return new Promise<string>((resolve, reject) => {
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
      const endpoint = endpointFrom(event.payload);
      if (endpoint) {
        finish(() => resolve(endpoint));
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
        const endpoint = endpointFrom(status);
        if (endpoint) finish(() => resolve(endpoint));
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

function endpointFrom(status: DesktopRuntimeStatus): string | null {
  if (status.state !== "ready" || !status.runtime_url) return null;
  const url = new URL(status.runtime_url);
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "[::1]", "::1"].includes(url.hostname)
  )
    throw new Error("桌面宿主返回了不安全的 Runtime 地址。将在本地阻止连接。");
  return url.toString().replace(/\/$/, "");
}
