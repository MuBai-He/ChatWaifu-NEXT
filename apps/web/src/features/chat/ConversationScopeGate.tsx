import { useEffect, useState, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { ScopeControlsContext } from "../connection/clientControls";
import { useDialogNavigation } from "../connection/useDialogNavigation";
import { ModalPortal } from "./ModalPortal";
import { mutationReceiptSchema, requestRuntime } from "./runtime-client/http";
import {
  createParticipant,
  createScene,
  getParticipants,
  getScenes,
  OWNER_SCOPE,
  readConversationScope,
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
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const close = () => {
    pending.current?.abort();
    pending.current = null;
    setBusy(false);
    setOpen(false);
  };
  const { ref: dialogRef, onKeyDown: onDialogKeyDown } =
    useDialogNavigation<HTMLElement>(open, close);
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
    try {
      await operation(signal);
    } catch (e) {
      if (pending.current === controller) {
        setError(
          signal.aborted
            ? "服务暂时没有响应，可以重试或关闭此窗口。"
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
  const show = () => {
    setOpen(true);
    setDraft(scope);
    setLoaded(false);
    setParticipants([]);
    setScenes([]);
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
          onClick={show}
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
              className="conversation-scope-dialog"
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
              <p>
                私聊记忆按参与者延续。共享场景只使用该场景的记忆，切换会结束当前通话。
              </p>
              {busy && !loaded && <p role="status">正在读取参与者与场景…</p>}
              {!busy && !loaded && <button onClick={show}>重新加载</button>}
              <fieldset
                className="conversation-scope-fields"
                disabled={busy || !loaded}
              >
                <label>
                  当前说话者
                  <select
                    value={draft.participant_id}
                    onChange={(e) =>
                      setDraft({
                        participant_id: e.target.value,
                        scene_id: null,
                      })
                    }
                  >
                    {!loaded && (
                      <option value={draft.participant_id}>等待服务连接</option>
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
                <details>
                  <summary>添加参与者</summary>
                  <input
                    aria-label="参与者名称"
                    value={name}
                    maxLength={80}
                    onChange={(e) => setName(e.target.value)}
                  />
                  <button
                    disabled={busy || !name.trim()}
                    onClick={() =>
                      void act(async (signal) => {
                        const p = await createParticipant(name.trim(), signal);
                        signal.throwIfAborted();
                        setParticipants((list) => [...list, p]);
                        setDraft({
                          participant_id: p.participant_id,
                          scene_id: null,
                        });
                        setName("");
                      })
                    }
                  >
                    添加
                  </button>
                </details>
                <details>
                  <summary>创建共享场景</summary>
                  <input
                    aria-label="场景名称"
                    value={sceneName}
                    maxLength={120}
                    onChange={(e) => setSceneName(e.target.value)}
                  />
                  <fieldset>
                    <legend>听众（至少两人）</legend>
                    {participants.map((p) => (
                      <label key={p.participant_id}>
                        <input
                          type="checkbox"
                          checked={audience.includes(p.participant_id)}
                          onChange={(e) =>
                            setAudience((ids) =>
                              e.target.checked
                                ? [...ids, p.participant_id]
                                : ids.filter((id) => id !== p.participant_id),
                            )
                          }
                        />
                        {p.display_name}
                      </label>
                    ))}
                  </fieldset>
                  <p>
                    听众名单固定；更换听众请新建场景，避免沿用之前的共享记忆。
                  </p>
                  <button
                    disabled={busy || !sceneName.trim() || audience.length < 2}
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
                      })
                    }
                  >
                    创建场景
                  </button>
                </details>
              </fieldset>
              {error && <p role="alert">{error}</p>}
              <footer>
                <button onClick={close}>取消</button>
                <button
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
