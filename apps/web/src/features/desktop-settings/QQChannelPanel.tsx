import { useCallback, useEffect, useRef, useState } from "react";

import {
  deleteChannelConnection,
  getChannelConnections,
  updateChannelConnection,
  type ChannelConnectionSnapshot,
} from "../chat/runtimeClient";
import {
  cancelQQPairing,
  getQQPairing,
  startQQPairing,
  testQQChannelConnection,
  type QQPairingSnapshot,
} from "../chat/runtime-client/qqClient";
import { RuntimeRequestError } from "../chat/runtime-client/http";
import { QQProactivePanel } from "./QQProactivePanel";
import { SettingsIcon } from "./SettingsIcon";
import { SettingsToggle } from "./SettingsPrimitives";
import "./qq-channel-panel.css";

type Operation =
  "start" | "cancel" | "test" | "toggle" | "stickers" | "disconnect";

export function QQChannelPanel({
  characterId,
  runtimeOnline,
}: {
  characterId: string;
  runtimeOnline: boolean;
}) {
  const [connection, setConnection] =
    useState<ChannelConnectionSnapshot | null>(null);
  const [pairing, setPairing] = useState<QQPairingSnapshot | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [endpoint, setEndpoint] = useState("ws://127.0.0.1:3001");
  const [accessToken, setAccessToken] = useState("");
  const [loading, setLoading] = useState(true);
  const [connectionVerified, setConnectionVerified] = useState(false);
  const [connectionReadRevision, setConnectionReadRevision] = useState(0);
  const [lastRuntimeOnline, setLastRuntimeOnline] = useState(runtimeOnline);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pollRevision, setPollRevision] = useState(0);
  const mountedRef = useRef(false);
  const pairingRef = useRef<QQPairingSnapshot | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);
  const connectionRevisionRef = useRef(0);
  const cancellingRef = useRef<string | null>(null);

  if (lastRuntimeOnline !== runtimeOnline) {
    setLastRuntimeOnline(runtimeOnline);
    setLoading(true);
    setConnectionVerified(false);
  }

  const refreshConnection = useCallback(() => {
    setConnectionVerified(false);
    setLoading(true);
    setConnectionReadRevision((value) => value + 1);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      pollAbortRef.current?.abort();
      const current = pairingRef.current;
      if (
        current?.status === "pending" &&
        cancellingRef.current !== current.pairing_id
      ) {
        void cancelQQPairing(current.pairing_id).catch(() => {
          // The server's bounded expiry remains the fallback when cleanup is offline.
        });
      }
    };
  }, []);

  useEffect(() => {
    if (!runtimeOnline) return;
    const controller = new AbortController();
    const initialRevision = connectionRevisionRef.current;
    void getChannelConnections(controller.signal)
      .then((items) => {
        if (
          controller.signal.aborted ||
          initialRevision !== connectionRevisionRef.current
        )
          return;
        setConnection(
          items.find(
            (item) =>
              item.configuration.provider_id === "qq_napcat" &&
              item.configuration.character_id === characterId,
          ) ?? null,
        );
        setConnectionVerified(true);
        setNotice(null);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setConnectionVerified(false);
          setNotice(errorMessage(error, "无法读取 QQ 连接状态"));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [runtimeOnline, characterId, connectionReadRevision]);

  const pairingId = pairing?.pairing_id;
  const pairingStatus = pairing?.status;
  useEffect(() => {
    if (!runtimeOnline || !pairingId || pairingStatus !== "pending") return;
    const controller = new AbortController();
    pollAbortRef.current = controller;
    const poll = async () => {
      try {
        while (!controller.signal.aborted) {
          const current = await getQQPairing(pairingId, 20, controller.signal);
          if (controller.signal.aborted) return;
          pairingRef.current = current;
          setPairing(current);
          if (current.connection) {
            connectionRevisionRef.current += 1;
            setConnection(current.connection);
            setConnectionVerified(true);
          }
          if (current.status !== "pending") break;
        }
      } catch (error: unknown) {
        if (controller.signal.aborted) return;
        if (isMissingPairing(error)) {
          // Pairing resources are ephemeral; enrollment may already be durable.
          pairingRef.current = null;
          setPairing(null);
          refreshConnection();
        } else {
          setNotice(errorMessage(error, "QQ 配对状态更新失败，请刷新配对状态"));
        }
      }
    };
    void poll();
    return () => {
      controller.abort();
      if (pollAbortRef.current === controller) pollAbortRef.current = null;
    };
  }, [
    runtimeOnline,
    pairingId,
    pairingStatus,
    pollRevision,
    refreshConnection,
  ]);

  const startPairing = async () => {
    if (
      operation ||
      !runtimeOnline ||
      !connectionVerified ||
      accessToken.length < 16 ||
      !validEndpoint(endpoint)
    )
      return;
    const token = accessToken;
    setAccessToken("");
    setOperation("start");
    setNotice(null);
    try {
      const snapshot = await startQQPairing(
        endpoint.trim(),
        token,
        characterId,
      );
      if (!mountedRef.current) {
        if (snapshot.status === "pending")
          await cancelQQPairing(snapshot.pairing_id);
        return;
      }
      pairingRef.current = snapshot;
      setPairing(snapshot);
      if (snapshot.connection) {
        connectionRevisionRef.current += 1;
        setConnection(snapshot.connection);
        setConnectionVerified(true);
      }
    } catch {
      // Do not render a server-provided message that could echo the submitted token.
      if (mountedRef.current)
        setNotice(
          "无法开始 QQ 配对，请检查 NapCat 登录、连接地址和访问令牌后重试。",
        );
    } finally {
      if (mountedRef.current) setOperation(null);
    }
  };

  const cancelPairing = async () => {
    const current = pairingRef.current;
    if (current?.status !== "pending" || operation) return;
    pollAbortRef.current?.abort();
    cancellingRef.current = current.pairing_id;
    setOperation("cancel");
    setNotice(null);
    try {
      await cancelQQPairing(current.pairing_id);
      if (!mountedRef.current) return;
      // DELETE is a no-op if confirmation won the race; read the actual result.
      const snapshot = await getQQPairing(current.pairing_id, 0);
      if (!mountedRef.current) return;
      if (snapshot.status === "pending" || snapshot.status === "confirmed") {
        pairingRef.current = snapshot;
        setPairing(snapshot);
        if (snapshot.connection) {
          connectionRevisionRef.current += 1;
          setConnection(snapshot.connection);
          setConnectionVerified(true);
        }
        if (snapshot.status === "pending")
          setPollRevision((value) => value + 1);
      } else {
        pairingRef.current = null;
        setPairing(null);
      }
      refreshConnection();
    } catch (error: unknown) {
      if (mountedRef.current) {
        if (isMissingPairing(error)) {
          pairingRef.current = null;
          setPairing(null);
          refreshConnection();
        } else {
          setNotice(errorMessage(error, "无法取消 QQ 配对"));
          setPollRevision((value) => value + 1);
        }
      }
    } finally {
      cancellingRef.current = null;
      if (mountedRef.current) setOperation(null);
    }
  };

  const operateConnection = async (
    action: "test" | "toggle" | "stickers" | "disconnect",
  ) => {
    if (!connection || operation || !runtimeOnline || !connectionVerified)
      return;
    setOperation(action);
    setNotice(null);
    const id = connection.configuration.connection_id;
    try {
      if (action === "disconnect") {
        await deleteChannelConnection(id);
        if (!mountedRef.current) return;
        connectionRevisionRef.current += 1;
        setConnection(null);
        setPairing(null);
        pairingRef.current = null;
      } else {
        const updated =
          action === "test"
            ? await testQQChannelConnection(id)
            : await updateChannelConnection(
                id,
                {
                  ...connection.configuration,
                  ...(action === "stickers"
                    ? {
                        presentation_policy: {
                          ...connection.configuration.presentation_policy,
                          profile: "instant_message" as const,
                          stickers_enabled: !stickersEnabled,
                        },
                      }
                    : { enabled: !connection.configuration.enabled }),
                },
                connection.revision,
              );
        if (mountedRef.current) {
          connectionRevisionRef.current += 1;
          setConnection(updated);
          setConnectionVerified(true);
        }
      }
    } catch (error: unknown) {
      if (mountedRef.current) setNotice(errorMessage(error, "QQ 连接操作失败"));
    } finally {
      if (mountedRef.current) setOperation(null);
    }
  };

  const busy = operation !== null || loading;
  const stickersEnabled =
    connection?.configuration.presentation_policy?.profile ===
      "instant_message" &&
    connection.configuration.presentation_policy.stickers_enabled === true;
  const pending = pairing?.status === "pending";
  const endpointValid = validEndpoint(endpoint);
  const health = !runtimeOnline
    ? "Runtime 离线"
    : loading
      ? "QQ 状态待刷新"
      : !connectionVerified
        ? "QQ 状态未确认"
        : connection
          ? connectionHealth(connection.status)
          : "未连接";

  return (
    <section
      className="channels-settings-card qq-channel-panel"
      aria-label="QQ 连接"
    >
      <header className="channels-settings-provider-heading">
        <span className="channels-settings-provider-icon">
          <SettingsIcon name="channels" />
        </span>
        <div>
          <h2>QQ · NapCat</h2>
          <p>先在 NapCat 登录角色的 QQ，再配对你自己的 QQ。</p>
        </div>
        <span
          className={`channels-settings-state ${runtimeOnline && !loading && connectionVerified && connection?.status === "ready" ? "connected" : ""}`}
        >
          <i />
          {health}
        </span>
      </header>

      {runtimeOnline && loading ? (
        <p className="channels-settings-loading" role="status">
          正在读取 QQ 连接状态…
        </p>
      ) : connection ? (
        <div className="qq-channel-content">
          <h3>{connection.configuration.name || "角色的 QQ"}</h3>
          <p>
            已绑定当前角色和你的私聊。普通对话优先文字；角色可按本轮语义选择调用语音工具回复。
          </p>
          {connection.last_error ? (
            <p role="status">连接异常：{connection.last_error.message}</p>
          ) : null}
          <SettingsToggle
            label="启用 QQ 私聊"
            description="关闭后停止接收和回复，保留当前配对。"
            checked={connection.configuration.enabled !== false}
            disabled={!runtimeOnline || busy || !connectionVerified}
            onChange={() => void operateConnection("toggle")}
          />
          {characterId === "default" &&
          connection.capabilities?.outbound_message_kinds?.includes("image") ? (
            <SettingsToggle
              label="允许角色发送表情图片"
              description="角色可以根据对话附上表情；关闭后仍可理解你发来的静态图片。"
              checked={stickersEnabled}
              disabled={!runtimeOnline || busy || !connectionVerified}
              onChange={() => void operateConnection("stickers")}
            />
          ) : null}
          <div className="qq-channel-actions">
            <button
              className="channels-settings-primary-action"
              type="button"
              disabled={!runtimeOnline || busy || !connectionVerified}
              onClick={() => void operateConnection("test")}
            >
              {operation === "test" ? "正在检查…" : "检查 QQ 连接"}
            </button>
            <button
              className="qq-channel-secondary-action"
              type="button"
              disabled={!runtimeOnline || busy || !connectionVerified}
              onClick={() => void operateConnection("disconnect")}
            >
              {operation === "disconnect" ? "正在断开…" : "断开 QQ 连接"}
            </button>
          </div>
          {connection.capabilities?.supports_proactive_messages === true ? (
            <QQProactivePanel
              key={`${connection.configuration.connection_id}:${connection.revision}`}
              connectionId={connection.configuration.connection_id}
              runtimeOnline={runtimeOnline}
              connectionVerified={connectionVerified && !busy}
            />
          ) : null}
        </div>
      ) : pending ? (
        <div className="qq-channel-content">
          <h3>从你自己的 QQ 发送配对指令</h3>
          <p>
            向 {pairing.account_label || "NapCat 已登录的角色 QQ"}{" "}
            的私聊发送以下完整内容：
          </p>
          <code className="qq-channel-command">CW2 {pairing.pairing_code}</code>
          <p>
            收到指令后会自动完成主人绑定。本次配对有效期至{" "}
            {formatDate(pairing.expires_at)}。
          </p>
          <div className="qq-channel-actions">
            <button
              className="qq-channel-secondary-action"
              type="button"
              disabled={!runtimeOnline || busy}
              onClick={() => void cancelPairing()}
            >
              {operation === "cancel" ? "正在取消…" : "取消 QQ 配对"}
            </button>
            <button
              className="qq-channel-secondary-action"
              type="button"
              disabled={!runtimeOnline || busy}
              onClick={() => {
                setNotice(null);
                setPollRevision((value) => value + 1);
              }}
            >
              刷新配对状态
            </button>
          </div>
        </div>
      ) : (
        <div className="qq-channel-content">
          {pairing ? (
            <p role="status">{pairingStatusLabel(pairing.status)}</p>
          ) : null}
          <p>
            与桌宠共享当前角色、关系和记忆。普通对话优先文字，角色可按本轮语义选择角色语音。
          </p>
          {setupOpen ? (
            <form
              className="qq-channel-form"
              onSubmit={(event) => {
                event.preventDefault();
                void startPairing();
              }}
            >
              <label htmlFor="qq-napcat-endpoint">NapCat WebSocket 地址</label>
              <input
                id="qq-napcat-endpoint"
                type="url"
                value={endpoint}
                disabled={!runtimeOnline || busy}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setEndpoint(event.currentTarget.value)}
              />
              <small>
                服务器部署可用 SSH 隧道的本地 ws 地址，或远端 wss 地址。
              </small>
              <label htmlFor="qq-napcat-token">NapCat 访问令牌</label>
              <input
                id="qq-napcat-token"
                type="password"
                value={accessToken}
                disabled={!runtimeOnline || busy}
                autoComplete="new-password"
                minLength={16}
                onChange={(event) => setAccessToken(event.currentTarget.value)}
              />
              <small>
                使用 NapCat 正向 WebSocket 的 Token，至少 16
                个字符。提交后输入框会清空，凭据由 Runtime 安全保存。
              </small>
              <button
                className="channels-settings-primary-action"
                type="submit"
                disabled={
                  !runtimeOnline ||
                  busy ||
                  !connectionVerified ||
                  !endpointValid ||
                  accessToken.length < 16
                }
              >
                {operation === "start" ? "正在连接 NapCat…" : "开始 QQ 配对"}
              </button>
            </form>
          ) : (
            <button
              className="channels-settings-primary-action"
              type="button"
              disabled={!runtimeOnline || busy || !connectionVerified}
              onClick={() => setSetupOpen(true)}
            >
              设置 QQ 连接
            </button>
          )}
        </div>
      )}
      {!runtimeOnline ? (
        <p className="qq-channel-offline" role="status">
          Runtime 离线，恢复服务后会刷新 QQ 连接状态。
        </p>
      ) : null}
      {runtimeOnline && !loading && !connectionVerified ? (
        <div className="qq-channel-content">
          <button
            className="qq-channel-secondary-action"
            type="button"
            disabled={operation !== null}
            onClick={refreshConnection}
          >
            刷新 QQ 连接状态
          </button>
        </div>
      ) : null}
      {notice ? (
        <p className="channels-settings-notice" role="alert">
          {notice}
        </p>
      ) : null}
    </section>
  );
}

function validEndpoint(value: string): boolean {
  try {
    const url = new URL(value.trim());
    const encryptedOrLocal =
      url.protocol === "wss:" ||
      (url.protocol === "ws:" &&
        ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname));
    return (
      encryptedOrLocal &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}

function connectionHealth(status: ChannelConnectionSnapshot["status"]): string {
  if (status === "ready") return "QQ 连接正常";
  if (status === "disabled") return "QQ 已停用";
  if (status === "degraded") return "QQ 正在重连";
  if (status === "error") return "QQ 连接异常";
  return "QQ 待检查";
}

function pairingStatusLabel(status: QQPairingSnapshot["status"]): string {
  if (status === "expired") return "QQ 配对已过期，请重新输入令牌开始配对。";
  if (status === "cancelled") return "本次 QQ 配对已取消。";
  if (status === "failed") return "QQ 配对失败，请检查 NapCat 后重试。";
  return "QQ 配对已完成。";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && !(error instanceof TypeError)
    ? error.message
    : fallback;
}

function isMissingPairing(error: unknown): boolean {
  return error instanceof RuntimeRequestError && error.status === 404;
}
