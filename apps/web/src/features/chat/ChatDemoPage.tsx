import { useEffect, useRef, useState } from "react";
import { useChatSession } from "./useChatSession";

export function ChatDemoPage() {
  const {
    canvasRef,
    snapshot,
    touch,
    health,
    character,
    sessionId,
    messages,
    memories,
    connection,
    error,
    skillSummary,
    send: sendMessage,
    interruptActive,
    checkStatus,
  } = useChatSession();
  const [draft, setDraft] = useState("");
  const transcriptRef = useRef<HTMLDivElement>(null);
  const canSend = Boolean(sessionId && connection === "connected");

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
            <canvas ref={canvasRef} />
            <span className="avatar-state">
              {snapshot?.runtime.state ?? "loading"} ·{" "}
              {snapshot?.runtime.expression ?? "neutral"}
            </span>
          </button>

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
            <button
              className="interrupt-button"
              type="button"
              onClick={() => void interruptActive()}
            >
              ■ 打断
            </button>
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
