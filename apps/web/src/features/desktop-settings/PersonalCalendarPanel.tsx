import { useEffect, useRef, useState, type FormEvent } from "react";
import { z } from "zod";
import { requestRuntime } from "../chat/runtime-client/http";

const accountSchema = z.array(
  z.object({
    account_id: z.string(),
    status: z.string(),
    calendar_write: z.boolean().default(false),
    tasks_write: z.boolean().default(false),
    display_label: z.string().nullable().optional(),
  }),
);
const calendarsSchema = z.object({
  items: z.array(
    z.object({
      calendar: z.object({
        id: z.string(),
        title: z.string(),
        timezone: z.string().nullable(),
        access_role: z.string(),
        primary: z.boolean().default(false),
      }),
      selected: z.boolean(),
    }),
  ),
});
const timeSchema = z
  .object({
    day: z.string().nullable(),
    timestamp: z.string().nullable(),
    timezone: z.string().nullable(),
  })
  .nullable();
const eventsSchema = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      etag: z.string().nullable(),
      event_type: z.string().default("default"),
      title: z.string().nullable(),
      start: timeSchema,
      end: timeSchema,
    }),
  ),
});
const tasklistsSchema = z.object({
  items: z.array(
    z.object({
      tasklist: z.object({ id: z.string(), title: z.string() }),
      selected: z.boolean(),
    }),
  ),
});
const tasksSchema = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      title: z.string(),
      notes: z.string().nullable(),
      due: z.string().nullable(),
      status: z.enum(["needsAction", "completed"]),
      etag: z.string().nullable(),
    }),
  ),
});
type Calendar = z.infer<typeof calendarsSchema>["items"][number];
type Event = z.infer<typeof eventsSchema>["items"][number];
type GoogleTask = z.infer<typeof tasksSchema>["items"][number];
const itemSchema = z.object({ item: z.unknown() });
const deletedSchema = z.object({ deleted: z.boolean() });
const actionSchema = z.object({}).passthrough();
const destinationSchema = z.object({
  items: z.array(
    z.object({
      kind: z.enum(["calendar", "reminder"]),
      provider: z.enum(["google", "apple"]),
      account_id: z.string().nullable(),
      collection_id: z.string(),
      device_id: z.string().nullable(),
    }),
  ),
});
const localDateTime = (value: string | null | undefined) => {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
};
const formText = (form: FormData, name: string) => {
  const value = form.get(name);
  return typeof value === "string" ? value : "";
};

export function PersonalCalendarPanel({
  sessionId,
  onUpgrade,
}: {
  sessionId: string;
  onUpgrade: (accountId: string) => void;
}) {
  const [accounts, setAccounts] = useState<z.infer<typeof accountSchema>>([]);
  const [accountId, setAccountId] = useState("");
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [calendarId, setCalendarId] = useState("");
  const [tasklists, setTasklists] = useState<
    z.infer<typeof tasklistsSchema>["items"]
  >([]);
  const [listId, setListId] = useState("");
  const [tasks, setTasks] = useState<GoogleTask[]>([]);
  const [editingEvent, setEditingEvent] = useState<Event | null>(null);
  const [editingTask, setEditingTask] = useState<GoogleTask | null>(null);
  const [destinations, setDestinations] = useState<
    z.infer<typeof destinationSchema>["items"]
  >([]);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const active = useRef<AbortController | null>(null);
  const createRequestId = useRef<{ key: string; id: string } | null>(null);
  const [uncertainTaskWrite, setUncertainTaskWrite] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void requestRuntime(
      `/v1/personal-assistant/accounts?session_id=${encodeURIComponent(sessionId)}`,
      accountSchema,
      { signal: controller.signal },
    )
      .then((items) => {
        if (!controller.signal.aborted)
          setAccounts(items.filter((a) => a.status === "connected"));
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setNotice("无法读取账号；请确认当前为主人私聊并重试。");
      });
    void requestRuntime(
      `/v1/personal-assistant/destinations?session_id=${encodeURIComponent(sessionId)}`,
      destinationSchema,
      { signal: controller.signal },
    )
      .then((data) => {
        if (!controller.signal.aborted) setDestinations(data.items);
      })
      .catch(() => {});
    return () => {
      controller.abort();
      active.current?.abort();
    };
  }, [sessionId, refresh]);

  async function load(id: string) {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setAccountId(id);
    setCalendars([]);
    setEvents([]);
    setCalendarId("");
    setTasklists([]);
    setListId("");
    setTasks([]);
    setNotice("");
    setBusy(true);
    try {
      const query = new URLSearchParams({
        session_id: sessionId,
        account_id: id,
        discover: "true",
      });
      const result = await requestRuntime(
        `/v1/personal-assistant/calendars?${query}`,
        calendarsSchema,
        { signal: controller.signal, timeoutMs: 130000 },
      );
      if (!controller.signal.aborted) {
        setCalendars(result.items);
        const primary = result.items.find((item) => item.calendar.primary);
        if (primary)
          setAccounts((old) =>
            old.map((account) =>
              account.account_id === id
                ? {
                    ...account,
                    display_label:
                      primary.calendar.title || primary.calendar.id,
                  }
                : account,
            ),
          );
        setNotice(
          result.items.length
            ? "勾选允许查询的日历；默认不会选择任何日历。"
            : "这个账号没有可用日历。",
        );
      }
      if (accounts.find((a) => a.account_id === id)?.tasks_write) {
        try {
          const lists = await requestRuntime(
            `/v1/personal-assistant/google-tasklists?${new URLSearchParams({ session_id: sessionId, account_id: id, discover: "true" })}`,
            tasklistsSchema,
            { signal: controller.signal, timeoutMs: 130000 },
          );
          if (!controller.signal.aborted) setTasklists(lists.items);
        } catch {
          if (!controller.signal.aborted)
            setNotice(
              "日历已读取；Google 待办列表读取失败，请检查 Tasks API 与授权。",
            );
        }
      }
    } catch {
      if (!controller.signal.aborted)
        setNotice("读取日历失败，请重试；可能需要重新授权。");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function select(item: Calendar, selected: boolean) {
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setEvents([]);
    try {
      await requestRuntime(
        "/v1/personal-assistant/calendars/selection",
        z.object({ selected: z.boolean() }),
        {
          method: "PUT",
          signal: controller.signal,
          body: JSON.stringify({
            session_id: sessionId,
            account_id: accountId,
            calendar_id: item.calendar.id,
            selected,
          }),
        },
      );
      if (!controller.signal.aborted)
        setCalendars((items) =>
          items.map((c) =>
            c.calendar.id === item.calendar.id ? { ...c, selected } : c,
          ),
        );
    } catch {
      if (!controller.signal.aborted)
        setNotice("选择未确认，请重新读取日历核对结果。");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function query(item: Calendar) {
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setEvents([]);
    setCalendarId(item.calendar.id);
    setNotice("");
    const start = new Date();
    const end = new Date(start.getTime() + 7 * 86400000);
    try {
      const params = new URLSearchParams({
        session_id: sessionId,
        account_id: accountId,
        calendar_id: item.calendar.id,
        start: start.toISOString(),
        end: end.toISOString(),
      });
      const result = await requestRuntime(
        `/v1/personal-assistant/events?${params}`,
        eventsSchema,
        { signal: controller.signal, timeoutMs: 130000 },
      );
      if (!controller.signal.aborted) {
        setEvents(result.items);
        setNotice(
          `${item.calendar.title} · 未来 7 天 · Google 实时查询${result.items.length ? "" : "，没有安排"}`,
        );
      }
    } catch {
      if (!controller.signal.aborted)
        setNotice("日程查询失败，请重试。未显示旧数据作为最新结果。");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function queryTasks(id: string) {
    const controller = new AbortController();
    active.current?.abort();
    active.current = controller;
    setListId(id);
    setTasks([]);
    setBusy(true);
    try {
      const params = new URLSearchParams({
        session_id: sessionId,
        account_id: accountId,
        list_id: id,
      });
      const result = await requestRuntime(
        `/v1/personal-assistant/google-tasks?${params}`,
        tasksSchema,
        { signal: controller.signal, timeoutMs: 130000 },
      );
      if (!controller.signal.aborted) setTasks(result.items);
    } catch (error) {
      if (!controller.signal.aborted)
        setNotice(error instanceof Error ? error.message : "待办读取失败");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function selectTasklist(id: string, selected: boolean) {
    setBusy(true);
    try {
      await requestRuntime(
        "/v1/personal-assistant/google-tasklists/selection",
        z.object({ selected: z.boolean() }),
        {
          method: "PUT",
          body: JSON.stringify({
            session_id: sessionId,
            account_id: accountId,
            list_id: id,
            selected,
          }),
        },
      );
      setTasklists((items) =>
        items.map((item) =>
          item.tasklist.id === id ? { ...item, selected } : item,
        ),
      );
      if (!selected && listId === id) {
        setListId("");
        setTasks([]);
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "列表选择未确认");
    } finally {
      setBusy(false);
    }
  }
  async function setDefault(
    kind: "calendar" | "reminder",
    collectionId: string,
  ) {
    setBusy(true);
    try {
      const result = await requestRuntime(
        "/v1/personal-assistant/destinations",
        z.object({ destination: destinationSchema.shape.items.element }),
        {
          method: "PUT",
          body: JSON.stringify({
            session_id: sessionId,
            kind,
            provider: "google",
            account_id: accountId,
            collection_id: collectionId,
          }),
        },
      );
      setDestinations((items) => [
        ...items.filter((item) => item.kind !== kind),
        result.destination,
      ]);
      setNotice(
        kind === "calendar" ? "已设为默认写入日历。" : "已设为默认待办列表。",
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "默认目标未保存");
    } finally {
      setBusy(false);
    }
  }
  async function saveEvent(form: FormEvent<HTMLFormElement>) {
    form.preventDefault();
    if (!calendarId) return;
    const values = new FormData(form.currentTarget);
    const title = formText(values, "title").trim();
    const start = new Date(formText(values, "start")).toISOString();
    const end = new Date(formText(values, "end")).toISOString();
    const key = JSON.stringify({ accountId, calendarId, title, start, end });
    const id =
      createRequestId.current?.key === key
        ? createRequestId.current.id
        : crypto.randomUUID();
    createRequestId.current = { key, id };
    setBusy(true);
    setNotice("");
    try {
      await requestRuntime(
        editingEvent
          ? `/v1/personal-assistant/events/${encodeURIComponent(editingEvent.id)}`
          : "/v1/personal-assistant/events",
        itemSchema,
        {
          method: editingEvent ? "PATCH" : "POST",
          timeoutMs: 30000,
          body: JSON.stringify({
            session_id: sessionId,
            account_id: accountId,
            calendar_id: calendarId,
            title,
            start,
            end,
            ...(editingEvent
              ? { etag: editingEvent.etag }
              : { request_id: id }),
          }),
        },
      );
      createRequestId.current = null;
      setEditingEvent(null);
      const source = calendars.find((item) => item.calendar.id === calendarId);
      if (source) await query(source);
      setNotice("已保存到 Google 日历。");
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "写入结果不确定，请在 Google 日历核对。",
      );
    } finally {
      setBusy(false);
    }
  }
  async function removeEvent(item: Event) {
    if (
      !item.etag ||
      !window.confirm(`从 Google 日历删除“${item.title || "无标题日程"}”？`)
    )
      return;
    setBusy(true);
    try {
      await requestRuntime(
        `/v1/personal-assistant/events/${encodeURIComponent(item.id)}`,
        deletedSchema,
        {
          method: "DELETE",
          body: JSON.stringify({
            session_id: sessionId,
            account_id: accountId,
            calendar_id: calendarId,
            etag: item.etag,
          }),
          timeoutMs: 30000,
        },
      );
      const source = calendars.find((c) => c.calendar.id === calendarId);
      if (source) await query(source);
      setNotice("已从 Google 日历删除。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除结果不确定");
    } finally {
      setBusy(false);
    }
  }
  async function saveTask(form: FormEvent<HTMLFormElement>) {
    form.preventDefault();
    if (!listId) return;
    if (uncertainTaskWrite) {
      setNotice("请先在 Google Tasks 核对上次写入结果，再确认继续。");
      return;
    }
    const values = new FormData(form.currentTarget);
    const title = formText(values, "title").trim();
    const due = formText(values, "due");
    setBusy(true);
    try {
      await requestRuntime(
        editingTask
          ? `/v1/personal-assistant/google-tasks/${encodeURIComponent(editingTask.id)}`
          : "/v1/personal-assistant/google-tasks",
        itemSchema,
        {
          method: editingTask ? "PATCH" : "POST",
          timeoutMs: 30000,
          body: JSON.stringify({
            session_id: sessionId,
            account_id: accountId,
            list_id: listId,
            title,
            due: due || null,
            ...(editingTask ? { etag: editingTask.etag } : {}),
          }),
        },
      );
      setEditingTask(null);
      setUncertainTaskWrite(false);
      await queryTasks(listId);
      setNotice("已保存到 Google Tasks。");
    } catch (error) {
      setUncertainTaskWrite(true);
      setNotice(
        `${error instanceof Error ? error.message : "写入结果不确定"}。请先在 Google Tasks 核对，避免重复创建。`,
      );
    } finally {
      setBusy(false);
    }
  }
  async function changeTask(item: GoogleTask, action: "complete" | "delete") {
    if (
      !item.etag ||
      (action === "delete" && !window.confirm(`删除待办“${item.title}”？`))
    )
      return;
    setBusy(true);
    try {
      await requestRuntime(
        `/v1/personal-assistant/google-tasks/${encodeURIComponent(item.id)}`,
        actionSchema,
        {
          method: action === "delete" ? "DELETE" : "PATCH",
          timeoutMs: 30000,
          body: JSON.stringify({
            session_id: sessionId,
            account_id: accountId,
            list_id: listId,
            etag: item.etag,
            ...(action === "complete" ? { status: "completed" } : {}),
          }),
        },
      );
      await queryTasks(listId);
      setNotice(action === "complete" ? "已完成待办。" : "已删除待办。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "操作结果不确定");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="personal-calendar-panel" aria-label="已连接的日历">
      <div className="personal-calendar-header">
        <div>
          <strong>Google 账户与列表</strong>
          <small>选择已有日历与待办列表，事项仍保存在原账户。</small>
        </div>
        <button disabled={busy} onClick={() => setRefresh((v) => v + 1)}>
          刷新账号
        </button>
      </div>
      {!accounts.length && <p>尚无已连接账号，请先完成上方 Google 授权。</p>}
      {accounts.map((a, i) => (
        <button
          key={a.account_id}
          disabled={busy}
          onClick={() => void load(a.account_id)}
          aria-pressed={accountId === a.account_id}
        >
          {a.display_label || `Google 账号 ${i + 1}`} ·{" "}
          {a.calendar_write ? "日历可写" : "日历只读"} ·{" "}
          {a.tasks_write ? "待办可写" : "待办未授权"}
        </button>
      ))}
      {accountId &&
        (!accounts.find((a) => a.account_id === accountId)?.calendar_write ||
          !accounts.find((a) => a.account_id === accountId)?.tasks_write) && (
          <div className="personal-calendar-upgrade">
            <p>
              这个账号沿用旧只读授权。升级后仍保留原账号和已选日历，请在浏览器中选同一个
              Google 账号。
            </p>
            <button disabled={busy} onClick={() => onUpgrade(accountId)}>
              升级此账号权限
            </button>
          </div>
        )}
      {!!calendars.length && <h4>日历</h4>}
      {calendars.map((item) => (
        <div className="desktop-settings-connection-row" key={item.calendar.id}>
          <label>
            <input
              type="checkbox"
              checked={item.selected}
              disabled={busy}
              onChange={(e) => void select(item, e.target.checked)}
            />
            {item.calendar.title}{" "}
            {item.calendar.timezone && `（${item.calendar.timezone}）`}
            {!["writer", "owner"].includes(item.calendar.access_role) &&
              " · 只读"}
          </label>
          <button
            disabled={busy || !item.selected}
            onClick={() => void query(item)}
          >
            查看未来 7 天
          </button>
          {item.selected &&
            ["writer", "owner"].includes(item.calendar.access_role) &&
            accounts.find((a) => a.account_id === accountId)
              ?.calendar_write && (
              <button
                disabled={busy}
                onClick={() => void setDefault("calendar", item.calendar.id)}
              >
                {destinations.some(
                  (d) =>
                    d.kind === "calendar" &&
                    d.provider === "google" &&
                    d.account_id === accountId &&
                    d.collection_id === item.calendar.id,
                )
                  ? "默认日历 ✓"
                  : "设为默认日历"}
              </button>
            )}
        </div>
      ))}
      {calendarId &&
        accounts.find((a) => a.account_id === accountId)?.calendar_write &&
        calendars.some(
          (item) =>
            item.calendar.id === calendarId &&
            item.selected &&
            ["owner", "writer"].includes(item.calendar.access_role),
        ) && (
          <form
            className="personal-calendar-form"
            key={editingEvent?.id ?? "new-event"}
            onSubmit={(event) => void saveEvent(event)}
          >
            <strong>{editingEvent ? "修改日程" : "添加日程"}</strong>
            <label>
              标题
              <input
                name="title"
                maxLength={200}
                required
                defaultValue={editingEvent?.title ?? ""}
              />
            </label>
            <label>
              开始
              <input
                name="start"
                type="datetime-local"
                required
                defaultValue={localDateTime(editingEvent?.start?.timestamp)}
              />
            </label>
            <label>
              结束
              <input
                name="end"
                type="datetime-local"
                required
                defaultValue={localDateTime(editingEvent?.end?.timestamp)}
              />
            </label>
            <div className="personal-calendar-actions">
              <button disabled={busy} type="submit">
                {editingEvent ? "保存修改" : "添加到日历"}
              </button>
              {editingEvent && (
                <button type="button" onClick={() => setEditingEvent(null)}>
                  取消编辑
                </button>
              )}
            </div>
          </form>
        )}
      {busy && <p role="status">正在读取或保存…</p>}
      {notice && <p role="status">{notice}</p>}
      <ul>
        {events.map((e) => (
          <li key={e.id}>
            <strong>{e.title || "无标题日程"}</strong> ·{" "}
            {e.start?.day
              ? `${e.start.day}（全天）`
              : e.start?.timestamp
                ? new Date(e.start.timestamp).toLocaleString()
                : "时间未提供"}
            {e.end?.timestamp &&
              ` — ${new Date(e.end.timestamp).toLocaleString()}`}
            {e.etag &&
              e.event_type === "default" &&
              accounts.find((a) => a.account_id === accountId)
                ?.calendar_write &&
              !e.start?.day && (
                <span className="personal-calendar-actions">
                  <button disabled={busy} onClick={() => setEditingEvent(e)}>
                    编辑
                  </button>
                  <button disabled={busy} onClick={() => void removeEvent(e)}>
                    删除
                  </button>
                </span>
              )}
          </li>
        ))}
      </ul>
      {!!tasklists.length && <h4>待办列表</h4>}
      {tasklists.map((list) => (
        <div className="desktop-settings-connection-row" key={list.tasklist.id}>
          <label>
            <input
              type="checkbox"
              checked={list.selected}
              disabled={busy}
              onChange={(event) =>
                void selectTasklist(list.tasklist.id, event.target.checked)
              }
            />
            {list.tasklist.title || "未命名列表"}
          </label>
          <button
            disabled={busy || !list.selected}
            aria-pressed={listId === list.tasklist.id}
            onClick={() => void queryTasks(list.tasklist.id)}
          >
            查看待办
          </button>
          {list.selected && (
            <button
              disabled={busy}
              onClick={() => void setDefault("reminder", list.tasklist.id)}
            >
              {destinations.some(
                (d) =>
                  d.kind === "reminder" &&
                  d.provider === "google" &&
                  d.account_id === accountId &&
                  d.collection_id === list.tasklist.id,
              )
                ? "默认待办 ✓"
                : "设为默认待办"}
            </button>
          )}
        </div>
      ))}
      {listId && (
        <>
          <p>
            Google Tasks 只保留到期日期，无法通过公开 API
            设置几点提醒。精确时间请另设桌宠提醒。
          </p>
          {uncertainTaskWrite && (
            <button type="button" onClick={() => setUncertainTaskWrite(false)}>
              已在 Google Tasks 核对，继续操作
            </button>
          )}
          <form
            className="personal-calendar-form"
            key={editingTask?.id ?? "new-task"}
            onSubmit={(event) => void saveTask(event)}
          >
            <strong>{editingTask ? "修改待办" : "添加待办"}</strong>
            <label>
              内容
              <input
                name="title"
                maxLength={200}
                required
                defaultValue={editingTask?.title ?? ""}
              />
            </label>
            <label>
              日期（可选）
              <input
                name="due"
                type="date"
                defaultValue={editingTask?.due ?? ""}
              />
            </label>
            <div className="personal-calendar-actions">
              <button disabled={busy} type="submit">
                {editingTask ? "保存修改" : "添加到列表"}
              </button>
              {editingTask && (
                <button type="button" onClick={() => setEditingTask(null)}>
                  取消编辑
                </button>
              )}
            </div>
          </form>
          <ul>
            {tasks.map((task) => (
              <li key={task.id}>
                <strong>
                  {task.status === "completed" ? "✓ " : ""}
                  {task.title || "无标题待办"}
                </strong>
                {task.due && ` · ${task.due}`}
                {task.etag && (
                  <span className="personal-calendar-actions">
                    <button
                      disabled={busy}
                      onClick={() => setEditingTask(task)}
                    >
                      编辑
                    </button>
                    {task.status !== "completed" && (
                      <button
                        disabled={busy}
                        onClick={() => void changeTask(task, "complete")}
                      >
                        完成
                      </button>
                    )}
                    <button
                      disabled={busy}
                      onClick={() => void changeTask(task, "delete")}
                    >
                      删除
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
