import { useCallback, useEffect, useRef, useState } from "react";

import "./realtime-settings.css";
import { isDesktopHost, observeDesktopRuntime } from "./runtimeEndpoint";
import {
  getRealtimeConfiguration,
  isAbortError,
  isConflictError,
  updateRealtimeConfiguration,
} from "./runtime-client/realtimeClient";
import type {
  RealtimeConfigurationSnapshot,
  RealtimeConnectionMode,
  RealtimeConfigurationUpdate,
} from "./types";

export interface RealtimeConfigurationPanelProps {
  className?: string;
  endpointKey?: string;
  onSaved?: (snapshot: RealtimeConfigurationSnapshot) => void;
}

export function RealtimeConfigurationPanel({
  className,
  endpointKey,
  onSaved,
}: RealtimeConfigurationPanelProps) {
  const [snapshot, setSnapshot] =
    useState<RealtimeConfigurationSnapshot | null>(null);
  const [connectionMode, setConnectionMode] =
    useState<RealtimeConnectionMode>("cascade");
  const [model, setModel] = useState("");
  const [voice, setVoice] = useState("marin");
  const [transcriptionModel, setTranscriptionModel] = useState(
    "gpt-4o-mini-transcribe",
  );
  const [cloudToolsEnabled, setCloudToolsEnabled] = useState(false);
  const [cloudEgressConsent, setCloudEgressConsent] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [hasConflict, setHasConflict] = useState(false);
  const [notice, setNotice] = useState<{
    tone: "info" | "success" | "error";
    text: string;
  } | null>(null);

  const dirtyRef = useRef(false);
  const requestCountRef = useRef(0);
  const saveCountRef = useRef(0);
  const getControllerRef = useRef<AbortController | null>(null);
  const saveControllerRef = useRef<AbortController | null>(null);

  const invalidateRequests = useCallback(() => {
    ++requestCountRef.current;
    ++saveCountRef.current;
    getControllerRef.current?.abort();
    saveControllerRef.current?.abort();
  }, []);

  const applySnapshot = useCallback((data: RealtimeConfigurationSnapshot) => {
    setSnapshot(data);
    setConnectionMode(data.connection_mode);
    setModel(data.model);
    setVoice(data.voice || "marin");
    setTranscriptionModel(data.transcription_model || "gpt-4o-mini-transcribe");
    setCloudToolsEnabled(data.cloud_tools_enabled);
    setCloudEgressConsent(data.cloud_egress_consent);
    setApiKey("");
    setClearApiKey(false);
    dirtyRef.current = false;
  }, []);

  const loadConfiguration = useCallback(
    async (forceReload = false) => {
      const reqId = ++requestCountRef.current;
      getControllerRef.current?.abort();
      const controller = new AbortController();
      getControllerRef.current = controller;

      if (forceReload) {
        setLoading(true);
        setHasConflict(false);
        setNotice(null);
      }

      try {
        const data = await getRealtimeConfiguration(controller.signal);
        if (reqId !== requestCountRef.current || controller.signal.aborted) {
          return;
        }

        if (dirtyRef.current && !forceReload) {
          setSnapshot(data);
          return;
        }

        applySnapshot(data);
        setHasConflict(false);
        setNotice(null);
      } catch (err: unknown) {
        if (reqId !== requestCountRef.current || isAbortError(err)) {
          return;
        }
        setNotice({
          tone: "error",
          text: err instanceof Error ? err.message : "读取实时语音配置失败",
        });
      } finally {
        if (reqId === requestCountRef.current) {
          setLoading(false);
        }
      }
    },
    [applySnapshot],
  );

  const handleEndpointSwitch = useCallback(
    (ready = true) => {
      invalidateRequests();
      getControllerRef.current = null;
      saveControllerRef.current = null;

      setApiKey("");
      setClearApiKey(false);
      setSnapshot(null);
      setSaving(false);
      setLoading(true);
      dirtyRef.current = false;
      setHasConflict(false);
      setNotice(
        ready
          ? null
          : { tone: "info", text: "等待 Runtime 连接，恢复后将重新读取配置。" },
      );
      if (ready) void loadConfiguration(true);
    },
    [loadConfiguration, invalidateRequests],
  );

  // Handle native desktop runtime endpoint changes
  useEffect(() => {
    if (!isDesktopHost()) return;
    const controller = new AbortController();
    let currentEndpointIdentity: string | null = null;

    void observeDesktopRuntime((status) => {
      if (status.state === "ready") {
        const nextIdentity = JSON.stringify([
          status.runtime_url,
          status.token,
          status.restart_count,
        ]);
        if (currentEndpointIdentity === nextIdentity) return;
        currentEndpointIdentity = nextIdentity;
        handleEndpointSwitch();
      } else {
        currentEndpointIdentity = null;
        handleEndpointSwitch(false);
      }
    }, controller.signal).catch(() => {});

    return () => {
      controller.abort();
      invalidateRequests();
    };
  }, [handleEndpointSwitch, invalidateRequests]);

  // Handle optional endpointKey prop changes
  const isFirstMount = useRef(true);
  useEffect(() => {
    if (isFirstMount.current) {
      isFirstMount.current = false;
      return;
    }
    handleEndpointSwitch();
  }, [endpointKey, handleEndpointSwitch]);

  // Mount effect
  useEffect(() => {
    if (isDesktopHost()) return;
    let active = true;
    const reqId = ++requestCountRef.current;
    const controller = new AbortController();
    getControllerRef.current = controller;

    void getRealtimeConfiguration(controller.signal)
      .then((data) => {
        if (
          !active ||
          reqId !== requestCountRef.current ||
          controller.signal.aborted
        ) {
          return;
        }
        if (dirtyRef.current) {
          setSnapshot(data);
          return;
        }
        applySnapshot(data);
        setHasConflict(false);
        setNotice(null);
      })
      .catch((err: unknown) => {
        if (!active || reqId !== requestCountRef.current || isAbortError(err)) {
          return;
        }
        setNotice({
          tone: "error",
          text: err instanceof Error ? err.message : "读取实时语音配置失败",
        });
      })
      .finally(() => {
        if (active && reqId === requestCountRef.current) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
      controller.abort();
      invalidateRequests();
    };
  }, [applySnapshot, invalidateRequests]);

  const markDirty = () => {
    dirtyRef.current = true;
  };

  const handleSave = async () => {
    if (loading || saving || !snapshot) return;

    const trimmedKey = apiKey.trim();
    if (clearApiKey && trimmedKey) {
      setNotice({
        tone: "error",
        text: "不能同时输入新密钥并勾选清除密钥。",
      });
      return;
    }

    saveControllerRef.current?.abort();
    const controller = new AbortController();
    saveControllerRef.current = controller;
    const saveId = ++saveCountRef.current;

    setSaving(true);
    setNotice(null);
    setHasConflict(false);

    const update: RealtimeConfigurationUpdate = {
      schema_version: "1.0",
      expected_revision: snapshot.revision,
      connection_mode: connectionMode,
      cloud_backend: "openai",
      model: model.trim(),
      voice: voice.trim() || "marin",
      transcription_model:
        transcriptionModel.trim() || "gpt-4o-mini-transcribe",
      cloud_tools_enabled: cloudToolsEnabled,
      cloud_egress_consent: cloudEgressConsent,
      api_key: clearApiKey ? null : trimmedKey ? trimmedKey : null,
      clear_api_key: clearApiKey ? true : false,
    };

    try {
      const updated = await updateRealtimeConfiguration(
        update,
        controller.signal,
      );
      if (saveId !== saveCountRef.current || controller.signal.aborted) {
        return;
      }

      applySnapshot(updated);
      setNotice({ tone: "success", text: "实时语音配置已保存。" });
      onSaved?.(updated);
    } catch (err: unknown) {
      if (saveId !== saveCountRef.current || isAbortError(err)) {
        return;
      }

      if (isConflictError(err)) {
        setHasConflict(true);
        setNotice({
          tone: "error",
          text: "配置已被其他客户端修改 (409 Conflict)，请重新加载最新配置。",
        });
      } else {
        setNotice({
          tone: "error",
          text: err instanceof Error ? err.message : "保存配置失败",
        });
      }
    } finally {
      if (saveId === saveCountRef.current) {
        setSaving(false);
      }
    }
  };

  const disabled = loading || saving;
  const apiKeyConfigured = Boolean(snapshot?.api_key_configured);

  const missingRequirements: string[] = [];
  if (!model.trim()) missingRequirements.push("模型");
  if ((clearApiKey || !apiKeyConfigured) && !apiKey.trim())
    missingRequirements.push("API Key");
  if (!cloudEgressConsent) missingRequirements.push("云端传输授权");
  const isCloudIncomplete = missingRequirements.length > 0;

  return (
    <section
      className={`realtime-configuration-panel ${className ?? ""}`.trim()}
      aria-label="实时语音设置"
    >
      <header>
        <div>
          <h2>实时语音设置</h2>
          <p>选择现有角色声线，或使用 OpenAI 自然实时语音。</p>
        </div>
        {loading && !snapshot && (
          <span className="realtime-loading-badge" role="status">
            正在读取…
          </span>
        )}
        {snapshot?.cloud_backend === "fake" && (
          <span className="realtime-badge-fake">测试模拟后端 (fake)</span>
        )}
      </header>

      {connectionMode === "cloud_realtime" && (
        <div className="realtime-notice-banner" role="note">
          OpenAI
          直接处理声音并生成语音，使用其预设音色。此模式不会使用已配置的角色声线。
        </div>
      )}

      {connectionMode === "cloud_realtime" &&
        snapshot?.cloud_backend === "fake" && (
          <div className="realtime-fake-backend-note" role="status">
            当前环境使用测试模拟后端 (fake)。保存配置时将更新为 OpenAI 后端。
          </div>
        )}

      <div className="realtime-mode-selector-group">
        <label className="realtime-field realtime-field-wide">
          <span>连接模式</span>
          <select
            aria-label="连接模式"
            value={connectionMode}
            disabled={disabled}
            onChange={(e) => {
              setConnectionMode(e.target.value as RealtimeConnectionMode);
              markDirty();
            }}
          >
            <option value="cascade">级联语音（现有角色声线）</option>
            <option value="cloud_realtime">自然实时语音（OpenAI）</option>
          </select>
          <small>
            {connectionMode === "cascade"
              ? "级联模式：语音转文字 → 对话模型回复 → 角色声音朗读。"
              : "实时模式：语音模型直接听取声音并生成语音。"}
          </small>
        </label>
      </div>

      {connectionMode === "cascade" ? (
        <div className="realtime-cascade-info" role="status">
          当前为级联语音模式，沿用已配置的角色声音。无需填写 OpenAI Realtime
          配置；是否联网取决于语音识别、对话模型和语音合成各自的设置。
        </div>
      ) : (
        <>
          <div className="realtime-fields-grid">
            <label className="realtime-field">
              <span>Realtime 模型</span>
              <input
                type="text"
                aria-label="Realtime 模型"
                value={model}
                disabled={disabled}
                placeholder="输入 Realtime 模型名称"
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => {
                  setModel(e.target.value);
                  markDirty();
                }}
              />
              <small>
                OpenAI 实时语音模型 ID，未配置模型时无法发起云端连接。
              </small>
            </label>

            <label className="realtime-field">
              <span>Realtime 音色</span>
              <input
                type="text"
                aria-label="Realtime 音色"
                value={voice}
                disabled={disabled}
                placeholder="marin"
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => {
                  setVoice(e.target.value);
                  markDirty();
                }}
              />
              <small>OpenAI 预设实时音色（默认为 marin）。</small>
            </label>

            <label className="realtime-field">
              <span>转写模型</span>
              <input
                type="text"
                aria-label="转写模型"
                value={transcriptionModel}
                disabled={disabled}
                placeholder="gpt-4o-mini-transcribe"
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => {
                  setTranscriptionModel(e.target.value);
                  markDirty();
                }}
              />
              <small>
                用户实时语音转写模型（默认为 gpt-4o-mini-transcribe）。
              </small>
            </label>

            <div className="realtime-field">
              <span>
                OpenAI API Key
                {apiKeyConfigured ? (
                  <span className="realtime-badge-configured"> · 已配置</span>
                ) : (
                  <span className="realtime-badge-missing"> · 未配置</span>
                )}
              </span>
              <input
                type="password"
                aria-label="OpenAI API Key"
                value={apiKey}
                disabled={disabled || clearApiKey}
                placeholder={
                  clearApiKey
                    ? "保存后将清除已有密钥"
                    : apiKeyConfigured
                      ? "留空保持原密钥"
                      : "输入 OpenAI API Key"
                }
                autoComplete="new-password"
                onChange={(e) => {
                  setApiKey(e.target.value);
                  markDirty();
                }}
              />
              {apiKeyConfigured && (
                <label className="realtime-checkbox-row realtime-clear-key-row">
                  <input
                    type="checkbox"
                    aria-label="清除已配置的 API Key"
                    checked={clearApiKey}
                    disabled={disabled}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setClearApiKey(checked);
                      if (checked) setApiKey("");
                      markDirty();
                    }}
                  />
                  <span>清除已配置的 API Key</span>
                </label>
              )}
            </div>
          </div>

          <label className="realtime-checkbox-row">
            <input
              type="checkbox"
              aria-label="允许云端只读工具调用"
              checked={cloudToolsEnabled}
              disabled={disabled}
              onChange={(e) => {
                setCloudToolsEnabled(e.target.checked);
                markDirty();
              }}
            />
            <div>
              <strong>允许云端只读工具调用</strong>
              <small>
                在实时语音中仅允许调用内置只读工具（写入或需人工确认的工具将被隔离）。
              </small>
            </div>
          </label>

          <label className="realtime-checkbox-row realtime-consent-row">
            <input
              type="checkbox"
              aria-label="云端数据传输授权"
              checked={cloudEgressConsent}
              disabled={disabled}
              onChange={(e) => {
                setCloudEgressConsent(e.target.checked);
                markDirty();
              }}
            />
            <div>
              <strong>云端数据传输授权</strong>
              <small>
                同意将麦克风实时音频、必要的角色与记忆上下文（以及在启用工具时的只读工具执行结果）发送至云端服务商进行实时语音处理。
              </small>
            </div>
          </label>

          {isCloudIncomplete ? (
            <div className="realtime-incomplete-info" role="status">
              云端实时语音配置未完成（缺少{missingRequirements.join("、")}
              ）。仍可保存配置，但在发起语音连接时将阻止接入。
            </div>
          ) : (
            <div className="realtime-complete-info" role="status">
              必填配置已填写，实际可用性将在连接时验证。
            </div>
          )}
        </>
      )}

      {snapshot && snapshot.active_connections > 0 && (
        <div className="realtime-active-connections-notice" role="status">
          当前有 {snapshot.active_connections}{" "}
          个正在进行的语音连接（修改将在下次连接时生效）。
        </div>
      )}

      <p className="realtime-connection-lifecycle-note">
        {connectionMode === "cloud_realtime"
          ? "保存的设置（包括云端传输授权）将在下一次发起语音连接时生效，当前通话不受影响。如需立即停止当前通话向云端发送数据，请先挂断。"
          : "保存的设置将在下一次发起语音连接时生效，当前通话不受影响。"}
      </p>

      {hasConflict && (
        <div className="realtime-conflict-box" role="alert">
          <span>
            配置已被其他客户端修改 (409 Conflict)，请重新加载最新配置。
          </span>
          <button
            type="button"
            className="realtime-reload-button"
            aria-label="重新加载最新配置"
            disabled={disabled}
            onClick={() => void loadConfiguration(true)}
          >
            重新加载最新配置
          </button>
        </div>
      )}

      {notice && !hasConflict && (
        <div
          role={notice.tone === "error" ? "alert" : "status"}
          className={`realtime-notice realtime-notice-${notice.tone}`}
          data-tone={notice.tone}
        >
          <span>{notice.text}</span>
        </div>
      )}

      <footer>
        {!snapshot && !loading && (
          <button type="button" onClick={() => void loadConfiguration(true)}>
            重新读取配置
          </button>
        )}
        <button
          type="button"
          className="realtime-save-button"
          aria-label="保存配置"
          disabled={disabled || !snapshot}
          onClick={() => void handleSave()}
        >
          {saving ? "正在保存…" : "保存配置"}
        </button>
      </footer>
    </section>
  );
}
