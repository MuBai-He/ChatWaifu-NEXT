import { useEffect, useState, type ReactNode } from "react";
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

export function ConversationScopeGate({ children }: { children: ReactNode }) {
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
  const act = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };
  const show = () => {
    setOpen(true);
    setDraft(scope);
    void act(async () => {
      const [people, rooms] = await Promise.all([
        getParticipants(),
        getScenes(),
      ]);
      setParticipants(people);
      setScenes(rooms);
    });
  };
  const apply = () =>
    act(async () => {
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
          { method: "DELETE" },
        );
      localStorage.setItem(await scopeStorageKey(), JSON.stringify(draft));
      setScope(draft);
      setRevision((r) => r + 1);
      setOpen(false);
    });
  const label =
    participants.find((p) => p.participant_id === scope.participant_id)
      ?.display_name ?? (scope.participant_id === "local" ? "主人" : "参与者");
  return (
    <>
      <div key={revision} className="conversation-scope-content">
        {children}
      </div>
      <button
        className="conversation-scope-trigger"
        onClick={show}
        aria-label="切换参与者与场景"
      >
        {label} · {scope.scene_id ? "共享场景" : "私聊"}
      </button>
      {open && (
        <ModalPortal>
          <div className="conversation-scope-overlay">
            <section
              role="dialog"
              aria-modal="true"
              aria-labelledby="conversation-scope-title"
              className="conversation-scope-dialog"
            >
              <h2 id="conversation-scope-title">参与者与场景</h2>
              <p>
                私聊记忆按参与者延续。共享场景只使用该场景的记忆，切换会结束当前通话。
              </p>
              <label>
                当前说话者
                <select
                  value={draft.participant_id}
                  onChange={(e) =>
                    setDraft({ participant_id: e.target.value, scene_id: null })
                  }
                >
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
                    void act(async () => {
                      const p = await createParticipant(name.trim());
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
                    void act(async () => {
                      const room = await createScene(
                        sceneName.trim(),
                        audience,
                      );
                      setScenes((list) => [...list, room]);
                      setDraft({
                        participant_id: audience.includes(draft.participant_id)
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
              {error && <p role="alert">{error}</p>}
              <footer>
                <button disabled={busy} onClick={() => setOpen(false)}>
                  取消
                </button>
                <button
                  disabled={busy || participants.length === 0}
                  onClick={() => void apply()}
                >
                  应用
                </button>
              </footer>
            </section>
          </div>
        </ModalPortal>
      )}
    </>
  );
}
