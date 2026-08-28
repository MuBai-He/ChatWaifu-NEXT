import { useEffect, useMemo, useState } from "react";

import { ModalPortal } from "../chat/ModalPortal";
import {
  createMcpConnection,
  deleteMcpConnection,
  getMcpCapabilities,
  getMcpConnections,
  getMcpPrompt,
  readMcpResource,
  testMcpConnection,
  updateMcpConnection,
} from "../chat/runtimeClient";
import type {
  McpCapabilitiesSnapshot,
  McpConnectionInput,
  McpConnectionSnapshot,
  McpNetworkPolicy,
  McpSandboxMode,
  McpTransport,
  McpTrustLevel,
} from "../chat/runtimeClient";
import { SettingsSecretField, SettingsStatus } from "../settings/SettingsFields";
import { useSettingsOperation } from "../settings/useSettingsOperation";
import type { SettingsNotice } from "../settings/useSettingsOperation";

import "./mcp-connections.css";

interface ConnectionDraft {
  name: string;
  transport: McpTransport;
  commandText: string;
  url: string;
  bearerToken: string;
  enabled: boolean;
  allowRemote: boolean;
  timeoutSeconds: number;
  trustLevel: McpTrustLevel;
  sandboxMode: McpSandboxMode;
  networkPolicy: McpNetworkPolicy;
}

function emptyDraft(): ConnectionDraft {
  return {
    name: "",
    transport: "stdio",
    commandText: "",
    url: "",
    bearerToken: "",
    enabled: true,
    allowRemote: false,
    timeoutSeconds: 30,
    trustLevel: "untrusted",
    sandboxMode: "required",
    networkPolicy: "deny",
  };
}

export function McpConnectionsPanel() {
  const [open, setOpen] = useState(false);
  const [connections, setConnections] = useState<McpConnectionSnapshot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ConnectionDraft>(() => emptyDraft());
  const [capabilities, setCapabilities] =
    useState<McpCapabilitiesSnapshot | null>(null);
  const [capabilityResult, setCapabilityResult] = useState<string | null>(null);
  const [promptArguments, setPromptArguments] = useState("{}");
  const { busy, notice, setNotice, run } = useSettingsOperation<string>();

  const selectedConnection = useMemo(
    () =>
      connections.find((connection) => connection.connection_id === selectedId) ??
      null,
    [connections, selectedId],
  );

  useEffect(() => {
    if (!open) return;
    let active = true;
    void getMcpConnections()
      .then((items) => {
        if (!active) return;
        setConnections(items);
        const first = items[0];
        if (first) {
          setSelectedId(first.connection_id);
          setDraft(draftFromConnection(first));
          setCapabilities(first.capabilities ?? null);
        } else {
          startNewConnection(setSelectedId, setDraft);
        }
        setNotice(null);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setNotice({
          tone: "error",
          text: error instanceof Error ? error.message : "读取 MCP 连接失败",
        });
      });
    return () => {
      active = false;
    };
  }, [open, setNotice]);

  const selectConnection = (connection: McpConnectionSnapshot) => {
    setSelectedId(connection.connection_id);
    setDraft(draftFromConnection(connection));
    setCapabilities(connection.capabilities ?? null);
    setCapabilityResult(null);
    setPromptArguments("{}");
    setNotice(null);
  };

  const newConnection = () => {
    startNewConnection(setSelectedId, setDraft);
    setCapabilities(null);
    setCapabilityResult(null);
    setPromptArguments("{}");
    setNotice(null);
  };

  const change = <Key extends keyof ConnectionDraft>(
    key: Key,
    value: ConnectionDraft[Key],
  ) => setDraft((current) => ({ ...current, [key]: value }));

  const save = async () => {
    const payload = validateAndBuildPayload(draft, setNotice);
    if (!payload) return;
    const wasNew = selectedId === null;
    const saved = await run(
      "save",
      () =>
        wasNew
          ? createMcpConnection(payload)
          : updateMcpConnection(selectedId, payload),
      {
        pending: "正在保存 MCP 连接…",
        success: wasNew ? "MCP 连接已创建" : "MCP 连接已保存",
        error: "保存 MCP 连接失败",
      },
    );
    if (!saved) return;
    setConnections((current) => upsertConnection(current, saved));
    setSelectedId(saved.connection_id);
    setDraft(draftFromConnection(saved));
    setCapabilities(saved.capabilities ?? null);
  };

  const toggleConnection = async (connection: McpConnectionSnapshot) => {
    const updated = await run(
      `toggle:${connection.connection_id}`,
      () =>
        updateMcpConnection(
          connection.connection_id,
          payloadFromConnection(connection, !connection.enabled),
        ),
      {
        success: connection.enabled ? "MCP 连接已停用" : "MCP 连接已启用",
        error: "切换 MCP 连接失败",
      },
    );
    if (!updated) return;
    setConnections((current) => upsertConnection(current, updated));
    if (selectedId === updated.connection_id) {
      setDraft(draftFromConnection(updated));
      setCapabilities(updated.capabilities ?? capabilities);
    }
  };

  const clearBearerToken = async (connection: McpConnectionSnapshot) => {
    const updated = await run(
      `clear-token:${connection.connection_id}`,
      () =>
        updateMcpConnection(connection.connection_id, {
          ...payloadFromConnection(connection, connection.enabled),
          clear_bearer_token: true,
        }),
      { success: "Bearer Token 已移除", error: "移除 Bearer Token 失败" },
    );
    if (!updated) return;
    setConnections((current) => upsertConnection(current, updated));
    setDraft(draftFromConnection(updated));
    setCapabilities(updated.capabilities ?? capabilities);
  };

  const removeConnection = async (connection: McpConnectionSnapshot) => {
    if (!window.confirm(`确定删除 MCP 连接“${connection.name}”吗？`)) return;
    const removedId = await run(
      `delete:${connection.connection_id}`,
      async () => {
        await deleteMcpConnection(connection.connection_id);
        return connection.connection_id;
      },
      { success: "MCP 连接已删除", error: "删除 MCP 连接失败" },
    );
    if (!removedId) return;
    const remaining = connections.filter(
      (candidate) => candidate.connection_id !== connection.connection_id,
    );
    setConnections(remaining);
    const next = remaining[0];
    if (next) selectConnection(next);
    else newConnection();
  };

  const probe = async () => {
    if (!selectedConnection) {
      setNotice({ tone: "info", text: "请先保存连接，再测试握手。" });
      return;
    }
    const result = await run(
      `test:${selectedConnection.connection_id}`,
      () => testMcpConnection(selectedConnection.connection_id),
      {
        pending: "正在执行 MCP initialize 与能力探测…",
        success: (result) => {
          const latency =
            "latency_ms" in result && result.latency_ms
              ? `，${result.latency_ms} ms`
              : "";
          const protocolVersion =
            "connection_id" in result
              ? result.capabilities?.protocol_version
              : result.protocol_version;
          const version = protocolVersion
            ? `，协议 ${protocolVersion}`
            : "";
          return `连接测试 ${result.status}${latency}${version}`;
        },
        error: "MCP 连接测试失败",
      },
    );
    if (result && "connection_id" in result) {
      setConnections((current) => upsertConnection(current, result));
      setDraft(draftFromConnection(result));
      setCapabilities(result.capabilities ?? null);
    }
  };

  const refreshCapabilities = async () => {
    if (!selectedConnection) {
      setNotice({ tone: "info", text: "请先保存连接，再读取能力。" });
      return;
    }
    const next = await run(
      `capabilities:${selectedConnection.connection_id}`,
      () => getMcpCapabilities(selectedConnection.connection_id),
      {
        pending: "正在读取 tools、resources、resource templates 与 prompts…",
        success: (result) =>
          `已发现 ${result.tools.length} 个工具、${result.resources.length} 个资源、${result.resource_templates.length} 个资源模板、${result.prompts.length} 个提示模板`,
        error: "读取 MCP 能力失败",
      },
    );
    if (next) setCapabilities(next);
  };

  const readResource = async (uri: string) => {
    if (!selectedConnection) return;
    const result = await run(
      `resource:${uri}`,
      () => readMcpResource(selectedConnection.connection_id, uri),
      { success: "资源读取完成", error: "读取 MCP 资源失败" },
    );
    if (result !== undefined) setCapabilityResult(formatResult(result));
  };

  const getPrompt = async (name: string) => {
    if (!selectedConnection) return;
    let args: unknown;
    try {
      args = JSON.parse(promptArguments);
    } catch {
      setNotice({ tone: "error", text: "Prompt 参数必须是合法 JSON。" });
      return;
    }
    if (typeof args !== "object" || args === null || Array.isArray(args)) {
      setNotice({ tone: "error", text: "Prompt 参数顶层必须是 JSON 对象。" });
      return;
    }
    if (Object.values(args).some((value) => typeof value !== "string")) {
      setNotice({ tone: "error", text: "Prompt 参数值必须全部是字符串。" });
      return;
    }
    const result = await run(
      `prompt:${name}`,
      () =>
        getMcpPrompt(
          selectedConnection.connection_id,
          name,
          args as Record<string, string>,
        ),
      { success: "Prompt 已获取", error: "获取 MCP Prompt 失败" },
    );
    if (result !== undefined) setCapabilityResult(formatResult(result));
  };

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        MCP 连接
      </button>
      {open ? (
        <ModalPortal
          className="mcp-connections-overlay"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            className="mcp-connections-panel"
            role="dialog"
            aria-modal="true"
            aria-label="MCP 连接管理"
          >
            <header>
              <div>
                <p>MODEL CONTEXT PROTOCOL</p>
                <h2>MCP 连接</h2>
                <span>统一管理本地 stdio 与远程 MCP 服务</span>
              </div>
              <button
                type="button"
                aria-label="关闭 MCP 连接管理"
                onClick={() => setOpen(false)}
              >
                ×
              </button>
            </header>

            <SettingsStatus notice={notice} className="mcp-connections-notice" />

            <div className="mcp-connections-layout">
              <aside className="mcp-connection-list">
                <div>
                  <strong>连接</strong>
                  <button type="button" onClick={newConnection}>
                    新建连接
                  </button>
                </div>
                {connections.length ? (
                  connections.map((connection) => (
                    <article
                      className={
                        selectedId === connection.connection_id ? "selected" : ""
                      }
                      key={connection.connection_id}
                    >
                      <button
                        type="button"
                        onClick={() => selectConnection(connection)}
                      >
                        <span>
                          <i className={connection.enabled ? "enabled" : ""} />
                          <strong>{connection.name}</strong>
                        </span>
                        <small>
                          {transportLabel(connection.transport)} · {connection.connection_id}
                        </small>
                      </button>
                      <div>
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() => void toggleConnection(connection)}
                        >
                          {connection.enabled ? "停用" : "启用"}
                        </button>
                        <button
                          type="button"
                          className="danger"
                          disabled={busy !== null}
                          onClick={() => void removeConnection(connection)}
                        >
                          删除
                        </button>
                      </div>
                    </article>
                  ))
                ) : (
                  <p>尚未配置 MCP 连接。</p>
                )}
              </aside>

              <main className="mcp-connection-workspace">
                <ConnectionEditor
                  draft={draft}
                  existing={selectedConnection}
                  busy={busy !== null}
                  onChange={change}
                  onSave={() => void save()}
                  onTest={() => void probe()}
                  onCapabilities={() => void refreshCapabilities()}
                  onClearToken={() => {
                    if (selectedConnection) {
                      void clearBearerToken(selectedConnection);
                    }
                  }}
                />
                <CapabilitiesBrowser
                  capabilities={capabilities}
                  result={capabilityResult}
                  promptArguments={promptArguments}
                  busy={busy !== null}
                  onPromptArguments={setPromptArguments}
                  onReadResource={(uri) => void readResource(uri)}
                  onGetPrompt={(name) => void getPrompt(name)}
                />
              </main>
            </div>
          </section>
        </ModalPortal>
      ) : null}
    </>
  );
}

function ConnectionEditor({
  draft,
  existing,
  busy,
  onChange,
  onSave,
  onTest,
  onCapabilities,
  onClearToken,
}: {
  draft: ConnectionDraft;
  existing: McpConnectionSnapshot | null;
  busy: boolean;
  onChange: <Key extends keyof ConnectionDraft>(
    key: Key,
    value: ConnectionDraft[Key],
  ) => void;
  onSave: () => void;
  onTest: () => void;
  onCapabilities: () => void;
  onClearToken: () => void;
}) {
  const isRemote = draft.transport !== "stdio";
  return (
    <section className="mcp-connection-editor" aria-label="MCP 连接配置">
      <header>
        <div>
          <strong>{existing ? existing.name : "新建 MCP 连接"}</strong>
          <small>
            {existing
              ? `${existing.connection_id} · ${transportLabel(existing.transport)}`
              : "连接 ID 保存后不可修改"}
          </small>
        </div>
        {existing?.bearer_token_configured ? <span>令牌已保存</span> : null}
      </header>

      <div className="mcp-connection-form-grid">
        <label>
          <span>连接名称</span>
          <input
            aria-label="MCP 连接名称"
            value={draft.name}
            disabled={busy}
            onChange={(event) => onChange("name", event.currentTarget.value)}
            placeholder="例如：本地文件工具"
          />
        </label>
        <label>
          <span>连接 ID（自动生成）</span>
          <input
            aria-label="MCP 连接 ID"
            value={existing?.connection_id ?? "保存后由 Runtime 生成"}
            disabled
            readOnly
          />
        </label>
        <label>
          <span>传输类型</span>
          <select
            aria-label="MCP 传输类型"
            value={draft.transport}
            disabled={busy}
            onChange={(event) => {
              const transport = event.currentTarget.value as McpTransport;
              onChange("transport", transport);
              onChange(
                "sandboxMode",
                transport === "stdio" ? "required" : "disabled",
              );
              onChange("allowRemote", false);
              onChange("networkPolicy", transport === "stdio" ? "deny" : "loopback");
            }}
          >
            <option value="stdio">本地 stdio</option>
            <option value="streamable_http">Streamable HTTP</option>
            <option value="sse">SSE（兼容旧服务）</option>
          </select>
        </label>
        {isRemote ? (
          <div className="mcp-connection-static-field">
            <span>沙箱策略</span>
            <strong>远程传输不适用本地进程沙箱</strong>
          </div>
        ) : (
          <label>
            <span>沙箱策略</span>
            <select
              aria-label="MCP 沙箱策略"
              value={draft.sandboxMode}
              disabled={busy}
              onChange={(event) =>
                onChange(
                  "sandboxMode",
                  event.currentTarget.value as McpSandboxMode,
                )
              }
            >
              <option value="required">必须启用，否则拒绝启动</option>
              <option value="preferred">优先启用</option>
              <option value="disabled">关闭沙箱</option>
            </select>
          </label>
        )}
        <label>
          <span>信任等级</span>
          <select
            aria-label="MCP 信任等级"
            value={draft.trustLevel}
            disabled={busy}
            onChange={(event) =>
              onChange("trustLevel", event.currentTarget.value as McpTrustLevel)
            }
          >
            <option value="untrusted">不受信任（推荐）</option>
            <option value="trusted">受信任</option>
          </select>
        </label>
        <label>
          <span>超时（秒）</span>
          <input
            aria-label="MCP 超时秒数"
            type="number"
            min={1}
            max={600}
            value={draft.timeoutSeconds}
            disabled={busy}
            onChange={(event) =>
              onChange("timeoutSeconds", Number(event.currentTarget.value))
            }
          />
        </label>
        {!isRemote ? (
          <label>
            <span>子进程网络策略</span>
            <select
              aria-label="MCP 网络策略"
              value={draft.networkPolicy}
              disabled={busy}
              onChange={(event) =>
                onChange(
                  "networkPolicy",
                  event.currentTarget.value as McpNetworkPolicy,
                )
              }
            >
              <option value="deny">禁止网络</option>
              <option value="loopback">仅允许本机回环</option>
              <option value="allow">允许网络</option>
            </select>
          </label>
        ) : null}
      </div>

      {isRemote ? (
        <>
          <label className="mcp-connection-wide-field">
            <span>服务 URL</span>
            <input
              aria-label="MCP 服务 URL"
              type="url"
              value={draft.url}
              disabled={busy}
              onChange={(event) => onChange("url", event.currentTarget.value)}
              placeholder="https://example.com/mcp"
            />
          </label>
          <SettingsSecretField
            label="Bearer Token"
            ariaLabel="MCP Bearer Token"
            configured={existing?.bearer_token_configured ?? false}
            value={draft.bearerToken}
            disabled={busy}
            help="不会回显或写入浏览器；留空保持已保存令牌。"
            onChange={(value) => onChange("bearerToken", value)}
          />
          {existing?.bearer_token_configured ? (
            <button
              className="mcp-clear-secret"
              type="button"
              disabled={busy}
              onClick={onClearToken}
            >
              移除已保存令牌
            </button>
          ) : null}
          <label className="mcp-connection-check-row">
            <span>
              <strong>允许远程网络连接</strong>
              <small>非回环地址只有开启后才允许连接。</small>
            </span>
            <input
              aria-label="允许远程网络连接"
              type="checkbox"
              role="switch"
              checked={draft.allowRemote}
              disabled={busy}
              onChange={(event) => {
                const allowRemote = event.currentTarget.checked;
                onChange("allowRemote", allowRemote);
                onChange("networkPolicy", allowRemote ? "allow" : "loopback");
              }}
            />
          </label>
          <p className="mcp-risk-note">
            远程 MCP 服务能看到发送给它的参数和读取请求。只连接可信服务；优先使用
            HTTPS。SSE 仅用于兼容旧服务，新接入优先 Streamable HTTP。
          </p>
        </>
      ) : (
        <label className="mcp-connection-wide-field">
          <span>启动命令</span>
          <textarea
            aria-label="MCP 启动命令"
            rows={4}
            value={draft.commandText}
            disabled={busy}
            onChange={(event) =>
              onChange("commandText", event.currentTarget.value)
            }
            placeholder={"每行一个参数，例如：\npython\n-I\n/path/server.py"}
          />
          <small>每行一个参数，不经过 Shell；带空格的路径保持在同一行。</small>
        </label>
      )}

      <label className="mcp-connection-check-row">
        <span>
          <strong>启用连接</strong>
          <small>停用后不会向 Agent 暴露该服务的能力。</small>
        </span>
        <input
          aria-label="启用 MCP 连接"
          type="checkbox"
          role="switch"
          checked={draft.enabled}
          disabled={busy}
          onChange={(event) => onChange("enabled", event.currentTarget.checked)}
        />
      </label>

      {!isRemote && draft.sandboxMode === "disabled" ? (
        <p className="mcp-risk-note danger">
          沙箱已关闭。MCP 进程可能访问当前用户可访问的文件和网络，请仅用于可信服务。
        </p>
      ) : null}
      {draft.trustLevel === "trusted" ? (
        <p className="mcp-risk-note danger">
          “受信任”会放宽部分防护策略，但不会绕过工具权限和危险操作确认。
        </p>
      ) : null}
      {!isRemote && draft.networkPolicy === "allow" ? (
        <p className="mcp-risk-note danger">
          此本地 MCP 子进程被允许访问网络。请确认命令与依赖来源可信。
        </p>
      ) : null}

      <footer>
        <button type="button" disabled={busy} onClick={onSave}>
          保存连接
        </button>
        <button type="button" disabled={busy || !existing} onClick={onTest}>
          测试连接
        </button>
        <button
          type="button"
          disabled={busy || !existing}
          onClick={onCapabilities}
        >
          刷新能力
        </button>
      </footer>
    </section>
  );
}

function CapabilitiesBrowser({
  capabilities,
  result,
  promptArguments,
  busy,
  onPromptArguments,
  onReadResource,
  onGetPrompt,
}: {
  capabilities: McpCapabilitiesSnapshot | null;
  result: string | null;
  promptArguments: string;
  busy: boolean;
  onPromptArguments: (value: string) => void;
  onReadResource: (uri: string) => void;
  onGetPrompt: (name: string) => void;
}) {
  return (
    <section className="mcp-capabilities" aria-label="MCP 能力浏览">
      <header>
        <strong>能力浏览</strong>
        <small>tools · resources/templates · prompts</small>
      </header>
      {!capabilities ? (
        <p className="mcp-capabilities-empty">
          保存并测试连接后，点击“刷新能力”查看服务公开的能力。
        </p>
      ) : (
        <div className="mcp-capability-grid">
          <div>
            <h3>Tools <span>{capabilities.tools.length}</span></h3>
            {capabilities.tools.map((tool) => (
              <article key={tool.name}>
                <strong>{tool.name}</strong>
                <p>{tool.description || "未提供说明"}</p>
                {tool.input_schema ? (
                  <details>
                    <summary>输入 Schema</summary>
                    <pre>{formatResult(tool.input_schema)}</pre>
                  </details>
                ) : null}
              </article>
            ))}
          </div>
          <div>
            <h3>Resources <span>{capabilities.resources.length}</span></h3>
            {capabilities.resources.map((resource) => (
              <article key={resource.uri}>
                <strong>{resource.name || resource.uri}</strong>
                <code>{resource.uri}</code>
                <p>{resource.description || resource.mime_type || "MCP 资源"}</p>
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`读取资源 ${resource.uri}`}
                  onClick={() => onReadResource(resource.uri)}
                >
                  读取
                </button>
              </article>
            ))}
          </div>
          <div>
            <h3>
              Resource Templates <span>{capabilities.resource_templates.length}</span>
            </h3>
            {capabilities.resource_templates.map((template) => (
              <article key={template.uri_template}>
                <strong>{template.title || template.name}</strong>
                <code>{template.uri_template}</code>
                <p>{
                  template.description ||
                  template.mime_type ||
                  "参数化 MCP 资源模板"
                }</p>
                <small>URI 模板；填充参数后才能读取</small>
              </article>
            ))}
          </div>
          <div>
            <h3>Prompts <span>{capabilities.prompts.length}</span></h3>
            {capabilities.prompts.length ? (
              <label>
                <span>Prompt 参数 JSON</span>
                <textarea
                  aria-label="MCP Prompt 参数"
                  rows={3}
                  value={promptArguments}
                  disabled={busy}
                  onChange={(event) =>
                    onPromptArguments(event.currentTarget.value)
                  }
                />
              </label>
            ) : null}
            {capabilities.prompts.map((prompt) => (
              <article key={prompt.name}>
                <strong>{prompt.name}</strong>
                <p>{prompt.description || "未提供说明"}</p>
                {prompt.arguments?.length ? (
                  <small>
                    参数：
                    {prompt.arguments
                      .map((argument) =>
                        argument.required ? `${argument.name}*` : argument.name,
                      )
                      .join("、")}
                  </small>
                ) : null}
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`获取 Prompt ${prompt.name}`}
                  onClick={() => onGetPrompt(prompt.name)}
                >
                  获取
                </button>
              </article>
            ))}
          </div>
        </div>
      )}
      {result ? (
        <div className="mcp-capability-result" role="status">
          <strong>返回内容</strong>
          <pre>{result}</pre>
        </div>
      ) : null}
    </section>
  );
}

function validateAndBuildPayload(
  draft: ConnectionDraft,
  setNotice: (notice: SettingsNotice | null) => void,
): McpConnectionInput | null {
  const name = draft.name.trim();
  if (!name) {
    setNotice({ tone: "error", text: "请填写连接名称。" });
    return null;
  }
  if (
    !Number.isFinite(draft.timeoutSeconds) ||
    draft.timeoutSeconds < 1 ||
    draft.timeoutSeconds > 600
  ) {
    setNotice({ tone: "error", text: "超时必须在 1 到 600 秒之间。" });
    return null;
  }

  const base: McpConnectionInput = {
    name,
    transport: draft.transport,
    enabled: draft.enabled,
    allow_remote: draft.transport === "stdio" ? false : draft.allowRemote,
    timeout_seconds: draft.timeoutSeconds,
    trust_level: draft.trustLevel,
    sandbox_mode:
      draft.transport === "stdio" ? draft.sandboxMode : "disabled",
    network_policy:
      draft.transport === "stdio"
        ? draft.networkPolicy
        : draft.allowRemote
          ? "allow"
          : "loopback",
    ...(draft.bearerToken.trim()
      ? { bearer_token: draft.bearerToken.trim() }
      : {}),
  };
  if (draft.transport === "stdio") {
    const command = commandFromText(draft.commandText);
    if (!command.length) {
      setNotice({ tone: "error", text: "请填写 stdio 服务启动命令。" });
      return null;
    }
    return { ...base, command };
  }

  const url = draft.url.trim();
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    setNotice({ tone: "error", text: "请输入有效的 MCP 服务 URL。" });
    return null;
  }
  if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
    setNotice({ tone: "error", text: "MCP 服务 URL 仅支持 HTTP 或 HTTPS。" });
    return null;
  }
  if (!isLoopbackHost(parsed.hostname) && !draft.allowRemote) {
    setNotice({
      tone: "error",
      text: "非回环地址需要明确开启“允许远程网络连接”。",
    });
    return null;
  }
  return { ...base, url };
}

function draftFromConnection(connection: McpConnectionSnapshot): ConnectionDraft {
  return {
    name: connection.name,
    transport: connection.transport,
    commandText: (connection.command ?? []).join("\n"),
    url: connection.url ?? "",
    bearerToken: "",
    enabled: connection.enabled,
    allowRemote: connection.allow_remote,
    timeoutSeconds: connection.timeout_seconds,
    trustLevel: connection.trust_level,
    sandboxMode: connection.sandbox_mode,
    networkPolicy: connection.network_policy,
  };
}

function payloadFromConnection(
  connection: McpConnectionSnapshot,
  enabled: boolean,
): McpConnectionInput {
  return {
    name: connection.name,
    transport: connection.transport,
    ...(connection.command ? { command: connection.command } : {}),
    ...(connection.url ? { url: connection.url } : {}),
    enabled,
    allow_remote: connection.allow_remote,
    timeout_seconds: connection.timeout_seconds,
    trust_level: connection.trust_level,
    sandbox_mode: connection.sandbox_mode,
    network_policy: connection.network_policy,
  };
}

function startNewConnection(
  setSelectedId: (value: string | null) => void,
  setDraft: (value: ConnectionDraft) => void,
) {
  setSelectedId(null);
  setDraft(emptyDraft());
}

function upsertConnection(
  connections: McpConnectionSnapshot[],
  saved: McpConnectionSnapshot,
): McpConnectionSnapshot[] {
  const index = connections.findIndex(
    (connection) => connection.connection_id === saved.connection_id,
  );
  if (index === -1) return [...connections, saved];
  return connections.map((connection, candidateIndex) =>
    candidateIndex === index ? saved : connection,
  );
}

function commandFromText(value: string): string[] {
  return value
    .split(/\r?\n/u)
    .map((part) => part.trim())
    .filter(Boolean);
}

function isLoopbackHost(hostname: string): boolean {
  return new Set(["localhost", "127.0.0.1", "::1", "[::1]"]).has(
    hostname.toLowerCase(),
  );
}

function transportLabel(transport: McpTransport): string {
  return {
    stdio: "stdio",
    streamable_http: "Streamable HTTP",
    sse: "SSE",
  }[transport];
}

function formatResult(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
