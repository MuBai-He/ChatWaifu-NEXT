import { useEffect, useMemo, useState } from "react";

import "./model-settings.css";

import {
  getCharacterState,
  getIndexRebuildStatus,
  getModelConfigurations,
  rebuildIndexes,
  testModelConfiguration,
  updateModelConfiguration,
} from "./runtimeClient";
import type {
  CharacterKernelSnapshot,
  IndexRebuildStatus,
  ModelRole,
  ModelRoleConfiguration,
} from "./types";
import {
  SettingsSecretField,
  SettingsStatus,
} from "../settings/SettingsFields";
import { useSettingsOperation } from "../settings/useSettingsOperation";

const ROLE_ORDER: ModelRole[] = [
  "chat",
  "memory_extraction",
  "memory_summary",
  "embedding",
];

const ROLE_LABELS: Record<ModelRole, { title: string; description: string }> = {
  chat: { title: "聊天模型", description: "生成宁宁的最终回复" },
  memory_extraction: {
    title: "记忆提取模型",
    description: "从用户回合提出结构化记忆候选",
  },
  memory_summary: {
    title: "记忆总结模型",
    description: "在 Prompt 超预算时压缩较早对话",
  },
  embedding: {
    title: "Embedding 模型",
    description: "构建可重建的语义检索索引",
  },
};

interface Props {
  sessionId: string | null;
}

export function ModelSettingsPanel({ sessionId }: Props) {
  const [configurations, setConfigurations] = useState<
    ModelRoleConfiguration[]
  >([]);
  const [characterState, setCharacterState] =
    useState<CharacterKernelSnapshot | null>(null);
  const [apiKeys, setApiKeys] = useState<Partial<Record<ModelRole, string>>>(
    {},
  );
  const [persistedEmbedding, setPersistedEmbedding] = useState<{
    provider: string;
    model: string;
    base_url: string;
  } | null>(null);
  const [showWarningModal, setShowWarningModal] = useState(false);
  const [rebuildStatus, setRebuildStatus] = useState<IndexRebuildStatus | null>(
    null,
  );
  const [isRebuilding, setIsRebuilding] = useState(false);
  const { busy, notice, setNotice, run } = useSettingsOperation<ModelRole>();

  useEffect(() => {
    let active = true;
    void Promise.all([
      getModelConfigurations(),
      sessionId ? getCharacterState(sessionId) : Promise.resolve(null),
      getIndexRebuildStatus().catch(() => null),
    ])
      .then(([models, state, currentRebuildStatus]) => {
        if (!active) return;
        setConfigurations(models);
        setCharacterState(state);
        const emb = models.find((m) => m.role === "embedding");
        if (emb) {
          setPersistedEmbedding({
            provider: emb.provider,
            model: emb.model,
            base_url: emb.base_url,
          });
        }
        if (currentRebuildStatus && currentRebuildStatus.state !== "idle") {
          setRebuildStatus(currentRebuildStatus);
        }
        setNotice(null);
      })
      .catch((error: unknown) => {
        if (active)
          setNotice({
            tone: "error",
            text: error instanceof Error ? error.message : "读取设置失败",
          });
      });
    return () => {
      active = false;
    };
  }, [sessionId, setNotice]);

  useEffect(() => {
    if (!showWarningModal) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowWarningModal(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [showWarningModal]);

  useEffect(() => {
    if (rebuildStatus?.state !== "running") return;
    let active = true;
    const interval = setInterval(() => {
      void getIndexRebuildStatus()
        .then((status) => {
          if (!active) return;
          setRebuildStatus(status);
        })
        .catch(() => {
          // ignore transient poll error
        });
    }, 500);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [rebuildStatus?.state]);

  const handleTriggerRebuild = async () => {
    if (isRebuilding || rebuildStatus?.state === "running") return;
    setIsRebuilding(true);
    try {
      const status = await rebuildIndexes();
      setRebuildStatus(status);
    } catch (error: unknown) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : "触发重建索引失败",
      });
    } finally {
      setIsRebuilding(false);
    }
  };

  const byRole = useMemo(
    () => new Map(configurations.map((item) => [item.role, item])),
    [configurations],
  );

  const change = (
    role: ModelRole,
    field: keyof ModelRoleConfiguration,
    value: string | number | boolean,
  ) => {
    setConfigurations((current) =>
      current.map((item) =>
        item.role === role ? { ...item, [field]: value } : item,
      ),
    );
  };

  const save = async (role: ModelRole, clearApiKey = false) => {
    const item = byRole.get(role);
    if (!item) return;
    const apiKey = apiKeys[role]?.trim();
    const updated = await run(
      role,
      () =>
        updateModelConfiguration(role, {
          provider: item.provider,
          model: item.model,
          base_url: item.base_url,
          timeout_seconds: item.timeout_seconds,
          context_window: item.context_window,
          enabled: item.enabled,
          ...(apiKey ? { api_key: apiKey } : {}),
          ...(clearApiKey ? { clear_api_key: true } : {}),
        }),
      {
        success: `${ROLE_LABELS[role].title}已保存`,
        error: "保存失败",
      },
    );
    if (!updated) return;
    if (role === "embedding") {
      if (
        persistedEmbedding &&
        (persistedEmbedding.provider !== updated.provider ||
          persistedEmbedding.model !== updated.model ||
          persistedEmbedding.base_url !== updated.base_url)
      ) {
        setShowWarningModal(true);
      }
      setPersistedEmbedding({
        provider: updated.provider,
        model: updated.model,
        base_url: updated.base_url,
      });
    }
    setConfigurations((current) =>
      current.map((candidate) =>
        candidate.role === role ? updated : candidate,
      ),
    );
    setApiKeys((current) => ({ ...current, [role]: "" }));
  };

  const probe = async (role: ModelRole) => {
    await run(role, () => testModelConfiguration(role), {
      success: (result) => {
        const detail = result.dimensions
          ? `，${result.dimensions} 维`
          : result.characters
            ? `，返回 ${result.characters} 字符`
            : "";
        return `${ROLE_LABELS[role].title}连接 ${result.status}${detail}`;
      },
      error: "连接测试失败",
    });
  };

  return (
    <div className="model-settings">
      <CharacterStateCard snapshot={characterState} />
      <div className="model-settings-heading">
        <div>
          <small>MODEL ROUTING</small>
          <strong>模型路由</strong>
        </div>
        <span>四条链路独立生效</span>
      </div>
      {ROLE_ORDER.map((role) => {
        const item = byRole.get(role);
        if (!item) return null;
        const isOpenAi = item.provider === "openai_compatible";
        return (
          <section className="model-role-card" key={role}>
            <header>
              <div>
                <strong>{ROLE_LABELS[role].title}</strong>
                <small>{ROLE_LABELS[role].description}</small>
              </div>
              <label className="model-enabled">
                <input
                  type="checkbox"
                  checked={item.enabled}
                  onChange={(event) =>
                    change(role, "enabled", event.target.checked)
                  }
                />
                启用
              </label>
            </header>
            <label>
              <span>Provider</span>
              <select
                aria-label={`${ROLE_LABELS[role].title} Provider`}
                value={item.provider}
                onChange={(event) =>
                  change(role, "provider", event.target.value)
                }
              >
                {role === "embedding" ? (
                  <>
                    <option value="local_hash">本地 Hash（零配置回退）</option>
                    <option value="openai_compatible">OpenAI 兼容</option>
                    <option value="disabled">禁用</option>
                  </>
                ) : (
                  <>
                    <option value="demo">本地确定性 Demo</option>
                    <option value="openai_compatible">OpenAI 兼容</option>
                    <option value="disabled">禁用</option>
                  </>
                )}
              </select>
            </label>
            <label>
              <span>模型 ID</span>
              <input
                aria-label={`${ROLE_LABELS[role].title} 模型 ID`}
                value={item.model}
                onChange={(event) => change(role, "model", event.target.value)}
              />
            </label>
            {isOpenAi ? (
              <>
                <label>
                  <span>Base URL</span>
                  <input
                    aria-label={`${ROLE_LABELS[role].title} Base URL`}
                    value={item.base_url}
                    onChange={(event) =>
                      change(role, "base_url", event.target.value)
                    }
                    placeholder="https://…/v1"
                  />
                </label>
                <SettingsSecretField
                  ariaLabel={`${ROLE_LABELS[role].title} API Key`}
                  configured={item.api_key_configured}
                  value={apiKeys[role] ?? ""}
                  disabled={busy === role}
                  onChange={(value) =>
                    setApiKeys((current) => ({ ...current, [role]: value }))
                  }
                />
              </>
            ) : null}
            <div className="model-role-grid">
              <label>
                <span>上下文窗口</span>
                <input
                  type="number"
                  min={1024}
                  value={item.context_window}
                  onChange={(event) =>
                    change(role, "context_window", Number(event.target.value))
                  }
                />
              </label>
              <label>
                <span>超时（秒）</span>
                <input
                  type="number"
                  min={1}
                  value={item.timeout_seconds}
                  onChange={(event) =>
                    change(role, "timeout_seconds", Number(event.target.value))
                  }
                />
              </label>
            </div>
            <footer>
              <button
                type="button"
                disabled={busy === role}
                onClick={() => void save(role)}
              >
                保存
              </button>
              <button
                type="button"
                disabled={busy === role}
                onClick={() => void probe(role)}
              >
                测试
              </button>
              {role === "embedding" ? (
                <button
                  type="button"
                  className="reindex-button"
                  disabled={
                    busy === role ||
                    isRebuilding ||
                    rebuildStatus?.state === "running"
                  }
                  onClick={() => void handleTriggerRebuild()}
                >
                  {rebuildStatus?.state === "running" ? "重建中…" : "重建索引"}
                </button>
              ) : null}
              {isOpenAi && item.api_key_configured ? (
                <button
                  className="danger"
                  type="button"
                  disabled={busy === role}
                  onClick={() => void save(role, true)}
                >
                  移除密钥
                </button>
              ) : null}
            </footer>
            {role === "embedding" &&
            rebuildStatus &&
            rebuildStatus.state !== "idle" ? (
              <div className="reindex-status-card" aria-label="索引重建状态">
                <div className="reindex-status-header">
                  <span>索引状态：{rebuildStateLabel(rebuildStatus.state)}</span>
                  {rebuildStatus.state === "failed" ? (
                    <button
                      type="button"
                      className="reindex-retry-button"
                      disabled={isRebuilding}
                      onClick={() => void handleTriggerRebuild()}
                    >
                      重试
                    </button>
                  ) : null}
                </div>
                {rebuildStatus.error ? (
                  <p className="reindex-status-error">{rebuildStatus.error}</p>
                ) : null}
                <div className="reindex-domain-grid">
                  {Object.entries(rebuildStatus.domains).map(
                    ([domainName, dom]) => (
                      <div key={domainName} className="reindex-domain-item">
                        <strong>
                          {domainName === "memory"
                            ? "结构化记忆"
                            : domainName === "photo"
                              ? "照片语义"
                              : domainName}
                        </strong>
                        <span>状态：{rebuildStateLabel(dom.state)}</span>
                        <span>
                          已索引：{dom.indexed_count} / {dom.total_count}
                        </span>
                        {dom.failed_count > 0 ? (
                          <span className="domain-failed">
                            失败：{dom.failed_count}
                          </span>
                        ) : null}
                        {dom.error ? (
                          <small className="domain-error">{dom.error}</small>
                        ) : null}
                      </div>
                    ),
                  )}
                </div>
              </div>
            ) : null}
          </section>
        );
      })}
      <p className="model-secret-note">
        密钥不会回显或写入浏览器；保存后只进入本机 Runtime 的 0600 私密文件。
      </p>
      <SettingsStatus notice={notice} className="model-settings-notice" />
      {showWarningModal ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setShowWarningModal(false)}
        >
          <div
            className="reindex-warning-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reindex-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="reindex-modal-title">Embedding 模型已更换</h3>
            <p className="reindex-warning-text">
              embedding 模型已更换，现有索引仍由旧模型生成。不重建可能导致漏检、错误匹配或相关度下降；向量维度不兼容的条目将无法参与语义检索。建议重建索引。
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="reindex-confirm-button danger"
                onClick={() => {
                  setShowWarningModal(false);
                  void handleTriggerRebuild();
                }}
              >
                重建索引
              </button>
              <button
                type="button"
                className="reindex-cancel-button"
                onClick={() => setShowWarningModal(false)}
              >
                稍后
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function rebuildStateLabel(state: string): string {
  switch (state) {
    case "running":
      return "重建中";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    default:
      return "空闲";
  }
}

function CharacterStateCard({
  snapshot,
}: {
  snapshot: CharacterKernelSnapshot | null;
}) {
  if (!snapshot) return null;
  const relationship = snapshot.relationship;
  const affect = snapshot.affect;
  return (
    <section className="character-kernel-card" aria-label="角色状态">
      <header>
        <div>
          <small>CHARACTER KERNEL</small>
          <strong>角色状态</strong>
        </div>
        <span>
          {stageLabel(relationship.stage ?? "acquaintance")} · #
          {snapshot.revision}
        </span>
      </header>
      <div className="kernel-metrics">
        <Metric label="熟悉" value={relationship.familiarity ?? 0} />
        <Metric label="信任" value={relationship.trust ?? 0} />
        <Metric label="好感" value={relationship.affinity ?? 0} />
        <Metric label="舒适" value={relationship.comfort ?? 0} />
      </div>
      <p>
        当前情绪：
        {(affect.valence ?? 0) >= 0.25
          ? "温暖"
          : (affect.valence ?? 0) < -0.1
            ? "低落"
            : "平静"}
        {(affect.embarrassment ?? 0) >= 0.35 ? " · 害羞" : ""}
        {(affect.tension ?? 0) >= 0.35 ? " · 紧张" : ""}
        ；互动 {relationship.interaction_count ?? 0} 回合
      </p>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <span>{label}</span>
      <i>
        <b style={{ transform: `scaleX(${value})` }} />
      </i>
      <small>{Math.round(value * 100)}</small>
    </div>
  );
}

function stageLabel(
  stage: CharacterKernelSnapshot["relationship"]["stage"],
): string {
  if (!stage) return "初识";
  return {
    acquaintance: "初识",
    familiar: "熟悉",
    trusted: "信赖",
    close: "亲近",
  }[stage];
}
