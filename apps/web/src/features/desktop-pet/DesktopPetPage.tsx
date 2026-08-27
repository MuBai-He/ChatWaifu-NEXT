import { useState } from "react";
import { useChatSession } from "../chat/useChatSession";
import { useDesktopPreferences } from "./useDesktopPreferences";

export function DesktopPetPage() {
  const {
    canvasRef,
    snapshot,
    rendererKind,
    avatarWarning,
    touch,
    character,
    messages,
    connection,
    error,
    resetting,
    sessionId,
    voiceState,
    voiceConnected,
    voiceActivationMode,
    voiceTransmitting,
    beginPushToTalk,
    endPushToTalk,
    toggleVoice,
    send,
  } = useChatSession({ playbackEnabled: true });
  const [controlError, setControlError] = useState<string | null>(null);
  const [displaySettingsOpen, setDisplaySettingsOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const {
    preferences,
    error: preferenceError,
    setDisplay,
  } = useDesktopPreferences();
  const latestAssistant = messages.findLast(
    (message) => message.role === "assistant",
  );
  const dialogue = latestAssistant?.text || character?.greeting || "";
  const pending = latestAssistant?.pending ?? false;
  const canUseVoice = Boolean(
    sessionId &&
    connection === "connected" &&
    !resetting &&
    voiceState !== "unsupported",
  );
  const canSend = Boolean(
    sessionId && connection === "connected" && !resetting,
  );

  const sendDraft = async () => {
    const text = draft.trim();
    if (!text || !canSend || sending) return;
    setDraft("");
    setSending(true);
    try {
      await send(text);
    } finally {
      setSending(false);
    }
  };

  const openControlCenter = async () => {
    setControlError(null);
    try {
      if ("__TAURI_INTERNALS__" in window) {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("show_control_center");
        return;
      }
      window.location.assign("/desktop-settings");
    } catch (openError: unknown) {
      setControlError(
        openError instanceof Error ? openError.message : "无法打开控制中心",
      );
    }
  };

  return (
    <main
      className="desktop-pet-shell"
      data-connection={connection}
      data-actions-active={
        displaySettingsOpen ||
        voiceTransmitting ||
        sending ||
        Boolean(draft.trim())
      }
    >
      <div
        className="desktop-pet-drag-region"
        data-tauri-drag-region
        aria-label="拖动桌宠"
      >
        {preferences.showStatus ? (
          <>
            <i />
            <span title={`${rendererKind} · ${snapshot?.status ?? "loading"}`}>
              {connection === "connected" ? "NENE ONLINE" : connection}
            </span>
            <i />
          </>
        ) : null}
      </div>

      <button
        className="desktop-pet-avatar"
        type="button"
        onClick={touch}
        aria-label="摸摸绫地宁宁"
        data-avatar-status={snapshot?.status ?? "loading"}
      >
        <canvas key={rendererKind} ref={canvasRef} />
      </button>

      {preferences.showSubtitles && (dialogue || pending) ? (
        <section className="desktop-pet-dialogue" aria-live="polite">
          <small>{character?.display_name ?? "绫地宁宁"}</small>
          <p>
            {dialogue}
            {pending ? <i className="typing-caret" /> : null}
          </p>
        </section>
      ) : null}

      {displaySettingsOpen ? (
        <fieldset className="desktop-pet-display-settings">
          <legend>显示</legend>
          <label>
            <input
              type="checkbox"
              checked={preferences.showSubtitles}
              onChange={(event) =>
                void setDisplay({
                  showSubtitles: event.currentTarget.checked,
                })
              }
            />
            字幕
          </label>
          <label>
            <input
              type="checkbox"
              checked={preferences.showStatus}
              onChange={(event) =>
                void setDisplay({
                  showStatus: event.currentTarget.checked,
                })
              }
            />
            在线状态
          </label>
        </fieldset>
      ) : null}

      <form
        className="desktop-pet-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void sendDraft();
        }}
      >
        <input
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.currentTarget.value)}
          placeholder={`和${character?.display_name ?? "绫地宁宁"}说点什么…`}
          aria-label="桌宠文字消息"
          autoComplete="off"
          disabled={!canSend}
        />
        <button
          type="submit"
          aria-label="发送文字消息"
          title="发送"
          disabled={!canSend || sending || !draft.trim()}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m5 12 12-7-3.8 14-2.4-5-5.8-2Zm5.8 2L17 5" />
          </svg>
        </button>
      </form>

      <nav className="desktop-pet-actions" aria-label="桌宠操作">
        <button
          type="button"
          onClick={() => void openControlCenter()}
          aria-label="打开控制中心"
          title="打开控制中心"
        >
          ◇
        </button>
        <button
          className={displaySettingsOpen ? "active" : ""}
          type="button"
          onClick={() => setDisplaySettingsOpen((open) => !open)}
          aria-label="桌宠显示设置"
          aria-expanded={displaySettingsOpen}
          title="字幕与状态显示"
        >
          HUD
        </button>
        <button
          className={voiceConnected ? "active" : ""}
          type="button"
          onClick={() => void toggleVoice()}
          disabled={!canUseVoice}
          aria-label={voiceConnected ? "断开麦克风" : "连接麦克风"}
          aria-pressed={voiceConnected}
          title={voiceConnected ? "断开麦克风" : "连接麦克风"}
        >
          ◉
        </button>
        {voiceConnected && voiceActivationMode === "push_to_talk" ? (
          <button
            className={voiceTransmitting ? "active transmitting" : ""}
            type="button"
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture?.(event.pointerId);
              beginPushToTalk();
            }}
            onPointerUp={endPushToTalk}
            onPointerCancel={endPushToTalk}
            onBlur={endPushToTalk}
            aria-label="按住说话"
            aria-pressed={voiceTransmitting}
            title="按住说话"
          >
            TALK
          </button>
        ) : null}
      </nav>

      {avatarWarning || error || preferenceError || controlError ? (
        <p className="desktop-pet-notice" role="status">
          {controlError ??
            preferenceError ??
            error ??
            "Live2D 未就绪，已使用安全回退。"}
        </p>
      ) : null}
    </main>
  );
}
