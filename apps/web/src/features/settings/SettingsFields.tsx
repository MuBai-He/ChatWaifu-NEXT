import type { SettingsNotice } from "./useSettingsOperation";

export function SettingsSecretField({
  label = "API Key",
  ariaLabel,
  configured,
  value,
  disabled = false,
  help,
  onChange,
}: {
  label?: string;
  ariaLabel: string;
  configured: boolean;
  value: string;
  disabled?: boolean;
  help?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      <span>
        {label}
        {configured ? " · 已在 Runtime 本地配置" : ""}
      </span>
      <input
        aria-label={ariaLabel}
        type="password"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.value)}
        autoComplete="new-password"
        placeholder={configured ? "留空则保持原密钥" : "本地无鉴权服务可留空"}
      />
      {help ? <small>{help}</small> : null}
    </label>
  );
}

export function SettingsStatus({
  notice,
  className,
}: {
  notice: SettingsNotice | null;
  className: string;
}) {
  if (!notice) return null;
  return (
    <p className={className} role="status" data-tone={notice.tone}>
      {notice.text}
    </p>
  );
}
