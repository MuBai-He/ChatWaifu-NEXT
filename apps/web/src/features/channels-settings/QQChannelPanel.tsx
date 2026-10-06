import { effectiveChannelPresentation } from "./channelPresentationDefaults";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  getRuntimeContextRevision,
  subscribeRuntimeContext,
  readRuntimeRequestContext,
  assertRuntimeRequestContext,
  type RuntimeRequestContext,
} from "../chat/runtimeEndpoint";

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
import { QQGroupRoutesPanel } from "./QQGroupRoutesPanel";
import { SettingsIcon } from "../settings/SettingsIcon";
import { SettingsToggle } from "../settings/SettingsPrimitives";
import {
  ChannelConnectionDetails,
  ChannelSettingsDisclosure,
} from "./ChannelSettingsPrimitives";
import { ChannelPresentationPanel } from "./ChannelPresentationPanel";
import { StickerLibraryPanel } from "./StickerLibraryPanel";
import "./qq-channel-panel.css";

type Operation =
  "start" | "cancel" | "test" | "toggle" | "stickers" | "disconnect";

type Props = {
  characterId: string;
  runtimeOnline: boolean;
};

export function QQChannelPanel(props: Props) {
  const runtimeContext = useSyncExternalStore(
    subscribeRuntimeContext,
    getRuntimeContextRevision,
    getRuntimeContextRevision,
  );
  return (
    <QQChannelContent
      key={`${runtimeContext}:${props.characterId}`}
      {...props}
    />
  );
}

function QQChannelContent({ characterId, runtimeOnline }: Props) {
  const [connection, setConnectionState] =
    useState<ChannelConnectionSnapshot | null>(null);
  const [connections, setConnections] = useState<ChannelConnectionSnapshot[]>(
    [],
  );
  const selectedConnectionRef = useRef<string | null>(null);
  const setConnection = useCallback(
    (value: ChannelConnectionSnapshot | null) => {
      const previousId = selectedConnectionRef.current;
      selectedConnectionRef.current =
        value?.configuration.connection_id ?? null;
      setConnectionState(value);
      setConnections((items) =>
        value
          ? [
              ...items.filter(
                (item) =>
                  item.configuration.connection_id !==
                  value.configuration.connection_id,
              ),
              value,
            ]
          : items.filter(
              (item) => item.configuration.connection_id !== previousId,
            ),
      );
    },
    [],
  );
  const [pairing, setPairing] = useState<QQPairingSnapshot | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [groupsOpen, setGroupsOpen] = useState(false);
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
  const originRef = useRef<RuntimeRequestContext | null>(null);
  const pairingOriginRef = useRef<RuntimeRequestContext | null>(null);
  const operationAbortRef = useRef<AbortController | null>(null);

  if (lastRuntimeOnline !== runtimeOnline) {
    setLastRuntimeOnline(runtimeOnline);
    setLoading(true);
    setConnectionVerified(false);
    if (!runtimeOnline) setOperation(null);
  }

  const currentOrigin = useCallback(
    (origin: RuntimeRequestContext, signal?: AbortSignal) => {
      if (!mountedRef.current || signal?.aborted) return false;
      try {
        assertRuntimeRequestContext(origin);
        return true;
      } catch {
        return false;
      }
    },
    [],
  );
  const beginOperation = (origin = originRef.current) => {
    if (!origin || operationAbortRef.current || !currentOrigin(origin))
      return null;
    const controller = new AbortController();
    operationAbortRef.current = controller;
    return {
      origin,
      controller,
      options: { expectedContext: origin, signal: controller.signal },
    };
  };

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
      operationAbortRef.current?.abort();
      const current = pairingRef.current;
      const origin = pairingOriginRef.current;
      pairingRef.current = null;
      pairingOriginRef.current = null;
      originRef.current = null;
      if (
        current?.status === "pending" &&
        origin &&
        cancellingRef.current !== current.pairing_id
      ) {
        try {
          assertRuntimeRequestContext(origin);
        } catch {
          return;
        }
        void cancelQQPairing(current.pairing_id, {
          expectedContext: origin,
        }).catch(() => {
          // The server's bounded expiry remains the fallback when cleanup is offline.
        });
      }
    };
  }, []);

  useEffect(() => {
    if (!runtimeOnline) operationAbortRef.current?.abort();
  }, [runtimeOnline]);

  useEffect(() => {
    if (!runtimeOnline) return;
    const controller = new AbortController();
    const initialRevision = connectionRevisionRef.current;
    originRef.current = null;
    void readRuntimeRequestContext()
      .then(async (origin) => {
        controller.signal.throwIfAborted();
        assertRuntimeRequestContext(origin);
        const items = await getChannelConnections(controller.signal, {
          expectedContext: origin,
        });
        assertRuntimeRequestContext(origin);
        return { items, origin };
      })
      .then(({ items, origin }) => {
        if (
          controller.signal.aborted ||
          initialRevision !== connectionRevisionRef.current
        )
          return;
        originRef.current = origin;
        const matches = items.filter(
          (item) =>
            item.configuration.provider_id === "qq_napcat" &&
            item.configuration.character_id === characterId,
        );
        setConnections(matches);
        setConnection(
          matches.find(
            (item) =>
              item.configuration.connection_id ===
              selectedConnectionRef.current,
          ) ??
            matches.find((item) => item.status === "ready") ??
            matches[0] ??
            null,
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
  }, [runtimeOnline, characterId, connectionReadRevision, setConnection]);

  const pairingId = pairing?.pairing_id;
  const pairingStatus = pairing?.status;
  useEffect(() => {
    if (!runtimeOnline || !pairingId || pairingStatus !== "pending") return;
    const controller = new AbortController();
    const origin = pairingOriginRef.current;
    if (!origin) return;
    pollAbortRef.current = controller;
    const poll = async () => {
      try {
        while (!controller.signal.aborted) {
          assertRuntimeRequestContext(origin);
          const current = await getQQPairing(pairingId, 20, controller.signal, {
            expectedContext: origin,
          });
          if (!currentOrigin(origin, controller.signal)) return;
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
        if (!currentOrigin(origin, controller.signal)) return;
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
    currentOrigin,
    setConnection,
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
    const request = beginOperation();
    if (!request) return;
    const { origin, controller, options } = request;
    setAccessToken("");
    setOperation("start");
    setNotice(null);
    try {
      const snapshot = await startQQPairing(
        endpoint.trim(),
        token,
        characterId,
        options,
      );
      if (!currentOrigin(origin, controller.signal)) {
        if (snapshot.status === "pending") {
          assertRuntimeRequestContext(origin);
          await cancelQQPairing(snapshot.pairing_id, {
            expectedContext: origin,
          });
        }
        return;
      }
      pairingOriginRef.current = origin;
      pairingRef.current = snapshot;
      setPairing(snapshot);
      if (snapshot.connection) {
        connectionRevisionRef.current += 1;
        setConnection(snapshot.connection);
        setConnectionVerified(true);
      }
    } catch {
      // Do not render a server-provided message that could echo the submitted token.
      if (currentOrigin(origin, controller.signal))
        setNotice(
          "无法开始 QQ 配对，请检查 NapCat 登录、连接地址和访问令牌后重试。",
        );
    } finally {
      if (operationAbortRef.current === controller)
        operationAbortRef.current = null;
      if (currentOrigin(origin, controller.signal)) setOperation(null);
    }
  };

  const cancelPairing = async () => {
    const current = pairingRef.current;
    if (current?.status !== "pending" || operation) return;
    const pairingOrigin = pairingOriginRef.current;
    if (!pairingOrigin) return;
    const request = beginOperation(pairingOrigin);
    if (!request) return;
    const { origin, controller, options } = request;
    pollAbortRef.current?.abort();
    cancellingRef.current = current.pairing_id;
    setOperation("cancel");
    setNotice(null);
    try {
      await cancelQQPairing(current.pairing_id, options);
      if (!currentOrigin(origin, controller.signal)) return;
      // DELETE is a no-op if confirmation won the race; read the actual result.
      const snapshot = await getQQPairing(
        current.pairing_id,
        0,
        controller.signal,
        options,
      );
      if (!currentOrigin(origin, controller.signal)) return;
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
      if (currentOrigin(origin, controller.signal)) {
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
      if (operationAbortRef.current === controller)
        operationAbortRef.current = null;
      if (currentOrigin(origin, controller.signal)) setOperation(null);
    }
  };

  const operateConnection = async (
    action: "test" | "toggle" | "stickers" | "disconnect",
  ) => {
    if (!connection || operation || !runtimeOnline || !connectionVerified)
      return;
    const request = beginOperation();
    if (!request) return;
    const { origin, controller, options } = request;
    setOperation(action);
    setNotice(null);
    const id = connection.configuration.connection_id;
    try {
      if (action === "disconnect") {
        await deleteChannelConnection(id, options);
        if (!currentOrigin(origin, controller.signal)) return;
        connectionRevisionRef.current += 1;
        setConnection(null);
        setPairing(null);
        pairingRef.current = null;
      } else {
        const updated =
          action === "test"
            ? await testQQChannelConnection(id, controller.signal, options)
            : await updateChannelConnection(
                id,
                {
                  ...connection.configuration,
                  ...(action === "stickers"
                    ? {
                        presentation_policy: {
                          ...effectiveChannelPresentation(connection),
                          stickers_enabled: !stickersEnabled,
                        },
                      }
                    : { enabled: !connection.configuration.enabled }),
                },
                connection.revision,
                controller.signal,
                options,
              );
        if (currentOrigin(origin, controller.signal)) {
          connectionRevisionRef.current += 1;
          setConnection(updated);
          setConnectionVerified(true);
        }
      }
    } catch (error: unknown) {
      if (currentOrigin(origin, controller.signal))
        setNotice(errorMessage(error, "QQ 连接操作失败"));
    } finally {
      if (operationAbortRef.current === controller)
        operationAbortRef.current = null;
      if (currentOrigin(origin, controller.signal)) setOperation(null);
    }
  };

  const busy = operation !== null || loading;
  const stickersEnabled = Boolean(
    connection &&
    effectiveChannelPresentation(connection).profile === "instant_message" &&
    effectiveChannelPresentation(connection).stickers_enabled === true,
  );
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
          {connections.length > 1 ? (
            <label className="channel-settings-select">
              管理哪个 QQ 连接
              <select
                value={connection.configuration.connection_id}
                disabled={!runtimeOnline || !connectionVerified || busy}
                onChange={(event) => {
                  setConnection(
                    connections.find(
                      (item) =>
                        item.configuration.connection_id ===
                        event.currentTarget.value,
                    ) ?? null,
                  );
                  setGroupsOpen(false);
                  setNotice(null);
                }}
              >
                {connections.map((item) => (
                  <option
                    key={item.configuration.connection_id}
                    value={item.configuration.connection_id}
                  >
                    {item.configuration.name} · QQ{" "}
                    {item.configuration.account_key ?? "待确认"}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <h3>{connection.configuration.name || "角色的 QQ"}</h3>
          <p>
            已绑定当前角色和你的私聊。普通对话优先文字；允许语音回复时，角色可按本轮语义选择调用语音工具。能力开关在“权限与预算”中管理。
          </p>
          {connection.last_error ? (
            <p role="status">连接异常：{connection.last_error.message}</p>
          ) : null}
          <SettingsToggle
            label="启用 QQ 连接"
            description="关闭后停止本连接的私聊和群聊，保留配对与历史；恢复后群需重新核对成员。"
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
              disabled={
                !runtimeOnline ||
                busy ||
                !connectionVerified ||
                effectiveChannelPresentation(connection).profile !==
                  "instant_message"
              }
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
          <ChannelPresentationPanel
            connection={connection}
            editable={runtimeOnline && !busy && connectionVerified}
            onSaved={(updated) => {
              connectionRevisionRef.current += 1;
              setConnection(updated);
              setConnectionVerified(true);
            }}
            onRefresh={refreshConnection}
          />
          <ChannelConnectionDetails connection={connection} />
          {characterId === "default" ? (
            <ChannelSettingsDisclosure
              title="私聊表情学习与使用记录"
              description="与微信主人共用私聊表情库；群表情库单独隔离"
            >
              <StickerLibraryPanel
                characterId={characterId}
                runtimeOnline={runtimeOnline && connectionVerified && !busy}
              />
            </ChannelSettingsDisclosure>
          ) : null}
          {connection.capabilities?.supports_proactive_messages === true ? (
            <ChannelSettingsDisclosure
              title="主动文字问候"
              description="只向已配对主人发送；次数、安静时段和历史"
            >
              <QQProactivePanel
                key={`${connection.configuration.connection_id}:${connection.revision}`}
                connectionId={connection.configuration.connection_id}
                runtimeOnline={runtimeOnline}
                connectionVerified={connectionVerified && !busy}
              />
            </ChannelSettingsDisclosure>
          ) : null}
          <button
            type="button"
            className="qq-channel-secondary-action"
            aria-expanded={groupsOpen}
            onClick={() => setGroupsOpen((value) => !value)}
          >
            {groupsOpen ? "收起 QQ 群管理" : "管理 QQ 群路由"}
          </button>
          {groupsOpen ? (
            <QQGroupRoutesPanel
              connection={connection}
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
            与桌宠共享当前角色、关系和记忆。普通对话优先文字；语音、联网与账号收藏在“权限与预算”中单独管理。
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
