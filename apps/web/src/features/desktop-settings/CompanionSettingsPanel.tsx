import { useEffect, useMemo, useState } from "react";
import {
  getCompanionStatus,
  sleepCompanionResources,
  updateCompanionSettings,
  wakeCompanionResources,
} from "../chat/runtimeClient";
import {
  isDesktopHost,
  readDesktopRuntimeStatus,
  restartDesktopRuntime,
  type DesktopRuntimeStatus,
} from "../chat/runtimeEndpoint";
import type { CompanionSettings, ResourceStatus } from "../chat/types";
import { SettingsIcon } from "./SettingsIcon";

const fallbackSettings: CompanionSettings = {
  schema_version: "1.0",
  wake_phrase_enabled: true,
  wake_phrases: ["宁宁", "绫地宁宁"],
  quiet_hours_enabled: true,
  quiet_start: "23:00",
  quiet_end: "08:00",
  proactive_enabled: false,
  proactive_idle_minutes: 45,
  proactive_cooldown_minutes: 60,
  proactive_daily_budget: 3,
  resource_sleep_enabled: true,
  resource_idle_minutes: 10,
  updated_at: new Date(0).toISOString(),
};

export function CompanionSettingsPanel() {
  const [settings, setSettings] = useState(fallbackSettings);
  const [resources, setResources] = useState<ResourceStatus | null>(null);
  const [host, setHost] = useState<DesktopRuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const phrases = useMemo(() => settings.wake_phrases.join("、"), [settings]);

  useEffect(() => {
    let active = true;
    let unlisten: (() => void) | undefined;
    const load = async () => {
      try {
        const [status, runtimeHost] = await Promise.all([
          getCompanionStatus(),
          readDesktopRuntimeStatus(),
        ]);
        if (!active) return;
        setSettings(status.settings);
        setResources(status.resources);
        setHost(runtimeHost);
        if (isDesktopHost()) {
          const { listen } = await import("@tauri-apps/api/event");
          unlisten = await listen<DesktopRuntimeStatus>(
            "desktop-runtime-status-changed",
            (event) => {
              if (active) setHost(event.payload);
            },
          );
        }
      } catch (loadError: unknown) {
        if (active) setError(message(loadError, "无法读取陪伴设置"));
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
      unlisten?.();
    };
  }, []);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const { schema_version: _, updated_at: __, ...payload } = settings;
      setSettings(await updateCompanionSettings(payload));
    } catch (saveError: unknown) {
      setError(message(saveError, "无法保存陪伴设置"));
    } finally {
      setSaving(false);
    }
  };

  const resourceAction = async (action: "sleep" | "wake") => {
    setError(null);
    try {
      setResources(
        action === "sleep"
          ? await sleepCompanionResources()
          : await wakeCompanionResources(),
      );
    } catch (actionError: unknown) {
      setError(message(actionError, "无法切换模型资源状态"));
    }
  };

  return (
    <>
      <section className="desktop-settings-voice-card companion-settings-hero">
        <div className="desktop-settings-section-intro">
          <span>
            <SettingsIcon name="companion" />
          </span>
          <div>
            <h2>陪伴模式</h2>
            <p>
              决定宁宁什么时候听你说话、什么时候主动出现，以及空闲时保留多少资源。
            </p>
          </div>
        </div>
      </section>

      <SettingsBlock
        title="注意力"
        description="按键说话始终直接响应；开放麦克风时可要求先叫名字"
      >
        <ToggleRow
          label="开放麦克风需要唤醒词"
          description="避免把你和旁人的普通交谈当成对宁宁说话"
          checked={settings.wake_phrase_enabled}
          disabled={loading || saving}
          onChange={(wake_phrase_enabled) =>
            setSettings((current) => ({ ...current, wake_phrase_enabled }))
          }
        />
        <label className="companion-settings-input-row">
          <span>
            <strong>称呼</strong>
            <small>用顿号或逗号分隔，例如：宁宁、绫地宁宁</small>
          </span>
          <input
            aria-label="桌宠唤醒称呼"
            value={phrases}
            disabled={loading || saving}
            onChange={(event) =>
              setSettings((current) => ({
                ...current,
                wake_phrases: splitPhrases(event.currentTarget.value),
              }))
            }
          />
        </label>
      </SettingsBlock>

      <SettingsBlock
        title="安静时段"
        description="安静时段内不会主动说话，但你仍可正常呼叫和聊天"
      >
        <ToggleRow
          label="启用安静时段"
          description={`${settings.quiet_start} 至 ${settings.quiet_end}`}
          checked={settings.quiet_hours_enabled}
          disabled={loading || saving}
          onChange={(quiet_hours_enabled) =>
            setSettings((current) => ({ ...current, quiet_hours_enabled }))
          }
        />
        <div className="companion-settings-time-row">
          <label>
            开始
            <input
              aria-label="安静时段开始"
              type="time"
              value={settings.quiet_start}
              onChange={(event) =>
                setSettings((current) => ({
                  ...current,
                  quiet_start: event.currentTarget.value,
                }))
              }
            />
          </label>
          <label>
            结束
            <input
              aria-label="安静时段结束"
              type="time"
              value={settings.quiet_end}
              onChange={(event) =>
                setSettings((current) => ({
                  ...current,
                  quiet_end: event.currentTarget.value,
                }))
              }
            />
          </label>
        </div>
      </SettingsBlock>

      <SettingsBlock
        title="主动陪伴"
        description="默认关闭；启用后仍受安静时段、冷却和每日次数限制"
      >
        <ToggleRow
          label="允许宁宁主动开口"
          description={`空闲 ${settings.proactive_idle_minutes} 分钟后考虑一次，每日最多 ${settings.proactive_daily_budget} 次`}
          checked={settings.proactive_enabled}
          disabled={loading || saving}
          onChange={(proactive_enabled) =>
            setSettings((current) => ({ ...current, proactive_enabled }))
          }
        />
        <NumberRow
          label="等待时间"
          suffix="分钟"
          value={settings.proactive_idle_minutes}
          min={1}
          max={1440}
          onChange={(proactive_idle_minutes) =>
            setSettings((current) => ({ ...current, proactive_idle_minutes }))
          }
        />
        <NumberRow
          label="两次主动问候间隔"
          suffix="分钟"
          value={settings.proactive_cooldown_minutes}
          min={1}
          max={10080}
          onChange={(proactive_cooldown_minutes) =>
            setSettings((current) => ({
              ...current,
              proactive_cooldown_minutes,
            }))
          }
        />
        <NumberRow
          label="每日上限"
          suffix="次"
          value={settings.proactive_daily_budget}
          min={0}
          max={24}
          onChange={(proactive_daily_budget) =>
            setSettings((current) => ({ ...current, proactive_daily_budget }))
          }
        />
      </SettingsBlock>

      <SettingsBlock
        title="资源休眠"
        description="只卸载模型权重；服务和对话仍保持，下一次使用会自动加载"
      >
        <ToggleRow
          label="空闲时释放模型"
          description={`连续空闲 ${settings.resource_idle_minutes} 分钟后释放 ASR/TTS 权重`}
          checked={settings.resource_sleep_enabled}
          disabled={loading || saving}
          onChange={(resource_sleep_enabled) =>
            setSettings((current) => ({ ...current, resource_sleep_enabled }))
          }
        />
        <NumberRow
          label="空闲阈值"
          suffix="分钟"
          value={settings.resource_idle_minutes}
          min={1}
          max={1440}
          onChange={(resource_idle_minutes) =>
            setSettings((current) => ({ ...current, resource_idle_minutes }))
          }
        />
        <div className="companion-settings-action-row">
          <div>
            <strong>
              {resources?.state === "sleeping" ? "模型已休眠" : "模型按需待命"}
            </strong>
            <small>已自动或手动休眠 {resources?.sleep_count ?? 0} 次</small>
          </div>
          <button
            type="button"
            onClick={() =>
              void resourceAction(
                resources?.state === "sleeping" ? "wake" : "sleep",
              )
            }
          >
            {resources?.state === "sleeping" ? "唤醒" : "立即休眠"}
          </button>
        </div>
      </SettingsBlock>

      {host ? (
        <SettingsBlock
          title="本地服务"
          description="桌面宿主会自动检测崩溃并恢复 Runtime 与语音进程"
        >
          <div className="companion-settings-action-row">
            <div>
              <strong>{hostLabel(host.state)}</strong>
              <small>
                {host.detail ?? `已托管 ${host.workers.length} 个本地 Worker`}
              </small>
            </div>
            <button
              type="button"
              disabled={host.state === "starting" || host.state === "backoff"}
              onClick={() => void restartDesktopRuntime().then(setHost)}
            >
              重启本地服务
            </button>
          </div>
        </SettingsBlock>
      ) : null}

      <div className="companion-settings-save-row">
        <span>{error ?? "设置保存在本机 Runtime 中。"}</span>
        <button
          type="button"
          disabled={loading || saving}
          onClick={() => void save()}
        >
          {saving ? "正在保存…" : "保存陪伴设置"}
        </button>
      </div>
    </>
  );
}

function SettingsBlock({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
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

function ToggleRow({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (value: boolean) => void;
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
        onChange={(event) => onChange(event.currentTarget.checked)}
      />
    </label>
  );
}

function NumberRow({
  label,
  suffix,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  suffix: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="companion-settings-number-row">
      <strong>{label}</strong>
      <span>
        <input
          aria-label={label}
          type="number"
          value={value}
          min={min}
          max={max}
          onChange={(event) => onChange(event.currentTarget.valueAsNumber)}
        />
        {suffix}
      </span>
    </label>
  );
}

function splitPhrases(value: string): string[] {
  return value
    .split(/[、,，]/u)
    .map((item) => item.trim())
    .filter(Boolean);
}

function hostLabel(state: DesktopRuntimeStatus["state"]): string {
  if (state === "ready") return "本地服务运行正常";
  if (state === "starting") return "正在启动本地服务";
  if (state === "backoff") return "正在自动恢复";
  if (state === "circuit_open") return "自动恢复已暂停";
  return "本地服务已停止";
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
