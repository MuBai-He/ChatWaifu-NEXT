import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  readRuntimeRequestContext,
  type RuntimeRequestContext,
} from "../chat/runtimeEndpoint";
import { SettingsGroup } from "../settings/SettingsPrimitives";
import {
  getPasswordStatus,
  replacePasswordSession,
  updatePassword,
  type PasswordStatus,
} from "./runtimePassword";
import "./password-settings.css";

export function PasswordSettingsPanel({
  active = true,
  online,
}: {
  active?: boolean;
  online: boolean;
}) {
  const [refresh, setRefresh] = useState(0);
  return (
    <SettingsGroup
      title="登录密码"
      description="设置自定义密码后，连接服务器时即可使用密码登录。"
    >
      {active && online ? (
        <PasswordSettingsForm
          key={refresh}
          onReload={() => setRefresh((value) => value + 1)}
        />
      ) : (
        <p>连接后可设置登录密码。</p>
      )}
    </SettingsGroup>
  );
}

function PasswordSettingsForm({ onReload }: { onReload: () => void }) {
  const [status, setStatus] = useState<PasswordStatus | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const lifetime = useRef<AbortController | null>(null);
  const context = useRef<RuntimeRequestContext | null>(null);
  const saving = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    void (async () => {
      try {
        const snapshot = await readRuntimeRequestContext();
        const value = await getPasswordStatus(snapshot, controller.signal);
        context.current = snapshot;
        setStatus(value);
      } catch (error) {
        if (!controller.signal.aborted)
          setNotice(
            error instanceof Error ? error.message : "无法读取密码设置。",
          );
      }
    })();
    return () => {
      controller.abort();
      lifetime.current = null;
    };
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    const snapshot = context.current;
    const controller = lifetime.current;
    if (!snapshot || !controller || !status || saving.current) return;
    if (newPassword !== confirmation) {
      setNotice("两次输入的新密码不一致。");
      return;
    }
    if ([...newPassword].length < 12 || [...newPassword].length > 128) {
      setNotice("新密码请使用 12–128 个字符。");
      return;
    }
    saving.current = true;
    setBusy(true);
    setNotice("");
    try {
      const token = await updatePassword(
        snapshot,
        {
          new_password: newPassword,
          current_password: status.configured ? currentPassword : undefined,
          expected_revision: status.revision,
        },
        controller.signal,
      );
      controller.signal.throwIfAborted();
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setStatus({
        schema_version: 1,
        configured: true,
        revision: status.revision + 1,
      });
      setNotice("密码已保存，下次连接服务器可选择密码登录。");
      await replacePasswordSession(snapshot, token);
    } catch (error) {
      if (!controller.signal.aborted)
        setNotice(error instanceof Error ? error.message : "保存密码失败。");
    } finally {
      if (!controller.signal.aborted) {
        saving.current = false;
        setBusy(false);
      }
    }
  }

  return (
    <form
      className="runtime-password-form"
      onSubmit={(event) => void save(event)}
    >
      <p>
        {status
          ? status.configured
            ? "已设置登录密码"
            : "尚未设置登录密码，请先设置一次。"
          : notice
            ? "无法读取密码设置。"
            : "正在读取密码设置…"}
      </p>
      {status?.configured && (
        <label>
          当前密码
          <input
            type="password"
            aria-label="当前密码"
            autoComplete="current-password"
            required
            value={currentPassword}
            disabled={busy || !status}
            onChange={(event) => setCurrentPassword(event.target.value)}
          />
        </label>
      )}
      <label>
        新密码
        <input
          type="password"
          aria-label="新密码"
          autoComplete="new-password"
          required
          value={newPassword}
          disabled={busy || !status}
          onChange={(event) => setNewPassword(event.target.value)}
        />
        <small>12–128 个字符，可使用中文、空格和符号。</small>
      </label>
      <label>
        确认新密码
        <input
          type="password"
          aria-label="确认新密码"
          autoComplete="new-password"
          required
          value={confirmation}
          disabled={busy || !status}
          onChange={(event) => setConfirmation(event.target.value)}
        />
      </label>
      <small>修改密码后，其他设备的密码登录需要重新连接。</small>
      <button type="submit" disabled={busy || !status}>
        {busy ? "正在保存…" : status?.configured ? "修改密码" : "设置密码"}
      </button>
      <button type="button" disabled={busy} onClick={onReload}>
        重新读取密码设置
      </button>
      {notice && <p role="status">{notice}</p>}
    </form>
  );
}
