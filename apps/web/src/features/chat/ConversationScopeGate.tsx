import "../settings/settings-glass.css";
import "../settings/settings-controls.css";
import { useEffect, useState, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { ScopeControlsContext } from "../connection/clientControls";
import { useDialogNavigation } from "../connection/useDialogNavigation";
import { ModalPortal } from "./ModalPortal";
import {
  mutationReceiptSchema,
  requestRuntime,
  RuntimeRequestError,
} from "./runtime-client/http";
import {
  createParticipant,
  createScene,
  getParticipants,
  getScenes,
  OWNER_SCOPE,
  readConversationScope,
  renameParticipant,
  scopedSessionStorageKey,
  scopeStorageKey,
  SCOPE_STORAGE_PREFIX,
  type ConversationScope,
  type Participant,
  type Scene,
} from "./conversationScope";
import "./conversation-scope.css";

export function ConversationScopeGate({
  children,
  showSwitch = true,
}: {
  children: ReactNode;
  showSwitch?: boolean;
}) {
  const [revision, setRevision] = useState(0);
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<ConversationScope>(OWNER_SCOPE);
  const [draft, setDraft] = useState<ConversationScope>(OWNER_SCOPE);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [name, setName] = useState("");
  const [sceneName, setSceneName] = useState("");
  const [audience, setAudience] = useState<string[]>([]);
  const [editedName, setEditedName] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const close = () => {
    pending.current?.abort();
    pending.current = null;
    setBusy(false);
    setOpen(false);
  };
  const { ref: dialogRef, onKeyDown: onDialogKeyDown } =
    useDialogNavigation<HTMLElement>(open, close, opener);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    let active = true;
    void readConversationScope()
      .then((value) => {
        if (active) setScope(value);
      })
      .catch(() => {
        /* Connection errors are displayed by the mounted client. */
      });
    const changed = (event: StorageEvent) => {
      if (!event.key?.startsWith(SCOPE_STORAGE_PREFIX)) return;
      void scopeStorageKey()
        .then((key) => {
          if (!active || event.key !== key) return;
          void readConversationScope()
            .then((value) => {
              if (active) {
                pending.current?.abort();
                pending.current = null;
                setBusy(false);
                setScope(value);
                setOpen(false);
                setRevision((r) => r + 1);
              }
            })
            .catch(() => undefined);
        })
        .catch(() => undefined);
    };
    window.addEventListener("storage", changed);
    return () => {
      active = false;
      window.removeEventListener("storage", changed);
    };
  }, []);
  const act = async (operation: (signal: AbortSignal) => Promise<void>) => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    const signal = AbortSignal.any([
      controller.signal,
      AbortSignal.timeout(8_000),
    ]);
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await operation(signal);
    } catch (e) {
      if (pending.current === controller) {
        setError(
          signal.aborted
            ? "服务暂时没有响应，可以重试或关闭此窗口。"
            : e instanceof RuntimeRequestError && e.status === 409
              ? "名称已被其他操作修改，请重新打开窗口后重试。"
              : e instanceof TypeError
                ? "无法连接服务，请检查连接设置后重试。"
                : e instanceof Error
                  ? e.message
                  : "操作失败",
        );
      }
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setBusy(false);
      }
    }
  };
  const show = (trigger?: HTMLElement) => {
    // Keep the actual opener even if WebKit does not focus the clicked button
    // or the background becomes inert before the dialog's effect runs.
    if (trigger) opener.current = trigger;
    setOpen(true);
    setDraft(scope);
    setLoaded(false);
    setParticipants([]);
    setScenes([]);
    setName("");
    setSceneName("");
    setAudience([]);
    setEditedName(null);
    void act(async (signal) => {
      const [people, rooms] = await Promise.all([
        getParticipants(signal),
        getScenes(signal),
      ]);
      signal.throwIfAborted();
      setLoaded(true);
      setParticipants(people);
      setScenes(rooms);
    });
  };
  const apply = () =>
    act(async (signal) => {
      const room = scenes.find((s) => s.scene_id === draft.scene_id);
      if (
        draft.scene_id &&
        !room?.participant_ids.includes(draft.participant_id)
      )
        throw new Error("请选择场景中的参与者");
      if (
        scope.participant_id === draft.participant_id &&
        scope.scene_id === draft.scene_id
      ) {
        setOpen(false);
        return;
      }
      // Close server media before another audience can mount a client using it.
      const previous = localStorage.getItem(scopedSessionStorageKey(scope));
      if (previous)
        await requestRuntime(
          `/v1/sessions/${previous}`,
          mutationReceiptSchema,
          { method: "DELETE", signal },
        );
      const key = await scopeStorageKey();
      signal.throwIfAborted();
      localStorage.setItem(key, JSON.stringify(draft));
      setScope(draft);
      setRevision((r) => r + 1);
      setOpen(false);
    });
  const label =
    participants.find((p) => p.participant_id === scope.participant_id)
      ?.display_name ?? (scope.participant_id === "local" ? "我" : "参与者");
  const selectedParticipant = participants.find(
    (p) => p.participant_id === draft.participant_id,
  );
  const nextName = editedName ?? selectedParticipant?.display_name ?? "";
  return (
    <ScopeControlsContext.Provider
      value={{
        label: `${label} · ${scope.scene_id ? "共享场景" : "独立对话"}`,
        open: show,
      }}
    >
      <div key={revision} className="conversation-scope-content" inert={open}>
        {children}
      </div>
      {showSwitch && (
        <button
          className="conversation-scope-trigger"
          onClick={(event) => show(event.currentTarget)}
          aria-label="切换参与者与场景"
        >
          对话设置
        </button>
      )}
      {open && (
        <ModalPortal>
          <div
            className="conversation-scope-overlay"
            data-native-interactive="true"
            onClick={(event) => {
              if (event.target === event.currentTarget) close();
            }}
          >
            <section
              ref={dialogRef}
              onKeyDown={onDialogKeyDown}
              role="dialog"
              aria-modal="true"
              aria-labelledby="conversation-scope-title"
              className="conversation-scope-dialog settings-material settings-surface settings-controls"
            >
              <header className="conversation-scope-heading">
                <h2 id="conversation-scope-title">参与者与场景</h2>
                <button
                  type="button"
                  data-dialog-close
                  aria-label="关闭参与者与场景"
                  onClick={close}
                >
                  <X size={18} />
                </button>
              </header>
              <div className="conversation-scope-body">
                <p className="conversation-scope-description">
                  选择桌宠与 Web
                  当前的说话者。独立对话按人保留记忆，共享场景只使用该场景的记忆；切换会结束当前通话。
                </p>
                {busy && !loaded && <p role="status">正在读取参与者与场景…</p>}
                {!busy && !loaded && (
                  <button onClick={() => show()}>重新加载</button>
                )}
                <fieldset
                  className="conversation-scope-fields"
                  disabled={busy || !loaded}
                >
                  <label>
                    当前说话者
                    <select
                      value={draft.participant_id}
                      onChange={(e) => {
                        setEditedName(null);
                        setDraft({
                          participant_id: e.target.value,
                          scene_id: null,
                        });
                      }}
                    >
                      {!loaded && (
                        <option value={draft.participant_id}>
                          等待服务连接
                        </option>
                      )}
                      {participants.map((p) => (
                        <option key={p.participant_id} value={p.participant_id}>
                          {p.display_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    对话场景
                    <select
                      value={draft.scene_id ?? ""}
                      onChange={(e) =>
                        setDraft({ ...draft, scene_id: e.target.value || null })
                      }
                    >
                      <option value="">独立私聊</option>
                      {scenes
                        .filter((s) =>
                          s.participant_ids.includes(draft.participant_id),
                        )
                        .map((s) => (
                          <option key={s.scene_id} value={s.scene_id}>
                            {s.display_name}
                          </option>
                        ))}
                    </select>
                  </label>
                  {draft.scene_id && (
                    <p>
                      听众：
                      {scenes
                        .find((s) => s.scene_id === draft.scene_id)
                        ?.participant_ids.map(
                          (id) =>
                            participants.find((p) => p.participant_id === id)
                              ?.display_name ?? id,
                        )
                        .join("、")}
                    </p>
                  )}
                  <details className="conversation-scope-editor">
                    <summary>修改当前参与者名称</summary>
                    <div className="conversation-scope-editor-body">
                      <label>
                        显示名称
                        <input
                          value={nextName}
                          maxLength={80}
                          onChange={(e) => setEditedName(e.target.value)}
                        />
                      </label>
                      <p>仅修改显示名称，已有身份关联、对话和记忆继续沿用。</p>
                      <button
                        type="button"
                        disabled={
                          busy ||
                          !selectedParticipant ||
                          !nextName.trim() ||
                          nextName.trim() === selectedParticipant.display_name
                        }
                        onClick={() => {
                          if (!selectedParticipant) return;
                          const selected = selectedParticipant;
                          void act(async (signal) => {
                            const result = await renameParticipant(
                              selected,
                              nextName.trim(),
                              signal,
                            );
                            signal.throwIfAborted();
                            if (
                              result.participant_id !== selected.participant_id
                            )
                              throw new Error("服务返回的参与者身份不一致");
                            setParticipants((list) =>
                              list.map((p) =>
                                p.participant_id === result.participant_id
                                  ? result
                                  : p,
                              ),
                            );
                            setEditedName(null);
                            setNotice("参与者名称已保存");
                          });
                        }}
                      >
                        保存名称
                      </button>
                    </div>
                  </details>
                  <details className="conversation-scope-editor">
                    <summary>添加参与者</summary>
                    <div className="conversation-scope-editor-body">
                      <label>
                        参与者名称
                        <input
                          placeholder="例如：小林"
                          value={name}
                          maxLength={80}
                          onChange={(e) => setName(e.target.value)}
                        />
                      </label>
                      <button
                        type="button"
                        disabled={busy || !name.trim()}
                        onClick={() =>
                          void act(async (signal) => {
                            const p = await createParticipant(
                              name.trim(),
                              signal,
                            );
                            signal.throwIfAborted();
                            setParticipants((list) => [...list, p]);
                            setDraft({
                              participant_id: p.participant_id,
                              scene_id: null,
                            });
                            setName("");
                            setEditedName(null);
                          })
                        }
                      >
                        添加
                      </button>
                    </div>
                  </details>
                  <details className="conversation-scope-editor">
                    <summary>创建共享场景</summary>
                    <div className="conversation-scope-editor-body">
                      <label>
                        场景名称
                        <input
                          placeholder="例如：周末闲聊"
                          value={sceneName}
                          maxLength={120}
                          onChange={(e) => setSceneName(e.target.value)}
                        />
                      </label>
                      <fieldset className="conversation-scope-audience">
                        <legend>
                          听众 · 已选 {audience.length} 人（2–32 人）
                        </legend>
                        <div className="conversation-scope-member-list">
                          {participants.map((p) => (
                            <label
                              key={p.participant_id}
                              className="conversation-scope-member"
                            >
                              <input
                                type="checkbox"
                                value={p.participant_id}
                                disabled={
                                  audience.length >= 32 &&
                                  !audience.includes(p.participant_id)
                                }
                                checked={audience.includes(p.participant_id)}
                                onChange={(e) =>
                                  setAudience((ids) =>
                                    e.target.checked
                                      ? [...ids, p.participant_id]
                                      : ids.filter(
                                          (id) => id !== p.participant_id,
                                        ),
                                  )
                                }
                              />
                              <span>{p.display_name}</span>
                            </label>
                          ))}
                        </div>
                      </fieldset>
                      <p>
                        听众名单固定；更换听众请新建场景，避免沿用之前的共享记忆。
                      </p>
                      <button
                        type="button"
                        disabled={
                          busy || !sceneName.trim() || audience.length < 2
                        }
                        onClick={() =>
                          void act(async (signal) => {
                            const room = await createScene(
                              sceneName.trim(),
                              audience,
                              signal,
                            );
                            signal.throwIfAborted();
                            setScenes((list) => [...list, room]);
                            setDraft({
                              participant_id: audience.includes(
                                draft.participant_id,
                              )
                                ? draft.participant_id
                                : audience[0],
                              scene_id: room.scene_id,
                            });
                            setSceneName("");
                            setEditedName(null);
                          })
                        }
                      >
                        创建场景
                      </button>
                    </div>
                  </details>
                </fieldset>
                {notice && <p role="status">{notice}</p>}
                {error && <p role="alert">{error}</p>}
              </div>
              <footer>
                <button type="button" onClick={close}>
                  取消
                </button>
                <button
                  type="button"
                  className="conversation-scope-primary"
                  disabled={busy || !loaded || participants.length === 0}
                  onClick={() => void apply()}
                >
                  应用
                </button>
              </footer>
            </section>
          </div>
        </ModalPortal>
      )}
    </ScopeControlsContext.Provider>
  );
}
