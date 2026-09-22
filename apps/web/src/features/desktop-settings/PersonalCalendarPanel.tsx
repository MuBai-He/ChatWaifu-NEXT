import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { requestRuntime } from "../chat/runtime-client/http";

const accountSchema = z.array(
  z.object({ account_id: z.string(), status: z.string() }),
);
const calendarsSchema = z.object({
  items: z.array(
    z.object({
      calendar: z.object({
        id: z.string(),
        title: z.string(),
        timezone: z.string().nullable(),
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
      title: z.string().nullable(),
      start: timeSchema,
      end: timeSchema,
    }),
  ),
});
type Calendar = z.infer<typeof calendarsSchema>["items"][number];
type Event = z.infer<typeof eventsSchema>["items"][number];

export function PersonalCalendarPanel({ sessionId }: { sessionId: string }) {
  const [accounts, setAccounts] = useState<z.infer<typeof accountSchema>>([]);
  const [accountId, setAccountId] = useState("");
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const active = useRef<AbortController | null>(null);
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
        setNotice(
          result.items.length
            ? "勾选允许查询的日历；默认不会选择任何日历。"
            : "这个账号没有可用日历。",
        );
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
  return (
    <section className="personal-calendar-panel" aria-label="已连接的日历">
      <button disabled={busy} onClick={() => setRefresh((v) => v + 1)}>
        刷新账号
      </button>
      {!accounts.length && <p>尚无已连接账号，请先完成上方 Google 授权。</p>}
      {accounts.map((a, i) => (
        <button
          key={a.account_id}
          disabled={busy}
          onClick={() => void load(a.account_id)}
        >
          读取 Google 账号 {i + 1} 的日历
        </button>
      ))}
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
          </label>
          <button
            disabled={busy || !item.selected}
            onClick={() => void query(item)}
          >
            查看未来 7 天
          </button>
        </div>
      ))}
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
          </li>
        ))}
      </ul>
    </section>
  );
}
