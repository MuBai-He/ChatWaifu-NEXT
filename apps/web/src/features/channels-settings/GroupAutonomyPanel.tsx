import { useEffect, useRef, useState } from "react";
import type { GroupAutonomyPolicy, DecisionRecord } from "@chatwaifu/protocol";
import {
  getGroupAutonomy,
  getGroupDecisions,
  saveGroupAutonomy,
} from "../chat/runtime-client/agentClient";
import {
  assertRuntimeRequestContext,
  readRuntimeRequestContext,
} from "../chat/runtimeEndpoint";

export function GroupAutonomyPanel(props: {
  routeId: string;
  routeRevision: number;
}) {
  return (
    <GroupAutonomyState
      key={props.routeId + ":" + props.routeRevision}
      {...props}
    />
  );
}

function GroupAutonomyState({
  routeId,
  routeRevision,
}: {
  routeId: string;
  routeRevision: number;
}) {
  const [policy, setPolicy] = useState<GroupAutonomyPolicy | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState(false);
  const epoch = useRef(0);
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  useEffect(() => {
    if (!opened) return;
    const current = ++epoch.current;
    void getGroupAutonomy(routeId)
      .then((result) => {
        if (current === epoch.current) setPolicy(result);
      })
      .catch((e: unknown) => {
        if (current === epoch.current)
          setNotice(e instanceof Error ? e.message : "读取失败");
      });
    void getGroupDecisions(routeId)
      .then((items) => {
        if (current === epoch.current) setDecisions(items);
      })
      .catch(() => undefined);
    return () => {
      epoch.current = current + 1;
    };
  }, [routeId, routeRevision, opened]);
  async function save() {
    if (!policy) return;
    const current = epoch.current;
    setBusy(true);
    setNotice("");
    try {
      const context = await readRuntimeRequestContext();
      assertRuntimeRequestContext(context);
      const result = await saveGroupAutonomy({
        ...policy,
        route_revision: routeRevision,
      });
      assertRuntimeRequestContext(context);
      if (current === epoch.current) {
        setPolicy(result);
        setNotice("本群参与模式已保存");
      }
    } catch (e: unknown) {
      if (current === epoch.current)
        setNotice(e instanceof Error ? e.message : "保存失败");
    } finally {
      if (current === epoch.current) setBusy(false);
    }
  }
  if (!opened)
    return <button onClick={() => setOpened(true)}>读取本群参与设置</button>;
  return (
    <div className="channel-settings-body">
      <h4>自主参与</h4>
      <p>
        影子模式只记录参与判断。常驻群友模式允许宁宁根据讨论主动接话；仍受成员授权、安静时段和预算限制。
      </p>
      {notice && <p role="status">{notice}</p>}
      <details>
        <summary>最近参与判断与能力缺口</summary>
        <ul>
          {decisions.map((d, index) => (
            <li key={index}>
              {d.action}：{d.reason}
              <p>{d.goal}</p>
              <small>来源：{d.source_refs?.join("、")}</small>
            </li>
          ))}
        </ul>
      </details>
      {policy && (
        <>
          <label>
            参与模式{" "}
            <select
              value={policy.mode ?? "off"}
              onChange={(e) =>
                setPolicy({
                  ...policy,
                  mode: e.target.value as "off" | "shadow" | "member",
                })
              }
            >
              <option value="off">关闭，保持原行为</option>
              <option value="shadow">影子决策</option>
              <option value="member">常驻群友</option>
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={policy.memory_enabled ?? false}
              onChange={(e) =>
                setPolicy({ ...policy, memory_enabled: e.target.checked })
              }
            />
            记住有出处的群内偏好、约定和未完成事项
          </label>
          <label>
            每小时最多主动发言{" "}
            <input
              type="number"
              min={0}
              max={20}
              value={policy.messages_per_hour ?? 20}
              onChange={(e) =>
                setPolicy({
                  ...policy,
                  messages_per_hour: Number(e.target.value),
                })
              }
            />
          </label>
          <details>
            <summary>观察预算与安静时段</summary>
            {(
              [
                ["merge_seconds", "消息合并（秒）", 3, 1, 10],
                ["decision_interval_seconds", "最短判断间隔（秒）", 10, 10, 60],
                ["observations_per_hour", "每小时最多观察判断", 240, 1, 240],
                ["quiet_start", "安静时段开始（小时）", 23, 0, 23],
                ["quiet_end", "安静时段结束（小时）", 8, 0, 23],
              ] as const
            ).map(([field, label, fallback, min, max]) => (
              <label key={String(field)}>
                {label}
                <input
                  type="number"
                  min={min}
                  max={max}
                  value={
                    (policy[field as keyof GroupAutonomyPolicy] as number) ??
                    Number(fallback)
                  }
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      [String(field)]: Number(e.target.value),
                    })
                  }
                />
              </label>
            ))}
            <label>
              时区
              <input
                value={policy.timezone ?? "Asia/Shanghai"}
                maxLength={64}
                onChange={(e) =>
                  setPolicy({ ...policy, timezone: e.target.value })
                }
              />
            </label>
          </details>
          <label>
            最短发言间隔（秒）{" "}
            <input
              type="number"
              min={30}
              max={3600}
              value={policy.message_interval_seconds ?? 30}
              onChange={(e) =>
                setPolicy({
                  ...policy,
                  message_interval_seconds: Number(e.target.value),
                })
              }
            />
          </label>
          <button disabled={busy} onClick={() => void save()}>
            保存自主参与设置
          </button>
        </>
      )}
    </div>
  );
}
