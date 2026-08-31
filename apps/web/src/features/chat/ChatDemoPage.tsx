import { useEffect, useRef, useState } from "react";
import { BrandMark } from "../../components/BrandMark";
import { ProductIcon } from "../../components/ProductIcon";
import { MemoryControlCenter } from "./MemoryControlCenter";
import { ModelSettingsPanel } from "./ModelSettingsPanel";
import { SkillConfirmationPrompt } from "./SkillConfirmationPrompt";
import { SkillsControlCenter } from "./SkillsControlCenter";
import {
  buildTtsProviderChoices,
  providerSelectorValue,
  readTtsProviderPreferences,
  resolveProviderSelection,
  saveTtsProviderPreference,
} from "./ttsProviderPresentation";
import { useChatSession } from "./useChatSession";

type ChatDemoPageProps = {
  mediaOwner?: boolean;
};

export function ChatDemoPage({ mediaOwner = true }: ChatDemoPageProps) {
  const {
    canvasRef,
    snapshot,
    rendererKind,
    avatarWarning,
    touch,
    health,
    character,
    sessionId,
    messages,
    connection,
    error,
    resetting,
    ttsProviders,
    ttsProviderId,
    ttsSwitching,
    voiceState,
    voiceConnected,
    voiceDevices,
    voiceDeviceId,
    voiceInputLevel,
    voiceActivationMode,
    voiceTransmitting,
    voiceActivity,
    voiceTranscript,
    setVoiceDeviceId,
    setVoiceActivationMode,
    beginPushToTalk,
    endPushToTalk,
    refreshVoiceDevices,
    toggleVoice,
    changeTtsProvider,
    send: sendMessage,
    interruptActive,
    resetAll,
    refreshMemories,
  } = useChatSession({ playbackEnabled: mediaOwner });
  const [draft, setDraft] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [avatarFraming, setAvatarFraming] = useState<"bust" | "full">("bust");
  const ttsProviderPreferences = readTtsProviderPreferences(ttsProviders);
  const ttsProviderChoices = buildTtsProviderChoices(
    ttsProviders,
    ttsProviderId,
    ttsProviderPreferences,
  );
  const ttsSelectorValue = providerSelectorValue(ttsProviders, ttsProviderId);
  const selectTtsProvider = (selectionId: string) => {
    const providerId = resolveProviderSelection(
      selectionId,
      ttsProviders,
      ttsProviderId,
      ttsProviderPreferences,
    );
    saveTtsProviderPreference(ttsProviders, providerId);
    void changeTtsProvider(providerId);
  };
  const historyRef = useRef<HTMLDivElement>(null);
  const canSend = Boolean(
    sessionId && connection === "connected" && !resetting,
  );
  const currentMessage = messages.at(-1);
  const currentSpeaker = currentMessage
    ? currentMessage.role === "user"
      ? "你"
      : (character?.display_name ?? "绫地宁宁")
    : (character?.display_name ?? "绫地宁宁");
  const currentText = currentMessage
    ? currentMessage.text
    : character?.greeting || "你好呀，Runtime 准备好后我们就可以聊天。";

  useEffect(() => {
    const history = historyRef.current;
    if (historyOpen && history) history.scrollTop = history.scrollHeight;
  }, [historyOpen, messages]);

  const send = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    void sendMessage(text);
  };

  const reset = async () => {
    const confirmed = window.confirm(
      "确定要重置吗？这会永久清空当前对话、全部明确记忆和本地生成语音，且无法撤销。",
    );
    if (!confirmed) return;
    if (await resetAll()) {
      setDraft("");
      setHistoryOpen(false);
    }
  };

  return (
    <main className="vn-shell">
      <SkillConfirmationPrompt sessionId={sessionId} />
      <section className="vn-stage" aria-label="Conversation">
        <div className="vn-sky" aria-hidden="true">
          <i className="vn-moon" />
          <i className="vn-horizon" />
          <i className="vn-glow one" />
          <i className="vn-glow two" />
        </div>

        <header className="vn-topbar">
          <a className="vn-brand" href="/" aria-label="ChatWaifu NEXT home">
            <span className="vn-brand-mark" aria-hidden="true">
              <BrandMark />
            </span>
            <span className="vn-brand-copy">
              <small>LOCAL CHARACTER STORY</small>
              <strong>
                ChatWaifu <em>NEXT</em>
              </strong>
            </span>
          </a>
          <div className={"vn-runtime " + connection}>
            <i />
            <span>
              {connection === "connected" ? "LOCAL LINK" : connection}
            </span>
            <small>{health?.providers.tts ?? "voice offline"}</small>
          </div>
        </header>

        <div className="vn-character-title">
          <p>綾地 寧々</p>
          <h1>{character?.display_name ?? "绫地宁宁"}</h1>
          <span>{character?.tagline ?? "正在连接角色 Runtime…"}</span>
        </div>

        <button
          className={`avatar-frame vn-avatar framing-${avatarFraming}`}
          type="button"
          onClick={touch}
          aria-label="Touch avatar"
          data-avatar-status={snapshot?.status ?? "loading"}
        >
          <canvas key={rendererKind} ref={canvasRef} />
          <span className="avatar-state">
            {rendererKind === "live2d" ? "Live2D" : "Fallback"} ·{" "}
            {snapshot?.runtime.procedural.mode ?? "loading"} ·{" "}
            {snapshot?.runtime.expression ?? "neutral"}
            {snapshot?.runtime.motion ? ` · ${snapshot.runtime.motion}` : ""}
          </span>
        </button>

        {avatarWarning ? (
          <p className="avatar-warning" title={avatarWarning}>
            Live2D 未就绪，当前使用安全回退
          </p>
        ) : null}

        <nav className="vn-system-menu" aria-label="游戏菜单">
          <SkillsControlCenter sessionId={sessionId} />
          <MemoryControlCenter
            sessionId={sessionId}
            onChanged={refreshMemories}
          />
          <button
            type="button"
            aria-pressed={historyOpen}
            onClick={() => {
              setHistoryOpen((open) => !open);
              setSettingsOpen(false);
            }}
          >
            <ProductIcon name="history" />
            <small>LOG</small>
            历史
          </button>
          <button
            type="button"
            aria-pressed={settingsOpen}
            onClick={() => {
              setSettingsOpen((open) => !open);
              setHistoryOpen(false);
            }}
          >
            <ProductIcon name="controlCenter" />
            <small>CONFIG</small>
            设置
          </button>
        </nav>

        {historyOpen ? (
          <aside className="vn-history-panel" aria-label="对话历史">
            <header>
              <div>
                <small>BACKLOG</small>
                <strong>对话历史</strong>
              </div>
              <button
                type="button"
                aria-label="关闭对话历史"
                onClick={() => setHistoryOpen(false)}
              >
                <ProductIcon name="close" />
              </button>
            </header>
            <div className="vn-history-list" ref={historyRef}>
              {messages.length ? (
                messages.map((message) => (
                  <article
                    className={"vn-history-message " + message.role}
                    key={message.id}
                  >
                    <span>
                      {message.role === "user"
                        ? "你"
                        : (character?.display_name ?? "绫地宁宁")}
                    </span>
                    <p>{message.text}</p>
                  </article>
                ))
              ) : (
                <p className="vn-history-empty">故事还没有开始。</p>
              )}
            </div>
          </aside>
        ) : null}

        {settingsOpen ? (
          <aside className="vn-settings-panel" aria-label="角色和模型设置">
            <header>
              <div>
                <small>CHARACTER &amp; SYSTEM</small>
                <strong>角色和模型设置</strong>
              </div>
              <button
                type="button"
                aria-label="关闭角色和模型设置"
                onClick={() => setSettingsOpen(false)}
              >
                <ProductIcon name="close" />
              </button>
            </header>
            <label>
              <span>角色构图</span>
              <select
                value={avatarFraming}
                onChange={(event) =>
                  setAvatarFraming(event.target.value as "bust" | "full")
                }
                aria-label="角色构图"
              >
                <option value="bust">上半身（推荐）</option>
                <option value="full">全身</option>
              </select>
            </label>
            <label>
              <span>角色声音</span>
              <select
                value={ttsSelectorValue}
                onChange={(event) => selectTtsProvider(event.target.value)}
                disabled={!sessionId || ttsSwitching}
                aria-label="选择语音模型"
              >
                {ttsProviderChoices.length === 0 ? (
                  <option value={ttsSelectorValue}>
                    {ttsProviderId || "正在读取 Runtime 配置"}
                  </option>
                ) : (
                  ttsProviderChoices.map((provider) => (
                    <option
                      value={provider.id}
                      key={provider.id}
                      disabled={provider.status === "unavailable"}
                    >
                      {provider.displayName}
                      {provider.modelLoaded ? " · 已加载" : ""}
                      {provider.status === "unavailable" ? " · 离线" : ""}
                    </option>
                  ))
                )}
              </select>
            </label>
            <label>
              <span>语音响应方式</span>
              <select
                value={voiceActivationMode}
                onChange={(event) =>
                  setVoiceActivationMode(
                    event.target.value as "push_to_talk" | "open_mic",
                  )
                }
                aria-label="语音响应方式"
              >
                <option value="push_to_talk">按住说话（推荐）</option>
                <option value="open_mic">自由对话（会听到附近人声）</option>
              </select>
            </label>
            <label>
              <span>输入设备</span>
              <select
                value={voiceDeviceId}
                onFocus={() => void refreshVoiceDevices()}
                onChange={(event) => setVoiceDeviceId(event.target.value)}
                disabled={
                  !mediaOwner || voiceConnected || voiceState === "unsupported"
                }
                aria-label="选择麦克风"
              >
                {voiceDevices.length === 0 ? (
                  <option value="">默认麦克风</option>
                ) : (
                  voiceDevices.map((device) => (
                    <option value={device.deviceId} key={device.deviceId}>
                      {device.label}
                    </option>
                  ))
                )}
              </select>
            </label>
            <div className="vn-mic-status">
              <div className="input-meter" aria-label="麦克风音量">
                <i style={{ transform: `scaleX(${voiceInputLevel})` }} />
              </div>
              <small>
                {mediaOwner
                  ? voiceStatusLabel(
                      voiceState,
                      voiceActivity,
                      voiceActivationMode,
                      voiceTransmitting,
                    )
                  : "桌宠窗口负责麦克风和语音播放，避免双窗口重叠播放"}
              </small>
            </div>
            <ModelSettingsPanel sessionId={sessionId} />
          </aside>
        ) : null}

        <div className="conversation-notices vn-notices">
          {error ? (
            <div className="runtime-error" role="alert">
              <strong>连接提示</strong>
              <span>{error}</span>
              {connection === "offline" ? <code>make demo</code> : null}
            </div>
          ) : null}
          {voiceTranscript && voiceActivity !== "idle" ? (
            <div className="voice-transcript" aria-live="polite">
              <span>
                {voiceActivity === "transcribing" ? "转写中" : "听到"}
              </span>
              {voiceTranscript}
            </div>
          ) : null}
        </div>

        <section className="vn-dialogue" aria-label="当前对话">
          <div className="vn-nameplate">
            <small>{currentMessage?.role === "user" ? "PLAYER" : "NENE"}</small>
            <strong>{currentSpeaker}</strong>
          </div>
          <div className="vn-dialogue-copy" aria-live="polite">
            <p>
              {currentText}
              {currentMessage?.pending ? <i className="typing-caret" /> : null}
            </p>
            <ProductIcon className="vn-continue" name="continue" />
          </div>
          <div className="vn-dialogue-actions">
            <button
              className={"vn-voice-button" + (voiceConnected ? " active" : "")}
              type="button"
              onClick={() => void toggleVoice()}
              disabled={
                !mediaOwner ||
                !sessionId ||
                connection !== "connected" ||
                resetting
              }
              aria-label={voiceConnected ? "断开麦克风" : "连接麦克风"}
              aria-pressed={voiceConnected}
            >
              <ProductIcon name="microphone" />
              <small>VOICE</small>
              {voiceConnected ? "已连接" : "语音"}
            </button>
            {voiceConnected && voiceActivationMode === "push_to_talk" ? (
              <button
                className={
                  "vn-push-to-talk" + (voiceTransmitting ? " transmitting" : "")
                }
                type="button"
                onPointerDown={(event) => {
                  event.currentTarget.setPointerCapture?.(event.pointerId);
                  beginPushToTalk();
                }}
                onPointerUp={endPushToTalk}
                onPointerCancel={endPushToTalk}
                onKeyDown={(event) => {
                  if (
                    !event.repeat &&
                    (event.key === " " || event.key === "Enter")
                  ) {
                    event.preventDefault();
                    beginPushToTalk();
                  }
                }}
                onKeyUp={(event) => {
                  if (event.key === " " || event.key === "Enter") {
                    event.preventDefault();
                    endPushToTalk();
                  }
                }}
                onBlur={endPushToTalk}
                aria-label="按住说话"
                aria-pressed={voiceTransmitting}
              >
                <ProductIcon name="pushToTalk" />
                {voiceTransmitting ? "松开发送" : "按住说话"}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void interruptActive()}
              disabled={!sessionId || resetting}
              aria-label="打断当前回复"
            >
              <ProductIcon name="stop" />
              <small>STOP</small>
              打断
            </button>
            <button
              type="button"
              onClick={() => void reset()}
              disabled={!sessionId || resetting}
              aria-label="重置对话和记忆"
            >
              <ProductIcon name="reset" />
              <small>RESET</small>
              {resetting ? "重置中" : "重置"}
            </button>
          </div>
          <form
            className="vn-composer"
            onSubmit={(event) => {
              event.preventDefault();
              send();
            }}
          >
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              placeholder={`和${character?.display_name ?? "绫地宁宁"}说点什么…`}
              aria-label="Message"
              rows={1}
              disabled={!canSend}
            />
            <button
              type="submit"
              disabled={!canSend || !draft.trim()}
              aria-label="Send message"
            >
              发送
              <ProductIcon name="send" />
            </button>
          </form>
        </section>

        <p className="vn-disclosure">
          {character?.content_notice ??
            "非官方角色 Demo；语音与记忆均由 ChatWaifu Runtime 处理。"}
        </p>
      </section>
    </main>
  );
}

function voiceStatusLabel(
  state:
    | "unsupported"
    | "disconnected"
    | "requesting"
    | "connecting"
    | "reconnecting"
    | "connected"
    | "failed",
  activity: "idle" | "listening" | "transcribing" | "thinking",
  activationMode: "push_to_talk" | "open_mic",
  transmitting: boolean,
): string {
  if (state === "unsupported") return "当前浏览器不支持 WebRTC 麦克风";
  if (state === "requesting") return "等待麦克风权限";
  if (state === "connecting") return "正在建立本地 WebRTC";
  if (state === "reconnecting") return "设备或连接中断，正在自动恢复";
  if (state === "failed") return "连接失败，可再次尝试";
  if (state !== "connected") {
    return activationMode === "push_to_talk"
      ? "连接后按住说话，旁边聊天不会触发"
      : "自由监听会响应附近人声";
  }
  if (activationMode === "push_to_talk" && transmitting)
    return "正在发送；松开后由 VAD 自动结束";
  if (activity === "listening") return "正在聆听…";
  if (activity === "transcribing") return "本地转写中…";
  if (activity === "thinking") return "已自动结束回合";
  if (activationMode === "push_to_talk") return "按住“说话”时才会送入 VAD";
  return "自由监听中；附近人声也可能触发";
}
