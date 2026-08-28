import type { ReactNode } from "react";

import { SettingsIcon, type SettingsIconName } from "./SettingsIcon";
import type { SettingsNotice } from "./useSettingsOperation";

export function SettingsSectionIntro({
  icon,
  title,
  description,
}: {
  icon: SettingsIconName;
  title: string;
  description: string;
}) {
  return (
    <div className="desktop-settings-section-intro">
      <span>
        <SettingsIcon name={icon} />
      </span>
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
    </div>
  );
}

export function SettingsGroup({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="desktop-settings-group">
      <header>
        <h2>{title}</h2>
        <p>{description}</p>
      </header>
      <div>{children}</div>
    </section>
  );
}

export function SettingsToggle({
  label,
  description,
  checked,
  disabled = false,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (enabled: boolean) => Promise<void> | void;
}) {
  return (
    <label className="desktop-settings-toggle-row">
      <span>
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => void onChange(event.currentTarget.checked)}
        aria-label={label}
      />
    </label>
  );
}

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
