import { useEffect, useMemo, useState } from "react";

import {
  getCharacterState,
  getModelConfigurations,
  testModelConfiguration,
  updateModelConfiguration,
} from "./runtimeClient";
import type {
  CharacterKernelSnapshot,
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
  const { busy, notice, setNotice, run } = useSettingsOperation<ModelRole>();

  useEffect(() => {
    let active = true;
    void Promise.all([
      getModelConfigurations(),
      sessionId ? getCharacterState(sessionId) : Promise.resolve(null),
    ])
      .then(([models, state]) => {
        if (!active) return;
        setConfigurations(models);
        setCharacterState(state);
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
          </section>
        );
      })}
      <p className="model-secret-note">
        密钥不会回显或写入浏览器；保存后只进入本机 Runtime 的 0600 私密文件。
      </p>
      <SettingsStatus notice={notice} className="model-settings-notice" />
    </div>
  );
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
