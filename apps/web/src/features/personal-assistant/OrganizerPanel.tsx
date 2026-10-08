import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  isDesktopHost,
  resolveRuntimeConnection,
} from "../chat/runtimeEndpoint";
import {
  deviceCall,
  errorText,
  organizerRequest,
  type AppleItem,
  type AssistantTask,
  type AppleSource,
  type DeviceBinding,
  type Organizer,
  type WriteDestination,
} from "./organizer";
import "./organizer.css";

const textField = (form: FormData, name: string) => {
  const value = form.get(name);
  return typeof value === "string" ? value : "";
};
const empty: Organizer = {
  devices: [],
  tasks: [],
  operations: [],
  scheduler_error: null,
};
const states: Record<string, string> = {
  active: "已安排",
  paused: "已暂停",
  completed: "已到期",
  cancelled: "已取消",
  queued: "等待设备",
  leased: "设备处理中",
  succeeded: "设备已完成",
  failed: "执行失败",
  uncertain: "结果不确定，请在 Apple 应用核对",
  expired: "已过期，未自动重试",
};
const localDate = (seconds: number) => {
  const d = new Date(seconds * 1000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};

export function OrganizerPanel({
  sessionId,
  active = true,
}: {
  sessionId: string;
  active?: boolean;
}) {
  const [page, setPage] = useState<"tasks" | "apple" | "devices">("tasks");
  const [data, setData] = useState<Organizer>(empty);
  const [destinations, setDestinations] = useState<WriteDestination[]>([]);
  const [binding, setBinding] = useState<DeviceBinding | null>(null);
  const [sources, setSources] = useState<AppleSource[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [observedAt, setObservedAt] = useState(0);
  const [target, setTarget] = useState("");
  const [sourceKey, setSourceKey] = useState("");
  const [operationId, setOperationId] = useState("");
  const [editingTask, setEditingTask] = useState<AssistantTask | null>(null);
  const [editing, setEditing] = useState<AppleItem | null>(null);
  const epoch = useRef({ value: 0 });
  const retryIds = useRef<Record<string, { key: string; id: string }>>({});
  const retryId = (kind: string, payload: unknown) => {
    const key = JSON.stringify(payload);
    const prior = retryIds.current[kind];
    if (prior?.key === key) return prior.id;
    const id = crypto.randomUUID();
    retryIds.current[kind] = { key, id };
    return id;
  };
  const refresh = useCallback(async () => {
    const rev = epoch.current.value;
    const connection = await resolveRuntimeConnection();
    if (rev !== epoch.current.value) return;
    const result = await organizerRequest<Organizer>(
      `/organizer?session_id=${encodeURIComponent(sessionId)}`,
      undefined,
      connection,
    );
    if (rev !== epoch.current.value) return;
    const defaults = await organizerRequest<{ items: WriteDestination[] }>(
      `/destinations?session_id=${encodeURIComponent(sessionId)}`,
      undefined,
      connection,
    ).catch(() => ({ items: [] as WriteDestination[] }));
    const local = isDesktopHost()
      ? await deviceCall<DeviceBinding | null>(connection.baseUrl, "load")
      : null;
    if (rev !== epoch.current.value) return;
    setData(result);
    setDestinations(Array.isArray(defaults?.items) ? defaults.items : []);
    setObservedAt(Date.now() / 1000);
    setBinding(local);
    setTarget((old) =>
      result.devices.some((d) => d.device_id === old)
        ? old
        : (local?.device_id ?? result.devices[0]?.device_id ?? ""),
    );
  }, [sessionId]);
  useEffect(() => {
    const revision = epoch.current;
    ++revision.value;
    if (!active) return;
    const expected = revision.value;
    const refreshSafe = () =>
      void refresh().catch((e) => {
        if (expected === revision.value) setError(errorText(e));
      });
    refreshSafe();
    const timer = setInterval(refreshSafe, 5000);
    return () => {
      ++revision.value;
      clearInterval(timer);
    };
  }, [active, refresh]);
  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await work();
      await refresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const selectedDevice = data.devices.find((d) => d.device_id === target);
  const source = selectedDevice?.sources.find(
    (s) => `${s.resource}:${s.id}` === sourceKey,
  );
  const setDefaultApple = () =>
    run(async () => {
      if (!source || !target || !source.writable)
        throw new Error("请先选择可写的 Apple 列表。");
      const c = await resolveRuntimeConnection();
      await organizerRequest(
        "/destinations",
        {
          session_id: sessionId,
          kind: source.resource,
          provider: "apple",
          collection_id: source.id,
          device_id: target,
        },
        c,
        undefined,
        "PUT",
      );
      setMessage(
        source.resource === "calendar"
          ? "已设为默认写入日历。"
          : "已设为默认待办列表。",
      );
    });
  const operation = data.operations.find((o) => o.operation_id === operationId);
  const pair = () =>
    run(async () => {
      const c = await resolveRuntimeConnection();
      const created = await organizerRequest<{
        device_id: string;
        secret: string;
      }>("/devices", { session_id: sessionId, name: "我的桌宠设备" }, c);
      try {
        await deviceCall(c.baseUrl, "pair", created);
      } catch (e) {
        await organizerRequest(
          `/devices/${created.device_id}/revoke`,
          { session_id: sessionId },
          c,
        );
        throw e;
      }
      setMessage("已配对。保持桌宠运行，约几秒后设备会上线。");
    });
  const authorize = (resource: "calendar" | "reminder") =>
    run(async () => {
      const c = await resolveRuntimeConnection();
      const permission = await deviceCall<{
        granted?: boolean;
        error?: string;
      }>(c.baseUrl, "permission", { resource });
      if (permission.error) throw new Error(permission.error);
      if (!permission.granted) throw new Error("apple_permission_required");
      const result = await deviceCall<{
        items?: AppleSource[];
        error?: string;
      }>(c.baseUrl, "sources", { resource });
      if (result.error) throw new Error(result.error);
      setSources((old) => [
        ...old.filter((s) => s.resource !== resource),
        ...(result.items ?? []),
      ]);
      setMessage(
        "勾选允许 ChatWaifu 访问的日历或列表。取消勾选会阻止后续访问。",
      );
    });
  const selectSource = (item: AppleSource, selected: boolean) =>
    run(async () => {
      const c = await resolveRuntimeConnection();
      const current = binding?.sources ?? [];
      const next = selected
        ? [...current, item]
        : current.filter(
            (s) => s.id !== item.id || s.resource !== item.resource,
          );
      await deviceCall(c.baseUrl, "select", next);
      const updated = await deviceCall<DeviceBinding>(c.baseUrl, "load");
      await organizerRequest(
        "/devices/sources",
        {
          device_id: updated.device_id,
          secret: updated.secret,
          sources: updated.sources,
          source_revision: updated.source_revision,
        },
        c,
      );
      setOperationId("");
      setEditing(null);
    });
  const createTask = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    void run(async () => {
      const task = {
        device_id: target,
        title: values.get("title"),
        kind: values.get("kind"),
        due_at: new Date(textField(values, "due")).toISOString(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        repeat: values.get("repeat"),
      };
      await organizerRequest(
        editingTask ? `/tasks/${editingTask.request_id}/replace` : "/tasks",
        {
          session_id: sessionId,
          task: {
            ...task,
            request_id: editingTask?.request_id ?? retryId("task", task),
          },
          ...(editingTask ? { expected_revision: editingTask.revision } : {}),
        },
      );
      delete retryIds.current.task;
      setEditingTask(null);
      setMessage(
        "任务已保存到服务器。到期时指定设备需在线；过期闹钟不会补响。",
      );
    });
  };
  const apple = (action: string, values: Record<string, unknown> = {}) =>
    run(async () => {
      if (!source) throw new Error("请先选择 Apple 日历或提醒事项列表。");
      const now = new Date();
      const end = new Date(now.getTime() + 7 * 86400000);
      const operation = {
        device_id: target,
        resource: source.resource,
        action,
        calendar_id: source.id,
        ...(action === "list" && source.resource === "calendar"
          ? { start: now.toISOString(), end: end.toISOString() }
          : {}),
        ...values,
      };
      const id = retryId("apple", operation);
      // Keep the receipt ID visible even when the enqueue response is lost.
      setOperationId(id);
      await organizerRequest("/apple/operations", {
        session_id: sessionId,
        operation: { ...operation, request_id: id },
      });
      delete retryIds.current.apple;
      setEditing(null);
      setMessage(
        "已发送到配对设备，等待实际执行结果。Apple 云同步由系统负责。",
      );
    });
  const submitApple = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const start = textField(values, "start");
    const end = textField(values, "end");
    void apple(editing ? "update" : "create", {
      title: values.get("title"),
      ...(start ? { start: new Date(start).toISOString() } : {}),
      ...(end ? { end: new Date(end).toISOString() } : {}),
      ...(editing
        ? { item_id: editing.id, expected_modified: editing.modified }
        : {}),
    });
  };
  return (
    <div className="assistant-organizer">
      <div className="assistant-organizer-heading">
        <div>
          <h3>日程与提醒</h3>
          <p>安排日常任务，连接你的 Apple 日历。</p>
        </div>
        <button type="button" onClick={() => void run(refresh)} disabled={busy}>
          刷新
        </button>
      </div>
      <nav className="assistant-organizer-nav" aria-label="日程与提醒功能">
        {(
          [
            ["tasks", "定时任务"],
            ["apple", "日历与提醒"],
            ["devices", "设备与权限"],
          ] as const
        ).map(([id, title]) => (
          <button
            type="button"
            key={id}
            aria-pressed={page === id}
            aria-controls={`organizer-${id}`}
            onClick={() => setPage(id)}
          >
            {title}
          </button>
        ))}
      </nav>
      {message && (
        <p className="assistant-feedback" role="status">
          {message}
        </p>
      )}
      {(error || data.scheduler_error) && (
        <p className="assistant-feedback assistant-feedback-error" role="alert">
          {error ?? data.scheduler_error}
        </p>
      )}
      {!data.devices.length && page !== "devices" && (
        <div className="assistant-empty">
          <strong>先连接一台桌宠设备</strong>
          <p>配对后，就能接收提醒和访问 Apple 日历。</p>
          <button type="button" onClick={() => setPage("devices")}>
            前往配对
          </button>
        </div>
      )}
      <section
        id="organizer-devices"
        className="assistant-panel"
        hidden={page !== "devices"}
      >
        <h3>提醒设备</h3>
        <p>在此设备接收提醒，并选择允许访问的 Apple 日历和提醒事项。</p>
        <div className="assistant-actions">
          {binding &&
            !data.devices.some((d) => d.device_id === binding.device_id) && (
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const c = await resolveRuntimeConnection();
                    await deviceCall(c.baseUrl, "forget");
                  })
                }
              >
                清除失效的本地配对
              </button>
            )}
          {!binding && (
            <button
              disabled={busy || !isDesktopHost()}
              onClick={() => void pair()}
            >
              配对此设备
            </button>
          )}
          {binding && (
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const c = await resolveRuntimeConnection();
                  await deviceCall(c.baseUrl, "notification_permission");
                  setMessage("通知权限已请求；实际提醒还会在桌宠中显示。");
                })
              }
            >
              允许系统通知
            </button>
          )}
        </div>
        {data.devices.map((d) => (
          <div className="assistant-device-row" key={d.device_id}>
            <span>
              {d.name} · {observedAt - d.last_seen < 20 ? "在线" : "离线"}
            </span>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  if (
                    !window.confirm(
                      "撤销此设备会取消发给它的待执行任务，是否继续？",
                    )
                  )
                    return;
                  const c = await resolveRuntimeConnection();
                  await organizerRequest(
                    `/devices/${d.device_id}/revoke`,
                    { session_id: sessionId },
                    c,
                  );
                  if (binding?.device_id === d.device_id)
                    await deviceCall(c.baseUrl, "forget");
                })
              }
            >
              撤销配对
            </button>
          </div>
        ))}
        {binding && (
          <>
            <h3>Apple 访问权限</h3>
            <div className="assistant-actions">
              <button
                disabled={busy}
                onClick={() => void authorize("calendar")}
              >
                连接 Apple 日历
              </button>
              <button
                disabled={busy}
                onClick={() => void authorize("reminder")}
              >
                连接提醒事项
              </button>
            </div>
            {[
              ...sources,
              ...binding.sources.filter(
                (s) =>
                  !sources.some(
                    (v) => v.id === s.id && v.resource === s.resource,
                  ),
              ),
            ].map((s) => (
              <label className="assistant-source" key={`${s.resource}:${s.id}`}>
                <input
                  type="checkbox"
                  disabled={busy}
                  checked={binding.sources.some(
                    (v) => v.id === s.id && v.resource === s.resource,
                  )}
                  onChange={(e) => void selectSource(s, e.target.checked)}
                />
                {s.resource === "calendar" ? "日历" : "提醒事项"} · {s.title}
                {!s.writable && "（只读）"}
              </label>
            ))}
          </>
        )}
      </section>
      <div className="assistant-target" hidden={page === "devices"}>
        <label>
          使用设备
          <select
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              setSourceKey("");
              setEditing(null);
              setOperationId("");
            }}
          >
            <option value="">选择设备</option>
            {data.devices.map((d) => (
              <option key={d.device_id} value={d.device_id}>
                {d.name}
                {data.devices.filter((item) => item.name === d.name).length > 1
                  ? ` · ${d.device_id.slice(0, 8)}`
                  : ""}
              </option>
            ))}
          </select>
        </label>
      </div>
      <section
        id="organizer-tasks"
        className="assistant-panel"
        hidden={page !== "tasks"}
      >
        <h3>定时提醒与闹钟</h3>
        <form key={editingTask?.request_id ?? "new-task"} onSubmit={createTask}>
          <label className="assistant-wide">
            提醒内容
            <input
              name="title"
              defaultValue={editingTask?.title ?? ""}
              maxLength={200}
              required
              placeholder="例如：吃药、休息一下"
            />
          </label>
          <label>
            方式
            <select name="kind" defaultValue={editingTask?.kind ?? "reminder"}>
              <option value="reminder">提醒</option>
              <option value="alarm">闹钟</option>
            </select>
          </label>
          <label>
            重复
            <select name="repeat" defaultValue={editingTask?.repeat ?? "none"}>
              <option value="none">仅一次</option>
              <option value="daily">每天</option>
              <option value="weekdays">工作日（周一至周五）</option>
            </select>
          </label>
          <label className="assistant-wide">
            首次时间（{Intl.DateTimeFormat().resolvedOptions().timeZone}）
            <input
              name="due"
              type="datetime-local"
              required
              defaultValue={editingTask ? localDate(editingTask.next_due) : ""}
            />
          </label>
          <button
            className="assistant-primary assistant-submit"
            disabled={busy || !target}
          >
            {editingTask ? "保存修改" : "保存任务"}
          </button>
          {editingTask && (
            <button type="button" onClick={() => setEditingTask(null)}>
              取消编辑
            </button>
          )}
        </form>
        <p>
          电脑睡眠或关机时不能响铃。闹钟超过 2 分钟、提醒超过 1
          小时会记为错过；贪睡为 5 分钟。
        </p>
        <div className="assistant-list">
          {data.tasks.map((t) => (
            <article key={t.request_id}>
              <strong>{t.title}</strong>
              <small>
                {states[t.state] ?? t.state} ·{" "}
                {new Date(t.next_due * 1000).toLocaleString()} · {t.timezone} ·{" "}
                {t.repeat === "none"
                  ? "仅一次"
                  : t.repeat === "daily"
                    ? "每天"
                    : "工作日"}
              </small>
              <div className="assistant-actions">
                {["active", "paused"].includes(t.state) && (
                  <button
                    disabled={busy}
                    onClick={() => {
                      setEditingTask(t);
                      setTarget(t.device_id);
                    }}
                  >
                    修改时间／内容
                  </button>
                )}

                {["active", "paused"].includes(t.state) && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await organizerRequest(`/tasks/${t.request_id}`, {
                          session_id: sessionId,
                          action: t.state === "paused" ? "resume" : "pause",
                        });
                      })
                    }
                  >
                    {t.state === "paused" ? "恢复" : "暂停"}
                  </button>
                )}
                {t.state !== "cancelled" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await organizerRequest(`/tasks/${t.request_id}`, {
                          session_id: sessionId,
                          action: "cancel",
                        });
                      })
                    }
                  >
                    取消
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      </section>
      <section
        id="organizer-apple"
        className="assistant-panel"
        hidden={page !== "apple"}
      >
        <h3>Apple 日程与提醒事项</h3>
        <label>
          已允许的列表
          <select
            value={sourceKey}
            onChange={(e) => {
              setSourceKey(e.target.value);
              setEditing(null);
              setOperationId("");
            }}
          >
            <option value="">选择列表</option>
            {selectedDevice?.sources.map((s) => (
              <option
                key={`${s.resource}:${s.id}`}
                value={`${s.resource}:${s.id}`}
              >
                {s.resource === "calendar" ? "日历" : "提醒事项"} · {s.title}
              </option>
            ))}
          </select>
        </label>
        <button
          className="assistant-primary assistant-submit"
          disabled={busy || !source}
          onClick={() => void apple("list")}
        >
          {source?.resource === "calendar" ? "读取未来 7 天" : "读取列表"}
        </button>
        {source?.writable && (
          <button disabled={busy} onClick={() => void setDefaultApple()}>
            {destinations.some(
              (d) =>
                d.kind === source.resource &&
                d.provider === "apple" &&
                d.collection_id === source.id &&
                d.device_id === target,
            )
              ? "默认写入位置 ✓"
              : "设为默认写入位置"}
          </button>
        )}
        {source?.writable && (
          <form
            key={`${sourceKey}:${editing?.id ?? "new"}`}
            onSubmit={submitApple}
          >
            <label className="assistant-wide">
              {editing ? "编辑标题" : "新事项标题"}
              <input
                name="title"
                required
                maxLength={200}
                defaultValue={editing?.title ?? ""}
              />
            </label>
            <label>
              {source.resource === "calendar" ? "开始时间" : "到期时间（可选）"}
              <input
                name="start"
                type="datetime-local"
                required={source.resource === "calendar"}
                defaultValue={
                  editing?.start || editing?.due
                    ? localDate(editing.start ?? editing.due!)
                    : ""
                }
              />
            </label>
            {source.resource === "calendar" && (
              <label>
                结束时间
                <input
                  name="end"
                  type="datetime-local"
                  required
                  defaultValue={editing?.end ? localDate(editing.end) : ""}
                />
              </label>
            )}
            <button
              className="assistant-primary assistant-submit"
              disabled={busy}
            >
              {editing ? "保存修改" : "创建事项"}
            </button>
            {editing && (
              <button type="button" onClick={() => setEditing(null)}>
                取消编辑
              </button>
            )}
          </form>
        )}
        {data.operations.length > 0 && (
          <label>
            最近的 Apple 操作
            <select
              value={operationId}
              onChange={(e) => {
                setOperationId(e.target.value);
                setEditing(null);
              }}
            >
              <option value="">选择操作查看结果</option>
              {data.operations.map((item) => (
                <option key={item.operation_id} value={item.operation_id}>
                  {item.operation_id.slice(0, 8)} ·{" "}
                  {states[item.state] ?? item.state}
                </option>
              ))}
            </select>
          </label>
        )}
        {operation && (
          <div className="assistant-list">
            <p>
              {states[operation.state] ?? operation.state}
              {operation.result.error &&
                ` · ${errorText(operation.result.error)}`}
            </p>
            {operation.result.item && (
              <p>已保存：{operation.result.item.title}</p>
            )}
            {operation.result.deleted && <p>已从 Apple 本机数据库删除。</p>}
            {operation.result.truncated && (
              <p>仅显示前 200 项，请在 Apple 应用中查看完整列表。</p>
            )}
            {operation.result.items?.map((item) => (
              <article key={`${item.id}:${item.start ?? 0}`}>
                <strong>
                  {item.completed ? "✓ " : ""}
                  {item.title}
                </strong>
                {(item.start || item.due) && (
                  <small>
                    {new Date(
                      (item.start ?? item.due!) * 1000,
                    ).toLocaleString()}
                  </small>
                )}
                {source?.writable && source.id === item.calendar_id && (
                  <div className="assistant-actions">
                    <button disabled={busy} onClick={() => setEditing(item)}>
                      编辑
                    </button>
                    {source.resource === "reminder" && !item.completed && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void apple("complete", {
                            item_id: item.id,
                            expected_modified: item.modified,
                          })
                        }
                      >
                        完成
                      </button>
                    )}
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (window.confirm(`从 Apple 中删除“${item.title}”？`))
                          void apple("delete", {
                            item_id: item.id,
                            expected_modified: item.modified,
                          });
                      }}
                    >
                      删除
                    </button>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
        <p>
          只支持普通事项。重复日程、邀请、全天事件等复杂编辑会被拒绝，请在 Apple
          应用中处理。
        </p>
      </section>
    </div>
  );
}
