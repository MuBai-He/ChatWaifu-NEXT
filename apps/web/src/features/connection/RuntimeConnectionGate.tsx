import { useEffect, useState, type ReactNode, type FormEvent } from "react";
import {
  ArrowRight,
  Check,
  Cloud,
  Eye,
  EyeOff,
  Laptop,
  LoaderCircle,
  ShieldCheck,
} from "lucide-react";
import { BrandMark } from "../../components/BrandMark";
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
  const loopback = url.hostname === "127.0.0.1";
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
  const [showToken, setShowToken] = useState(false);

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
      {isDesktopHost() && (
        <div
          className="connection-window-grip"
          data-tauri-drag-region
          aria-hidden="true"
        />
      )}
      <form
        className="runtime-connection-card"
        onSubmit={(event) => void connect(event)}
      >
        <header className="connection-brand">
          <span className="connection-brand-icon">
            <BrandMark />
          </span>
          <span>
            ChatWaifu<small>你的桌面陪伴</small>
          </span>
        </header>
        <div className="connection-heading">
          <h1>{active ? "换一种连接方式" : "从这里开始"}</h1>
          <p>选择运行方式，让陪伴来到桌面。</p>
        </div>
        <fieldset className="connection-modes" disabled={busy}>
          <legend>运行方式</legend>
          {(
            [
              {
                value: "local",
                title: "本机运行",
                detail: "服务在这台设备上",
                icon: Laptop,
              },
              {
                value: "remote",
                title: "连接服务器",
                detail: "只运行轻量桌宠",
                icon: Cloud,
              },
            ] as const
          ).map(({ value, title, detail, icon: Icon }) => (
            <label className="connection-mode" key={value}>
              <input
                type="radio"
                name="connection-mode"
                value={value}
                checked={mode === value}
                onChange={() => {
                  setMode(value);
                  setError("");
                }}
              />
              <span className="connection-mode-content">
                <Icon size={21} strokeWidth={1.6} aria-hidden="true" />
                <span className="connection-mode-check" aria-hidden="true">
                  <Check size={11} strokeWidth={3} />
                </span>
                <strong>{title}</strong>
                <small>{detail}</small>
              </span>
            </label>
          ))}
        </fieldset>
        {mode === "remote" && (
          <div className="connection-fields">
            <label className="connection-field">
              服务器地址
              <input
                type="url"
                required
                placeholder="https://你的服务器地址"
                autoComplete="url"
                value={address}
                disabled={busy}
                onChange={(event) => setAddress(event.target.value)}
              />
            </label>
            <label className="connection-field">
              <span>
                访问令牌 <small>由服务器提供</small>
              </span>
              <span className="connection-secret">
                <input
                  type={showToken ? "text" : "password"}
                  required
                  autoComplete="off"
                  placeholder="粘贴访问令牌"
                  value={token}
                  disabled={busy}
                  onChange={(event) => setToken(event.target.value)}
                />
                <button
                  type="button"
                  className="connection-reveal"
                  aria-label={showToken ? "隐藏令牌" : "显示令牌"}
                  aria-pressed={showToken}
                  onClick={() => setShowToken(!showToken)}
                >
                  {showToken ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </span>
            </label>
          </div>
        )}
        {mode === "local" && (
          <div className="connection-local-note">
            <Laptop size={24} strokeWidth={1.5} aria-hidden="true" />
            <strong>
              {isDesktopHost() ? "在本机开启陪伴" : "连接本机开发服务"}
            </strong>
            <p>
              {isDesktopHost()
                ? "界面与后端一起运行，使用这台设备上的模型和服务配置。"
                : "请先启动本机后端，再进入界面。"}
            </p>
          </div>
        )}
        {error && <p role="alert">{error}</p>}
        <button className="connection-submit" type="submit" disabled={busy}>
          {busy ? "正在连接…" : mode === "remote" ? "连接并进入" : "启动并进入"}
          {busy ? (
            <LoaderCircle
              className="connection-spinner"
              size={17}
              aria-hidden="true"
            />
          ) : (
            <ArrowRight size={17} aria-hidden="true" />
          )}
        </button>
        <p className="connection-footnote">
          <ShieldCheck size={14} aria-hidden="true" />
          {mode === "local"
            ? "随时可以切换为远程连接"
            : isDesktopHost()
              ? "连接信息仅保存在这台设备"
              : "令牌仅保留在当前浏览器会话"}
        </p>
        {active && (
          <button
            type="button"
            className="connection-back"
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
