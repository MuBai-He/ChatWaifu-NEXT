import { useState } from "react";
import { useChatSession } from "../chat/useChatSession";

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
  } = useChatSession({ playbackEnabled: true });
  const [controlError, setControlError] = useState<string | null>(null);
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

  const openControlCenter = async () => {
    setControlError(null);
    try {
      if ("__TAURI_INTERNALS__" in window) {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("show_control_center");
        return;
      }
      window.location.assign("/control-center");
    } catch (openError: unknown) {
      setControlError(
        openError instanceof Error ? openError.message : "无法打开控制中心",
      );
    }
  };

  return (
    <main className="desktop-pet-shell" data-connection={connection}>
      <div
        className="desktop-pet-drag-region"
        data-tauri-drag-region
        aria-label="拖动桌宠"
      >
        <i />
        <span title={`${rendererKind} · ${snapshot?.status ?? "loading"}`}>
          {connection === "connected" ? "NENE ONLINE" : connection}
        </span>
        <i />
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

      {dialogue || pending ? (
        <section className="desktop-pet-dialogue" aria-live="polite">
          <small>{character?.display_name ?? "绫地宁宁"}</small>
          <p>
            {dialogue}
            {pending ? <i className="typing-caret" /> : null}
          </p>
        </section>
      ) : null}

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

      {avatarWarning || error || controlError ? (
        <p className="desktop-pet-notice" role="status">
          {controlError ?? error ?? "Live2D 未就绪，已使用安全回退。"}
        </p>
      ) : null}
    </main>
  );
}
