import { useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";

import { ProductIcon } from "../../components/ProductIcon";
import {
  cancelChannelAuthorization,
  deleteChannelConnection,
  getChannelAuthorization,
  getChannelConnections,
  startChannelAuthorization,
  submitChannelAuthorizationVerification,
  updateChannelPresentationPolicy,
  type ChannelAuthorizationSnapshot,
  type ChannelConnectionSnapshot,
  type ChannelPresentationPolicy,
} from "../chat/runtimeClient";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { SettingsIcon } from "./SettingsIcon";
import { SettingsSectionIntro, SettingsToggle } from "./SettingsPrimitives";
import { StickerLibraryPanel } from "./StickerLibraryPanel";
import "./channels-settings.css";

const WEIXIN_PROVIDER_ID = "weixin_ilink";

type ChannelOperation = "start" | "verify" | "cancel" | "disconnect";

export function ChannelsSettingsSection({
  context,
}: {
  context: DesktopSettingsContext;
}) {
  const [connection, setConnection] =
    useState<ChannelConnectionSnapshot | null>(null);
  const [authorization, setAuthorization] =
    useState<ChannelAuthorizationSnapshot | null>(null);
  const [verificationCode, setVerificationCode] = useState("");
  const [loading, setLoading] = useState(true);
  const [operation, setOperation] = useState<ChannelOperation | null>(null);
  const [updatingPolicy, setUpdatingPolicy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [pollRevision, setPollRevision] = useState(0);
  const pollAbortRef = useRef<AbortController | null>(null);
  const authorizationRef = useRef(authorization);
  const runtimeOnline = context.runtime.connection === "connected";
  const characterId = context.appearance.character?.character_id ?? "default";

  useEffect(() => {
    authorizationRef.current = authorization;
  }, [authorization]);

  useEffect(() => {
    const controller = new AbortController();
    void getChannelConnections(controller.signal)
      .then((items) => {
        setConnection(
          items.find(
            (item) =>
              item.configuration.provider_id === WEIXIN_PROVIDER_ID &&
              item.configuration.enabled !== false &&
              item.status !== "disabled",
          ) ?? null,
        );
      })
      .catch((error: unknown) => {
        if (!isAbortError(error)) setNotice(message(error, "无法读取渠道状态"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  const authSessionId = authorization?.auth_session_id ?? null;
  useEffect(() => {
    const initialAuthorization = authorizationRef.current;
    if (
      !authSessionId ||
      !initialAuthorization ||
      isTerminal(initialAuthorization.status)
    )
      return;
    const controller = new AbortController();
    pollAbortRef.current = controller;
    let active = true;

    const poll = async () => {
      let current = initialAuthorization;
      try {
        while (active && !isTerminal(current.status)) {
          current = await getChannelAuthorization(
            authSessionId,
            20,
            controller.signal,
          );
          if (!active) return;
          setAuthorization(current);
          if (current.connection) {
            setConnection(current.connection);
            setVerificationCode("");
          }
        }
      } catch (error: unknown) {
        if (active && !isAbortError(error))
          setNotice(message(error, "微信绑定状态更新失败"));
      }
    };

    void poll();
    return () => {
      active = false;
      controller.abort();
      if (pollAbortRef.current === controller) pollAbortRef.current = null;
    };
  }, [authSessionId, pollRevision]);

  const startAuthorization = async () => {
    setOperation("start");
    setNotice(null);
    pollAbortRef.current?.abort();
    try {
      const snapshot = await startChannelAuthorization(
        WEIXIN_PROVIDER_ID,
        characterId,
      );
      setAuthorization(snapshot);
      setVerificationCode("");
      setPollRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(message(error, "无法开始微信绑定"));
    } finally {
      setOperation(null);
    }
  };

  const submitVerification = async () => {
    if (!authorization || !verificationCode.trim()) return;
    setOperation("verify");
    setNotice(null);
    pollAbortRef.current?.abort();
    try {
      const snapshot = await submitChannelAuthorizationVerification(
        authorization.auth_session_id,
        verificationCode.trim(),
      );
      setAuthorization(snapshot);
      setVerificationCode("");
      if (snapshot.connection) setConnection(snapshot.connection);
      setPollRevision((value) => value + 1);
    } catch (error: unknown) {
      setNotice(message(error, "验证码提交失败"));
      setPollRevision((value) => value + 1);
    } finally {
      setOperation(null);
    }
  };

  const cancelAuthorization = async () => {
    if (!authorization) return;
    setOperation("cancel");
    setNotice(null);
    pollAbortRef.current?.abort();
    try {
      await cancelChannelAuthorization(authorization.auth_session_id);
      setAuthorization(null);
      setVerificationCode("");
    } catch (error: unknown) {
      setNotice(message(error, "无法取消本次绑定"));
    } finally {
      setOperation(null);
    }
  };

  const disconnect = async () => {
    if (!connection) return;
    setOperation("disconnect");
    setNotice(null);
    try {
      await deleteChannelConnection(connection.configuration.connection_id);
      setConnection(null);
      setAuthorization(null);
    } catch (error: unknown) {
      setNotice(message(error, "无法断开微信连接"));
    } finally {
      setOperation(null);
    }
  };

  const handleToggleStickers = async (enabled: boolean) => {
    if (!connection) return;
    setUpdatingPolicy(true);
    setNotice(null);
    try {
      const basePolicy = connection.configuration.presentation_policy ?? {
        profile: "single_text",
        cadence_enabled: true,
        stickers_enabled: false,
      };
      const nextPolicy: ChannelPresentationPolicy = {
        ...basePolicy,
        stickers_enabled: enabled,
      };
      const updated = await updateChannelPresentationPolicy(
        connection,
        nextPolicy,
      );
      setConnection(updated);
    } catch (error: unknown) {
      setNotice(message(error, "更新表情设置失败"));
    } finally {
      setUpdatingPolicy(false);
    }
  };

  return (
    <div className="channels-settings-section">
      <section className="desktop-settings-voice-card channels-settings-intro">
        <SettingsSectionIntro
          icon="channels"
          title="消息渠道"
          description="把微信消息接入同一个宁宁角色、关系和记忆。绑定凭据只保存在本机安全存储中。"
        />
      </section>

      <section className="channels-settings-card" aria-label="微信连接">
        <header className="channels-settings-provider-heading">
          <span className="channels-settings-provider-icon">
            <SettingsIcon name="channels" />
          </span>
          <div>
            <h2>微信</h2>
            <p>手机扫码即可完成绑定，连接信息由本机 Runtime 安全托管。</p>
          </div>
          <span
            className={`channels-settings-state ${connection ? "connected" : ""}`}
          >
            <i />
            {connection ? "已连接" : "未连接"}
          </span>
        </header>

        {loading ? (
          <div className="channels-settings-loading" role="status">
            正在读取连接状态…
          </div>
        ) : connection ? (
          <ConnectedWeixinCard
            connection={connection}
            characterId={characterId}
            updatingPolicy={updatingPolicy}
            runtimeOnline={runtimeOnline}
            busy={operation === "disconnect"}
            onToggleStickers={handleToggleStickers}
            onDisconnect={() => void disconnect()}
          />
        ) : authorization ? (
          <AuthorizationCard
            authorization={authorization}
            verificationCode={verificationCode}
            busy={operation}
            onVerificationCodeChange={setVerificationCode}
            onVerify={() => void submitVerification()}
            onCancel={() => void cancelAuthorization()}
            onRetry={() => void startAuthorization()}
          />
        ) : (
          <div className="channels-settings-empty-state">
            <span className="channels-settings-phone-illustration">
              <SettingsIcon name="channels" />
            </span>
            <h3>让宁宁也能在微信里陪你聊天</h3>
            <p>
              点击后会生成一次性二维码。扫码确认后，这台电脑会自动接收并回复你的微信消息。
            </p>
            <button
              className="channels-settings-primary-action"
              type="button"
              disabled={!runtimeOnline || operation === "start"}
              onClick={() => void startAuthorization()}
            >
              {operation === "start" ? "正在生成二维码…" : "扫码绑定微信"}
            </button>
            {!runtimeOnline ? (
              <small>Runtime 离线，恢复本地服务后即可绑定。</small>
            ) : null}
          </div>
        )}

        {notice ? (
          <p className="channels-settings-notice" role="alert">
            {notice}
          </p>
        ) : null}
      </section>
    </div>
  );
}

function AuthorizationCard({
  authorization,
  verificationCode,
  busy,
  onVerificationCodeChange,
  onVerify,
  onCancel,
  onRetry,
}: {
  authorization: ChannelAuthorizationSnapshot;
  verificationCode: string;
  busy: ChannelOperation | null;
  onVerificationCodeChange: (value: string) => void;
  onVerify: () => void;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const terminal = isTerminal(authorization.status);
  const showVerification = authorization.status === "verification_required";
  const showQr = Boolean(authorization.qr_code_content) && !terminal;
  return (
    <div className="channels-settings-auth">
      {showQr ? (
        <div
          className="channels-settings-qr"
          role="img"
          aria-label="微信绑定二维码"
        >
          <QRCodeSVG
            value={authorization.qr_code_content ?? ""}
            size={184}
            level="M"
            marginSize={2}
            bgColor="#ffffff"
            fgColor="#302534"
          />
        </div>
      ) : (
        <span className={`channels-settings-auth-mark ${authorization.status}`}>
          {statusMark(authorization.status)}
        </span>
      )}

      <div className="channels-settings-auth-copy">
        <small>微信连接</small>
        <h3>{authorizationTitle(authorization.status)}</h3>
        <p>
          {authorization.status_message ??
            authorizationDescription(authorization.status)}
        </p>

        {showVerification ? (
          <form
            className="channels-settings-verification"
            onSubmit={(event) => {
              event.preventDefault();
              onVerify();
            }}
          >
            <label htmlFor="weixin-verification-code">手机验证码</label>
            <div>
              <input
                id="weixin-verification-code"
                value={verificationCode}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={12}
                disabled={busy === "verify"}
                onChange={(event) =>
                  onVerificationCodeChange(event.currentTarget.value)
                }
                placeholder="输入微信显示的验证码"
              />
              <button
                type="submit"
                disabled={!verificationCode.trim() || busy === "verify"}
              >
                {busy === "verify" ? "正在验证…" : "确认"}
              </button>
            </div>
          </form>
        ) : null}

        <div className="channels-settings-auth-actions">
          {terminal && authorization.status !== "confirmed" ? (
            <button type="button" disabled={busy === "start"} onClick={onRetry}>
              {busy === "start" ? "正在刷新…" : "重新生成二维码"}
            </button>
          ) : null}
          {!terminal ? (
            <button
              className="secondary"
              type="button"
              disabled={busy === "cancel"}
              onClick={onCancel}
            >
              {busy === "cancel" ? "正在取消…" : "取消绑定"}
            </button>
          ) : null}
        </div>
        <small className="channels-settings-expiry">
          二维码有效期至 {formatDate(authorization.expires_at)}
        </small>
      </div>
    </div>
  );
}

function ConnectedWeixinCard({
  connection,
  characterId,
  updatingPolicy,
  runtimeOnline,
  busy,
  onToggleStickers,
  onDisconnect,
}: {
  connection: ChannelConnectionSnapshot;
  characterId: string;
  updatingPolicy: boolean;
  runtimeOnline: boolean;
  busy: boolean;
  onToggleStickers: (enabled: boolean) => Promise<void>;
  onDisconnect: () => void;
}) {
  const isDefaultCharacter =
    characterId === "default" &&
    connection.configuration.character_id === "default";
  const profile =
    connection.configuration.presentation_policy?.profile ?? "single_text";
  const isInstantMessage = profile === "instant_message";
  const stickersEnabled =
    connection.configuration.presentation_policy?.stickers_enabled ?? false;

  const toggleDisabled =
    !isDefaultCharacter ||
    !isInstantMessage ||
    updatingPolicy ||
    !runtimeOnline ||
    busy;

  const explanation = !isDefaultCharacter
    ? "（仅默认角色支持）"
    : !isInstantMessage
      ? "（仅即时消息模式支持）"
      : "";
  const shortCopy = `发送原创小猫和已学表情，默认关闭${explanation}`;

  return (
    <div className="channels-settings-connected-card">
      <span className="channels-settings-connected-mark">
        <ProductIcon name="connected" />
      </span>
      <div>
        <small>连接正常</small>
        <h3>{connection.configuration.name || "微信"}</h3>
        <p>
          微信消息会进入当前角色的对话与记忆，并保留消息来源。桌宠与微信共享同一个宁宁。
        </p>
        <p>
          可以发送单张静态图片（PNG/JPEG，最大 5
          MB）。图片会交给当前聊天模型理解，
          未开启表情学习时，图片仅用于本次理解。
        </p>
        <span>最近更新：{formatDate(connection.updated_at)}</span>
      </div>
      <button
        type="button"
        disabled={busy || updatingPolicy}
        onClick={onDisconnect}
      >
        {busy ? "正在断开…" : "断开连接"}
      </button>

      <div className="channels-settings-policy-toggle">
        <SettingsToggle
          label="合适的时候发送表情"
          description={shortCopy}
          checked={stickersEnabled}
          disabled={toggleDisabled}
          onChange={(enabled) => void onToggleStickers(enabled)}
        />
      </div>

      {isDefaultCharacter ? (
        <div className="channels-settings-sticker-library">
          <StickerLibraryPanel
            characterId={characterId}
            runtimeOnline={runtimeOnline}
          />
        </div>
      ) : null}
    </div>
  );
}

function isTerminal(status: ChannelAuthorizationSnapshot["status"]): boolean {
  return ["confirmed", "expired", "cancelled", "failed"].includes(status);
}

function authorizationTitle(
  status: ChannelAuthorizationSnapshot["status"],
): string {
  if (status === "pending") return "请使用微信扫码";
  if (status === "scanned") return "已扫码，请在手机上确认";
  if (status === "verification_required") return "还需要一步安全验证";
  if (status === "confirmed") return "微信已连接";
  if (status === "expired") return "二维码已过期";
  if (status === "cancelled") return "本次绑定已取消";
  return "微信绑定未完成";
}

function authorizationDescription(
  status: ChannelAuthorizationSnapshot["status"],
): string {
  if (status === "pending") return "打开微信扫一扫，并在手机上确认登录。";
  if (status === "scanned")
    return "这台电脑正在等待手机端确认，请不要关闭页面。";
  if (status === "verification_required")
    return "微信要求额外验证。只有此时，本页才会请求验证码。";
  if (status === "confirmed") return "连接已经建立，可以直接从微信发消息。";
  if (status === "expired") return "为保护账号安全，请生成新的二维码后再试。";
  if (status === "cancelled") return "你可以随时重新发起绑定。";
  return "请检查网络和微信状态，然后重新生成二维码。";
}

function statusMark(status: ChannelAuthorizationSnapshot["status"]) {
  if (status === "confirmed") return <ProductIcon name="connected" />;
  if (status === "expired") return <ProductIcon name="refresh" />;
  if (status === "cancelled") return <ProductIcon name="disconnected" />;
  return <ProductIcon name="alert" />;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function message(error: unknown, fallback: string): string {
  if (error instanceof TypeError) return fallback;
  return error instanceof Error ? error.message : fallback;
}
