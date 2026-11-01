import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ActivityEntry,
  StandingIntent,
  WorkItem,
  StandingIntentInput,
} from "@chatwaifu/protocol";
import {
  actStandingIntent,
  createStandingIntent,
  getHarnessActivity,
  getHarnessActivitySnapshot,
  getHarnessReview,
  decideHarnessReview,
  getHarnessSources,
  getHarnessWork,
  getStandingIntents,
  reviewHarnessWork,
  readHarnessEvidence,
  updateStandingIntent,
  linkHarnessWork,
} from "../chat/runtime-client/harnessClient";

export function AgentHarnessPanel({ sessionId }: { sessionId: string }) {
  const [intents, setIntents] = useState<StandingIntent[]>([]);
  const [work, setWork] = useState<WorkItem[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [sources, setSources] = useState<
    Awaited<ReturnType<typeof getHarnessSources>>
  >({
    rooms: [],
    skills: [],
    calendar_ids: [],
    tasklist_ids: [],
    resources: [],
    devices: [],
  });
  const [review, setReview] = useState<
    Awaited<ReturnType<typeof getHarnessReview>>["items"]
  >([]);
  const intentDetails = useRef<HTMLDetailsElement>(null);
  const [scheduleAt, setScheduleAt] = useState("");
  const [repeat, setRepeat] = useState<"none" | "daily" | "weekly">("none");
  const [observeCalendar, setObserveCalendar] = useState(false);
  const [editedDue, setEditedDue] = useState("");
  const [editedStart, setEditedStart] = useState("");
  const [editedEnd, setEditedEnd] = useState("");
  const [editedReminder, setEditedReminder] = useState("");
  const [editedDevice, setEditedDevice] = useState("");
  const [editingIntent, setEditingIntent] = useState<StandingIntent | null>(
    null,
  );
  const [destinations, setDestinations] = useState<string[]>([]);
  const [editedInvolvement, setEditedInvolvement] = useState<
    "self" | "other" | "unclear"
  >("unclear");
  const [olderActivity, setOlderActivity] = useState<ActivityEntry[]>([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [goal, setGoal] = useState("");
  const [rooms, setRooms] = useState<string[]>([]);
  const [skills, setSkills] = useState<string[]>([]);
  const [writes, setWrites] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | null>(null);
  const [editedTitle, setEditedTitle] = useState("");
  const [editedDetails, setEditedDetails] = useState("");
  const [evidence, setEvidence] = useState<string>("");
  const activityEpoch = useRef("");
  const refreshInFlight = useRef(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  const inFlight = useRef(false);
  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    try {
      const values = await Promise.all([
        getStandingIntents(sessionId),
        getHarnessWork(sessionId),
        getHarnessActivitySnapshot(sessionId),
        getHarnessSources(sessionId),
        getHarnessReview(sessionId),
      ]);
      if (!active.current) return;
      setIntents(values[0]);
      setWork(values[1]);
      if (
        activityEpoch.current &&
        activityEpoch.current !== values[2].context_epoch
      ) {
        setOlderActivity([]);
        setEvidence("");
      }
      activityEpoch.current = values[2].context_epoch;
      setActivity(values[2].items);
      setSources(values[3]);
      setReview(values[4].items);
      setError("");
    } finally {
      refreshInFlight.current = false;
      if (active.current) setLoading(false);
    }
  }, [sessionId]);
  useEffect(() => {
    active.current = true;
    const update = () =>
      void refresh().catch((e) => {
        if (active.current)
          setError(e instanceof Error ? e.message : "连接未完成，请重试");
      });
    update();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !inFlight.current) update();
    }, 5000);
    return () => {
      active.current = false;
      window.clearInterval(timer);
    };
  }, [refresh]);
  const run = async (action: () => Promise<unknown>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
      await refresh();
    } catch (e) {
      if (active.current)
        setError(
          e instanceof Error
            ? `${e.message}；请刷新后核对来源、账户与当前版本。`
            : "操作未完成，请刷新后重试",
        );
    } finally {
      inFlight.current = false;
      if (active.current) setBusy(false);
    }
  };
  const toggle = (values: string[], value: string) =>
    values.includes(value)
      ? values.filter((v) => v !== value)
      : [...values, value];
  const pending = work.filter(
    (w) => w.acceptance === "pending" && w.state === "proposed",
  );
  return (
    <section aria-label="宁宁持续协助" className="agent-harness-panel">
      <h3>持续协助</h3>
      <p>
        指定宁宁持续关注的资料和可以使用的能力。明确的范围内操作自动完成，信息不完整时再向你核对。
      </p>
      {error && <p role="alert">{error}</p>}
      <button disabled={busy} onClick={() => void run(() => Promise.resolve())}>
        刷新状态
      </button>
      {loading && <p role="status">正在读取持续协助状态…</p>}
      {evidence && (
        <div role="region" aria-label="消息依据">
          <pre style={{ whiteSpace: "pre-wrap" }}>{evidence}</pre>
          <button onClick={() => setEvidence("")}>关闭依据</button>
        </div>
      )}
      <details ref={intentDetails}>
        <summary>{editingIntent ? "调整持续委托" : "添加持续委托"}</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const input = {
                session_id: sessionId,
                goal,
                room_ids: rooms,
                allowed_skill_ids: skills,
                calendar_ids: destinations.filter((id) =>
                  sources.calendar_ids.includes(id),
                ),
                tasklist_ids: destinations.filter((id) =>
                  sources.tasklist_ids.includes(id),
                ),
                observe_calendar: observeCalendar,
                schedule: scheduleAt
                  ? {
                      first_wake_at: new Date(scheduleAt).toISOString(),
                      repeat,
                      timezone:
                        Intl.DateTimeFormat().resolvedOptions().timeZone,
                      kind: editingIntent?.schedule?.kind ?? "run",
                      title: editingIntent?.schedule?.title ?? null,
                    }
                  : null,
                resource_roots: editingIntent?.resource_roots ?? ["."],
                expires_at: editingIntent?.expires_at ?? null,
                allowed_operations: editingIntent?.allowed_operations ?? [
                  "create",
                  "update",
                  "complete",
                  "cancel",
                ],
                max_tool_calls: editingIntent?.max_tool_calls ?? 20,
                max_active_seconds: editingIntent?.max_active_seconds ?? 300,
                allow_writes: writes,
                shadow: true,
              } as StandingIntentInput;
              if (editingIntent)
                await updateStandingIntent(editingIntent.intent_id, {
                  ...input,
                  expected_revision: editingIntent.revision,
                });
              else await createStandingIntent(input);
              if (active.current) {
                setGoal("");
                setEditingIntent(null);
              }
            });
          }}
        >
          <label>
            希望宁宁持续负责什么
            <textarea
              required
              value={goal}
              maxLength={2000}
              onChange={(e) => setGoal(e.target.value)}
            />
          </label>
          <fieldset disabled={busy}>
            <legend>允许关注的来源</legend>
            {!sources.rooms.length && (
              <p>可同步手机资料，也可使用定时检查与日历变化。</p>
            )}
            {sources.rooms.map((room) => (
              <label key={room.room_id}>
                <input
                  type="checkbox"
                  checked={rooms.includes(room.room_id)}
                  onChange={() => setRooms(toggle(rooms, room.room_id))}
                />
                {room.label}
              </label>
            ))}
          </fieldset>
          <fieldset disabled={busy}>
            <legend>可以使用的能力</legend>
            {sources.skills.map((skill) => (
              <label key={skill.skill_id}>
                <input
                  type="checkbox"
                  checked={skills.includes(skill.skill_id)}
                  onChange={() => setSkills(toggle(skills, skill.skill_id))}
                />
                {skill.name}
              </label>
            ))}
          </fieldset>
          <fieldset disabled={busy}>
            <legend>允许使用的日历和待办列表</legend>
            {!sources.resources.length && (
              <p>
                还没有可用目标，请先在日历与提醒设置中连接 Google
                账号并选择资源。
              </p>
            )}
            {sources.resources.map((resource) => (
              <label key={resource.id}>
                <input
                  type="checkbox"
                  checked={destinations.includes(resource.id)}
                  onChange={() =>
                    setDestinations(toggle(destinations, resource.id))
                  }
                />
                {resource.label} ·{" "}
                {resource.kind === "calendar" ? "日历" : "待办"}
              </label>
            ))}
          </fieldset>
          <label>
            定时检查（可选）
            <input
              type="datetime-local"
              value={scheduleAt}
              onChange={(e) => setScheduleAt(e.target.value)}
            />
          </label>
          {scheduleAt && (
            <label>
              重复
              <select
                value={repeat}
                onChange={(e) => setRepeat(e.target.value as typeof repeat)}
              >
                <option value="none">仅一次</option>
                <option value="daily">每天</option>
                <option value="weekly">每周</option>
              </select>
            </label>
          )}
          <label>
            <input
              type="checkbox"
              checked={observeCalendar}
              onChange={(e) => setObserveCalendar(e.target.checked)}
            />
            同时关注所选日历的变化
          </label>
          <label>
            <input
              type="checkbox"
              checked={writes}
              onChange={(e) => setWrites(e.target.checked)}
            />
            允许在所选日历和待办列表中创建、修改、完成或取消个人事项
          </label>
          <p>先观察决策和依据，核对后可开启自动执行。可随时暂停或撤销。</p>
          <button
            disabled={
              busy ||
              !goal.trim() ||
              (!rooms.length && !destinations.length && !scheduleAt) ||
              !skills.length ||
              (writes &&
                skills.includes("agenda.manage") &&
                !destinations.length)
            }
          >
            {editingIntent ? "保存调整" : "保存委托"}
          </button>
          {editingIntent && (
            <button
              type="button"
              onClick={() => {
                setEditingIntent(null);
                setGoal("");
              }}
            >
              取消调整
            </button>
          )}
        </form>
      </details>
      {!intents.length && <p>还没有持续委托。</p>}
      <ul>
        {intents.map((item) => (
          <li key={item.intent_id}>
            <strong>{item.goal}</strong>
            <p>
              关注来源：
              {(item.room_ids ?? [])
                .map(
                  (id) =>
                    sources.rooms.find((room) => room.room_id === id)?.label ??
                    "已移除的来源",
                )
                .join("、") || "定时或日历变化"}
            </p>
            <p>
              允许范围：{item.allow_writes ? "所选范围内可修改" : "只读"}；
              {[...(item.calendar_ids ?? []), ...(item.tasklist_ids ?? [])]
                .map(
                  (id) =>
                    sources.resources.find((resource) => resource.id === id)
                      ?.label ?? "未连接的资源",
                )
                .join("、") || "未指定外部日历或待办列表"}
            </p>
            <p>
              每轮最多 {item.max_tool_calls ?? 20} 次工具调用、
              {item.max_active_seconds ?? 300} 秒活动时间。
              {item.expires_at
                ? `有效至 ${new Date(item.expires_at).toLocaleString()}`
                : "持续有效，可随时暂停或撤销。"}
            </p>

            <p>
              {item.state === "revoked"
                ? "已撤销"
                : item.state === "paused"
                  ? "已暂停"
                  : item.shadow
                    ? "观察中"
                    : "自动执行已开启"}
            </p>
            {item.state !== "revoked" && (
              <div>
                <button
                  disabled={busy}
                  onClick={() => {
                    setEditingIntent(item);
                    setGoal(item.goal);
                    setRooms(item.room_ids ?? []);
                    setSkills(item.allowed_skill_ids);
                    setWrites(item.allow_writes ?? false);
                    setDestinations([
                      ...(item.calendar_ids ?? []),
                      ...(item.tasklist_ids ?? []),
                    ]);
                    setObserveCalendar(item.observe_calendar ?? false);
                    setScheduleAt(
                      item.schedule?.first_wake_at
                        ? localDateTime(item.schedule.first_wake_at)
                        : "",
                    );
                    setRepeat(item.schedule?.repeat ?? "none");
                    if (intentDetails.current)
                      intentDetails.current.open = true;
                  }}
                >
                  调整范围
                </button>
                {item.shadow && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(() =>
                        actStandingIntent(
                          sessionId,
                          item.intent_id,
                          item.revision,
                          "enable",
                        ),
                      )
                    }
                  >
                    开启自动执行
                  </button>
                )}
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      actStandingIntent(
                        sessionId,
                        item.intent_id,
                        item.revision,
                        item.state === "paused" ? "resume" : "pause",
                      ),
                    )
                  }
                >
                  {item.state === "paused" ? "恢复" : "暂停"}
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      actStandingIntent(
                        sessionId,
                        item.intent_id,
                        item.revision,
                        "revoke",
                      ),
                    )
                  }
                >
                  撤销委托
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      <details open>
        <summary>需要核对 · {pending.length + review.length}</summary>
        {review.map((item) => (
          <article key={item.kind + item.reference}>
            <strong>{item.title}</strong>
            <p>{item.text}</p>
            <p>{item.reason}</p>
            {item.evidence?.map((e, i) => (
              <blockquote key={i}>
                {e.text} · {new Date(e.occurred_at).toLocaleString()}
              </blockquote>
            ))}
            {(item.kind === "memory"
              ? [
                  { label: "确认保存", decision: "accept" as const },
                  { label: "拒绝保存", decision: "reject" as const },
                ]
              : [
                  { label: "保留本机修改", decision: "keep_local" as const },
                  { label: "采用服务端版本", decision: "keep_server" as const },
                ]
            ).map((option) => (
              <button
                key={option.decision}
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    decideHarnessReview(sessionId, item, option.decision),
                  )
                }
              >
                {option.label}
              </button>
            ))}
          </article>
        ))}
        {!pending.length && !review.length && <p>暂时没有需要你核对的内容。</p>}
        <ul>
          {pending.map((item) => (
            <li key={item.work_item_id}>
              <strong>{item.title}</strong>
              <p>{item.details}</p>
              {(item.temporal_status ?? "current") !== "current" && (
                <p>
                  历史核对：{item.temporal_reason} 确认后才表示现在仍需处理。
                </p>
              )}
              {item.ingestion_mode === "historical" && (
                <p>历史资料 · 不会追溯执行过去的动作</p>
              )}
              {item.start_date && (
                <p>
                  全天活动：{item.start_date} 至 {item.end_date}
                  （结束日期不包含在内）
                </p>
              )}
              {(item.starts_at || item.due_at) && (
                <p>
                  {item.starts_at ? "活动时间" : "截止时间"}：
                  {new Date(item.starts_at ?? item.due_at!).toLocaleString()}
                </p>
              )}
              {item.waiting_reason && <p>{item.waiting_reason}</p>}
              <p>
                依据 {item.evidence.length} 条来源 ·{" "}
                {new Date(item.updated_at).toLocaleString()}
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const rows = await Promise.all(
                      item.evidence
                        .filter((ref) => ref.kind === "knowledge")
                        .map((ref) =>
                          readHarnessEvidence(
                            sessionId,
                            ref.reference,
                            ref.revision ?? 1,
                          ),
                        ),
                    );
                    if (active.current)
                      setEvidence(
                        rows
                          .map((row) =>
                            [
                              row.sender_name,
                              new Date(row.occurred_at).toLocaleString(),
                              row.text,
                            ]
                              .filter(Boolean)
                              .join("\n"),
                          )
                          .join("\n\n"),
                      );
                  })
                }
              >
                查看依据
              </button>
              <button
                disabled={busy}
                onClick={() => {
                  setEditing(item.work_item_id);
                  setEditedTitle(item.title);
                  setEditedDetails(item.details ?? "");
                  setEditedInvolvement(item.involvement ?? "unclear");
                  setEditedDue(item.due_date ?? "");
                  setEditedStart(
                    item.start_date ?? localDateTime(item.starts_at),
                  );
                  setEditedEnd(item.end_date ?? localDateTime(item.ends_at));
                  setEditedReminder(localDateTime(item.remind_at));
                  setEditedDevice(item.primary_device_id ?? "");
                }}
              >
                修改内容
              </button>
              {editing === item.work_item_id && (
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void run(async () => {
                      await reviewHarnessWork(
                        sessionId,
                        item.work_item_id,
                        item.revision ?? 1,
                        "edit",
                        {
                          title: editedTitle,
                          details: editedDetails,
                          involvement: editedInvolvement,
                          due_date: editedDue || null,
                          start_date: item.start_date
                            ? editedStart || null
                            : null,
                          end_date: item.start_date ? editedEnd || null : null,
                          starts_at: item.start_date
                            ? null
                            : isoDate(editedStart),
                          ends_at: item.start_date ? null : isoDate(editedEnd),
                          remind_at: isoDate(editedReminder),
                          primary_device_id: editedDevice || null,
                        },
                      );
                      if (active.current) setEditing(null);
                    });
                  }}
                >
                  <label>
                    标题
                    <input
                      required
                      value={editedTitle}
                      maxLength={256}
                      onChange={(event) => setEditedTitle(event.target.value)}
                    />
                  </label>
                  <label>
                    完整内容
                    <textarea
                      value={editedDetails}
                      maxLength={2400}
                      onChange={(event) => setEditedDetails(event.target.value)}
                    />
                  </label>
                  <label>
                    事项归属
                    <select
                      value={editedInvolvement}
                      onChange={(event) =>
                        setEditedInvolvement(
                          event.target.value as "self" | "other" | "unclear",
                        )
                      }
                    >
                      <option value="unclear">尚不明确</option>
                      <option value="self">与我有关</option>
                      <option value="other">关于其他人</option>
                    </select>
                  </label>
                  {item.kind === "todo" && (
                    <label>
                      截止日期
                      <input
                        type="date"
                        value={editedDue}
                        onChange={(e) => setEditedDue(e.target.value)}
                      />
                    </label>
                  )}
                  {item.kind === "event" && (
                    <>
                      <label>
                        开始时间
                        <input
                          type={item.start_date ? "date" : "datetime-local"}
                          value={editedStart}
                          onChange={(e) => setEditedStart(e.target.value)}
                        />
                      </label>
                      <label>
                        结束时间
                        <input
                          type={item.start_date ? "date" : "datetime-local"}
                          value={editedEnd}
                          onChange={(e) => setEditedEnd(e.target.value)}
                        />
                      </label>
                    </>
                  )}
                  {["event", "todo"].includes(item.kind) && (
                    <>
                      <label>
                        提醒时刻（留空不提醒）
                        <input
                          type="datetime-local"
                          value={editedReminder}
                          onChange={(e) => setEditedReminder(e.target.value)}
                        />
                      </label>
                      <label>
                        主提醒设备
                        <select
                          value={editedDevice}
                          onChange={(e) => setEditedDevice(e.target.value)}
                        >
                          <option value="">尚未指定</option>
                          {sources.devices.map((d) => (
                            <option key={d.device_id} value={d.device_id}>
                              {d.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    </>
                  )}
                  <button disabled={busy}>保存修改</button>
                  <button type="button" onClick={() => setEditing(null)}>
                    取消修改
                  </button>
                </form>
              )}
              {work.some(
                (target) =>
                  target.work_item_id !== item.work_item_id &&
                  target.kind === item.kind &&
                  ((target.bindings ?? []).length > 0 ||
                    target.acceptance === "human"),
              ) && (
                <label>
                  关联已有事项
                  <select
                    aria-label={`关联 ${item.title}`}
                    defaultValue=""
                    disabled={busy}
                    onChange={(event) => {
                      const target = work.find(
                        (row) => row.work_item_id === event.target.value,
                      );
                      if (target)
                        void run(() =>
                          linkHarnessWork(sessionId, item, target),
                        );
                    }}
                  >
                    <option value="">选择后关联，避免重复创建</option>
                    {work
                      .filter(
                        (target) =>
                          target.work_item_id !== item.work_item_id &&
                          target.kind === item.kind &&
                          ((target.bindings ?? []).length > 0 ||
                            target.acceptance === "human"),
                      )
                      .map((target) => (
                        <option
                          value={target.work_item_id}
                          key={target.work_item_id}
                        >
                          {target.title} ·{" "}
                          {target.starts_at
                            ? new Date(target.starts_at).toLocaleString()
                            : "已有绑定"}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <button
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    reviewHarnessWork(
                      sessionId,
                      item.work_item_id,
                      item.revision ?? 1,
                      "confirm",
                    ),
                  )
                }
              >
                {(item.temporal_status ?? "current") === "current"
                  ? "确认"
                  : "确认仍需处理"}
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    reviewHarnessWork(
                      sessionId,
                      item.work_item_id,
                      item.revision ?? 1,
                      "reject",
                    ),
                  )
                }
              >
                不保存
              </button>
            </li>
          ))}
        </ul>
      </details>
      <details
        open={work.some(
          (item) => item.state === "waiting" || item.state === "blocked",
        )}
      >
        <summary>正在等待</summary>
        {!work.some(
          (item) => item.state === "waiting" || item.state === "blocked",
        ) && <p>目前没有等待中的事项。</p>}
        <ul>
          {work
            .filter(
              (item) => item.state === "waiting" || item.state === "blocked",
            )
            .map((item) => (
              <li key={item.work_item_id}>
                <strong>{item.title}</strong>
                <p>
                  {item.waiting_reason ||
                    "需要补充资料或检查授权，可在对话中继续。"}
                </p>
              </li>
            ))}
        </ul>
      </details>
      <details>
        <summary>行动时间线</summary>
        {!activity.length && <p>尚无处理记录。</p>}
        <ol>
          {[
            ...new Map(
              [...olderActivity, ...activity].map((entry) => [
                entry.sequence,
                entry,
              ]),
            ).values(),
          ]
            .sort((a, b) => b.sequence - a.sequence)
            .map((entry) => (
              <li key={entry.sequence}>
                <time>{new Date(entry.created_at).toLocaleString()}</time>
                <p>{entry.summary}</p>
                <small>
                  {(
                    {
                      verified: "已核验",
                      needs_verification: "结果待核对",
                      no_action: "无需行动",
                      open: "已接受",
                      proposed: "待核对",
                      shadow: "观察决策",
                      waiting: "等待中",
                      blocked: "需要处理",
                    } as Record<string, string>
                  )[entry.state] ?? entry.state}{" "}
                  · 依据 {(entry.evidence ?? []).length} 条来源
                </small>
                {Object.values(entry.delivery ?? {}).includes("opened") ? (
                  <p>已打开查看</p>
                ) : Object.values(entry.delivery ?? {}).includes(
                    "presented",
                  ) ? (
                  <p>设备已呈现通知</p>
                ) : Object.values(entry.delivery ?? {}).includes("blocked") ? (
                  <p>通知受阻，请检查手机通知权限。</p>
                ) : Object.values(entry.delivery ?? {}).includes("unknown") ? (
                  <p>通知呈现结果待核对</p>
                ) : null}
              </li>
            ))}
        </ol>
        {activity.length > 0 && olderActivity.length < 400 && (
          <button
            disabled={historyBusy}
            onClick={() => {
              setHistoryBusy(true);
              const first = Math.min(
                ...[...activity, ...olderActivity].map(
                  (entry) => entry.sequence,
                ),
              );
              void getHarnessActivity(sessionId, first)
                .then((rows) => {
                  if (active.current)
                    setOlderActivity((previous) =>
                      [...rows, ...previous].slice(-400),
                    );
                })
                .catch((error) => {
                  if (active.current)
                    setError(
                      error instanceof Error
                        ? error.message
                        : "较早的记录暂时无法读取",
                    );
                })
                .finally(() => {
                  if (active.current) setHistoryBusy(false);
                });
            }}
          >
            {historyBusy ? "正在读取…" : "查看较早记录"}
          </button>
        )}
      </details>
    </section>
  );
}

function localDateTime(value?: string | null) {
  if (!value) return "";
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
function isoDate(value: string) {
  return value ? new Date(value).toISOString() : null;
}
