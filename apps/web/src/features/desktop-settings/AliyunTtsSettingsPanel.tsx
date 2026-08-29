import { useEffect, useMemo, useState } from "react";

import {
  getTtsConfiguration,
  getTtsConfigurationRegistrations,
  testTtsConfiguration,
  updateTtsConfiguration,
} from "../chat/runtimeClient";
import type {
  TtsConfigurationRegistration,
  TtsConfigurationSnapshot,
  TtsConfigurationUiField,
} from "../chat/types";
import { useSettingsOperation } from "../settings/useSettingsOperation";
import { SettingsSecretField } from "./SettingsPrimitives";

const INTERNAL_FIELDS = new Set(["provider_id", "updated_at"]);

export function TtsConfigurationPanel({
  preferredProviderId,
  onProviderIdChange,
  onSaved,
}: {
  preferredProviderId?: string;
  onProviderIdChange?: (providerId: string) => Promise<void> | void;
  onSaved: () => Promise<void>;
}) {
  const [registrations, setRegistrations] = useState<
    TtsConfigurationRegistration[]
  >([]);
  const [providerId, setProviderId] = useState(preferredProviderId ?? "");
  const [configuration, setConfiguration] =
    useState<TtsConfigurationSnapshot | null>(null);
  const [secretValues, setSecretValues] = useState<Record<string, string>>({});
  const [clearedSecrets, setClearedSecrets] = useState<Set<string>>(
    () => new Set(),
  );
  const { busy, notice, setNotice, run } = useSettingsOperation<
    "save" | "test"
  >();

  useEffect(() => {
    let active = true;
    void getTtsConfigurationRegistrations()
      .then((items) => {
        if (!active) return;
        setRegistrations(items);
        const selected =
          items.find((item) => item.provider_id === preferredProviderId) ??
          items[0];
        setProviderId(selected?.provider_id ?? "");
        setConfiguration(selected?.configuration ?? null);
        setNotice(null);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setNotice({
          tone: "error",
          text: errorMessage(error, "读取 TTS 配置注册表失败"),
        });
      });
    return () => {
      active = false;
    };
  }, [preferredProviderId, setNotice]);

  useEffect(() => {
    if (!providerId) return;
    let active = true;
    void getTtsConfiguration(providerId)
      .then((item) => {
        if (active) setConfiguration(item);
      })
      .catch((error: unknown) => {
        if (active)
          setNotice({
            tone: "error",
            text: errorMessage(error, "读取 TTS 配置失败"),
          });
      });
    return () => {
      active = false;
    };
  }, [providerId, registrations, setNotice]);

  const registration = useMemo(
    () => registrations.find((item) => item.provider_id === providerId) ?? null,
    [providerId, registrations],
  );

  const selectProvider = async (nextProviderId: string) => {
    setProviderId(nextProviderId);
    setSecretValues({});
    setClearedSecrets(new Set());
    await onProviderIdChange?.(nextProviderId);
  };

  const change = (field: string, value: string | number | boolean) => {
    setConfiguration((current) =>
      current ? { ...current, [field]: value } : current,
    );
  };

  const persist = async () => {
    if (!configuration || !registration) throw new Error("TTS 配置尚未加载");
    const updated = await updateTtsConfiguration(
      registration.provider_id,
      editableConfiguration(
        configuration,
        registration.ui_schema.fields,
        secretValues,
        clearedSecrets,
      ),
    );
    setConfiguration(updated);
    setRegistrations((current) =>
      current.map((item) =>
        item.provider_id === updated.provider_id
          ? { ...item, configuration: updated }
          : item,
      ),
    );
    setSecretValues({});
    setClearedSecrets(new Set());
    await onSaved();
    return updated;
  };

  const save = async () => {
    if (!configuration || !registration || busy) return;
    await run("save", persist, {
      success: `${registration.display_name}配置已保存。`,
      error: "保存失败",
    });
  };

  const test = async () => {
    if (!configuration || !registration || busy) return;
    await run(
      "test",
      async () => {
        const updated = await persist();
        return testTtsConfiguration(updated.provider_id);
      },
      {
        pending: "正在保存配置并生成一小段测试语音…",
        success: (result) =>
          result.status === "ok"
            ? `连接、音色与实时音频可用（${result.duration_ms ?? 0} ms）。`
            : `测试结果：${result.status}`,
        error: "连接测试失败",
      },
    );
  };

  return (
    <section className="aliyun-tts-panel" aria-label="TTS Provider 设置">
      <header>
        <div>
          <h2>TTS Provider</h2>
          <p>
            {registration?.display_name ?? "正在读取配置注册表"}
            {registration?.configuration_schema.description
              ? `：${registration.configuration_schema.description}`
              : ""}
          </p>
        </div>
        <label className="aliyun-tts-api-selector">
          <span>配置入口</span>
          <select
            aria-label="TTS 配置入口"
            value={providerId}
            disabled={Boolean(busy) || !registrations.length}
            onChange={(event) => void selectProvider(event.currentTarget.value)}
          >
            {registrations.map((item) => (
              <option value={item.provider_id} key={item.provider_id}>
                {item.display_name}
              </option>
            ))}
          </select>
        </label>
        {configuration &&
        registration?.ui_schema.fields.some(
          (field) => field.key === "enabled" && field.control === "toggle",
        ) ? (
          <label className="desktop-settings-switch">
            <input
              type="checkbox"
              checked={configuration.enabled === true}
              onChange={(event) =>
                change("enabled", event.currentTarget.checked)
              }
            />
            <span />
          </label>
        ) : null}
      </header>

      {configuration && registration ? (
        <div className="aliyun-tts-fields">
          {regularFields(registration.ui_schema.fields).map((field) =>
            field.control === "secret" ? (
              <SettingsSecretField
                key={field.key}
                ariaLabel={field.label}
                configured={configuration[`${field.key}_configured`] === true}
                value={secretValues[field.key] ?? ""}
                disabled={Boolean(busy)}
                help={field.help_text}
                onChange={(value) =>
                  setSecretValues((current) => ({
                    ...current,
                    [field.key]: value,
                  }))
                }
              />
            ) : (
              <ConfigurationField
                key={field.key}
                field={field}
                value={configuration[field.key]}
                onChange={(value) => change(field.key, value)}
              />
            ),
          )}
          {advancedFields(registration.ui_schema.fields).length ? (
            <details className="aliyun-tts-wide-field">
              <summary>高级设置</summary>
              <div className="aliyun-tts-fields">
                {advancedFields(registration.ui_schema.fields).map((field) => (
                  <ConfigurationField
                    key={field.key}
                    field={field}
                    value={configuration[field.key]}
                    onChange={(value) => change(field.key, value)}
                  />
                ))}
              </div>
            </details>
          ) : null}
        </div>
      ) : (
        <p className="desktop-settings-empty">
          {notice?.text ?? "正在读取可配置的 TTS Provider…"}
        </p>
      )}

      {configuration && registration
        ? registration.ui_schema.fields
            .filter(
              (field) =>
                field.control === "secret" &&
                configuration[`${field.key}_configured`] === true,
            )
            .map((field) => (
              <label
                className="aliyun-tts-clear-key"
                key={`clear-${field.key}`}
              >
                <input
                  type="checkbox"
                  checked={clearedSecrets.has(field.key)}
                  onChange={(event) =>
                    setClearedSecrets((current) => {
                      const next = new Set(current);
                      if (event.currentTarget.checked) next.add(field.key);
                      else next.delete(field.key);
                      return next;
                    })
                  }
                />
                移除此 Provider 单独保存的 {field.label}
              </label>
            ))
        : null}

      <footer>
        <span role={notice ? "status" : undefined} data-tone={notice?.tone}>
          {notice?.text}
        </span>
        <div>
          <button
            type="button"
            disabled={Boolean(busy) || !configuration}
            onClick={() => void test()}
          >
            {busy === "test" ? "测试中…" : "保存并测试"}
          </button>
          <button
            type="button"
            disabled={Boolean(busy) || !configuration}
            onClick={() => void save()}
          >
            {busy === "save" ? "保存中…" : "保存配置"}
          </button>
        </div>
      </footer>

      <p className="desktop-settings-info">
        Provider 的字段、枚举与范围由 Runtime
        注册表提供。新增语音后，设置页不再需要添加专属 Qwen/CosyVoice 分支。
      </p>
    </section>
  );
}

// Compatibility export for callers that still use the previous component name.
export const AliyunTtsSettingsPanel = TtsConfigurationPanel;

function ConfigurationField({
  field,
  value,
  onChange,
}: {
  field: TtsConfigurationUiField;
  value: unknown;
  onChange: (value: string | number | boolean) => void;
}) {
  if (field.control === "toggle") {
    return (
      <label>
        <span>{field.label}</span>
        <input
          type="checkbox"
          checked={value === true}
          onChange={(event) => onChange(event.currentTarget.checked)}
        />
        {field.help_text ? <small>{field.help_text}</small> : null}
      </label>
    );
  }
  if (field.control === "select") {
    return (
      <label>
        <span>{field.label}</span>
        <select
          value={scalarText(value)}
          onChange={(event) =>
            onChange(
              optionValue(field.options ?? [], event.currentTarget.value),
            )
          }
        >
          {field.options.map((option) => (
            <option value={String(option.value)} key={String(option.value)}>
              {option.label}
            </option>
          ))}
        </select>
        {field.help_text ? <small>{field.help_text}</small> : null}
      </label>
    );
  }
  if (field.control === "number") {
    return (
      <label>
        <span>{field.label}</span>
        <input
          type="number"
          value={typeof value === "number" ? value : ""}
          min={field.minimum ?? undefined}
          max={field.maximum ?? undefined}
          step={field.step ?? "any"}
          onChange={(event) => onChange(Number(event.currentTarget.value))}
        />
        {field.help_text ? <small>{field.help_text}</small> : null}
      </label>
    );
  }
  if (field.control === "textarea") {
    return (
      <label className="aliyun-tts-wide-field">
        <span>{field.label}</span>
        <textarea
          rows={2}
          value={scalarText(value)}
          placeholder={field.placeholder}
          onChange={(event) => onChange(event.currentTarget.value)}
        />
        {field.help_text ? <small>{field.help_text}</small> : null}
      </label>
    );
  }
  return (
    <label>
      <span>{field.label}</span>
      <input
        value={scalarText(value)}
        placeholder={field.placeholder}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
      {field.help_text ? <small>{field.help_text}</small> : null}
    </label>
  );
}

function regularFields(fields: TtsConfigurationUiField[]) {
  return fields.filter(
    (field) =>
      !field.advanced &&
      field.key !== "enabled" &&
      !INTERNAL_FIELDS.has(field.key),
  );
}

function advancedFields(fields: TtsConfigurationUiField[]) {
  return fields.filter(
    (field) =>
      field.advanced &&
      field.control !== "secret" &&
      !INTERNAL_FIELDS.has(field.key),
  );
}

function editableConfiguration(
  configuration: TtsConfigurationSnapshot,
  fields: TtsConfigurationUiField[],
  secretValues: Record<string, string>,
  clearedSecrets: ReadonlySet<string>,
): Record<string, unknown> {
  const payload = Object.fromEntries(
    fields
      .filter(
        (field) =>
          field.control !== "secret" &&
          !INTERNAL_FIELDS.has(field.key) &&
          field.key in configuration,
      )
      .map((field) => [field.key, configuration[field.key]]),
  );
  for (const field of fields.filter(
    (candidate) => candidate.control === "secret",
  )) {
    const secret = secretValues[field.key]?.trim();
    if (secret) payload[field.key] = secret;
    if (clearedSecrets.has(field.key)) payload[`clear_${field.key}`] = true;
  }
  return payload;
}

function scalarText(value: unknown): string {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : "";
}

function optionValue(
  options: Array<{ value: string | number | boolean }>,
  selected: string,
): string | number | boolean {
  return (
    options.find((option) => String(option.value) === selected)?.value ??
    selected
  );
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
