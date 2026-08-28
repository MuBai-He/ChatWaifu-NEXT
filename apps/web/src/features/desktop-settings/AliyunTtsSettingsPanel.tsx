import { useEffect, useState } from "react";
import {
  getAliyunTtsConfiguration,
  testAliyunTtsConfiguration,
  updateAliyunTtsConfiguration,
} from "../chat/runtimeClient";
import type {
  AliyunCloudTtsConfiguration,
  AliyunCloudTtsProviderId,
} from "../chat/types";
import { SettingsSecretField } from "./SettingsPrimitives";
import { useSettingsOperation } from "./useSettingsOperation";

type UpdatePayload = Parameters<typeof updateAliyunTtsConfiguration>[0];

const PROVIDERS: Array<{
  providerId: AliyunCloudTtsProviderId;
  title: string;
  description: string;
  models: string[];
  emotionControl: boolean;
}> = [
  {
    providerId: "aliyun_qwen_realtime",
    title: "Qwen3-TTS VC · 实时复刻",
    description: "复刻声线支持实时流式；当前 VC 模型不支持情绪指令。",
    models: [
      "qwen3-tts-vc-realtime-2026-01-15",
      "qwen3-tts-vc-realtime-2025-11-27",
    ],
    emotionControl: false,
  },
  {
    providerId: "aliyun_cosyvoice_realtime",
    title: "CosyVoice 3.5 · 实时情绪复刻",
    description: "边生成边播放，并自动叠加 Character Kernel 的当前情绪。",
    models: [
      "cosyvoice-v3.5-plus",
      "cosyvoice-v3.5-flash",
      "cosyvoice-v3-plus",
      "cosyvoice-v3-flash",
      "cosyvoice-v2",
    ],
    emotionControl: true,
  },
];

const COSYVOICE_INSTRUCTION_MODELS = new Set([
  "cosyvoice-v3.5-plus",
  "cosyvoice-v3.5-flash",
  "cosyvoice-v3-flash",
]);

export function AliyunTtsSettingsPanel({
  providerId,
  onProviderIdChange,
  onSaved,
}: {
  providerId: AliyunCloudTtsProviderId;
  onProviderIdChange: (
    providerId: AliyunCloudTtsProviderId,
  ) => Promise<void> | void;
  onSaved: () => Promise<void>;
}) {
  const provider = PROVIDERS.find((item) => item.providerId === providerId);
  if (!provider) return null;
  return (
    <AliyunTtsSettingsCard
      key={provider.providerId}
      {...provider}
      onProviderIdChange={onProviderIdChange}
      onSaved={onSaved}
    />
  );
}

function AliyunTtsSettingsCard({
  providerId,
  title,
  description,
  models,
  emotionControl,
  onProviderIdChange,
  onSaved,
}: (typeof PROVIDERS)[number] & {
  onProviderIdChange: (
    providerId: AliyunCloudTtsProviderId,
  ) => Promise<void> | void;
  onSaved: () => Promise<void>;
}) {
  const [configuration, setConfiguration] =
    useState<AliyunCloudTtsConfiguration | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const { busy, notice, setNotice, run } = useSettingsOperation<
    "save" | "test"
  >();

  useEffect(() => {
    let disposed = false;
    void getAliyunTtsConfiguration(providerId)
      .then((item) => {
        if (!disposed) setConfiguration(item);
      })
      .catch((error: unknown) => {
        if (!disposed)
          setNotice({
            tone: "error",
            text:
              error instanceof Error ? error.message : "读取百炼配置失败",
          });
      });
    return () => {
      disposed = true;
    };
  }, [providerId, setNotice]);

  const change = <Key extends keyof AliyunCloudTtsConfiguration>(
    key: Key,
    value: AliyunCloudTtsConfiguration[Key],
  ) => {
    setConfiguration((current) =>
      current ? { ...current, [key]: value } : current,
    );
  };

  const persist = async () => {
    if (!configuration) throw new Error("百炼配置尚未加载");
    const updated = await updateAliyunTtsConfiguration({
      ...editableConfiguration(configuration),
      api_key: apiKey.trim() || undefined,
      clear_api_key: clearApiKey,
    });
    setConfiguration(updated);
    setApiKey("");
    setClearApiKey(false);
    await onSaved();
    return updated;
  };

  const save = async () => {
    if (!configuration || busy) return;
    await run("save", persist, {
      success: `${title}配置已保存。`,
      error: "保存失败",
    });
  };

  const test = async () => {
    if (!configuration || busy) return;
    await run(
      "test",
      async () => {
        const updated = await persist();
        return testAliyunTtsConfiguration(updated.provider_id);
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

  if (!configuration || configuration.provider_id !== providerId)
    return (
      <section className="aliyun-tts-panel" aria-label="阿里云百炼 API 设置">
        <header>
          <div>
            <h2>阿里云百炼</h2>
            <p>选择具体实时语音 API，再填写对应模型和克隆音色。</p>
          </div>
          <AliyunApiSelector
            providerId={providerId}
            disabled={Boolean(busy)}
            onChange={onProviderIdChange}
          />
        </header>
        <p className="desktop-settings-empty">
          {notice?.text ?? `正在读取${title}配置…`}
        </p>
      </section>
    );

  const cosyVoice = configuration.provider_id === "aliyun_cosyvoice_realtime";
  const supportsEmotionInstruction =
    emotionControl && COSYVOICE_INSTRUCTION_MODELS.has(configuration.model);

  const changeModel = (model: string) => {
    setConfiguration((current) =>
      current
        ? {
            ...current,
            model,
            instruction:
              emotionControl && !COSYVOICE_INSTRUCTION_MODELS.has(model)
                ? ""
                : current.instruction,
          }
        : current,
    );
  };

  return (
    <section className="aliyun-tts-panel" aria-label="阿里云百炼 API 设置">
      <header>
        <div>
          <h2>阿里云百炼</h2>
          <p>{title}：{description} 仅发送当前待朗读句段。</p>
        </div>
        <AliyunApiSelector
          providerId={providerId}
          disabled={Boolean(busy)}
          onChange={onProviderIdChange}
        />
        <label className="desktop-settings-switch">
          <input
            type="checkbox"
            checked={configuration.enabled}
            onChange={(event) => change("enabled", event.currentTarget.checked)}
          />
          <span />
        </label>
      </header>

      <div className="aliyun-tts-fields">
        <label>
          <span>声音复刻音色 ID</span>
          <input
            value={configuration.voice_id}
            placeholder={cosyVoice ? "cosyvoice-v3.5-plus-…" : "qwen-tts-vc-…"}
            onChange={(event) => change("voice_id", event.currentTarget.value)}
          />
        </label>
        <label>
          <span>基础模型</span>
          <select
            value={configuration.model}
            onChange={(event) => changeModel(event.currentTarget.value)}
          >
            {models.map((model) => (
              <option value={model} key={model}>
                {model}
              </option>
            ))}
          </select>
          <small>音色的 target_model 必须与这里完全一致。</small>
        </label>
        <label>
          <span>服务区域</span>
          <select
            value={configuration.region}
            onChange={(event) =>
              change(
                "region",
                event.currentTarget
                  .value as AliyunCloudTtsConfiguration["region"],
              )
            }
          >
            <option value="beijing">华北 2（北京）</option>
            <option value="singapore">新加坡</option>
          </select>
        </label>
        <label>
          <span>语言</span>
          <select
            value={configuration.language_type}
            onChange={(event) =>
              change(
                "language_type",
                event.currentTarget
                  .value as AliyunCloudTtsConfiguration["language_type"],
              )
            }
          >
            <option value={cosyVoice ? "auto" : "Auto"}>
              自动（中日混合）
            </option>
            <option value={cosyVoice ? "zh" : "Chinese"}>中文</option>
            <option value={cosyVoice ? "ja" : "Japanese"}>日语</option>
            <option value={cosyVoice ? "en" : "English"}>英语</option>
          </select>
        </label>
        <SettingsSecretField
          ariaLabel="阿里云百炼 API Key"
          configured={configuration.api_key_configured}
          value={apiKey}
          disabled={Boolean(busy)}
          help="同地域的 Qwen 与 CosyVoice 可以复用已保存的百炼 Key。"
          onChange={setApiKey}
        />
        <label>
          <span>业务空间 ID（可选）</span>
          <input
            value={configuration.workspace_id}
            onChange={(event) =>
              change("workspace_id", event.currentTarget.value)
            }
          />
        </label>
        {supportsEmotionInstruction ? (
          <label className="aliyun-tts-wide-field">
            <span>基础情绪指令</span>
            <textarea
              rows={2}
              value={configuration.instruction}
              onChange={(event) =>
                change("instruction", event.currentTarget.value)
              }
            />
            <small>
              每次回复还会叠加 Character Kernel
              的语气和表情，合并后自动限制在官方长度内。
            </small>
          </label>
        ) : emotionControl ? (
          <p className="aliyun-tts-wide-field aliyun-tts-model-note">
            当前型号不接受情绪指令；切回 CosyVoice v3.5 Plus / Flash 或 v3 Flash
            后可恢复该能力。
          </p>
        ) : null}
        <label>
          <span>语速 · {configuration.speech_rate.toFixed(2)}</span>
          <input
            type="range"
            min="0.5"
            max="2"
            step="0.05"
            value={configuration.speech_rate}
            onChange={(event) =>
              change("speech_rate", Number(event.currentTarget.value))
            }
          />
        </label>
        <label>
          <span>音量 · {configuration.volume}</span>
          <input
            type="range"
            min="0"
            max="100"
            step="1"
            value={configuration.volume}
            onChange={(event) =>
              change("volume", Number(event.currentTarget.value))
            }
          />
        </label>
      </div>

      {configuration.api_key_configured ? (
        <label className="aliyun-tts-clear-key">
          <input
            type="checkbox"
            checked={clearApiKey}
            onChange={(event) => setClearApiKey(event.currentTarget.checked)}
          />
          移除此语音单独保存的 API Key
        </label>
      ) : null}

      <footer>
        <span role={notice ? "status" : undefined} data-tone={notice?.tone}>
          {notice?.text}
        </span>
        <div>
          <button
            type="button"
            disabled={Boolean(busy)}
            onClick={() => void test()}
          >
            {busy === "test" ? "测试中…" : "保存并测试"}
          </button>
          <button
            type="button"
            disabled={Boolean(busy)}
            onClick={() => void save()}
          >
            {busy === "save" ? "保存中…" : "保存配置"}
          </button>
        </div>
      </footer>

      <p className="desktop-settings-info">
        云端模式不会发送对话历史、结构化记忆、系统提示词或模型密钥。取消或抢话时，当前
        generation 的迟到音频会被丢弃。
      </p>
    </section>
  );
}

function AliyunApiSelector({
  providerId,
  disabled,
  onChange,
}: {
  providerId: AliyunCloudTtsProviderId;
  disabled: boolean;
  onChange: (providerId: AliyunCloudTtsProviderId) => Promise<void> | void;
}) {
  return (
    <label className="aliyun-tts-api-selector">
      <span>百炼语音 API</span>
      <select
        aria-label="百炼语音 API"
        value={providerId}
        disabled={disabled}
        onChange={(event) =>
          void onChange(event.currentTarget.value as AliyunCloudTtsProviderId)
        }
      >
        {PROVIDERS.map((provider) => (
          <option value={provider.providerId} key={provider.providerId}>
            {provider.providerId === "aliyun_cosyvoice_realtime"
              ? "CosyVoice（情绪）"
              : "Qwen3-TTS VC"}
          </option>
        ))}
      </select>
    </label>
  );
}

function editableConfiguration(
  configuration: AliyunCloudTtsConfiguration,
): UpdatePayload {
  return {
    provider_id: configuration.provider_id,
    enabled: configuration.enabled,
    model: configuration.model,
    voice_id: configuration.voice_id,
    region: configuration.region,
    workspace_id: configuration.workspace_id,
    language_type: configuration.language_type,
    sample_rate: configuration.sample_rate,
    speech_rate: configuration.speech_rate,
    volume: configuration.volume,
    pitch_rate: configuration.pitch_rate,
    instruction: configuration.instruction,
    timeout_seconds: configuration.timeout_seconds,
    max_audio_bytes: configuration.max_audio_bytes,
  };
}
