import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { z } from "zod";
import { requestRuntime } from "../chat/runtime-client/http";
import {
  organizerRequest,
  type Organizer,
  type WriteDestination,
} from "./organizer";
import "./organizer.css";

const accountsSchema = z.array(
  z.object({
    account_id: z.string(),
    status: z.string(),
    tasks_write: z.boolean().default(false),
    display_label: z.string().nullable().optional(),
  }),
);
const calendarsSchema = z.object({
  items: z.array(
    z.object({
      calendar: z.object({ id: z.string(), title: z.string() }),
      selected: z.boolean(),
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
const eventSchema = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      title: z.string().nullable(),
      start: z
        .object({
          day: z.string().nullable(),
          timestamp: z.string().nullable(),
        })
        .nullable(),
      end: z
        .object({
          day: z.string().nullable(),
          timestamp: z.string().nullable(),
        })
        .nullable(),
    }),
  ),
});
const tasksSchema = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      title: z.string(),
      due: z.string().nullable(),
      status: z.enum(["needsAction", "completed"]),
    }),
  ),
});
const destinationsSchema = z.object({
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
const receiptSchema = z
  .object({ state: z.string(), provider: z.string() })
  .passthrough();

type AgendaEntry = {
  key: string;
  type: "日程" | "待办" | "提醒" | "闹钟";
  title: string;
  when: number | null;
  detail: string;
  source: string;
  completed?: boolean;
};

function windowFor(days: number) {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + days);
  return { start, end };
}
function dueTime(value: string | null) {
  return value ? new Date(`${value}T00:00:00`).getTime() : null;
}
function localDateTime(value: Date) {
  return new Date(value.getTime() - value.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}
function formText(form: FormData, name: string) {
  const value = form.get(name);
  return typeof value === "string" ? value : "";
}

function appleReadSources(
  devices: Organizer["devices"],
  defaults: WriteDestination[],
) {
  const available = devices.flatMap((device) =>
    device.sources.map((source) => ({ device, source })),
  );
  const selected: typeof available = [];
  const add = (entry: (typeof available)[number] | undefined) => {
    if (entry && selected.length < 8 && !selected.includes(entry))
      selected.push(entry);
  };
  for (const destination of defaults.filter(
    (item) => item.provider === "apple",
  ))
    add(
      available.find(
        ({ device, source }) =>
          device.device_id === destination.device_id &&
          source.id === destination.collection_id &&
          source.resource === destination.kind,
      ),
    );
  add(available.find(({ source }) => source.resource === "calendar"));
  add(available.find(({ source }) => source.resource === "reminder"));
  for (const entry of available) add(entry);
  return { selected, total: available.length };
}

export function AgendaOverview({ sessionId }: { sessionId: string }) {
  const [days, setDays] = useState<1 | 7 | 30>(7);
  const [entries, setEntries] = useState<AgendaEntry[]>([]);
  const [organizer, setOrganizer] = useState<Organizer | null>(null);
  const [defaults, setDefaults] = useState<WriteDestination[]>([]);
  const [appleReads, setAppleReads] = useState<
    { id: string; source: string; resource: string }[]
  >([]);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [createKind, setCreateKind] = useState<"calendar" | "reminder">(
    "calendar",
  );
  const pendingCreate = useRef<{ key: string; id: string } | null>(null);
  const [uncertainCreate, setUncertainCreate] = useState(false);
  const [dismissing, setDismissing] = useState<string | null>(null);

  const dismissHistory = async (deliveryId: string) => {
    setDismissing(deliveryId);
    try {
      await organizerRequest(`/organizer/history/${deliveryId}/dismiss`, {
        session_id: sessionId,
      });
      setOrganizer((current) =>
        current
          ? {
              ...current,
              history: current.history?.filter(
                (item) => item.delivery_id !== deliveryId,
              ),
            }
          : current,
      );
      setNotice("");
    } catch {
      setNotice("未能清除这条记录，请恢复连接后重试。");
    } finally {
      setDismissing(null);
    }
  };

  const load = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      setNotice("");
      const { start, end } = windowFor(days);
      const suffix = `session_id=${encodeURIComponent(sessionId)}`;
      const [accountsResult, organizerResult, defaultsResult] =
        await Promise.allSettled([
          requestRuntime(
            `/v1/personal-assistant/accounts?${suffix}`,
            accountsSchema,
            { signal },
          ),
          organizerRequest<Organizer>(
            `/organizer?${suffix}`,
            undefined,
            undefined,
            signal,
          ),
          requestRuntime(
            `/v1/personal-assistant/destinations?${suffix}`,
            destinationsSchema,
            { signal },
          ),
        ]);
      if (signal.aborted) return;
      const accounts =
        accountsResult.status === "fulfilled" ? accountsResult.value : [];
      const current =
        organizerResult.status === "fulfilled" ? organizerResult.value : null;
      if (current) setOrganizer(current);
      if (defaultsResult.status === "fulfilled")
        setDefaults(defaultsResult.value.items);
      const items: AgendaEntry[] = [];
      if (current)
        for (const task of current.tasks) {
          if (task.state === "cancelled") continue;
          const when = task.next_due * 1000;
          if (when >= end.getTime()) continue;
          items.push({
            key: `local:${task.request_id}`,
            type: task.kind === "alarm" ? "闹钟" : "提醒",
            title: task.title,
            when,
            source: "ChatWaifu · 桌宠提醒",
            detail: task.state,
          });
        }
      const queries: Promise<AgendaEntry[]>[] = [];
      let sourceFailed = defaultsResult.status === "rejected";
      for (const account of accounts.filter((a) => a.status === "connected")) {
        try {
          const calendars = await requestRuntime(
            `/v1/personal-assistant/calendars?${suffix}&account_id=${encodeURIComponent(account.account_id)}`,
            calendarsSchema,
            { signal },
          );
          for (const selected of calendars.items
            .filter((item) => item.selected)
            .slice(0, 8)) {
            const params = new URLSearchParams({
              session_id: sessionId,
              account_id: account.account_id,
              calendar_id: selected.calendar.id,
              start: start.toISOString(),
              end: end.toISOString(),
            });
            queries.push(
              requestRuntime(
                `/v1/personal-assistant/events?${params}`,
                eventSchema,
                { signal, timeoutMs: 130000 },
              ).then((response) =>
                response.items.map((event) => ({
                  key: `google:event:${account.account_id}:${selected.calendar.id}:${event.id}`,
                  type: "日程" as const,
                  title: event.title || "无标题日程",
                  when: event.start?.timestamp
                    ? Date.parse(event.start.timestamp)
                    : dueTime(event.start?.day ?? null),
                  source: `Google · ${account.display_label || account.account_id.slice(0, 8)} · ${selected.calendar.title}`,
                  detail: event.start?.day
                    ? "全天"
                    : event.end?.timestamp
                      ? new Date(event.end.timestamp).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "",
                })),
              ),
            );
          }
          if (account.tasks_write) {
            const lists = await requestRuntime(
              `/v1/personal-assistant/google-tasklists?${suffix}&account_id=${encodeURIComponent(account.account_id)}`,
              tasklistsSchema,
              { signal },
            );
            for (const selected of lists.items
              .filter((item) => item.selected)
              .slice(0, 8)) {
              const params = new URLSearchParams({
                session_id: sessionId,
                account_id: account.account_id,
                list_id: selected.tasklist.id,
              });
              queries.push(
                requestRuntime(
                  `/v1/personal-assistant/google-tasks?${params}`,
                  tasksSchema,
                  { signal, timeoutMs: 130000 },
                ).then((response) =>
                  response.items
                    .filter((task) => {
                      const due = dueTime(task.due);
                      return (
                        task.status !== "completed" &&
                        (due === null || due < end.getTime())
                      );
                    })
                    .map((task) => ({
                      key: `google:task:${account.account_id}:${selected.tasklist.id}:${task.id}`,
                      type: "待办" as const,
                      title: task.title,
                      when: dueTime(task.due),
                      source: `Google Tasks · ${account.display_label || account.account_id.slice(0, 8)} · ${selected.tasklist.title}`,
                      detail: "待完成",
                    })),
                ),
              );
            }
          }
        } catch {
          // A disconnected source must not hide available sources from other accounts.
          sourceFailed = true;
        }
      }
      const results = await Promise.allSettled(queries);
      if (signal.aborted) return;
      for (const result of results)
        if (result.status === "fulfilled") items.push(...result.value);
      setEntries(items);
      if (
        sourceFailed ||
        results.some((result) => result.status === "rejected") ||
        accountsResult.status === "rejected" ||
        organizerResult.status === "rejected"
      ) {
        setNotice("部分来源暂时无法读取；已显示成功返回的事项。");
      }
      if (current) {
        const { selected, total } = appleReadSources(
          current.devices,
          defaultsResult.status === "fulfilled"
            ? defaultsResult.value.items
            : [],
        );
        const readResults = await Promise.allSettled(
          selected.map(async ({ device, source }) => {
            const id = crypto.randomUUID();
            await organizerRequest("/apple/operations", {
              session_id: sessionId,
              operation: {
                request_id: id,
                device_id: device.device_id,
                resource: source.resource,
                action: "list",
                calendar_id: source.id,
                ...(source.resource === "calendar"
                  ? { start: start.toISOString(), end: end.toISOString() }
                  : {}),
              },
            });
            return {
              id,
              source: `Apple · ${source.title}`,
              resource: source.resource,
            };
          }),
        );
        if (!signal.aborted) {
          setAppleReads(
            readResults.flatMap((r) =>
              r.status === "fulfilled" ? [r.value] : [],
            ),
          );
          if (selected.length < total)
            setNotice(
              "Apple 来源超过 8 个；本次优先读取默认来源，并兼顾日历与提醒事项。可在设备设置中调整。",
            );
        }
      }
      if (!signal.aborted) setLoading(false);
    },
    [days, sessionId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve()
      .then(() => load(controller.signal))
      .catch((error) => {
        if (!controller.signal.aborted) {
          setNotice(
            error instanceof Error ? error.message : "今日安排读取失败",
          );
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [load, refresh]);

  useEffect(() => {
    if (!appleReads.length) return;
    let disposed = false;
    const rangeEnd = windowFor(days).end.getTime();
    const poll = async () => {
      try {
        const current = await organizerRequest<Organizer>(
          `/organizer?session_id=${encodeURIComponent(sessionId)}`,
        );
        if (disposed) return;
        setOrganizer(current);
        const fresh: AgendaEntry[] = [];
        for (const read of appleReads) {
          const operation = current.operations.find(
            (item) => item.operation_id === read.id,
          );
          if (operation?.state !== "succeeded") continue;
          for (const [index, item] of (
            operation.result.items ?? []
          ).entries()) {
            const when = item.start ?? item.due ?? null;
            if (
              read.resource === "reminder" &&
              (item.completed || (when !== null && when * 1000 >= rangeEnd))
            )
              continue;
            fresh.push({
              key: `apple:${read.id}:${item.id}:${item.start ?? item.due ?? "undated"}:${index}`,
              type: read.resource === "calendar" ? "日程" : "待办",
              title: item.title,
              when: when === null ? null : when * 1000,
              source: read.source,
              detail: item.completed ? "已完成" : "",
              completed: item.completed,
            });
          }
        }
        setEntries((old) => [
          ...old.filter((item) => !item.key.startsWith("apple:")),
          ...fresh,
        ]);
        if (
          appleReads.every((read) => {
            const state = current.operations.find(
              (item) => item.operation_id === read.id,
            )?.state;
            return state && !["queued", "leased"].includes(state);
          })
        ) {
          window.clearInterval(timer);
          if (
            appleReads.some(
              (read) =>
                current.operations.find((item) => item.operation_id === read.id)
                  ?.state !== "succeeded",
            )
          )
            setNotice("部分 Apple 来源读取失败；请在设备设置中核对连接。");
          else if (
            appleReads.some(
              (read) =>
                current.operations.find((item) => item.operation_id === read.id)
                  ?.result.truncated,
            )
          )
            setNotice(
              "部分 Apple 来源超过单次读取上限；请缩短范围或在原日历查看。",
            );
        }
      } catch {
        if (!disposed)
          setNotice("Apple 设备读取暂时不可用，其他来源仍可查看。");
      }
    };
    const timer = window.setInterval(() => void poll(), 5000);
    void poll();
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [appleReads, days, sessionId]);

  const visible = useMemo(
    () =>
      [...entries].sort((a, b) => (a.when ?? Infinity) - (b.when ?? Infinity)),
    [entries],
  );
  const applePending = appleReads.some(({ id }) => {
    const state = organizer?.operations.find(
      (item) => item.operation_id === id,
    )?.state;
    return !state || state === "queued" || state === "leased";
  });
  const defaultTarget = defaults.find((item) => item.kind === createKind);
  async function createItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!defaultTarget) {
      setNotice("请先选择默认日历或待办列表。");
      return;
    }
    if (uncertainCreate) {
      setNotice("请先到原账户核对上次写入结果，再确认继续。");
      return;
    }
    const form = new FormData(event.currentTarget);
    const title = formText(form, "title").trim();
    const start = formText(form, "start");
    const end = formText(form, "end");
    const due = formText(form, "due");
    const key = JSON.stringify({
      sessionId,
      createKind,
      defaultTarget,
      title,
      start,
      end,
      due,
    });
    const requestId =
      pendingCreate.current?.key === key
        ? pendingCreate.current.id
        : crypto.randomUUID();
    pendingCreate.current = { key, id: requestId };
    setLoading(true);
    try {
      const result = await requestRuntime(
        "/v1/personal-assistant/agenda/items",
        receiptSchema,
        {
          method: "POST",
          timeoutMs: 30000,
          body: JSON.stringify({
            session_id: sessionId,
            request_id: requestId,
            kind: createKind,
            title,
            ...(createKind === "calendar"
              ? {
                  start: new Date(start).toISOString(),
                  end: new Date(end).toISOString(),
                }
              : defaultTarget.provider === "google"
                ? { due_date: due || null }
                : { due_at: due ? new Date(due).toISOString() : null }),
          }),
        },
      );
      setNotice(
        result.state === "queued"
          ? "已发送到 Apple 设备，等待执行结果。"
          : "已保存到原账户。",
      );
      pendingCreate.current = null;
      setUncertainCreate(false);
      setRefresh((value) => value + 1);
    } catch (error) {
      setUncertainCreate(true);
      setNotice(
        `${error instanceof Error ? error.message : "写入结果不确定"}。请先在原账户核对，避免重复创建。`,
      );
      setLoading(false);
    }
  }

  return (
    <section className="agenda-overview" aria-label="今日与未来安排">
      <div className="agenda-overview-header">
        <div>
          <h3>安排</h3>
          <p>日程、待办和桌宠闹钟按时间排在一起。</p>
        </div>
        <button
          disabled={loading}
          onClick={() => setRefresh((value) => value + 1)}
        >
          更新
        </button>
      </div>
      <div
        className="agenda-overview-filters"
        role="group"
        aria-label="查看日期范围"
      >
        {(
          [
            [1, "今天"],
            [7, "未来 7 天"],
            [30, "未来 30 天"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            aria-pressed={days === value}
            onClick={() => setDays(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {notice && (
        <p role="status" className="agenda-overview-notice">
          {notice}
        </p>
      )}
      {applePending && (
        <p role="status" className="agenda-overview-notice">
          Apple 日历与提醒事项仍在从配对设备读取，列表会继续更新。
        </p>
      )}
      {!!organizer?.history?.length && (
        <section className="agenda-inbox" aria-label="未处理提醒">
          <div className="agenda-inbox-heading">
            <strong>未处理提醒</strong>
            <small>不会自动补响，也不会修改原日程或待办</small>
          </div>
          {organizer.history.slice(0, 10).map((item) => (
            <article key={item.delivery_id}>
              <div>
                <strong>
                  {item.title || (item.kind === "alarm" ? "闹钟" : "提醒")}
                </strong>
                <small>
                  {item.state === "missed"
                    ? "设备未确认送达"
                    : "已展示，未处理"}
                  {" · "}
                  {new Date(item.due * 1000).toLocaleString()}
                </small>
              </div>
              <button
                type="button"
                disabled={dismissing === item.delivery_id}
                onClick={() => void dismissHistory(item.delivery_id)}
              >
                知道了
              </button>
            </article>
          ))}
          {organizer.history.length > 10 && (
            <small>另有 {organizer.history.length - 10} 条，请逐批处理。</small>
          )}
        </section>
      )}
      {loading && <p role="status">正在汇总来源…</p>}
      <div className="agenda-overview-list">
        {visible.length
          ? visible.map((item) => (
              <article
                key={item.key}
                className={item.completed ? "agenda-completed" : ""}
              >
                <time>
                  {item.when === null
                    ? "无日期"
                    : new Date(item.when).toLocaleString([], {
                        month: "numeric",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                </time>
                <div>
                  <strong>{item.title}</strong>
                  <small>
                    {item.source}
                    {item.detail ? ` · ${item.detail}` : ""}
                  </small>
                </div>
                <span>{item.type}</span>
              </article>
            ))
          : !loading && !applePending && <p>这个范围内暂无已读取的安排。</p>}
      </div>
      <form
        className="agenda-overview-create"
        onSubmit={(event) => void createItem(event)}
      >
        <strong>添加到原账户</strong>
        <label>
          类型
          <select
            value={createKind}
            onChange={(event) =>
              setCreateKind(event.target.value as "calendar" | "reminder")
            }
          >
            <option value="calendar">日程</option>
            <option value="reminder">待办</option>
          </select>
        </label>
        <label>
          内容
          <input
            name="title"
            required
            maxLength={200}
            placeholder="例如：下周见面"
          />
        </label>
        {createKind === "calendar" ? (
          <>
            <label>
              开始
              <input
                name="start"
                type="datetime-local"
                required
                defaultValue={localDateTime(new Date())}
              />
            </label>
            <label>
              结束
              <input name="end" type="datetime-local" required />
            </label>
          </>
        ) : (
          <label>
            {defaultTarget?.provider === "google"
              ? "日期（可选）"
              : "提醒时间（可选）"}
            <input
              name="due"
              type={
                defaultTarget?.provider === "google" ? "date" : "datetime-local"
              }
            />
          </label>
        )}
        <div className="agenda-overview-create-footer">
          <small>
            {defaultTarget
              ? `${defaultTarget.provider === "google" ? "Google" : "Apple"} · 已选默认写入位置`
              : "请先在下方账户或设备设置中选择默认写入位置"}
          </small>
          <button
            disabled={loading || !defaultTarget || uncertainCreate}
            type="submit"
          >
            添加
          </button>
        </div>
      </form>
      {uncertainCreate && (
        <button
          type="button"
          onClick={() => {
            pendingCreate.current = null;
            setUncertainCreate(false);
          }}
        >
          已在原账户核对，继续添加
        </button>
      )}
      {organizer &&
        appleReads.some((read) =>
          organizer.operations.some(
            (item) =>
              item.operation_id === read.id &&
              ["queued", "leased"].includes(item.state),
          ),
        ) && <p>Apple 设备仍在读取，结果会自动出现。</p>}
    </section>
  );
}
