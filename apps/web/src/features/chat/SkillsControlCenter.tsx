import type {
  PluginSnapshot,
  SkillCapability,
  SkillDefinition,
  SkillRunSnapshot,
} from "@chatwaifu/protocol";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ModalPortal } from "./ModalPortal";
import {
  cancelSkillRun,
  decideSkillConfirmation,
  getPlugins,
  getSkillConfirmations,
  getSkillInstructions,
  getSkillRuns,
  getSkills,
  installExamplePlugin,
  installLocalPlugin,
  invokeSkill,
  setPluginEnabled,
  uninstallPlugin,
} from "./runtimeClient";
import type { SkillConfirmation } from "./runtimeClient";

interface Selection {
  skillId: string;
  capability: SkillCapability;
}

export function SkillsControlCenter({
  sessionId,
}: {
  sessionId: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [plugins, setPlugins] = useState<PluginSnapshot[]>([]);
  const [runs, setRuns] = useState<SkillRunSnapshot[]>([]);
  const [confirmations, setConfirmations] = useState<SkillConfirmation[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [argumentsText, setArgumentsText] = useState("{}");
  const [sourcePath, setSourcePath] = useState("");
  const [instructions, setInstructions] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    const [nextSkills, nextPlugins, nextRuns, nextConfirmations] =
      await Promise.all([
        getSkills(),
        getPlugins(),
        getSkillRuns(sessionId),
        getSkillConfirmations(sessionId),
      ]);
    setSkills(nextSkills);
    setPlugins(nextPlugins);
    setRuns(nextRuns);
    setConfirmations(nextConfirmations);
  }, [sessionId]);

  useEffect(() => {
    if (!open) return;
    const initial = window.setTimeout(() => {
      void refresh().catch((error: unknown) => setNotice(errorMessage(error)));
    }, 0);
    const timer = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 900);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [open, refresh]);

  const installedExample = plugins.some(
    (plugin) => plugin.plugin_id === "local.echo",
  );
  const activeRuns = useMemo(
    () => runs.filter((run) => !isTerminal(String(run.state))),
    [runs],
  );

  const act = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key);
    setNotice(null);
    try {
      await action();
      await refresh();
    } catch (error: unknown) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(null);
    }
  };

  const selectCapability = (
    skill: SkillDefinition,
    capability: SkillCapability,
  ) => {
    setSelection({ skillId: skill.skill_id, capability });
    setArgumentsText(defaultArguments(skill.skill_id, capability.name));
  };

  const runSelected = async () => {
    if (!sessionId || !selection) return;
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
        selection.skillId,
        selection.capability.name,
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
    await act(`instructions:${skillId}`, async () => {
      const text = await getSkillInstructions(skillId);
      setInstructions((current) => ({ ...current, [skillId]: text }));
    });
  };

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} disabled={!sessionId}>
        Skills &amp; 插件
      </button>
      {open ? (
        <ModalPortal
          className="skills-center-overlay"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            className="skills-center"
            role="dialog"
            aria-modal="true"
            aria-label="Skills 与插件控制中心"
          >
            <header>
              <div>
                <p>RUNTIME CAPABILITIES</p>
                <h2>Skills &amp; 插件</h2>
                <span>权限授予和每次危险操作确认相互独立</span>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="关闭 Skills 控制中心"
              >
                ×
              </button>
            </header>

            {notice ? (
              <div className="skills-notice" role="alert">
                {notice}
              </div>
            ) : null}

            <div className="skills-center-grid">
              <div className="skills-column">
                <div className="skills-section-title">
                  <strong>已发现 Skills</strong>
                  <small>{skills.length}</small>
                </div>
                {skills.map((skill) => (
                  <article
                    className={`skill-card ${skill.enabled ? "" : "disabled"}`}
                    key={skill.skill_id}
                  >
                    <div className="skill-card-heading">
                      <div>
                        <strong>{skill.name}</strong>
                        <code>
                          {skill.skill_id}@{skill.version}
                        </code>
                      </div>
                      <span>{skill.source}</span>
                    </div>
                    <p>{skill.description}</p>
                    <div className="capability-list">
                      {(skill.capabilities ?? []).map((capability) => (
                        <button
                          type="button"
                          key={capability.name}
                          disabled={!skill.enabled}
                          className={
                            selection?.skillId === skill.skill_id &&
                            selection.capability.name === capability.name
                              ? "selected"
                              : ""
                          }
                          onClick={() => selectCapability(skill, capability)}
                        >
                          <span>{capability.name}</span>
                          <small>{capability.side_effect}</small>
                        </button>
                      ))}
                    </div>
                    <button
                      className="instructions-toggle"
                      type="button"
                      disabled={busy === `instructions:${skill.skill_id}`}
                      onClick={() => void loadInstructions(skill.skill_id)}
                    >
                      {instructions[skill.skill_id]
                        ? "收起 SKILL.md"
                        : "按需加载 SKILL.md"}
                    </button>
                    {instructions[skill.skill_id] ? (
                      <pre>{instructions[skill.skill_id]}</pre>
                    ) : null}
                  </article>
                ))}

                {selection ? (
                  <div className="skill-invoker">
                    <div>
                      <strong>
                        {selection.skillId}.{selection.capability.name}
                      </strong>
                      <small>{selection.capability.description}</small>
                    </div>
                    <textarea
                      rows={5}
                      value={argumentsText}
                      onChange={(event) => setArgumentsText(event.target.value)}
                      aria-label="Skill JSON 参数"
                    />
                    <button
                      type="button"
                      disabled={busy === "invoke"}
                      onClick={() => void runSelected()}
                    >
                      {busy === "invoke" ? "提交中…" : "运行 Skill"}
                    </button>
                  </div>
                ) : null}
              </div>

              <div className="skills-column">
                <div className="skills-section-title">
                  <strong>插件管理</strong>
                  <small>stdio MCP · 软隔离</small>
                </div>
                {!installedExample ? (
                  <button
                    className="install-example"
                    type="button"
                    disabled={busy === "install-example"}
                    onClick={() =>
                      void act("install-example", installExamplePlugin)
                    }
                  >
                    + 安装 Local Echo 测试插件
                  </button>
                ) : null}
                <div className="local-plugin-install">
                  <input
                    value={sourcePath}
                    onChange={(event) => setSourcePath(event.target.value)}
                    placeholder="本地插件目录绝对路径"
                    aria-label="本地插件目录"
                  />
                  <button
                    type="button"
                    disabled={!sourcePath.trim() || busy === "install-local"}
                    onClick={() =>
                      void act("install-local", () =>
                        installLocalPlugin(sourcePath.trim()),
                      )
                    }
                  >
                    安装
                  </button>
                </div>
                {plugins.map((plugin) => (
                  <article className="plugin-card" key={plugin.plugin_id}>
                    <div>
                      <strong>{plugin.name}</strong>
                      <code>
                        {plugin.plugin_id}@{plugin.version}
                      </code>
                    </div>
                    <p>{plugin.description}</p>
                    <div>
                      <button
                        type="button"
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
                        onClick={() => {
                          if (
                            window.confirm(
                              "卸载后插件会移入本地回收目录，确定继续吗？",
                            )
                          ) {
                            void act(`remove:${plugin.plugin_id}`, () =>
                              uninstallPlugin(plugin.plugin_id),
                            );
                          }
                        }}
                      >
                        卸载
                      </button>
                    </div>
                  </article>
                ))}

                {confirmations.length ? (
                  <div className="confirmation-stack">
                    <div className="skills-section-title">
                      <strong>等待确认</strong>
                      <small>{confirmations.length}</small>
                    </div>
                    {confirmations.map((confirmation) => (
                      <article key={confirmation.request_id}>
                        <strong>
                          {confirmation.skill_id}.{confirmation.capability}
                        </strong>
                        <p>{confirmation.reason}</p>
                        <code>
                          {confirmation.side_effect} ·{" "}
                          {confirmation.permissions.join(", ") || "no grant"}
                        </code>
                        <div>
                          <button
                            type="button"
                            onClick={() =>
                              void act(
                                `confirm:${confirmation.request_id}`,
                                () =>
                                  decideSkillConfirmation(
                                    confirmation.request_id,
                                    "allow_once",
                                  ),
                              )
                            }
                          >
                            仅这次
                          </button>
                          {!dangerousSideEffect(confirmation.side_effect) ? (
                            <button
                              type="button"
                              onClick={() =>
                                void act(
                                  `confirm:${confirmation.request_id}`,
                                  () =>
                                    decideSkillConfirmation(
                                      confirmation.request_id,
                                      "allow_session",
                                    ),
                                )
                              }
                            >
                              本会话授权
                            </button>
                          ) : null}
                          {confirmation.side_effect === "read" ? (
                            <button
                              type="button"
                              onClick={() =>
                                void act(
                                  `confirm:${confirmation.request_id}`,
                                  () =>
                                    decideSkillConfirmation(
                                      confirmation.request_id,
                                      "allow_always",
                                    ),
                                )
                              }
                            >
                              始终允许
                            </button>
                          ) : null}
                          <button
                            className="danger"
                            type="button"
                            onClick={() =>
                              void act(
                                `confirm:${confirmation.request_id}`,
                                () =>
                                  decideSkillConfirmation(
                                    confirmation.request_id,
                                    "deny",
                                  ),
                              )
                            }
                          >
                            拒绝
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                ) : null}

                <div className="run-stack">
                  <div className="skills-section-title">
                    <strong>最近运行</strong>
                    <small>{activeRuns.length} active</small>
                  </div>
                  {runs.slice(0, 8).map((run) => (
                    <article key={run.skill_run_id}>
                      <div>
                        <strong>
                          {run.skill_id}.{run.capability}
                        </strong>
                        <span className={`run-state ${run.state}`}>
                          {run.state}
                        </span>
                      </div>
                      {run.error ? <p>{run.error.message}</p> : null}
                      {run.result?.spoken_summary ? (
                        <p>{run.result.spoken_summary}</p>
                      ) : null}
                      {!isTerminal(String(run.state)) &&
                      run.state !== "waiting_for_confirmation" ? (
                        <button
                          type="button"
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
                  ))}
                </div>
              </div>
            </div>
          </section>
        </ModalPortal>
      ) : null}
    </>
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

function dangerousSideEffect(sideEffect: string): boolean {
  return ["destructive", "external_communication", "device_control"].includes(
    sideEffect,
  );
}

function isTerminal(state: string): boolean {
  return ["succeeded", "failed", "cancelled", "expired"].includes(state);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Skills 控制中心操作失败";
}
