import { useEffect, useState, type ReactNode, type FormEvent } from "react";
import {
  isDesktopHost,
  runtimeFetchWithConnection,
  setRemoteRuntimeConnection,
} from "../chat/runtimeEndpoint";
import "./connection.css";

type ClientConnection =
  { mode: "local" } | { mode: "remote"; base_url: string; token: string };
const browserKey = "chatwaifu.client.connection";

function validateRemote(address: string, token: string): ClientConnection {
  const url = new URL(address.trim());
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (
    !(url.protocol === "https:" || (url.protocol === "http:" && loopback)) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  ) {
    throw new Error(
      "请填写 HTTPS 服务器根地址。本机 SSH 隧道可使用 http://127.0.0.1:端口。",
    );
  }
  if (token.trim().length < 32)
    throw new Error("请填写服务器生成的访问令牌（至少 32 字符）。");
  return { mode: "remote", base_url: url.origin, token: token.trim() };
}

async function activate(connection: ClientConnection): Promise<void> {
  setRemoteRuntimeConnection(
    connection.mode === "remote"
      ? { baseUrl: connection.base_url, token: connection.token }
      : null,
  );
  if (connection.mode === "local" && isDesktopHost()) {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("start_runtime");
  }
}

export function RuntimeConnectionGate({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [active, setActive] = useState<ClientConnection | null>(null);
  const [mode, setMode] = useState<"remote" | "local">("remote");
  const [address, setAddress] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    async function load() {
      let saved: ClientConnection | null;
      if (isDesktopHost()) {
        const [{ invoke }, { listen }] = await Promise.all([
          import("@tauri-apps/api/core"),
          import("@tauri-apps/api/event"),
        ]);
        const stop = await listen("client-connection-changed", () =>
          window.location.reload(),
        );
        if (cancelled) {
          stop();
          return;
        }
        unlisten = stop;
        saved = await invoke<ClientConnection | null>("get_client_connection");
      } else {
        const raw = sessionStorage.getItem(browserKey);
        saved = raw ? (JSON.parse(raw) as ClientConnection) : null;
        setAddress(localStorage.getItem(`${browserKey}.address`) ?? "");
        // Preserve explicitly configured source-development environments.
        if (!saved && import.meta.env.VITE_RUNTIME_URL)
          saved = { mode: "local" };
      }
      if (cancelled) return;
      if (saved) {
        if (saved.mode === "remote")
          saved = validateRemote(saved.base_url, saved.token);
        else if (saved.mode !== "local")
          throw new Error("连接配置无效，请重新填写。");
        setMode(saved.mode);
        if (saved.mode === "remote") {
          setAddress(saved.base_url);
          setToken(saved.token);
        }
        await activate(saved);
        if (!cancelled) setActive(saved);
      }
    }
    void load()
      .catch(() => {
        if (!cancelled) setError("无法恢复连接设置，请重新选择连接方式。");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  async function connect(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const next: ClientConnection =
        mode === "remote" ? validateRemote(address, token) : { mode: "local" };
      if (next.mode === "remote") {
        const response = await runtimeFetchWithConnection(
          { baseUrl: next.base_url, token: next.token },
          "/v1/characters",
          { signal: AbortSignal.timeout(10_000), redirect: "error" },
        );
        if (!response.ok)
          throw new Error(
            response.status === 401
              ? "访问令牌不正确。"
              : `服务器连接失败（${response.status}）。`,
          );
        const characters: unknown = await response.json();
        if (
          !characters ||
          typeof characters !== "object" ||
          !("items" in characters) ||
          !Array.isArray(characters.items)
        )
          throw new Error("这个地址没有返回有效的 ChatWaifu 服务。");
      }
      if (isDesktopHost()) {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("set_client_connection", { connection: next });
        // The native event reloads all windows, including this one.
      } else {
        sessionStorage.setItem(browserKey, JSON.stringify(next));
        if (next.mode === "remote")
          localStorage.setItem(`${browserKey}.address`, next.base_url);
        window.location.reload();
      }
    } catch (cause) {
      setError(
        cause instanceof TypeError
          ? "无法连接：请检查地址、HTTPS 证书及服务器允许的前端来源。"
          : cause instanceof Error
            ? cause.message
            : "连接失败，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading)
    return <div className="runtime-connection-screen">正在读取连接设置…</div>;
  if (active && !editing)
    return (
      <>
        {children}
        <button
          className="runtime-connection-switch"
          onClick={() => setEditing(true)}
          title={active.mode === "remote" ? active.base_url : "本地服务"}
        >
          {active.mode === "remote" ? "远程服务器" : "本地服务"} · 切换
        </button>
      </>
    );
  return (
    <div className="runtime-connection-screen">
      <form
        className="runtime-connection-card"
        onSubmit={(event) => void connect(event)}
      >
        <h1>连接 ChatWaifu</h1>
        <p>角色和对话在服务器上持续运行。此设备负责显示、麦克风和声音播放。</p>
        <label>
          运行方式
          <select
            value={mode}
            disabled={busy}
            onChange={(event) =>
              setMode(event.target.value as "local" | "remote")
            }
          >
            <option value="remote">连接远程服务器 · 轻量客户端</option>
            <option value="local">
              {isDesktopHost() ? "在这台设备上运行后端" : "连接本机开发后端"}
            </option>
          </select>
        </label>
        {mode === "remote" && (
          <>
            <label>
              服务器地址
              <input
                type="url"
                required
                placeholder="https://api.example.com"
                autoComplete="url"
                value={address}
                disabled={busy}
                onChange={(event) => setAddress(event.target.value)}
              />
            </label>
            <label>
              访问令牌
              <input
                type="password"
                required
                autoComplete="off"
                value={token}
                disabled={busy}
                onChange={(event) => setToken(event.target.value)}
              />
            </label>
            <small>
              {isDesktopHost()
                ? "连接信息保存在此设备的用户配置目录。"
                : "地址会记住，令牌只保留在当前浏览器会话中。语音需要 HTTPS 页面或 localhost。"}
            </small>
          </>
        )}
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "正在验证连接…" : "连接"}
        </button>
        {active && (
          <button
            type="button"
            disabled={busy}
            onClick={() => setEditing(false)}
          >
            返回当前连接
          </button>
        )}
      </form>
    </div>
  );
}
