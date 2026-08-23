import { useEffect, useRef, useState } from "react";
import { useChatSession } from "./useChatSession";

export function ChatDemoPage() {
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
    memories,
    connection,
    error,
    skillSummary,
    resetting,
    voiceState,
    voiceConnected,
    voiceDevices,
    voiceDeviceId,
    voiceInputLevel,
    voiceActivity,
    voiceTranscript,
    setVoiceDeviceId,
    refreshVoiceDevices,
    toggleVoice,
    send: sendMessage,
    interruptActive,
    checkStatus,
    resetAll,
  } = useChatSession();
  const [draft, setDraft] = useState("");
  const transcriptRef = useRef<HTMLDivElement>(null);
  const canSend = Boolean(
    sessionId && connection === "connected" && !resetting,
  );

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (transcript) transcript.scrollTop = transcript.scrollHeight;
  }, [messages]);

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
    if (await resetAll()) setDraft("");
  };

  return (
    <main className="chat-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="ChatWaifu NEXT home">
          <span className="brand-mark">CW</span>
          <span>
            <strong>ChatWaifu NEXT</strong>
            <small>local-first character runtime</small>
          </span>
        </a>
        <div className="runtime-badges">
          <span className={`connection-pill ${connection}`}>
            <i /> {connection === "connected" ? "Runtime online" : connection}
          </span>
          <span className="provider-pill">
            LLM · {health?.providers.llm ?? "—"}
          </span>
          <span className="provider-pill">
            TTS · {health?.providers.tts ?? "—"}
          </span>
          <span className="provider-pill">
            STT · {health?.providers.stt ?? "—"}
          </span>
        </div>
      </header>

      <div className="demo-grid">
        <section className="character-panel" aria-label="Character">
          <div className="character-heading">
            <p>YOUR LOCAL COMPANION</p>
            <h1>{character?.display_name ?? "小雾"}</h1>
            <span>{character?.tagline ?? "正在连接角色 Runtime…"}</span>
          </div>

          <button
            className="avatar-frame"
            type="button"
            onClick={touch}
            aria-label="Touch avatar"
          >
            <canvas key={rendererKind} ref={canvasRef} />
            <span className="avatar-state">
              {rendererKind === "live2d" ? "Live2D" : "Fallback"} ·{" "}
              {snapshot?.runtime.state ?? "loading"} ·{" "}
              {snapshot?.runtime.expression ?? "neutral"}
            </span>
          </button>

          {avatarWarning ? (
            <p className="avatar-warning" title={avatarWarning}>
              Live2D 未就绪，当前使用安全回退
            </p>
          ) : null}

          <div className="character-actions">
            <button
              type="button"
              onClick={() => void checkStatus()}
              disabled={!sessionId}
            >
              运行状态 Skill
            </button>
            <a href="/avatar-lab">Avatar Lab</a>
          </div>

          {skillSummary && <p className="skill-result">{skillSummary}</p>}

          <div className="memory-card">
            <div className="memory-title">
              <span>明确记忆</span>
              <small>{memories.length}</small>
            </div>
            {memories.length ? (
              <ul>
                {memories.slice(0, 4).map((memory) => (
                  <li key={memory.memory_id}>{memory.content}</li>
                ))}
              </ul>
            ) : (
              <p>试试说：“请记住我喜欢蓝色”</p>
            )}
          </div>
        </section>

        <section className="conversation-panel" aria-label="Conversation">
          <div className="conversation-header">
            <div>
              <p>SESSION</p>
              <strong>
                {sessionId ? sessionId.slice(0, 8) : "connecting"}
              </strong>
            </div>
            <div className="conversation-actions">
              <button
                className="reset-button"
                type="button"
                onClick={() => void reset()}
                disabled={!sessionId || resetting}
                aria-label="重置对话和记忆"
              >
                {resetting ? "重置中…" : "↻ 重置"}
              </button>
              <button
                className="interrupt-button"
                type="button"
                onClick={() => void interruptActive()}
                disabled={!sessionId || resetting}
              >
                ■ 打断
              </button>
            </div>
          </div>

          <div className={`voice-bar ${voiceConnected ? "active" : ""}`}>
            <button
              className="microphone-button"
              type="button"
              onClick={() => void toggleVoice()}
              disabled={
                !sessionId ||
                connection !== "connected" ||
                resetting ||
                voiceState === "unsupported"
              }
              aria-label={voiceConnected ? "断开麦克风" : "连接麦克风"}
              aria-pressed={voiceConnected}
            >
              <span>{voiceConnected ? "●" : "◉"}</span>
              {voiceConnected ? "语音已连接" : "开启语音"}
            </button>
            <div className="input-meter" aria-label="麦克风音量">
              <i style={{ transform: `scaleX(${voiceInputLevel})` }} />
            </div>
            <select
              value={voiceDeviceId}
              onFocus={() => void refreshVoiceDevices()}
              onChange={(event) => setVoiceDeviceId(event.target.value)}
              disabled={voiceConnected || voiceState === "unsupported"}
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
            <small>{voiceStatusLabel(voiceState, voiceActivity)}</small>
          </div>

          <div className="transcript" ref={transcriptRef} aria-live="polite">
            {messages.length === 0 && (
              <article className="message assistant welcome-message">
                <div className="message-avatar">雾</div>
                <div>
                  <span>小雾</span>
                  <p>
                    {character?.greeting ??
                      "你好呀，Runtime 准备好后我们就可以聊天。"}
                  </p>
                </div>
              </article>
            )}
            {messages.map((message) => (
              <article className={`message ${message.role}`} key={message.id}>
                <div className="message-avatar">
                  {message.role === "user" ? "你" : "雾"}
                </div>
                <div>
                  <span>
                    {message.role === "user"
                      ? "你"
                      : (character?.display_name ?? "小雾")}
                  </span>
                  <p>
                    {message.text}
                    {message.pending && <i className="typing-caret" />}
                  </p>
                </div>
              </article>
            ))}
          </div>

          {error && (
            <div className="runtime-error" role="alert">
              <strong>连接提示</strong>
              <span>{error}</span>
              {connection === "offline" && <code>make demo</code>}
            </div>
          )}

          {voiceTranscript && voiceActivity !== "idle" ? (
            <div className="voice-transcript" aria-live="polite">
              <span>
                {voiceActivity === "transcribing" ? "转写中" : "听到"}
              </span>
              {voiceTranscript}
            </div>
          ) : null}

          <form
            className="composer"
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
              placeholder="和小雾说点什么…  Enter 发送 / Shift+Enter 换行"
              aria-label="Message"
              rows={2}
              disabled={!canSend}
            />
            <button
              type="submit"
              disabled={!canSend || !draft.trim()}
              aria-label="Send message"
            >
              <span>发送</span>
              <b>↗</b>
            </button>
          </form>
          <p className="demo-disclosure">
            Demo LLM 会明确标注自己；语音与记忆均在本机处理。可在配置中切换本地
            OpenAI 兼容模型。
          </p>
        </section>
      </div>
    </main>
  );
}

function voiceStatusLabel(
  state:
    | "unsupported"
    | "disconnected"
    | "requesting"
    | "connecting"
    | "connected"
    | "failed",
  activity: "idle" | "listening" | "transcribing" | "thinking",
): string {
  if (state === "unsupported") return "当前浏览器不支持 WebRTC 麦克风";
  if (state === "requesting") return "等待麦克风权限";
  if (state === "connecting") return "正在建立本地 WebRTC";
  if (state === "failed") return "连接失败，可再次尝试";
  if (state !== "connected") return "点击后允许麦克风权限";
  if (activity === "listening") return "正在聆听…";
  if (activity === "transcribing") return "本地转写中…";
  if (activity === "thinking") return "已自动结束回合";
  return "VAD 已就绪，可直接说话";
}
