import type { ReactNode } from "react";

import { SettingsIcon, type SettingsIconName } from "./SettingsIcon";

export {
  SettingsSecretField,
  SettingsStatus,
} from "../settings/SettingsFields";

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
