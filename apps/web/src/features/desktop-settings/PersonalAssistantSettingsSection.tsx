import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  isDesktopHost,
  resolveRuntimeConnection,
  runtimeFetchWithConnection,
  type RuntimeConnection,
} from "../chat/runtimeEndpoint";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { SettingsGroup, SettingsSectionIntro } from "./SettingsPrimitives";

type Status = { state: string; authorization_available: boolean };
type Flow = {
  cancelled: boolean;
  nativeId?: string;
  state?: string;
  connection: RuntimeConnection;
  sessionId: string;
};

async function cancelFlow(flow: Flow) {
  flow.cancelled = true;
  await Promise.allSettled([
    flow.nativeId
      ? invoke("cancel_google_oauth", { flowId: flow.nativeId })
      : Promise.resolve(),
    flow.state
      ? runtimeFetchWithConnection(
          flow.connection,
          "/v1/personal-assistant/oauth/cancel",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              session_id: flow.sessionId,
              state: flow.state,
            }),
            signal: AbortSignal.timeout(10000),
          },
        )
      : Promise.resolve(),
  ]);
}

export function PersonalAssistantSettingsSection({
  context,
}: {
  context: DesktopSettingsContext;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const active = useRef<Flow | null>(null);
  const revision = useRef({ value: 0 });
  const sessionId = context.sessionId;

  useEffect(() => {
    const requestRevision = revision.current;
    let disposed = false;
    const controller = new AbortController();
    void (async () => {
      try {
        const connection = await resolveRuntimeConnection();
        const response = await runtimeFetchWithConnection(
          connection,
          "/v1/personal-assistant/status",
          {
            signal: controller.signal,
          },
        );
        if (!response.ok)
          throw new Error("无法读取个人助理状态，请检查服务器连接。");
        const data = (await response.json()) as Status;
        if (!disposed) setStatus(data);
      } catch {
        if (!disposed) setMessage("无法读取个人助理状态，请检查服务器连接。");
      }
    })();
    return () => {
      ++requestRevision.value;
      disposed = true;
      controller.abort();
      if (active.current) void cancelFlow(active.current);
      active.current = null;
      setBusy(false);
    };
  }, [sessionId]);

  async function connect() {
    if (!sessionId || busy || !isDesktopHost()) return;
    setBusy(true);
    setMessage("");
    const current = ++revision.current.value;
    let flow: Flow | null = null;
    let succeeded = false;
    try {
      const connection = await resolveRuntimeConnection();
      if (current !== revision.current.value) return;
      if (new URL(connection.baseUrl).protocol !== "https:") {
        throw new Error("连接 Google 账号需要 HTTPS 服务器地址。");
      }
      flow = { cancelled: false, connection, sessionId };
      active.current = flow;
      const reservation = await invoke<{
        flow_id: string;
        redirect_uri: string;
      }>("prepare_google_oauth");
      flow.nativeId = reservation.flow_id;
      if (flow.cancelled) return;
      const response = await runtimeFetchWithConnection(
        connection,
        "/v1/personal-assistant/oauth/begin",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId,
            redirect_uri: reservation.redirect_uri,
          }),
          signal: AbortSignal.timeout(15000),
        },
      );
      if (!response.ok)
        throw new Error("无法开始授权，请检查服务器 OAuth 和 HTTPS 配置。");
      const authorization = (await response.json()) as {
        state: string;
        authorization_url: string;
      };
      flow.state = authorization.state;
      if (flow.cancelled) return;
      setMessage("请在系统浏览器中选择 Google 账号并授权。");
      const callback = await invoke<{ state: string; code: string }>(
        "receive_google_oauth",
        {
          flowId: flow.nativeId,
          authorizationUrl: authorization.authorization_url,
        },
      );
      if (flow.cancelled) return;
      const completed = await runtimeFetchWithConnection(
        connection,
        "/v1/personal-assistant/oauth/complete",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: sessionId,
            state: callback.state,
            code: callback.code,
          }),
          signal: AbortSignal.timeout(30000),
        },
      );
      if (!completed.ok)
        throw new Error("授权未完成，请重试；不会自动重放授权码。");
      succeeded = true;
      if (!flow.cancelled)
        setMessage("Google 账号已连接。日历选择与查询界面正在接入。");
    } catch (error) {
      if (!flow?.cancelled && current === revision.current.value)
        setMessage(
          error instanceof Error ? error.message : "授权未完成或已取消。",
        );
    } finally {
      if (flow && !succeeded) await cancelFlow(flow);
      if (active.current === flow && current === revision.current.value) {
        active.current = null;
        setBusy(false);
      } else if (!flow && current === revision.current.value) setBusy(false);
    }
  }

  const description = !status
    ? "正在读取服务器状态…"
    : status.state === "disabled"
      ? "服务器尚未启用个人助理。"
      : status.state === "unconfigured"
        ? "服务器尚未配置 Google OAuth 客户端。"
        : status.state === "cleanup_failed"
          ? "账号清理遇到问题，请检查服务器状态。"
          : !status.authorization_available
            ? "连接账号前，需要配置支持授权的 HTTPS 服务器地址。"
            : "沿用你的 Google 日历，只申请读取权限。";

  return (
    <>
      <SettingsSectionIntro
        icon="companion"
        title="个人助理"
        description="连接已有日历与提醒事项，逐步接入桌宠提醒。"
      />
      <SettingsGroup title="Google 日历" description={description}>
        <div className="desktop-settings-connection-row">
          <span>
            <strong>Google 账号</strong>
            <small>使用系统浏览器登录，凭据由服务器保存。</small>
          </span>
          {busy ? (
            <button
              onClick={() => {
                ++revision.current.value;
                if (active.current) void cancelFlow(active.current);
                setMessage("已取消等待；已完成的账号连接不会自动撤销。");
                setBusy(false);
              }}
            >
              取消授权
            </button>
          ) : (
            <button
              onClick={() => void connect()}
              disabled={
                !status?.authorization_available ||
                !sessionId ||
                !isDesktopHost()
              }
            >
              连接账号
            </button>
          )}
        </div>
        {message && <p role="status">{message}</p>}
      </SettingsGroup>
      <SettingsGroup
        title="提醒事项与定时任务"
        description="Apple 提醒事项、定时提醒和闹钟尚在开发中。"
      >
        <p>现有日历和提醒事项不会迁移到新的服务。</p>
      </SettingsGroup>
    </>
  );
}
