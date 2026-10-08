import type {
  McpCapabilitiesSnapshot,
  McpConnectionSnapshot,
  McpNetworkPolicy,
  McpSandboxMode,
  McpTransport,
  McpTrustLevel,
} from "../../chat/runtimeClient";
import { SettingsSecretField } from "../../settings/SettingsFields";
import type { ConnectionDraft } from "./mcpConnectionPolicy";
import {
  formatMcpResult,
  sandboxStatusLabel,
  transportLabel,
} from "./mcpConnectionPolicy";

export function McpConnectionEditor({
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
        {existing ? (
          <div className="mcp-connection-badges">
            <span>{sandboxStatusLabel(existing)}</span>
            {existing.bearer_token_configured ? <span>令牌已保存</span> : null}
          </div>
        ) : null}
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
              onChange(
                "networkPolicy",
                transport === "stdio" ? "deny" : "loopback",
              );
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
              onChange={(event) => {
                const sandboxMode = event.currentTarget.value as McpSandboxMode;
                onChange("sandboxMode", sandboxMode);
                if (sandboxMode === "disabled")
                  onChange("trustLevel", "trusted");
                onChange(
                  "networkPolicy",
                  sandboxMode === "disabled" ? "allow" : "deny",
                );
              }}
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
            onChange={(event) => {
              const trustLevel = event.currentTarget.value as McpTrustLevel;
              onChange("trustLevel", trustLevel);
              if (
                !isRemote &&
                trustLevel === "untrusted" &&
                draft.sandboxMode === "disabled"
              ) {
                onChange("sandboxMode", "required");
                onChange("networkPolicy", "deny");
              }
            }}
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
            远程 MCP
            服务能看到发送给它的参数和读取请求。只连接可信服务；优先使用
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
          沙箱已关闭。MCP
          进程可能访问当前用户可访问的文件和网络，请仅用于可信服务。
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

export function McpCapabilitiesBrowser({
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
            <h3>
              Tools <span>{capabilities.tools.length}</span>
            </h3>
            {capabilities.tools.map((tool) => (
              <article key={tool.name}>
                <strong>{tool.name}</strong>
                <p>{tool.description || "未提供说明"}</p>
                {tool.input_schema ? (
                  <details>
                    <summary>输入 Schema</summary>
                    <pre>{formatMcpResult(tool.input_schema)}</pre>
                  </details>
                ) : null}
              </article>
            ))}
          </div>
          <div>
            <h3>
              Resources <span>{capabilities.resources.length}</span>
            </h3>
            {capabilities.resources.map((resource) => (
              <article key={resource.uri}>
                <strong>{resource.name || resource.uri}</strong>
                <code>{resource.uri}</code>
                <p>
                  {resource.description || resource.mime_type || "MCP 资源"}
                </p>
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
              Resource Templates{" "}
              <span>{capabilities.resource_templates.length}</span>
            </h3>
            {capabilities.resource_templates.map((template) => (
              <article key={template.uri_template}>
                <strong>{template.title || template.name}</strong>
                <code>{template.uri_template}</code>
                <p>
                  {template.description ||
                    template.mime_type ||
                    "参数化 MCP 资源模板"}
                </p>
                <small>URI 模板；填充参数后才能读取</small>
              </article>
            ))}
          </div>
          <div>
            <h3>
              Prompts <span>{capabilities.prompts.length}</span>
            </h3>
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
                      .map(promptArgumentLabel)
                      .filter(Boolean)
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

function promptArgumentLabel(argument: Record<string, unknown>): string {
  if (typeof argument.name !== "string") return "";
  return argument.required === true ? `${argument.name}*` : argument.name;
}
