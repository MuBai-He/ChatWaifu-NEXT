import { QRCodeSVG } from "qrcode.react";
import { ProductIcon } from "../../components/ProductIcon";
import type { ChannelAuthorizationSnapshot } from "../chat/runtimeClient";
type ChannelOperation = "start" | "verify" | "cancel";

export function WeixinAuthorizationCard({
  authorization,
  verificationCode,
  busy,
  disabled = false,
  onVerificationCodeChange,
  onVerify,
  onCancel,
  onRetry,
}: {
  authorization: ChannelAuthorizationSnapshot;
  verificationCode: string;
  busy: ChannelOperation | null;
  disabled?: boolean;
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
                disabled={disabled || busy !== null}
                onChange={(event) =>
                  onVerificationCodeChange(event.currentTarget.value)
                }
                placeholder="输入微信显示的验证码"
              />
              <button
                type="submit"
                disabled={disabled || !verificationCode.trim() || busy !== null}
              >
                {busy === "verify" ? "正在验证…" : "确认"}
              </button>
            </div>
          </form>
        ) : null}

        <div className="channels-settings-auth-actions">
          {terminal && authorization.status !== "confirmed" ? (
            <button
              type="button"
              disabled={disabled || busy !== null}
              onClick={onRetry}
            >
              {busy === "start" ? "正在刷新…" : "重新生成二维码"}
            </button>
          ) : null}
          {!terminal ? (
            <button
              className="secondary"
              type="button"
              disabled={disabled || busy !== null}
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
