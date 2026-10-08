import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type {
  AgentTask,
  ArtifactRef,
  CapabilityDescriptor,
  TaskAuthorization,
} from "@chatwaifu/protocol";
import {
  actOnAgentTask,
  createAgentTask,
  downloadAgentArtifact,
  getAgentArtifacts,
  getAgentCapabilities,
  getAgentTasks,
  getAgentDelivery,
  readAgentArtifactBlob,
  getAgentJournal,
  reconcileAgentOperation,
  updateTaskAuthorization,
} from "../chat/runtime-client/agentClient";
import { AgentDevelopmentPanel } from "./AgentDevelopmentPanel";
import { SkillConfirmationPrompt } from "../chat/SkillConfirmationPrompt";

const PdfArtifactPreview = lazy(() => import("./PdfArtifactPreview"));

const capabilityStatus: Record<string, string> = {
  available: "可执行",
  authorization_required: "需要授权",
  disabled: "已关闭",
  not_configured: "未配置",
  adapter_required: "需要适配",
  unsupported: "平台不支持",
};
const taskStatus: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  waiting_input: "等待补充信息",
  waiting_authorization: "等待批准",
  waiting_event: "等待事件",
  paused: "已暂停",
  succeeded: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
};

export function AgentSettingsSection(props: {
  context: { sessionId: string | null };
}) {
  return (
    <AgentSettingsState key={props.context.sessionId ?? "pending"} {...props} />
  );
}

function AgentSettingsState({
  context,
}: {
  context: { sessionId: string | null };
}) {
  const sessionId = context.sessionId;
  const [capabilities, setCapabilities] = useState<CapabilityDescriptor[]>([]);
  const [skillOptions, setSkillOptions] = useState<string[]>([]);
  const [visibleCapabilities, setVisibleCapabilities] = useState(32);
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactRef[]>([]);
  const [query, setQuery] = useState("");
  const [goal, setGoal] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [allowWrites, setAllowWrites] = useState(false);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [developmentOpen, setDevelopmentOpen] = useState(false);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const epoch = useRef(0);
  const refreshRevision = useRef(0);
  const [preview, setPreview] = useState<{
    blob: Blob;
    name: string;
    opener: HTMLElement;
  } | null>(null);
  const [journals, setJournals] = useState<
    Record<string, Record<string, unknown>[]>
  >({});
  const [resourceRoots, setResourceRoots] = useState(".");
  const [calendarIds, setCalendarIds] = useState("");
  const [deliveryStates, setDeliveryStates] = useState<Record<string, string>>(
    {},
  );

  async function refresh(expected = epoch.current) {
    if (!sessionId) return;
    const reading = ++refreshRevision.current;
    await Promise.all([
      (async () => {
        const all: CapabilityDescriptor[] = [];
        let cursor: string | undefined;
        do {
          const page = await getAgentCapabilities(sessionId, query, cursor);
          all.push(...(page.items ?? []));
          if (expected === epoch.current && reading === refreshRevision.current)
            setCapabilities([...all]);
          cursor = page.next_cursor ?? undefined;
        } while (cursor && all.length < 512 && expected === epoch.current);
        if (expected !== epoch.current) return;
        if (!query.trim())
          setSkillOptions([...new Set(all.map((c) => c.skill_id))]);
        if (reading !== refreshRevision.current) return;
        setCapabilities(all);
        setVisibleCapabilities(32);
      })(),
      (async () => {
        const [taskPage, files] = await Promise.all([
          getAgentTasks(sessionId),
          getAgentArtifacts(sessionId),
        ]);
        if (expected !== epoch.current || reading !== refreshRevision.current)
          return;
        setTasks(taskPage.items ?? []);
        setArtifacts(files);
      })(),
    ]);
  }
  useEffect(() => {
    const expected = ++epoch.current;
    if (sessionId)
      void refresh(expected).catch((e: unknown) => {
        if (expected === epoch.current)
          setNotice(e instanceof Error ? e.message : "读取失败");
      });
    return () => {
      epoch.current = expected + 1;
    };
    // Search runs on the explicit refresh button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (
      !sessionId ||
      !tasks.some((task) =>
        [
          "queued",
          "running",
          "waiting_authorization",
          "waiting_event",
          "waiting_input",
        ].includes(task.state),
      )
    )
      return;
    const expected = epoch.current;
    const timer = window.setTimeout(() => {
      void Promise.all([getAgentTasks(sessionId), getAgentArtifacts(sessionId)])
        .then(([page, files]) => {
          if (expected === epoch.current) {
            setTasks(page.items ?? []);
            setArtifacts(files);
          }
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [sessionId, tasks]);
  async function operate(action: () => Promise<unknown>, refreshAfter = true) {
    const expected = epoch.current;
    setBusy(true);
    setNotice("");
    try {
      await action();
      if (expected === epoch.current && refreshAfter) await refresh(expected);
    } catch (e: unknown) {
      if (expected === epoch.current)
        setNotice(e instanceof Error ? e.message : "操作失败");
    } finally {
      if (expected === epoch.current) setBusy(false);
    }
  }
  return (
    <div className="settings-section">
      <SkillConfirmationPrompt
        sessionId={
          tasks.find((t) => t.state === "waiting_authorization")?.session_id ??
          sessionId
        }
      />
      <h2>宁宁的能力与任务</h2>
      <p>
        显示当前身份的可用能力。任务在后台保存进度，新聊天和语音打断不会取消任务。
      </p>
      {notice && <p role="status">{notice}</p>}
      <label>
        查找能力{" "}
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          maxLength={500}
        />
      </label>
      <button
        disabled={busy || !sessionId}
        onClick={() => void operate(() => refresh(), false)}
      >
        刷新
      </button>
      <details open>
        <summary>能力与权限</summary>
        <ul>
          {capabilities.slice(0, visibleCapabilities).map((c) => (
            <li key={c.capability_id}>
              <details>
                <summary>
                  <strong>
                    {c.skill_id} / {c.name}
                  </strong>{" "}
                  · {capabilityStatus[c.status]} ·{" "}
                  {c.execution_location === "paired_device"
                    ? "配对设备"
                    : "运行主机"}
                </summary>
                <p>{c.description}</p>
                {c.availability_reason && <p>{c.availability_reason}</p>}
                <small>{(c.required_permissions ?? []).join("、")}</small>
              </details>
            </li>
          ))}
        </ul>
        {visibleCapabilities < capabilities.length && (
          <button onClick={() => setVisibleCapabilities((count) => count + 32)}>
            显示更多能力（还有 {capabilities.length - visibleCapabilities} 项）
          </button>
        )}
      </details>
      <details>
        <summary>创建授权任务</summary>
        <label>
          目标{" "}
          <textarea
            value={goal}
            maxLength={8000}
            onChange={(e) => setGoal(e.target.value)}
          />
        </label>
        <fieldset>
          <legend>本任务可以使用的能力</legend>
          {skillOptions
            .filter((s) => s !== "agent.tasks")
            .map((s) => (
              <label key={s}>
                <input
                  type="checkbox"
                  checked={selected.includes(s)}
                  onChange={(e) =>
                    setSelected((old) =>
                      e.target.checked
                        ? [...old, s]
                        : old.filter((v) => v !== s),
                    )
                  }
                />
                {s}
              </label>
            ))}
        </fieldset>
        <label>
          允许访问的工作区子目录（逗号分隔）
          <input
            value={resourceRoots}
            onChange={(e) => setResourceRoots(e.target.value)}
          />
        </label>
        <label>
          允许访问的 Calendar ID（留空沿用已选日历）
          <input
            value={calendarIds}
            onChange={(e) => setCalendarIds(e.target.value)}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={allowWrites}
            onChange={(e) => setAllowWrites(e.target.checked)}
          />
          允许任务在指定能力内写入；文件限定在宁宁工作区
        </label>
        <button
          disabled={busy || !sessionId || !goal.trim() || !selected.length}
          onClick={() => {
            if (resourceRoots.split(",").filter((v) => v.trim()).length > 16) {
              setNotice("工作区授权最多 16 个目录，请缩小范围");
              return;
            }
            if (sessionId)
              void operate(() =>
                createAgentTask({
                  session_id: sessionId,
                  goal,
                  completion_criteria: [goal],
                  authorization: {
                    allowed_skill_ids: selected,
                    resource_roots: resourceRoots
                      .split(",")
                      .map((v) => v.trim())
                      .filter(Boolean) as TaskAuthorization["resource_roots"],
                    calendar_ids: calendarIds
                      .split(",")
                      .map((v) => v.trim())
                      .filter(Boolean),
                    source_ref: "owner:agent-settings",
                    allow_writes: allowWrites,
                    expires_at: new Date(
                      Date.now() + 24 * 3600 * 1000,
                    ).toISOString(),
                  },
                }),
              );
          }}
        >
          授权并开始
        </button>
      </details>
      <details open>
        <summary>进行中的任务</summary>
        <ul>
          {tasks.map((task) => (
            <li key={task.task_id}>
              <strong>{task.goal}</strong> · {taskStatus[task.state]}
              <p>{task.blocked_reason ?? task.result_text}</p>
              {task.delivery_id && (
                <p>
                  结果投递：{deliveryStates[task.task_id] ?? "待读取回执"} ·
                  手机收件未验证
                  <button
                    onClick={() => {
                      if (sessionId)
                        void operate(async () => {
                          const delivery = await getAgentDelivery(
                            sessionId,
                            task.task_id,
                          );
                          if (delivery)
                            setDeliveryStates((previous) => ({
                              ...previous,
                              [task.task_id]:
                                delivery.status === "delivered"
                                  ? "平台已接受"
                                  : delivery.status === "failed"
                                    ? "失败或结果待核实，请查看执行记录"
                                    : delivery.status === "cancelled"
                                      ? "已取消"
                                      : "排队或发送中",
                            }));
                        });
                    }}
                  >
                    读取投递状态
                  </button>
                </p>
              )}
              {task.candidate_id && (
                <p>
                  能力缺口已进入候选功能开发：{task.candidate_id}
                  。请在下方审核。
                </p>
              )}
              <small>
                工具 {task.tool_calls}/{task.max_tool_calls} · 已运行{" "}
                {Math.round(task.active_seconds ?? 0)} 秒
              </small>
              <details
                onToggle={(e) => {
                  const expected = epoch.current;
                  if (e.currentTarget.open && sessionId)
                    void getAgentJournal(sessionId, task.task_id)
                      .then((value) => {
                        if (expected === epoch.current)
                          setJournals((old) => ({
                            ...old,
                            [task.task_id]: value.items,
                          }));
                      })
                      .catch(() => undefined);
                }}
              >
                <summary>执行记录与结果核对</summary>
                {(journals[task.task_id] ?? []).map((step) => (
                  <div key={String(step.step_key)}>
                    <p>
                      {typeof step.skill_id === "string"
                        ? step.skill_id
                        : "操作"}{" "}
                      /{" "}
                      {typeof step.capability === "string"
                        ? step.capability
                        : ""}{" "}
                      · {String(step.state)}
                    </p>
                    {step.state !== "settled" &&
                      step.side_effect !== "read" && (
                        <>
                          <input
                            aria-label="结果核对证据"
                            maxLength={1000}
                            value={inputs[String(step.step_key)] ?? ""}
                            onChange={(e) =>
                              setInputs((old) => ({
                                ...old,
                                [String(step.step_key)]: e.target.value,
                              }))
                            }
                          />
                          <p>
                            先核对平台记录或实际资源，再提交结果；同一操作不会自动重发。
                          </p>
                          {(["accepted", "rejected"] as const).map(
                            (outcome) => (
                              <button
                                key={outcome}
                                disabled={
                                  busy ||
                                  !inputs[String(step.step_key)]?.trim() ||
                                  ["running", "queued"].includes(task.state)
                                }
                                onClick={() =>
                                  sessionId &&
                                  void operate(() =>
                                    reconcileAgentOperation(
                                      sessionId,
                                      task.task_id,
                                      task.revision ?? 0,
                                      String(step.step_key),
                                      outcome,
                                      inputs[String(step.step_key)],
                                    ),
                                  )
                                }
                              >
                                {outcome === "accepted"
                                  ? "已核实执行成功"
                                  : "已核实未执行"}
                              </button>
                            ),
                          )}
                        </>
                      )}
                  </div>
                ))}
              </details>
              {[
                "paused",
                "waiting_input",
                "waiting_authorization",
                "waiting_event",
              ].includes(task.state) && (
                <button
                  disabled={busy}
                  onClick={() =>
                    sessionId &&
                    void operate(() =>
                      updateTaskAuthorization(
                        sessionId,
                        task.task_id,
                        task.revision ?? 0,
                        {
                          ...task.authorization,
                          source_ref: "owner:authorization-renewal",
                          expires_at: new Date(
                            Date.now() + 86400000,
                          ).toISOString(),
                        },
                      ),
                    )
                  }
                >
                  续期授权并暂停待确认
                </button>
              )}
              {["queued", "running", "waiting_authorization"].includes(
                task.state,
              ) && (
                <button
                  disabled={busy}
                  onClick={() =>
                    sessionId &&
                    void operate(() =>
                      actOnAgentTask(
                        sessionId,
                        task.task_id,
                        task.revision ?? 0,
                        "pause",
                      ),
                    )
                  }
                >
                  暂停
                </button>
              )}
              {["paused", "waiting_input"].includes(task.state) && (
                <>
                  <input
                    aria-label="任务补充信息"
                    value={inputs[task.task_id] ?? ""}
                    maxLength={8000}
                    onChange={(e) =>
                      setInputs({ ...inputs, [task.task_id]: e.target.value })
                    }
                  />
                  <button
                    disabled={busy}
                    onClick={() =>
                      sessionId &&
                      void operate(() =>
                        actOnAgentTask(
                          sessionId,
                          task.task_id,
                          task.revision ?? 0,
                          "resume",
                          inputs[task.task_id],
                        ),
                      )
                    }
                  >
                    补充并继续
                  </button>
                </>
              )}
              {!["succeeded", "failed", "cancelled"].includes(task.state) && (
                <button
                  disabled={busy}
                  onClick={() =>
                    sessionId &&
                    void operate(() =>
                      actOnAgentTask(
                        sessionId,
                        task.task_id,
                        task.revision ?? 0,
                        "cancel",
                      ),
                    )
                  }
                >
                  取消
                </button>
              )}
            </li>
          ))}
        </ul>
      </details>
      <details open>
        <summary>文件与预览</summary>
        <ul>
          {artifacts.map((artifact) => (
            <li key={artifact.artifact_id}>
              {artifact.name} ·{" "}
              {artifact.validation_status === "rendered"
                ? "已生成预览"
                : "结构检查通过"}
              <button
                disabled={busy}
                onClick={() =>
                  sessionId &&
                  void operate(() => downloadAgentArtifact(sessionId, artifact))
                }
              >
                下载
              </button>
              {artifact.media_type === "application/pdf" && (
                <button
                  disabled={busy}
                  onClick={(event) => {
                    const expected = epoch.current;
                    const opener = event.currentTarget;
                    if (sessionId)
                      void operate(async () => {
                        const blob = await readAgentArtifactBlob(
                          sessionId,
                          artifact,
                        );
                        if (expected === epoch.current)
                          setPreview({ blob, name: artifact.name, opener });
                      }, false);
                  }}
                >
                  预览
                </button>
              )}
            </li>
          ))}
        </ul>
      </details>
      {preview && (
        <Suspense fallback={<p role="status">正在加载文档预览…</p>}>
          <PdfArtifactPreview {...preview} onClose={() => setPreview(null)} />
        </Suspense>
      )}
      <details onToggle={(e) => setDevelopmentOpen(e.currentTarget.open)}>
        <summary>候选功能开发</summary>
        {developmentOpen && sessionId && (
          <AgentDevelopmentPanel sessionId={sessionId} />
        )}
      </details>
    </div>
  );
}
