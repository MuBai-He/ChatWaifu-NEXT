import { useCallback, useEffect, useRef, useState } from "react";
import { ProductIcon } from "../../components/ProductIcon";
import { acquireNativeInteractionGuard } from "../../nativeInteractionGuard";
import { SkillConfirmationPrompt } from "../chat/SkillConfirmationPrompt";
import { useChatSession } from "../chat/useChatSession";
import {
  calculatePagedSubtitleScrollTop,
  countSubtitleTextUnits,
  normalizeDesktopSubtitle,
} from "../chat/subtitlePlayback";
import { useDesktopPreferences } from "./useDesktopPreferences";
import { useDesktopAvatarDrag } from "./useDesktopAvatarDrag";
import { isPointInsideDesktopPetChrome } from "./desktopPetInteractionRegions";
import { useDesktopPointerPresence } from "./useDesktopPointerPresence";

export function DesktopPetPage() {
  const {
    canvasRef,
    snapshot,
    rendererKind,
    avatarWarning,
    hitTest,
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
    subtitlePlayback,
    beginPushToTalk,
    endPushToTalk,
    toggleVoice,
    send,
  } = useChatSession({ playbackEnabled: true });
  const [controlError, setControlError] = useState<string | null>(null);
  const [displaySettingsOpen, setDisplaySettingsOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const dialogueRef = useRef<HTMLParagraphElement>(null);
  const subtitleGenerationRef = useRef<string | null>(null);
  const pushToTalkGuardRef = useRef<(() => void) | null>(null);
  const {
    preferences,
    error: preferenceError,
    setDisplay,
  } = useDesktopPreferences();
  const isInteractiveAtPoint = useCallback(
    (clientX: number, clientY: number) =>
      hitTest(clientX, clientY).length > 0 ||
      isPointInsideDesktopPetChrome(document, { x: clientX, y: clientY }),
    [hitTest],
  );
  const pointerPresence = useDesktopPointerPresence({ isInteractiveAtPoint });
  const avatarDrag = useDesktopAvatarDrag({
    hitTest,
    touch,
    onError: setControlError,
  });
  const latestAssistant = messages.findLast(
    (message) => message.role === "assistant",
  );
  const dialogue = latestAssistant
    ? latestAssistant.text
    : character?.greeting || "";
  const displayDialogue = normalizeDesktopSubtitle(dialogue);
  const pending = latestAssistant?.pending ?? false;
  const canUseVoice = Boolean(
    sessionId && connection === "connected" && !resetting,
  );
  const canSend = Boolean(
    sessionId && connection === "connected" && !resetting,
  );

  useEffect(() => {
    const dialogueElement = dialogueRef.current;
    if (!dialogueElement) return;
    const generationId = latestAssistant?.generationId;
    if (!generationId) {
      subtitleGenerationRef.current = null;
      dialogueElement.scrollTop = 0;
      return;
    }
    if (subtitleGenerationRef.current !== generationId) {
      subtitleGenerationRef.current = generationId;
      dialogueElement.scrollTop = 0;
    }
    if (subtitlePlayback?.generationId !== generationId) return;
    if (subtitlePlayback.playedTextUnits <= 0) {
      dialogueElement.scrollTop = 0;
      return;
    }
    const totalTextUnits = countSubtitleTextUnits(displayDialogue);
    if (totalTextUnits <= 0) return;
    const computedLineHeight = Number.parseFloat(
      window.getComputedStyle(dialogueElement).lineHeight,
    );
    const lineHeight =
      computedLineHeight > 4
        ? computedLineHeight
        : dialogueElement.clientHeight / 3;
    const nextScrollTop = calculatePagedSubtitleScrollTop({
      playedTextUnits: subtitlePlayback.playedTextUnits,
      totalTextUnits,
      scrollHeight: dialogueElement.scrollHeight,
      clientHeight: dialogueElement.clientHeight,
      lineHeight,
    });
    dialogueElement.scrollTop = Math.max(
      dialogueElement.scrollTop,
      nextScrollTop,
    );
  }, [displayDialogue, latestAssistant?.generationId, subtitlePlayback]);

  useEffect(
    () => () => {
      pushToTalkGuardRef.current?.();
      pushToTalkGuardRef.current = null;
    },
    [],
  );

  const beginDesktopPushToTalk = () => {
    pushToTalkGuardRef.current?.();
    pushToTalkGuardRef.current = acquireNativeInteractionGuard("push-to-talk");
    beginPushToTalk();
  };

  const endDesktopPushToTalk = () => {
    endPushToTalk();
    pushToTalkGuardRef.current?.();
    pushToTalkGuardRef.current = null;
  };

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
      const reason =
        openError instanceof Error ? openError.message : String(openError);
      setControlError(
        reason ? `无法打开桌宠设置：${reason}` : "无法打开桌宠设置",
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
      data-pointer-inside={pointerPresence.pointerInside}
      onPointerEnter={pointerPresence.onPointerEnter}
      onPointerMove={pointerPresence.onPointerMove}
      onPointerLeave={pointerPresence.onPointerLeave}
    >
      <SkillConfirmationPrompt sessionId={sessionId} />
      <button
        className="desktop-pet-avatar"
        type="button"
        onPointerDown={avatarDrag.onPointerDown}
        onPointerMove={avatarDrag.onPointerMove}
        onPointerUp={avatarDrag.onPointerUp}
        onPointerCancel={avatarDrag.onPointerCancel}
        aria-label="摸摸绫地宁宁"
        data-avatar-status={snapshot?.status ?? "loading"}
      >
        <canvas key={rendererKind} ref={canvasRef} />
      </button>

      {preferences.showSubtitles && (displayDialogue || pending) ? (
        <section
          className="desktop-pet-dialogue"
          aria-live="polite"
          data-native-interactive="true"
        >
          <small>{character?.display_name ?? "绫地宁宁"}</small>
          <p ref={dialogueRef}>
            {displayDialogue}
            {pending ? <i className="typing-caret" /> : null}
          </p>
        </section>
      ) : null}

      {displaySettingsOpen ? (
        <fieldset
          className="desktop-pet-display-settings"
          data-native-interactive="true"
        >
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
        </fieldset>
      ) : null}

      <form
        className="desktop-pet-composer"
        data-native-interactive="true"
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
          <ProductIcon name="send" />
        </button>
      </form>

      <nav
        className="desktop-pet-actions"
        aria-label="桌宠操作"
        data-native-interactive="true"
      >
        <button
          type="button"
          onClick={() => void openControlCenter()}
          aria-label="打开控制中心"
          title="打开控制中心"
        >
          <ProductIcon name="controlCenter" />
        </button>
        <button
          className={displaySettingsOpen ? "active" : ""}
          type="button"
          onClick={() => setDisplaySettingsOpen((open) => !open)}
          aria-label="桌宠显示设置"
          aria-expanded={displaySettingsOpen}
          title="字幕与状态显示"
        >
          <ProductIcon name="captions" />
        </button>
        <button
          className={
            "voice " +
            (voiceConnected
              ? "active"
              : voiceState === "unsupported"
                ? "unavailable"
                : "ready")
          }
          type="button"
          onClick={() => void toggleVoice()}
          disabled={!canUseVoice}
          aria-label={
            voiceConnected
              ? "断开麦克风"
              : voiceState === "unsupported"
                ? "检查麦克风不可用原因"
                : "连接麦克风"
          }
          aria-pressed={voiceConnected}
          title={
            voiceConnected
              ? "断开麦克风"
              : voiceState === "unsupported"
                ? "麦克风暂不可用，点击查看原因"
                : "连接麦克风"
          }
        >
          <ProductIcon name="microphone" />
        </button>
        {voiceConnected && voiceActivationMode === "push_to_talk" ? (
          <button
            className={voiceTransmitting ? "active transmitting" : ""}
            type="button"
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture?.(event.pointerId);
              beginDesktopPushToTalk();
            }}
            onPointerUp={endDesktopPushToTalk}
            onPointerCancel={endDesktopPushToTalk}
            onBlur={endDesktopPushToTalk}
            aria-label="按住说话"
            aria-pressed={voiceTransmitting}
            title="按住说话"
          >
            <ProductIcon name="pushToTalk" />
          </button>
        ) : null}
      </nav>

      {avatarWarning || error || preferenceError || controlError ? (
        <p
          className="desktop-pet-notice"
          role="status"
          data-native-interactive="true"
        >
          {controlError ??
            preferenceError ??
            error ??
            avatarWarning ??
            "Live2D 未就绪，已使用安全回退。"}
        </p>
      ) : null}
    </main>
  );
}
