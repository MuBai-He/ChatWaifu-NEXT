import { useEffect, useState } from "react";
import {
  getAliyunTtsConfiguration,
  testAliyunTtsConfiguration,
  updateAliyunTtsConfiguration,
} from "../chat/runtimeClient";
import type { AliyunTtsConfiguration } from "../chat/types";

type EditableConfiguration = Omit<
  AliyunTtsConfiguration,
  "provider_id" | "api_key_configured" | "updated_at"
>;

export function AliyunTtsSettingsPanel({
  onSaved,
}: {
  onSaved: () => Promise<void>;
}) {
  const [configuration, setConfiguration] =
    useState<AliyunTtsConfiguration | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    void getAliyunTtsConfiguration()
      .then((item) => {
        if (!disposed) setConfiguration(item);
      })
      .catch((error: unknown) => {
        if (!disposed)
          setMessage(error instanceof Error ? error.message : "读取百炼配置失败");
      });
    return () => {
      disposed = true;
    };
  }, []);

  const change = <Key extends keyof EditableConfiguration>(
    key: Key,
    value: EditableConfiguration[Key],
  ) => {
    setConfiguration((current) =>
      current ? { ...current, [key]: value } : current,
    );
  };

  const save = async () => {
    if (!configuration || busy) return;
    setBusy("save");
    setMessage(null);
    try {
      const updated = await updateAliyunTtsConfiguration({
        ...editableConfiguration(configuration),
        api_key: apiKey.trim() || undefined,
        clear_api_key: clearApiKey,
      });
      setConfiguration(updated);
      setApiKey("");
      setClearApiKey(false);
      await onSaved();
      setMessage("百炼实时语音配置已保存。");
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(null);
    }
  };

  const test = async () => {
    if (!configuration || busy) return;
    setBusy("test");
    setMessage("正在保存配置并生成一小段测试语音…");
    try {
      const updated = await updateAliyunTtsConfiguration({
        ...editableConfiguration(configuration),
        api_key: apiKey.trim() || undefined,
        clear_api_key: clearApiKey,
      });
      setConfiguration(updated);
      setApiKey("");
      setClearApiKey(false);
      await onSaved();
      const result = await testAliyunTtsConfiguration();
      setMessage(
        result.status === "ok"
          ? `连接与音色可用（测试音频 ${result.duration_ms ?? 0} ms）。`
          : `测试结果：${result.status}`,
      );
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : "连接测试失败");
    } finally {
      setBusy(null);
    }
  };

  if (!configuration)
    return (
      <p className="desktop-settings-empty">
        {message ?? "正在读取百炼配置…"}
      </p>
    );

  return (
    <section className="aliyun-tts-panel" aria-label="阿里云百炼实时语音">
      <header>
        <div>
          <h2>阿里云百炼 · 实时声音复刻</h2>
          <p>使用 WebSocket 边生成边播放；只发送当前待朗读句段。</p>
        </div>
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
            onChange={(event) => change("voice_id", event.currentTarget.value)}
          />
        </label>
        <label>
          <span>基础模型</span>
          <input
            value={configuration.model}
            onChange={(event) => change("model", event.currentTarget.value)}
          />
          <small>
            音色必须由同一个 target_model 创建；非实时 VC 音色不能直接用于实时模型。
          </small>
        </label>
        <label>
          <span>服务区域</span>
          <select
            value={configuration.region}
            onChange={(event) =>
              change(
                "region",
                event.currentTarget.value as AliyunTtsConfiguration["region"],
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
                  .value as AliyunTtsConfiguration["language_type"],
              )
            }
          >
            <option value="Auto">自动（中日混合）</option>
            <option value="Chinese">中文</option>
            <option value="Japanese">日语</option>
            <option value="English">英语</option>
          </select>
        </label>
        <label>
          <span>API Key</span>
          <input
            type="password"
            value={apiKey}
            autoComplete="off"
            placeholder={
              configuration.api_key_configured
                ? "已安全保存；留空保持不变"
                : "输入 DashScope API Key"
            }
            onChange={(event) => setApiKey(event.currentTarget.value)}
          />
        </label>
        <label>
          <span>业务空间 ID（可选）</span>
          <input
            value={configuration.workspace_id}
            onChange={(event) =>
              change("workspace_id", event.currentTarget.value)
            }
          />
        </label>
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
          删除已保存的 API Key
        </label>
      ) : null}

      <footer>
        <span>{message}</span>
        <div>
          <button type="button" disabled={Boolean(busy)} onClick={() => void test()}>
            {busy === "test" ? "测试中…" : "保存并测试"}
          </button>
          <button type="button" disabled={Boolean(busy)} onClick={() => void save()}>
            {busy === "save" ? "保存中…" : "保存配置"}
          </button>
        </div>
      </footer>

      <p className="desktop-settings-info">
        云端模式会把当前待朗读句段发送到阿里云百炼。对话历史、结构化记忆、系统提示词和模型密钥不会随 TTS 请求发送。
      </p>
    </section>
  );
}

function editableConfiguration(
  configuration: AliyunTtsConfiguration,
): EditableConfiguration {
  return {
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
    timeout_seconds: configuration.timeout_seconds,
    max_audio_bytes: configuration.max_audio_bytes,
  };
}
