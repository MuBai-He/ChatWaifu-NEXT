import { OrganizerPanel } from "../personal-assistant/OrganizerPanel";
import { AgendaOverview } from "../personal-assistant/AgendaOverview";
import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  isDesktopHost,
  resolveRuntimeConnection,
  runtimeFetchWithConnection,
  type RuntimeConnection,
} from "../chat/runtimeEndpoint";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { PersonalCalendarPanel } from "./PersonalCalendarPanel";
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
  const [statusError, setStatusError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [accountRevision, setAccountRevision] = useState(0);
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
            signal: AbortSignal.any([
              controller.signal,
              AbortSignal.timeout(10000),
            ]),
          },
        );
        if (!response.ok)
          throw new Error(
            response.status === 404
              ? "服务器版本尚未包含个人助理，请先更新后端。当前聊天连接不受影响。"
              : response.status === 401 || response.status === 403
                ? "无权读取个人助理状态，请检查访问令牌。"
                : `个人助理服务暂时不可用（${response.status}），请重试。`,
          );
        const data = (await response.json()) as Status;
        if (
          !data ||
          !["disabled", "unconfigured", "ready", "cleanup_failed"].includes(
            data.state,
          ) ||
          typeof data.authorization_available !== "boolean"
        )
          throw new Error(
            "服务器返回了无法识别的个人助理状态，请检查后端版本。",
          );
        if (!disposed) {
          setStatus(data);
          setStatusError("");
        }
      } catch (error) {
        if (!disposed) {
          setStatus(null);
          setStatusError(
            error instanceof TypeError ||
              (error instanceof DOMException && error.name === "TimeoutError")
              ? "个人助理状态读取失败或超时，请检查网络后重试。"
              : error instanceof Error
                ? error.message
                : "无法读取个人助理状态，请重试。",
          );
        }
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
  }, [sessionId, refresh, context.runtime.connection]);

  async function connect(upgradeAccountId?: string) {
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
            ...(upgradeAccountId ? {upgrade_account_id: upgradeAccountId} : {}),
          }),
          signal: AbortSignal.timeout(15000),
        },
      );
      if (!response.ok) {
        const failure = await response.json().catch(() => null) as {detail?: unknown} | null;
        const detail = typeof failure?.detail === "string" ? failure.detail : "";
        throw new Error(detail === "google_primary_calendar_unavailable"
          ? "无法确认旧账号身份；请先刷新原账号日历，确认服务器能访问 Google。"
          : detail === "account_not_connected"
            ? "原账号已断开，请刷新账号列表后重试。"
            : `无法开始授权${detail ? `：${detail}` : "，请检查服务器 OAuth 和 HTTPS 配置。"}`);
      }
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
      setMessage("浏览器授权已返回，服务器正在连接 Google 完成账号绑定…");
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
      if (!completed.ok) {
        const failure: unknown = await completed.json().catch(() => null);
        const detail =
          failure && typeof failure === "object" && "detail" in failure
            ? failure.detail
            : null;
        throw new Error(
          detail === "transport_error"
            ? "浏览器授权已返回，但服务器无法连接 Google。请检查服务器出网或 VPN，恢复后重新授权。"
            : "服务器未能完成授权，请重新连接账号；不会自动重放授权码。",
        );
      }
      succeeded = true;
      if (!flow.cancelled) {
        setAccountRevision((value) => value + 1);
        setMessage(upgradeAccountId
          ? "原 Google 账号权限已升级。请选择待办列表和默认写入位置。"
          : "Google 账号已连接。请选择允许读取的日历和待办列表，再设置默认写入位置。");
      }
    } catch (error) {
      if (!flow?.cancelled && current === revision.current.value)
        setMessage(
          error instanceof DOMException && error.name === "TimeoutError"
            ? "等待服务器完成授权超时。请先刷新账号确认结果；若未连接，检查服务器网络后重新授权。"
            : error instanceof Error
              ? error.message
              : typeof error === "string"
                ? error
                : "授权未完成或已取消。",
        );
    } finally {
      if (flow && !succeeded) await cancelFlow(flow);
      if (active.current === flow && current === revision.current.value) {
        active.current = null;
        setBusy(false);
      } else if (!flow && current === revision.current.value) setBusy(false);
    }
  }

  const description = statusError
    ? statusError
    : !status
      ? "正在读取服务器状态…"
      : status.state === "disabled"
        ? "服务器尚未启用个人助理。"
        : status.state === "unconfigured"
          ? "服务器尚未配置 Google OAuth 客户端。"
          : status.state === "cleanup_failed"
            ? "账号清理遇到问题，请检查服务器状态。"
            : !status.authorization_available
              ? "连接账号前，需要配置支持授权的 HTTPS 服务器地址。"
              : "连接现有 Google 日历与 Tasks。新增写入权限需你在浏览器确认。";

  return (
    <div className="personal-assistant-settings">
      <SettingsSectionIntro
        icon="companion"
        title="个人助理"
        description="在原账户管理日程与待办，桌宠负责到期提醒。"
      />
      <SettingsGroup title="账户、日程与提醒" description={description}>
        {sessionId && <AgendaOverview key={`agenda:${sessionId}`} sessionId={sessionId} />}
        {statusError && (
          <button
            type="button"
            onClick={() => setRefresh((value) => value + 1)}
          >
            重新读取状态
          </button>
        )}
        <details className="assistant-settings-details">
          <summary>Google 账户与来源设置</summary>
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
                !!statusError ||
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
        {status?.state === "ready" && !statusError && sessionId && (
          <PersonalCalendarPanel
            key={`${sessionId}:${accountRevision}`}
            sessionId={sessionId}
            onUpgrade={(accountId) => void connect(accountId)}
          />
        )}
        </details>
        <details className="assistant-settings-details">
          <summary>Apple 设备与桌宠闹钟</summary>
        {sessionId ? (
          <OrganizerPanel key={sessionId} sessionId={sessionId} />
        ) : (
          <p>请先连接服务器。</p>
        )}
        </details>
      </SettingsGroup>
    </div>
  );
}
