import { effectiveChannelPresentation } from "./channelPresentationDefaults";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  cancelChannelAuthorization,
  deleteChannelConnection,
  getChannelAuthorization,
  getChannelConnections,
  startChannelAuthorization,
  submitChannelAuthorizationVerification,
  updateChannelConnection,
  updateChannelPresentationPolicy,
  type ChannelAuthorizationSnapshot,
  type ChannelConnectionSnapshot,
} from "../chat/runtimeClient";
import {
  assertRuntimeRequestContext,
  getRuntimeContextRevision,
  readRuntimeRequestContext,
  subscribeRuntimeContext,
  type RuntimeRequestContext,
} from "../chat/runtimeEndpoint";
import { SettingsIcon } from "../settings/SettingsIcon";
import { SettingsToggle } from "../settings/SettingsPrimitives";
import { WeixinAuthorizationCard } from "./WeixinAuthorizationCard";
import { ChannelConnectionDetails } from "./ChannelSettingsPrimitives";
import { ChannelPresentationPanel } from "./ChannelPresentationPanel";

type Props = { characterId: string; runtimeOnline: boolean };
type Operation =
  "start" | "verify" | "cancel" | "toggle" | "stickers" | "disconnect";
const terminal = (s: ChannelAuthorizationSnapshot["status"]) =>
  ["confirmed", "expired", "cancelled", "failed"].includes(s);

export function WeixinChannelPanel(props: Props) {
  const epoch = useSyncExternalStore(
    subscribeRuntimeContext,
    getRuntimeContextRevision,
    getRuntimeContextRevision,
  );
  return <WeixinContent key={`${epoch}:${props.characterId}`} {...props} />;
}
function WeixinContent({ characterId, runtimeOnline }: Props) {
  const [connections, setConnections] = useState<ChannelConnectionSnapshot[]>(
    [],
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [authorization, setAuthorization] =
    useState<ChannelAuthorizationSnapshot | null>(null);
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(true);
  const [verified, setVerified] = useState(false);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [readRevision, setReadRevision] = useState(0);
  const [pollRevision, setPollRevision] = useState(0);
  const [confirmRemoval, setConfirmRemoval] = useState(false);
  const originRef = useRef<RuntimeRequestContext | null>(null);
  const authRef = useRef<ChannelAuthorizationSnapshot | null>(null);
  const mountedRef = useRef(false);
  const opRef = useRef<AbortController | null>(null);
  const pollRef = useRef<AbortController | null>(null);
  const changeRef = useRef(0);
  const connection =
    connections.find((c) => c.configuration.connection_id === selectedId) ??
    null;
  const current = (origin: RuntimeRequestContext, signal?: AbortSignal) => {
    if (!mountedRef.current || signal?.aborted) return false;
    try {
      assertRuntimeRequestContext(origin);
      return true;
    } catch {
      return false;
    }
  };
  const remember = useCallback(
    (value: ChannelConnectionSnapshot) => {
      if (
        value.configuration.provider_id !== "weixin_ilink" ||
        value.configuration.character_id !== characterId
      )
        throw new Error("connection mismatch");
      ++changeRef.current;
      setConnections((old) => [
        ...old.filter(
          (c) =>
            c.configuration.connection_id !== value.configuration.connection_id,
        ),
        value,
      ]);
      setSelectedId(value.configuration.connection_id);
      setVerified(true);
      setConfirmRemoval(false);
    },
    [characterId],
  );
  const refresh = () => {
    setVerified(false);
    setLoading(true);
    setConfirmRemoval(false);
    setReadRevision((n) => n + 1);
  };
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      opRef.current?.abort();
      pollRef.current?.abort();
      const auth = authRef.current;
      const origin = originRef.current;
      if (auth && !terminal(auth.status) && origin) {
        try {
          assertRuntimeRequestContext(origin);
        } catch {
          return;
        }
        void cancelChannelAuthorization(auth.auth_session_id, {
          expectedContext: origin,
        }).catch(() => {});
      }
    };
  }, []);
  useEffect(() => {
    if (!runtimeOnline) {
      opRef.current?.abort();
      pollRef.current?.abort();
      opRef.current = null;
      originRef.current = null;
      return;
    }
    const controller = new AbortController();
    const revision = changeRef.current;
    originRef.current = null;
    void (async () => {
      const origin = await readRuntimeRequestContext();
      controller.signal.throwIfAborted();
      setVerified(false);
      setLoading(true);
      setOperation(null);
      const all = await getChannelConnections(controller.signal, {
        expectedContext: origin,
      });
      if (!current(origin, controller.signal) || revision !== changeRef.current)
        return;
      originRef.current = origin;
      const items = all.filter(
        (c) =>
          c.configuration.provider_id === "weixin_ilink" &&
          c.configuration.character_id === characterId,
      );
      setConnections(items);
      setSelectedId((old) =>
        items.some((c) => c.configuration.connection_id === old)
          ? old
          : ((
              items.find((c) => c.status === "ready") ??
              items.find((c) => c.configuration.enabled !== false) ??
              items[0]
            )?.configuration.connection_id ?? null),
      );
      setVerified(true);
    })()
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setNotice(message(error, "无法确认微信连接，请刷新后再操作。"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [runtimeOnline, characterId, readRevision]);

  const authId = authorization?.auth_session_id;
  useEffect(() => {
    const initial = authRef.current;
    const origin = originRef.current;
    if (
      !runtimeOnline ||
      !authId ||
      !initial ||
      terminal(initial.status) ||
      !origin
    )
      return;
    const controller = new AbortController();
    pollRef.current = controller;
    void (async () => {
      let snapshot = initial;
      while (!controller.signal.aborted && !terminal(snapshot.status)) {
        snapshot = await getChannelAuthorization(
          authId,
          20,
          controller.signal,
          { expectedContext: origin },
        );
        if (!current(origin, controller.signal)) return;
        authRef.current = snapshot;
        setAuthorization(snapshot);
        if (snapshot.connection) {
          remember(snapshot.connection);
          setCode("");
        }
      }
    })().catch((error: unknown) => {
      if (current(origin, controller.signal))
        setNotice(message(error, "微信绑定状态更新失败，请刷新绑定状态。"));
    });
    return () => {
      controller.abort();
      if (pollRef.current === controller) pollRef.current = null;
    };
  }, [runtimeOnline, authId, pollRevision, remember]);

  const run = async (
    kind: Operation,
    action: (
      origin: RuntimeRequestContext,
      signal: AbortSignal,
    ) => Promise<void>,
  ) => {
    const origin = originRef.current;
    if (
      !runtimeOnline ||
      !verified ||
      !origin ||
      opRef.current ||
      !current(origin)
    )
      return;
    const controller = new AbortController();
    opRef.current = controller;
    setOperation(kind);
    setNotice(null);
    try {
      await action(origin, controller.signal);
    } catch (error: unknown) {
      if (current(origin, controller.signal)) {
        setNotice(message(error, "操作结果未确认，请刷新并核对。"));
        refresh();
      }
    } finally {
      if (opRef.current === controller) opRef.current = null;
      if (current(origin, controller.signal)) setOperation(null);
    }
  };
  const start = () =>
    void run("start", async (origin, signal) => {
      pollRef.current?.abort();
      const previous = authRef.current;
      if (previous && !terminal(previous.status))
        await cancelChannelAuthorization(previous.auth_session_id, {
          expectedContext: origin,
          signal,
        });
      const result = await startChannelAuthorization(
        "weixin_ilink",
        characterId,
        signal,
        { expectedContext: origin },
      );
      if (!current(origin, signal)) return;
      authRef.current = result;
      setAuthorization(result);
      setCode("");
      setPollRevision((n) => n + 1);
      if (result.connection) remember(result.connection);
    });
  const verify = () => {
    const auth = authRef.current;
    if (!auth || !code.trim()) return;
    void run("verify", async (origin, signal) => {
      pollRef.current?.abort();
      try {
        const result = await submitChannelAuthorizationVerification(
          auth.auth_session_id,
          code.trim(),
          signal,
          { expectedContext: origin },
        );
        if (!current(origin, signal)) return;
        authRef.current = result;
        setAuthorization(result);
        setCode("");
        if (result.connection) remember(result.connection);
      } finally {
        if (current(origin, signal)) setPollRevision((n) => n + 1);
      }
    });
  };
  const cancel = () => {
    const auth = authRef.current;
    if (!auth) return;
    void run("cancel", async (origin, signal) => {
      pollRef.current?.abort();
      await cancelChannelAuthorization(auth.auth_session_id, {
        expectedContext: origin,
        signal,
      });
      // Confirmation can win the cancellation race; read authoritative state.
      const result = await getChannelAuthorization(
        auth.auth_session_id,
        0,
        signal,
        { expectedContext: origin },
      );
      if (!current(origin, signal)) return;
      if (result.connection) remember(result.connection);
      authRef.current = terminal(result.status) ? null : result;
      setAuthorization(authRef.current);
      setCode("");
      refresh();
    });
  };
  const mutate = (kind: "toggle" | "stickers" | "disconnect") => {
    if (!connection) return;
    void run(kind, async (origin, signal) => {
      const options = { expectedContext: origin, signal };
      if (kind === "disconnect") {
        await deleteChannelConnection(
          connection.configuration.connection_id,
          options,
        );
        if (!current(origin, signal)) return;
        ++changeRef.current;
        setConnections((all) =>
          all.filter(
            (c) =>
              c.configuration.connection_id !==
              connection.configuration.connection_id,
          ),
        );
        setSelectedId(null);
        setConfirmRemoval(false);
        refresh();
      } else {
        const result =
          kind === "stickers"
            ? await updateChannelPresentationPolicy(
                connection,
                {
                  ...effectiveChannelPresentation(connection),
                  stickers_enabled:
                    !effectiveChannelPresentation(connection).stickers_enabled,
                },
                signal,
                options,
              )
            : await updateChannelConnection(
                connection.configuration.connection_id,
                {
                  ...connection.configuration,
                  enabled: connection.configuration.enabled === false,
                },
                connection.revision,
                signal,
                options,
              );
        if (current(origin, signal)) remember(result);
      }
    });
  };
  const busy = operation !== null || loading;
  const editable = runtimeOnline && verified && !busy;
  const policy = connection ? effectiveChannelPresentation(connection) : null;
  const authActive = authorization && authorization.status !== "confirmed";
  const label = !runtimeOnline
    ? "Runtime 离线"
    : loading
      ? "状态待刷新"
      : !verified
        ? "状态未确认"
        : connection
          ? connectionState(connection)
          : "未连接";
  return (
    <section className="channels-settings-card" aria-label="微信连接">
      <header className="channels-settings-provider-heading">
        <span className="channels-settings-provider-icon">
          <SettingsIcon name="channels" />
        </span>
        <div>
          <h2>微信</h2>
          <p>扫码绑定；凭据和接收任务由当前连接的 Runtime 托管。</p>
        </div>
        <span
          className={`channels-settings-state ${runtimeOnline && verified && connection?.status === "ready" ? "connected" : ""}`}
        >
          <i />
          {label}
        </span>
      </header>
      <div className="channel-settings-body">
        {connections.length > 1 ? (
          <label className="channel-settings-select">
            管理哪个微信绑定
            <select
              value={selectedId ?? ""}
              disabled={!editable}
              onChange={(e) => {
                setSelectedId(e.currentTarget.value);
                setConfirmRemoval(false);
              }}
            >
              {connections.map((c) => (
                <option
                  key={c.configuration.connection_id}
                  value={c.configuration.connection_id}
                >
                  {c.configuration.name} · {connectionState(c)} ·{" "}
                  {c.configuration.account_key?.slice(-6)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {loading && runtimeOnline ? (
          <p role="status">正在读取连接状态…</p>
        ) : null}
        {authActive ? (
          <>
            <WeixinAuthorizationCard
              authorization={authorization}
              disabled={!editable}
              verificationCode={code}
              busy={
                operation === "start" ||
                operation === "cancel" ||
                operation === "verify"
                  ? operation
                  : null
              }
              onVerificationCodeChange={setCode}
              onVerify={verify}
              onCancel={cancel}
              onRetry={start}
            />
            <button
              type="button"
              className="qq-channel-secondary-action"
              disabled={!editable}
              onClick={() => setPollRevision((n) => n + 1)}
            >
              刷新绑定状态
            </button>
          </>
        ) : connection ? (
          <>
            <h3>{connection.configuration.name}</h3>
            <p>
              仅已绑定的主人私聊进入角色对话。可理解最多四张静态图片；照片保存和表情学习分别管理。微信当前回复文字和已开启的表情，不播放桌宠音频。
            </p>
            {connection.last_error ? (
              <p className="channels-settings-notice" role="status">
                {connection.last_error.message}
              </p>
            ) : null}
            <SettingsToggle
              label="启用微信消息"
              description="关闭后停止接收与回复，保留绑定。"
              checked={connection.configuration.enabled !== false}
              disabled={!editable}
              onChange={() => mutate("toggle")}
            />
            <SettingsToggle
              label="合适的时候发送表情"
              description={`发送原创小猫和已学表情，默认关闭${characterId !== "default" ? "（仅默认角色支持）" : policy?.profile !== "instant_message" ? "（仅即时消息模式支持）" : ""}`}
              checked={policy?.stickers_enabled ?? false}
              disabled={
                !editable ||
                characterId !== "default" ||
                policy?.profile !== "instant_message" ||
                !connection.capabilities?.outbound_message_kinds?.includes(
                  "image",
                )
              }
              onChange={() => mutate("stickers")}
            />
            <ChannelPresentationPanel
              connection={connection}
              editable={editable}
              onSaved={remember}
              onRefresh={refresh}
            />
            <ChannelConnectionDetails connection={connection} />
            <div className="qq-channel-actions">
              <button
                type="button"
                className="qq-channel-secondary-action"
                disabled={!runtimeOnline || busy}
                onClick={refresh}
              >
                刷新微信连接状态
              </button>
              <button
                type="button"
                className="qq-channel-secondary-action"
                disabled={!editable}
                onClick={start}
              >
                重新扫码绑定
              </button>
              <button
                type="button"
                className="qq-channel-secondary-action"
                disabled={!editable}
                onClick={() => setConfirmRemoval(true)}
              >
                断开连接
              </button>
            </div>
            {confirmRemoval ? (
              <div className="channel-settings-confirm" role="alert">
                <p>
                  解除当前微信绑定？接收任务和凭据会移除，已有对话和资料不会自动删除。
                </p>
                <button
                  type="button"
                  className="qq-channel-secondary-action"
                  disabled={!editable}
                  onClick={() => setConfirmRemoval(false)}
                >
                  保留绑定
                </button>
                <button
                  type="button"
                  className="channels-settings-primary-action"
                  disabled={!editable}
                  onClick={() => mutate("disconnect")}
                >
                  确认解除微信绑定
                </button>
              </div>
            ) : null}
          </>
        ) : !loading ? (
          <div className="channels-settings-empty-state">
            <h3>绑定微信消息</h3>
            <p>
              生成二维码后，用手机扫码并确认。Runtime
              会接收并回复绑定主人的消息。
            </p>
            <button
              type="button"
              className="channels-settings-primary-action"
              disabled={!editable}
              onClick={start}
            >
              {operation === "start" ? "正在生成二维码…" : "扫码绑定微信"}
            </button>
            <button
              type="button"
              className="qq-channel-secondary-action"
              disabled={!runtimeOnline || busy}
              onClick={refresh}
            >
              刷新微信连接状态
            </button>
          </div>
        ) : null}
        {!runtimeOnline ? (
          <p role="status">Runtime 离线，恢复连接后可读取和修改微信绑定。</p>
        ) : null}
        {notice ? (
          <p className="channels-settings-notice" role="alert">
            {notice}
          </p>
        ) : null}
      </div>
    </section>
  );
}
function connectionState(connection: ChannelConnectionSnapshot) {
  if (
    connection.configuration.enabled === false ||
    connection.status === "disabled"
  )
    return "已停用";
  return {
    ready: "已连接",
    untested: "待检查",
    degraded: "连接中断",
    error: "连接异常",
    disabled: "已停用",
  }[connection.status ?? "untested"];
}
function message(error: unknown, fallback: string) {
  return error instanceof Error && !(error instanceof TypeError)
    ? error.message
    : fallback;
}
