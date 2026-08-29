import type { DomainEvent } from "@chatwaifu/protocol";
import {
  type KeyboardEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { acquireNativeInteractionGuard } from "../../nativeInteractionGuard";
import {
  decideSkillConfirmation,
  getSkillConfirmations,
  type SkillConfirmation,
  type SkillConfirmationDecision,
} from "./runtimeClient";
import { ModalPortal } from "./ModalPortal";
import {
  RUNTIME_CONNECTION_NOTIFICATION,
  RUNTIME_EVENT_NOTIFICATION,
  type RuntimeConnectionNotification,
} from "./runtimeSocketClient";

const TERMINAL_SKILL_EVENTS = new Set([
  "skill.run_completed",
  "skill.run_failed",
  "skill.run_cancelled",
  "skill.run_expired",
]);

export function SkillConfirmationPrompt({
  sessionId,
}: {
  sessionId: string | null;
}) {
  if (!sessionId) return null;
  return (
    <SessionSkillConfirmationPrompt key={sessionId} sessionId={sessionId} />
  );
}

function SessionSkillConfirmationPrompt({ sessionId }: { sessionId: string }) {
  const [confirmations, setConfirmations] = useState<SkillConfirmation[]>([]);
  const [refreshSequence, setRefreshSequence] = useState(0);
  const [busyRequestId, setBusyRequestId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const promptRef = useRef<HTMLElement>(null);
  const denyButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let disposed = false;
    void getSkillConfirmations(sessionId)
      .then((items) => {
        if (disposed) return;
        setConfirmations(items);
        setError(null);
      })
      .catch(() => {
        // Connection errors are already surfaced by the conversation shell. A
        // permission prompt should stay quiet until there is an actionable
        // confirmation, rather than obscuring the character with another
        // offline warning.
      });
    return () => {
      disposed = true;
    };
  }, [refreshSequence, sessionId]);

  useEffect(() => {
    const refresh = () => setRefreshSequence((sequence) => sequence + 1);
    const refreshFromEvent = (rawEvent: Event) => {
      const event = rawEvent as CustomEvent<DomainEvent>;
      if (event.detail.session_id && event.detail.session_id !== sessionId)
        return;
      const eventType = String(event.detail.event_type);
      if (
        eventType === "skill.confirmation_requested" ||
        TERMINAL_SKILL_EVENTS.has(eventType) ||
        eventType === "session.data_reset"
      )
        refresh();
    };
    const refreshFromConnection = (rawEvent: Event) => {
      const event = rawEvent as CustomEvent<RuntimeConnectionNotification>;
      if (
        event.detail.sessionId === sessionId &&
        event.detail.state === "connected"
      )
        refresh();
    };
    window.addEventListener(RUNTIME_EVENT_NOTIFICATION, refreshFromEvent);
    window.addEventListener(
      RUNTIME_CONNECTION_NOTIFICATION,
      refreshFromConnection,
    );
    const timer = window.setInterval(refresh, 15_000);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(RUNTIME_EVENT_NOTIFICATION, refreshFromEvent);
      window.removeEventListener(
        RUNTIME_CONNECTION_NOTIFICATION,
        refreshFromConnection,
      );
    };
  }, [sessionId]);

  const confirmation = confirmations[0];
  const visible = Boolean(confirmation);

  useLayoutEffect(() => {
    if (!visible) return;
    const prompt = promptRef.current;
    const overlay = prompt?.parentElement;
    if (!prompt || !overlay) return;
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const inertSiblings = [...document.body.children]
      .filter(
        (element): element is HTMLElement =>
          element instanceof HTMLElement && element !== overlay,
      )
      .map((element) => ({ element, inert: element.inert === true }));
    for (const sibling of inertSiblings) sibling.element.inert = true;
    denyButtonRef.current?.focus();
    return () => {
      for (const sibling of inertSiblings)
        sibling.element.inert = sibling.inert;
      if (previousFocus?.isConnected && !previousFocus.closest("[inert]"))
        previousFocus.focus();
    };
  }, [confirmation?.request_id, visible]);

  useLayoutEffect(() => {
    if (!visible) return;
    return acquireNativeInteractionGuard("skill-confirmation");
  }, [visible]);

  if (!confirmation) return null;

  const decide = async (decision: SkillConfirmationDecision) => {
    if (busyRequestId) return;
    setBusyRequestId(confirmation.request_id);
    setError(null);
    try {
      await decideSkillConfirmation(confirmation.request_id, decision);
      setConfirmations((current) =>
        current.filter((item) => item.request_id !== confirmation.request_id),
      );
      setRefreshSequence((sequence) => sequence + 1);
    } catch (decisionError: unknown) {
      setError(errorMessage(decisionError));
      // A confirmation can expire or be cancelled while the user is deciding.
      // Reconcile with Runtime instead of leaving a stale actionable prompt.
      setRefreshSequence((sequence) => sequence + 1);
    } finally {
      setBusyRequestId(null);
    }
  };

  const dangerous = dangerousSideEffect(confirmation.side_effect);
  const busy = busyRequestId === confirmation.request_id;
  const trapKeyboard = (event: KeyboardEvent<HTMLElement>) => {
    event.stopPropagation();
    if (event.key !== "Tab") return;
    const focusable = focusableElements(promptRef.current);
    if (!focusable.length) {
      event.preventDefault();
      return;
    }
    const currentIndex = focusable.indexOf(
      document.activeElement as HTMLElement,
    );
    if (event.shiftKey && currentIndex <= 0) {
      event.preventDefault();
      focusable.at(-1)?.focus();
    } else if (!event.shiftKey && currentIndex === focusable.length - 1) {
      event.preventDefault();
      focusable[0].focus();
    }
  };
  return (
    <ModalPortal className="skill-confirmation-overlay" role="presentation">
      <section
        ref={promptRef}
        className={`skill-confirmation-prompt${dangerous ? " dangerous" : ""}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="skill-confirmation-title"
        aria-describedby="skill-confirmation-reason"
        onKeyDown={trapKeyboard}
      >
        <header>
          <div>
            <small>RUNTIME SKILL</small>
            <h2 id="skill-confirmation-title">需要你的确认</h2>
          </div>
          {confirmations.length > 1 ? (
            <span>{confirmations.length} 项待确认</span>
          ) : null}
        </header>
        <div className="skill-confirmation-capability">
          <strong>
            {confirmation.skill_id}.{confirmation.capability}
          </strong>
          <span>{sideEffectLabel(confirmation.side_effect)}</span>
        </div>
        <p id="skill-confirmation-reason">{confirmation.reason}</p>
        <section
          className="skill-confirmation-arguments"
          aria-label="将发送的参数"
        >
          <header>
            <strong>将发送的参数</strong>
            <span>
              {confirmation.argument_preview.redacted ? (
                <small>敏感字段已隐藏</small>
              ) : null}
              {confirmation.argument_preview.truncated ? (
                <small>内容已截断</small>
              ) : null}
            </span>
          </header>
          <pre>{confirmation.argument_preview.text}</pre>
        </section>
        {confirmation.permissions.length ? (
          <p className="skill-confirmation-permissions">
            所需权限：{confirmation.permissions.join("、")}
          </p>
        ) : null}
        {dangerous ? (
          <p className="skill-confirmation-warning">
            此操作可能影响外部系统或本地数据，只能单次授权。
          </p>
        ) : null}
        {error ? (
          <p className="skill-confirmation-error" role="alert">
            {error}
          </p>
        ) : null}
        <footer>
          <button
            ref={denyButtonRef}
            type="button"
            className="deny"
            disabled={busy}
            onClick={() => void decide("deny")}
          >
            拒绝
          </button>
          {confirmation.allowed_decisions.includes("allow_session") ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void decide("allow_session")}
            >
              本会话允许
            </button>
          ) : null}
          {confirmation.allowed_decisions.includes("allow_always") ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void decide("allow_always")}
            >
              始终允许
            </button>
          ) : null}
          {confirmation.allowed_decisions.includes("allow_once") ? (
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => void decide("allow_once")}
            >
              {busy ? "处理中…" : "允许一次"}
            </button>
          ) : null}
        </footer>
      </section>
    </ModalPortal>
  );
}

function dangerousSideEffect(sideEffect: string): boolean {
  return ["destructive", "external_communication", "device_control"].includes(
    sideEffect,
  );
}

function sideEffectLabel(sideEffect: string): string {
  const labels: Record<string, string> = {
    none: "无副作用",
    read: "读取",
    write: "写入",
    destructive: "危险操作",
    external_communication: "对外通信",
    device_control: "设备控制",
  };
  return labels[sideEffect] ?? sideEffect;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "无法提交确认，请重试。";
}

function focusableElements(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return [...root.querySelectorAll<HTMLElement>("button:not(:disabled)")];
}
