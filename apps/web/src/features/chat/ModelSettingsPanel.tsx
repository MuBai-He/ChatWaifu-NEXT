import "../settings/settings-glass.css";
import "../settings/settings-controls.css";
import { useEffect, useMemo, useRef, useState } from "react";

import { createPortal } from "react-dom";

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
import {
  modelContextBudgetSchema,
  type ModelContextBudget,
} from "./runtime-client/contracts";

const ROLE_ORDER: ModelRole[] = [
  "chat",
  "behavior_decision",
  "memory_extraction",
  "memory_summary",
  "embedding",
];

const ROLE_LABELS: Record<ModelRole, { title: string; description: string }> = {
  chat: { title: "聊天模型", description: "生成宁宁的最终回复" },
  behavior_decision: {
    title: "行为决策模型",
    description: "判断群聊、私聊与自主事件是否回应；正式回复仍由聊天模型生成",
  },
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
  compact?: boolean;
  active?: boolean;
}

export function ModelSettingsPanel({
  sessionId,
  compact = false,
  active = true,
}: Props) {
  const [selectedRole, setSelectedRole] = useState<ModelRole>("chat");
  const [configurations, setConfigurations] = useState<
    ModelRoleConfiguration[]
  >([]);
  const [savedConfigurations, setSavedConfigurations] = useState<
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
  const modalRef = useRef<HTMLDivElement>(null);
  const laterRef = useRef<HTMLButtonElement>(null);
  const embeddingSaveRef = useRef<HTMLButtonElement>(null);
  const rebuildInFlight = useRef(false);
  const rebuildRequest = useRef<AbortController | null>(null);
  useEffect(() => () => rebuildRequest.current?.abort(), []);
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
        setSavedConfigurations(models);
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
    const previousFocus = document.activeElement;
    const embeddingSaveButton = embeddingSaveRef.current;
    laterRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setShowWarningModal(false);
      }
      if (event.key === "Tab") {
        const buttons = modalRef.current?.querySelectorAll<HTMLButtonElement>(
          "button:not(:disabled)",
        );
        if (!buttons?.length) return;
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      if (
        previousFocus instanceof HTMLElement &&
        previousFocus !== document.body &&
        previousFocus.isConnected
      )
        previousFocus.focus();
      else embeddingSaveButton?.focus();
    };
  }, [showWarningModal]);

  useEffect(() => {
    if (!active || rebuildStatus?.state !== "running") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const status = await getIndexRebuildStatus(controller.signal);
        if (controller.signal.aborted) return;
        setRebuildStatus(status);
        if (status.state !== "running") return;
      } catch {
        if (controller.signal.aborted) return;
        setNotice({
          tone: "error",
          text: "暂时无法刷新重建进度，正在重新连接。",
        });
      }
      timer = setTimeout(() => void poll(), 1000);
    };
    timer = setTimeout(() => void poll(), 500);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, rebuildStatus?.state, setNotice]);

  const handleTriggerRebuild = async () => {
    if (rebuildInFlight.current || rebuildStatus?.state === "running") return;
    rebuildInFlight.current = true;
    const controller = new AbortController();
    rebuildRequest.current = controller;
    setIsRebuilding(true);
    try {
      const status = await rebuildIndexes(controller.signal);
      if (!controller.signal.aborted) setRebuildStatus(status);
    } catch (error: unknown) {
      if (!controller.signal.aborted)
        setNotice({
          tone: "error",
          text: error instanceof Error ? error.message : "触发重建索引失败",
        });
    } finally {
      rebuildInFlight.current = false;
      if (!controller.signal.aborted) setIsRebuilding(false);
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
        item.role === role
          ? {
              ...item,
              [field]: value,
              ...(role === "behavior_decision" &&
              field === "provider" &&
              value === "typesafe"
                ? {
                    model: "jev-latest",
                    base_url: "https://api.typesafe.ai/v1",
                    timeout_seconds: 10,
                  }
                : {}),
              ...(["provider", "model", "base_url"].includes(field) &&
              item[field] !== value
                ? { budget: undefined }
                : {}),
            }
          : item,
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
          budget: item.budget,
          enabled: item.enabled,
          ...(apiKey &&
          ["openai_compatible", "typesafe"].includes(item.provider)
            ? { api_key: apiKey }
            : {}),
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
    setSavedConfigurations((current) =>
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
          : result.action
            ? "，结构化决策已验证"
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
      {compact ? (
        <details className="settings-advanced">
          <summary>角色状态与上下文版本</summary>
          <CharacterStateCard snapshot={characterState} />
        </details>
      ) : (
        <CharacterStateCard snapshot={characterState} />
      )}
      {!compact && (
        <div className="model-settings-heading">
          <div>
            <small>MODEL ROUTING</small>
            <strong>模型路由</strong>
          </div>
          <span>五条模型链路</span>
        </div>
      )}
      {compact && (
        <nav className="model-role-navigation" aria-label="模型用途">
          {ROLE_ORDER.map((role) => (
            <button
              key={role}
              type="button"
              aria-current={selectedRole === role ? "page" : undefined}
              onClick={() => setSelectedRole(role)}
            >
              {ROLE_LABELS[role].title}
            </button>
          ))}
        </nav>
      )}
      {ROLE_ORDER.map((role) => {
        const item = byRole.get(role);
        if (!item) return null;
        const dirty =
          JSON.stringify(item) !==
            JSON.stringify(
              savedConfigurations.find((candidate) => candidate.role === role),
            ) || Boolean(apiKeys[role]?.trim());
        const isOpenAi = item.provider === "openai_compatible";
        const isTypeSafe = item.provider === "typesafe";
        const isDecision = role === "behavior_decision";
        const followsChat = item.provider === "inherit_chat";
        const budget = item.budget ?? modelContextBudgetSchema.parse({});
        const changeBudget = <K extends keyof ModelContextBudget>(
          field: K,
          value: ModelContextBudget[K],
        ) =>
          setConfigurations((current) =>
            current.map((candidate) =>
              candidate.role === role
                ? { ...candidate, budget: { ...budget, [field]: value } }
                : candidate,
            ),
          );
        const availableInput = Math.floor(
          Math.min(
            item.context_window - budget.output_reserve_tokens,
            budget.input_token_limit ?? Number.POSITIVE_INFINITY,
          ) /
            (1 + budget.estimate_margin_ratio),
        );
        return (
          <section
            className="model-role-card"
            key={role}
            hidden={compact && selectedRole !== role}
          >
            <header>
              <div>
                <strong>{ROLE_LABELS[role].title}</strong>
                <small>{ROLE_LABELS[role].description}</small>
              </div>
              {!isDecision ? (
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
              ) : null}
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
                {isDecision ? (
                  <>
                    <option value="inherit_chat">跟随聊天模型</option>
                    <option value="typesafe">TypeSafe Jev（原生决策）</option>
                    <option value="openai_compatible">
                      独立模型（OpenAI 兼容）
                    </option>
                  </>
                ) : role === "embedding" ? (
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
            {followsChat ? (
              <p>使用当前聊天模型及其连接配置，保存后下一次决策生效。</p>
            ) : (
              <label>
                <span>模型 ID</span>
                <input
                  aria-label={`${ROLE_LABELS[role].title} 模型 ID`}
                  value={item.model}
                  onChange={(event) =>
                    change(role, "model", event.target.value)
                  }
                />
              </label>
            )}
            {isOpenAi || isTypeSafe ? (
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
            {!followsChat ? (
              <div className="model-role-grid">
                {!isDecision ? (
                  <label>
                    <span>运行上下文窗口</span>
                    <input
                      type="number"
                      min={1024}
                      value={item.context_window}
                      onChange={(event) =>
                        change(
                          role,
                          "context_window",
                          Number(event.target.value),
                        )
                      }
                    />
                  </label>
                ) : null}
                <label>
                  <span>超时（秒）</span>
                  <input
                    type="number"
                    min={1}
                    max={isDecision ? 30 : 600}
                    value={item.timeout_seconds}
                    onChange={(event) =>
                      change(
                        role,
                        "timeout_seconds",
                        Number(event.target.value),
                      )
                    }
                  />
                </label>
              </div>
            ) : null}
            {isDecision ? (
              <p>
                {isTypeSafe
                  ? "Jev 通过 TypeSafe 原生接口选择动作，正式回复仍用聊天模型。"
                  : "独立 OpenAI 兼容模型需支持原生工具调用。"}
                请先保存再测试；测试不会发送 QQ
                消息。决策失败会记录错误并保持静默。
              </p>
            ) : null}
            {role !== "embedding" && !isDecision ? (
              <details className="settings-advanced">
                <summary>高级：模型输入与输出预算</summary>
                <p>
                  预计可发送输入 {Math.max(0, availableInput)} 参考
                  token。参考估算与供应商用量可能不同；请按当前端点验证能力填写，留空表示未知。
                </p>
                <div className="model-role-grid">
                  {(
                    [
                      ["input_token_limit", "模型输入上限", 1, 2000000],
                      ["output_token_limit", "模型输出上限", 1, 2000000],
                      ["max_output_tokens", "请求输出上限", 1, 2000000],
                    ] as const
                  ).map(([field, label, min, max]) => (
                    <label key={field}>
                      <span>{label}</span>
                      <input
                        type="number"
                        min={min}
                        max={max}
                        value={budget[field] ?? ""}
                        onChange={(event) =>
                          changeBudget(
                            field,
                            event.target.value === ""
                              ? null
                              : Number(event.target.value),
                          )
                        }
                      />
                    </label>
                  ))}
                  <label>
                    <span>输出预留 token</span>
                    <input
                      type="number"
                      min={0}
                      max={2000000}
                      value={budget.output_reserve_tokens}
                      onChange={(event) =>
                        changeBudget(
                          "output_reserve_tokens",
                          Number(event.target.value),
                        )
                      }
                    />
                  </label>
                  <label>
                    <span>输入估算余量（%）</span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={budget.estimate_margin_ratio * 100}
                      onChange={(event) =>
                        changeBudget(
                          "estimate_margin_ratio",
                          Number(event.target.value) / 100,
                        )
                      }
                    />
                  </label>
                  <label>
                    <span>分项预算</span>
                    <select
                      value={budget.section_policy}
                      onChange={(event) =>
                        changeBudget(
                          "section_policy",
                          event.target.value as "legacy" | "scaled",
                        )
                      }
                    >
                      <option value="legacy">保留原分项上限</option>
                      <option value="scaled">随输入预算增长</option>
                    </select>
                  </label>
                  {(
                    [
                      ["history_turn_limit", "本会话历史条数", 1, 128],
                      ["memory_candidate_limit", "记忆候选上限", 1, 64],
                      [
                        "tool_result_max_bytes",
                        "工具正文上限（字节）",
                        1024,
                        1048576,
                      ],
                    ] as const
                  ).map(([field, label, min, max]) => (
                    <label key={field}>
                      <span>{label}</span>
                      <input
                        type="number"
                        min={min}
                        max={max}
                        value={budget[field]}
                        onChange={(event) =>
                          changeBudget(field, Number(event.target.value))
                        }
                      />
                    </label>
                  ))}
                </div>
                <p>
                  输出预留会减少输入额度；请求输出上限需要端点支持，不能保证代理执行。增加总窗口不会自动补回网页读取或隐私过滤省略的资料。
                </p>
              </details>
            ) : null}
            <footer>
              <button
                type="button"
                disabled={busy === role || (compact && !dirty)}
                ref={role === "embedding" ? embeddingSaveRef : undefined}
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
              {(isOpenAi || isTypeSafe) && item.api_key_configured ? (
                <button
                  className="danger"
                  type="button"
                  disabled={busy === role}
                  onClick={() => void save(role, true)}
                >
                  移除密钥
                </button>
              ) : null}
              {compact && (
                <span className="settings-draft-status" role="status">
                  {busy === role
                    ? "处理中…"
                    : dirty
                      ? "有未保存的修改"
                      : "已保存"}
                </span>
              )}
            </footer>
            {role === "embedding" &&
            rebuildStatus &&
            rebuildStatus.state !== "idle" ? (
              <div className="reindex-status-card" aria-label="索引重建状态">
                <div className="reindex-status-header">
                  <span>
                    索引状态：{rebuildStateLabel(rebuildStatus.state)}
                  </span>
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
      {showWarningModal
        ? createPortal(
            <div
              className="reindex-modal-backdrop"
              role="presentation"
              onClick={() => setShowWarningModal(false)}
            >
              <div
                ref={modalRef}
                className="reindex-warning-modal settings-material settings-surface settings-controls"
                role="dialog"
                aria-modal="true"
                aria-labelledby="reindex-modal-title"
                aria-describedby="reindex-modal-description"
                onClick={(e) => e.stopPropagation()}
              >
                <h3 id="reindex-modal-title">Embedding 模型已更换</h3>
                <p
                  id="reindex-modal-description"
                  className="reindex-warning-text"
                >
                  embedding
                  模型已更换，现有索引仍由旧模型生成。不重建可能导致漏检、错误匹配或相关度下降；向量维度不兼容的条目将无法参与语义检索。建议重建索引。
                </p>
                <div className="reindex-modal-actions">
                  <button
                    type="button"
                    className="reindex-confirm-button"
                    disabled={
                      isRebuilding || rebuildStatus?.state === "running"
                    }
                    onClick={() => {
                      setShowWarningModal(false);
                      void handleTriggerRebuild();
                    }}
                  >
                    重建索引
                  </button>
                  <button
                    type="button"
                    ref={laterRef}
                    className="reindex-cancel-button"
                    onClick={() => setShowWarningModal(false)}
                  >
                    稍后
                  </button>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
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
