import { useCallback, useEffect, useId, useRef, useState } from "react";

import { ProductIcon } from "../../components/ProductIcon";
import { ModalPortal } from "../chat/ModalPortal";

const CONFIRMATION_PHRASE = "清除当前数据";

export function DataClearConfirmationDialog({
  open,
  busy,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<boolean>;
}) {
  const [step, setStep] = useState<"scope" | "phrase">("scope");
  const [phrase, setPhrase] = useState("");
  const phraseInputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  const cancel = useCallback(() => {
    setStep("scope");
    setPhrase("");
    onCancel();
  }, [onCancel]);

  useEffect(() => {
    if (step === "phrase") phraseInputRef.current?.focus();
  }, [step]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) cancel();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, cancel, open]);

  if (!open) return null;

  const confirmed = phrase.trim() === CONFIRMATION_PHRASE;
  const finish = async () => {
    if (!confirmed || busy) return;
    if (await onConfirm()) cancel();
  };

  return (
    <ModalPortal
      className="data-clear-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) cancel();
      }}
    >
      <section
        className="data-clear-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <header>
          <span className="data-clear-dialog-icon">
            <ProductIcon name="trash" />
          </span>
          <div>
            <small>LOCAL DATA · {step === "scope" ? "1 / 2" : "2 / 2"}</small>
            <h2 id={titleId}>清除当前对话与记忆</h2>
          </div>
          <button
            className="data-clear-dialog-close"
            type="button"
            aria-label="取消清除"
            disabled={busy}
            onClick={cancel}
          >
            <ProductIcon name="close" />
          </button>
        </header>

        {step === "scope" ? (
          <div className="data-clear-dialog-body">
            <p id={descriptionId}>
              这是第一次确认。此次操作只针对当前角色与当前会话，完成后无法撤销。
            </p>
            <div className="data-clear-scope-grid">
              <section>
                <strong>将永久清除</strong>
                <ul>
                  <li>当前会话的全部对话记录</li>
                  <li>当前角色与当前用户的记忆、关系和情绪状态</li>
                  <li>当前会话生成的本地语音文件</li>
                </ul>
              </section>
              <section>
                <strong>不会清除</strong>
                <ul>
                  <li>聊天、记忆、Embedding 与 TTS 模型配置</li>
                  <li>API 密钥、插件、MCP 与渠道设置</li>
                  <li>其他角色或其他会话的数据</li>
                </ul>
              </section>
            </div>
          </div>
        ) : (
          <div className="data-clear-dialog-body">
            <p id={descriptionId}>
              这是第二次确认。请输入下方短语，避免误触导致数据丢失。
            </p>
            <label className="data-clear-confirmation-field">
              输入“{CONFIRMATION_PHRASE}”
              <input
                ref={phraseInputRef}
                value={phrase}
                autoComplete="off"
                spellCheck={false}
                disabled={busy}
                onChange={(event) => setPhrase(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void finish();
                  }
                }}
              />
            </label>
          </div>
        )}

        <footer>
          <button type="button" disabled={busy} onClick={cancel}>
            取消
          </button>
          {step === "scope" ? (
            <button
              className="danger"
              type="button"
              onClick={() => setStep("phrase")}
            >
              我已了解，继续
            </button>
          ) : (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() => setStep("scope")}
              >
                返回上一步
              </button>
              <button
                className="danger"
                type="button"
                disabled={!confirmed || busy}
                onClick={() => void finish()}
              >
                <ProductIcon name="trash" />
                {busy ? "正在清除…" : "永久清除"}
              </button>
            </>
          )}
        </footer>
      </section>
    </ModalPortal>
  );
}
