import type {
  DomainEvent,
  PluginSnapshot,
  SkillCapability,
  SkillDefinition,
  SkillRunSnapshot,
} from "@chatwaifu/protocol";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ProductIcon } from "../../components/ProductIcon";
import { SettingsTabs } from "../settings/SettingsTabs";
import { SettingsDialog } from "../settings/SettingsDialog";
import "../settings/settings-extensions.css";
import {
  cancelSkillRun,
  getPlugins,
  getSkillInstructions,
  getSkillRuns,
  getSkills,
  installExamplePlugin,
  installLocalPlugin,
  invokeSkill,
  setPluginEnabled,
  uninstallPlugin,
} from "./runtimeClient";
import { RUNTIME_EVENT_NOTIFICATION } from "./runtimeSocketClient";

type SkillsView = "skills" | "plugins";
interface Selection {
  skillId: string;
  capability: SkillCapability;
}
interface Props {
  sessionId: string | null;
  presentation?: "embedded" | "dialog";
  active?: boolean;
  view?: SkillsView;
}

export function SkillsControlCenter({
  sessionId,
  presentation = "dialog",
  active = true,
  view,
}: Props) {
  const [open, setOpen] = useState(false);
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  const [tab, setTab] = useState<SkillsView>("skills");
  const workspace = (
    <SkillsWorkspace
      key={sessionId}
      sessionId={sessionId}
      active={active && (presentation === "embedded" || open)}
      view={view ?? tab}
    />
  );
  if (presentation === "embedded") return workspace;
  return (
    <>
      <button
        type="button"
        onClick={(event) => {
          setOpener(event.currentTarget);
          setOpen(true);
        }}
        disabled={!sessionId}
      >
        <ProductIcon name="skills" />
        Skills &amp; 插件
      </button>
      {open ? (
        <SettingsDialog
          title="Skills 与插件"
          description="查看能力、管理插件和检查运行记录。权限授予与每次操作确认分别管理。"
          label="Skills 与插件控制中心"
          closeLabel="关闭 Skills 控制中心"
          returnFocusTo={opener}
          onClose={() => setOpen(false)}
        >
          <SettingsTabs
            label="Skills 与插件"
            prefix="skills-dialog"
            tabs={[
              { id: "skills", label: "Skills" },
              { id: "plugins", label: "插件" },
            ]}
            selected={tab}
            onSelect={setTab}
          />
          <div
            role="tabpanel"
            id={`skills-dialog-content-${tab}`}
            aria-labelledby={`skills-dialog-tab-${tab}`}
          >
            {workspace}
          </div>
        </SettingsDialog>
      ) : null}
    </>
  );
}

function SkillsWorkspace({
  sessionId,
  active,
  view,
}: {
  sessionId: string | null;
  active: boolean;
  view: SkillsView;
}) {
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [plugins, setPlugins] = useState<PluginSnapshot[]>([]);
  const [runs, setRuns] = useState<SkillRunSnapshot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [query, setQuery] = useState("");
  const [argumentsText, setArgumentsText] = useState("{}");
  const [sourcePath, setSourcePath] = useState("");
  const [instructions, setInstructions] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const revision = useRef(0);
  const refreshRevision = useRef(0);
  const operation = useRef(false);
  const refresh = useCallback(async () => {
    if (!sessionId) return;
    const epoch = revision.current;
    const request = ++refreshRevision.current;
    try {
      const [nextSkills, nextPlugins, nextRuns] = await Promise.all([
        getSkills(),
        getPlugins(),
        getSkillRuns(sessionId),
      ]);
      if (epoch !== revision.current || request !== refreshRevision.current)
        return;
      setSkills(nextSkills);
      setPlugins(nextPlugins);
      setRuns(nextRuns);
      setLoaded(true);
      setNotice(null);
    } catch (error) {
      if (epoch === revision.current && request === refreshRevision.current)
        setNotice(errorMessage(error));
    } finally {
      if (epoch === revision.current && request === refreshRevision.current)
        setLoading(false);
    }
  }, [sessionId]);
  useEffect(() => {
    revision.current++;
    if (!active || !sessionId) return;
    const currentEpoch = revision.current;
    void Promise.resolve().then(() => {
      if (revision.current === currentEpoch) void refresh();
    });
    const refreshFromEvent = (raw: Event) => {
      const event = (raw as CustomEvent<DomainEvent>).detail.event_type;
      if (
        typeof event === "string" &&
        (event.startsWith("skill.") || event.startsWith("tool."))
      )
        void refresh();
    };
    window.addEventListener(RUNTIME_EVENT_NOTIFICATION, refreshFromEvent);
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => {
      revision.current = currentEpoch + 1;
      window.clearInterval(timer);
      window.removeEventListener(RUNTIME_EVENT_NOTIFICATION, refreshFromEvent);
    };
  }, [active, refresh, sessionId]);

  const act = async (key: string, action: () => Promise<unknown>) => {
    if (operation.current) return;
    operation.current = true;
    const epoch = revision.current;
    setBusy(key);
    setNotice(null);
    try {
      await action();
      if (epoch === revision.current) await refresh();
    } catch (error) {
      if (epoch === revision.current) setNotice(errorMessage(error));
    } finally {
      operation.current = false;
      setBusy(null);
    }
  };
  const filtered = useMemo(() => {
    const search = query.trim().toLocaleLowerCase();
    return skills.filter((skill) =>
      `${skill.name} ${skill.skill_id} ${skill.description}`
        .toLocaleLowerCase()
        .includes(search),
    );
  }, [skills, query]);
  const selected =
    skills.find((skill) => skill.skill_id === selectedId) ?? filtered[0];
  const currentSelection =
    selection?.skillId === selected?.skill_id ? selection : null;
  const selectCapability = (
    skill: SkillDefinition,
    capability: SkillCapability,
  ) => {
    setSelection({ skillId: skill.skill_id, capability });
    setArgumentsText(defaultArguments(skill.skill_id, capability.name));
  };
  const runSelected = async () => {
    if (!sessionId || !currentSelection) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(argumentsText);
    } catch {
      setNotice("参数必须是合法 JSON。");
      return;
    }
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed)
    ) {
      setNotice("参数顶层必须是 JSON 对象。");
      return;
    }
    await act("invoke", () =>
      invokeSkill(
        sessionId,
        currentSelection.skillId,
        currentSelection.capability.name,
        parsed as Record<string, unknown>,
      ),
    );
  };
  const loadInstructions = async (skillId: string) => {
    if (instructions[skillId]) {
      setInstructions((current) => {
        const next = { ...current };
        delete next[skillId];
        return next;
      });
      return;
    }
    const epoch = revision.current;
    await act(`instructions:${skillId}`, async () => {
      const text = await getSkillInstructions(skillId);
      if (epoch === revision.current)
        setInstructions((current) => ({ ...current, [skillId]: text }));
    });
  };
  if (!sessionId)
    return (
      <div className="extensions-empty" role="status">
        <strong>等待 Runtime 连接</strong>
        <p>连接后可查看 Skills、插件和运行记录。</p>
      </div>
    );
  return (
    <section
      className="extensions-panel"
      aria-label={view === "skills" ? "Skills 管理" : "插件管理"}
    >
      {notice ? (
        <div className="extensions-notice" role="alert">
          {notice}
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void refresh()}
          >
            重新读取
          </button>
        </div>
      ) : null}
      <header className="extensions-toolbar">
        <div>
          <h2>{view === "skills" ? "可用 Skills" : "已安装插件"}</h2>
          <p>
            {view === "skills"
              ? `${skills.length} 项能力 · 按需查看详情和运行参数`
              : summarizeSandbox(plugins)}
          </p>
        </div>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void refresh()}
        >
          <ProductIcon name="refresh" />
          刷新
        </button>
      </header>
      {loading && !loaded ? (
        <p className="extensions-loading" role="status">
          正在读取能力与插件…
        </p>
      ) : null}
      {view === "skills" ? (
        <>
          <div className="skills-workspace">
            <aside className="skills-browser" aria-label="Skills 列表">
              <label className="extensions-search">
                <span>查找 Skill</span>
                <input
                  type="search"
                  aria-label="查找 Skill"
                  placeholder="搜索名称、说明或 ID…"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <div className="skills-list">
                {filtered.map((skill) => (
                  <button
                    type="button"
                    className="skill-list-item"
                    key={skill.skill_id}
                    aria-label={`选择 ${skill.name}`}
                    aria-pressed={selected?.skill_id === skill.skill_id}
                    onClick={() => {
                      setSelectedId(skill.skill_id);
                      setSelection(null);
                    }}
                  >
                    <span>
                      <strong>{skill.name}</strong>
                      <small>{skill.skill_id}</small>
                    </span>
                    <span className="extension-badge">
                      {skill.enabled ? sourceLabel(skill.source) : "已停用"}
                    </span>
                  </button>
                ))}
                {!filtered.length && !loading ? (
                  <div className="extensions-empty" role="status">
                    <strong>
                      {query ? "没有匹配的 Skill" : "暂无可用 Skills"}
                    </strong>
                    <p>
                      {query
                        ? "试试其他名称，或清空搜索。"
                        : "刷新能力目录，或在插件页安装所需能力。"}
                    </p>
                  </div>
                ) : null}
              </div>
            </aside>
            <div className="skill-detail">
              {selected ? (
                <article className="extension-card" aria-label="Skill 详情">
                  <header>
                    <div>
                      <h3>{selected.name}</h3>
                      <code>
                        {selected.skill_id}@{selected.version}
                      </code>
                    </div>
                    <span className="extension-badge">
                      {selected.enabled
                        ? sourceLabel(selected.source)
                        : "已停用"}
                    </span>
                  </header>
                  <p>{selected.description}</p>
                  <div className="capability-list">
                    {(selected.capabilities ?? []).map((capability) => (
                      <button
                        type="button"
                        key={capability.name}
                        disabled={!selected.enabled || busy !== null}
                        aria-pressed={
                          currentSelection?.capability.name === capability.name
                        }
                        onClick={() => selectCapability(selected, capability)}
                      >
                        <span>{capability.name}</span>
                        <small>{effectLabel(capability.side_effect)}</small>
                      </button>
                    ))}
                  </div>
                  {currentSelection ? (
                    <div className="skill-invoke-form">
                      <h4>{currentSelection.capability.name}</h4>
                      <p>{currentSelection.capability.description}</p>
                      <label>
                        <span>JSON 参数</span>
                        <textarea
                          rows={5}
                          value={argumentsText}
                          onChange={(event) =>
                            setArgumentsText(event.target.value)
                          }
                          aria-label="Skill JSON 参数"
                        />
                      </label>
                      <button
                        className="extension-primary"
                        type="button"
                        disabled={busy !== null}
                        onClick={() => void runSelected()}
                      >
                        {busy === "invoke" ? "提交中…" : "运行 Skill"}
                      </button>
                    </div>
                  ) : (
                    <p className="extension-hint">
                      选择一项能力，查看参数并运行。写入操作仍受当前权限与确认策略约束。
                    </p>
                  )}
                  <button
                    type="button"
                    className="instructions-toggle"
                    disabled={busy !== null}
                    onClick={() => void loadInstructions(selected.skill_id)}
                  >
                    {instructions[selected.skill_id]
                      ? "收起 SKILL.md"
                      : "按需加载 SKILL.md"}
                  </button>
                  {instructions[selected.skill_id] ? (
                    <pre className="extension-instructions">
                      {instructions[selected.skill_id]}
                    </pre>
                  ) : null}
                </article>
              ) : null}
            </div>
          </div>
          <section className="extensions-runs" aria-label="最近运行">
            <header>
              <h3>最近运行</h3>
              <span>
                {runs.filter((run) => !isTerminal(String(run.state))).length}{" "}
                项进行中
              </span>
            </header>
            {!runs.length ? (
              <div className="extensions-empty">
                <strong>还没有运行记录</strong>
                <p>运行 Skill 后，结果和执行状态会显示在这里。</p>
              </div>
            ) : (
              runs.slice(0, 8).map((run) => (
                <article className="extension-card" key={run.skill_run_id}>
                  <header>
                    <strong>
                      {run.skill_id}.{run.capability}
                    </strong>
                    <span className={`run-state ${run.state}`}>
                      {run.state}
                    </span>
                  </header>
                  {run.error ? <p>{run.error.message}</p> : null}
                  {run.result?.spoken_summary ? (
                    <p>{run.result.spoken_summary}</p>
                  ) : null}
                  {!isTerminal(String(run.state)) &&
                  run.state !== "waiting_for_confirmation" ? (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() =>
                        void act(`cancel:${run.skill_run_id}`, () =>
                          cancelSkillRun(run.skill_run_id),
                        )
                      }
                    >
                      取消
                    </button>
                  ) : null}
                </article>
              ))
            )}
          </section>
        </>
      ) : (
        <>
          <section className="plugin-install extension-card">
            <h3>安装插件</h3>
            <label>
              <span>本地插件目录</span>
              <div className="local-plugin-install">
                <input
                  value={sourcePath}
                  onChange={(event) => setSourcePath(event.target.value)}
                  placeholder="插件目录的绝对路径…"
                  aria-label="本地插件目录"
                />
                <button
                  className="extension-primary"
                  type="button"
                  disabled={!sourcePath.trim() || busy !== null}
                  onClick={() =>
                    void act("install-local", () =>
                      installLocalPlugin(sourcePath.trim()),
                    )
                  }
                >
                  安装
                </button>
              </div>
            </label>
            {!plugins.some((plugin) => plugin.plugin_id === "local.echo") ? (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() =>
                  void act("install-example", installExamplePlugin)
                }
              >
                <ProductIcon name="plus" />
                安装 Local Echo 测试插件
              </button>
            ) : null}
          </section>
          {!plugins.length && !loading ? (
            <div className="extensions-empty">
              <strong>尚未安装插件</strong>
              <p>从本地目录安装插件，或使用 Local Echo 检查运行环境。</p>
            </div>
          ) : (
            plugins.map((plugin) => (
              <article className="extension-card" key={plugin.plugin_id}>
                <header>
                  <div>
                    <h3>{plugin.name}</h3>
                    <code>
                      {plugin.plugin_id}@{plugin.version}
                    </code>
                  </div>
                  <span className="extension-badge">
                    {plugin.enabled ? "已启用" : "已停用"}
                  </span>
                </header>
                <p>{plugin.description}</p>
                <p className="extension-hint">
                  隔离：{pluginSandboxLabel(plugin)} · 网络：
                  {plugin.network_policy ?? "deny"}
                </p>
                <footer>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() =>
                      void act(`toggle:${plugin.plugin_id}`, () =>
                        setPluginEnabled(plugin.plugin_id, !plugin.enabled),
                      )
                    }
                  >
                    {plugin.enabled ? "停用" : "启用"}
                  </button>
                  <button
                    className="danger"
                    type="button"
                    disabled={busy !== null}
                    onClick={() => {
                      if (
                        window.confirm(
                          "卸载后插件会移入本地回收目录，确定继续吗？",
                        )
                      )
                        void act(`remove:${plugin.plugin_id}`, () =>
                          uninstallPlugin(plugin.plugin_id),
                        );
                    }}
                  >
                    卸载
                  </button>
                </footer>
              </article>
            ))
          )}
        </>
      )}
    </section>
  );
}
function defaultArguments(skillId: string, capability: string): string {
  if (skillId === "local.echo" && capability === "echo")
    return '{\n  "text": "你好，MCP"\n}';
  if (skillId === "local.echo" && capability === "append_note")
    return '{\n  "text": "本地测试笔记"\n}';
  if (skillId === "local.echo" && capability === "wait")
    return '{\n  "seconds": 1\n}';
  return "{}";
}
function isTerminal(state: string): boolean {
  return ["succeeded", "failed", "cancelled", "expired"].includes(state);
}
function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Skills 控制中心操作失败，请重试。";
}
function pluginSandboxLabel(plugin: PluginSnapshot): string {
  if (plugin.sandbox_mode === "disabled") return "已关闭";
  if (plugin.sandbox_backend) return plugin.sandbox_backend;
  return plugin.sandbox_mode === "required" ? "等待受控启动" : "尚未验证";
}
function summarizeSandbox(plugins: PluginSnapshot[]): string {
  if (!plugins.length) return "插件安装后显示隔离状态";
  return `隔离：${[...new Set(plugins.map(pluginSandboxLabel))].join(" / ")}`;
}
function sourceLabel(source: string | undefined): string {
  return source === "builtin" ? "内置" : (source ?? "来源未提供");
}
function effectLabel(effect: string | undefined): string {
  if (!effect) return "未提供操作类型";
  return (
    (
      { read: "只读", write: "写入", external: "外部操作" } as Record<
        string,
        string
      >
    )[effect] ?? effect
  );
}
