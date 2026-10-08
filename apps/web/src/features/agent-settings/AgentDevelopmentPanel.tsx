import { useEffect, useRef, useState } from "react";
import type {
  AgentDevelopmentPolicy,
  CandidateFeature,
} from "@chatwaifu/protocol";
import {
  approveAgentCandidate,
  configureAgentDevelopment,
  createAgentCandidate,
  downloadAgentArtifact,
  getAgentCandidates,
  getAgentDevelopment,
} from "../chat/runtime-client/agentClient";
import { setPluginEnabled } from "../chat/runtime-client/skillsClient";

export function AgentDevelopmentPanel(props: { sessionId: string }) {
  return <AgentDevelopmentState key={props.sessionId} {...props} />;
}

function AgentDevelopmentState({ sessionId }: { sessionId: string }) {
  const [policy, setPolicy] = useState<AgentDevelopmentPolicy | null>(null);
  const [candidates, setCandidates] = useState<CandidateFeature[]>([]);
  const [goal, setGoal] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const epoch = useRef(0);
  async function refresh(expected = epoch.current) {
    const [p, items] = await Promise.all([
      getAgentDevelopment(),
      getAgentCandidates(sessionId),
    ]);
    if (expected === epoch.current) {
      setPolicy(p);
      setCandidates(items);
    }
  }
  useEffect(() => {
    const expected = ++epoch.current;
    void refresh(expected).catch((e: unknown) => {
      if (expected === epoch.current)
        setNotice(e instanceof Error ? e.message : "读取失败");
    });
    return () => {
      epoch.current = expected + 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);
  async function operate(action: () => Promise<unknown>) {
    const expected = epoch.current;
    setBusy(true);
    setNotice("");
    try {
      await action();
      if (expected === epoch.current) await refresh(expected);
    } catch (e: unknown) {
      if (expected === epoch.current)
        setNotice(e instanceof Error ? e.message : "操作失败");
    } finally {
      if (expected === epoch.current) setBusy(false);
    }
  }
  return (
    <div>
      <p>
        宁宁可以在隔离环境中开发和测试候选功能。默认关闭，每日最多一个；只有你审核并启用后才进入可执行能力目录。
      </p>
      {notice && <p role="status">{notice}</p>}
      {policy && (
        <label>
          <input
            type="checkbox"
            checked={policy.enabled ?? false}
            disabled={busy}
            onChange={(e) =>
              void operate(() =>
                configureAgentDevelopment({
                  ...policy,
                  enabled: e.target.checked,
                }),
              )
            }
          />
          允许自主开发候选功能
        </label>
      )}
      <label>
        需要补充的功能{" "}
        <textarea
          maxLength={4000}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
        />
      </label>
      <button
        disabled={busy || !policy?.enabled || !goal.trim()}
        onClick={() =>
          void operate(() =>
            createAgentCandidate({
              session_id: sessionId,
              goal,
              source_ref: "owner:development-settings",
            }),
          )
        }
      >
        开发并测试候选版本
      </button>
      <button disabled={busy} onClick={() => void operate(() => refresh())}>
        刷新候选功能
      </button>
      <ul>
        {candidates.map((item) => (
          <li key={item.candidate_id}>
            <strong>{item.goal}</strong> · {item.state}
            <p>{item.test_summary}</p>
            <code>{item.package_sha256}</code>
            {item.artifact && (
              <button
                disabled={busy}
                onClick={() => {
                  const artifact = item.artifact;
                  if (artifact)
                    void operate(() =>
                      downloadAgentArtifact(sessionId, artifact),
                    );
                }}
              >
                下载代码包审阅
              </button>
            )}
            {item.state === "tested" && item.package_sha256 && (
              <button
                disabled={busy}
                onClick={() => {
                  if (item.package_sha256)
                    void operate(() =>
                      approveAgentCandidate(
                        item.candidate_id,
                        item.revision ?? 0,
                        item.package_sha256!,
                      ),
                    );
                }}
              >
                批准并启用此版本
              </button>
            )}
            {item.state === "approved" && item.plugin_id && (
              <button
                disabled={busy}
                onClick={() => {
                  if (item.plugin_id)
                    void operate(() =>
                      setPluginEnabled(item.plugin_id!, false),
                    );
                }}
              >
                停用此插件
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
