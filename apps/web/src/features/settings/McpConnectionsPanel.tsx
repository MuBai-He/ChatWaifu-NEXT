import { useState } from "react";

import { ProductIcon } from "../../components/ProductIcon";
import { SettingsDialog } from "./SettingsDialog";
import { SettingsStatus } from "../settings/SettingsFields";
import {
  McpCapabilitiesBrowser,
  McpConnectionEditor,
} from "./mcp/McpConnectionView";
import { transportLabel } from "./mcp/mcpConnectionPolicy";
import { useMcpConnectionsController } from "./mcp/useMcpConnectionsController";

import "./mcp-connections.css";
import "./settings-extensions.css";

interface Props {
  sessionId?: string | null;
  presentation?: "embedded" | "dialog";
  active?: boolean;
}
export function McpConnectionsPanel(props: Props) {
  return <McpPanel key={props.sessionId} {...props} />;
}
function McpPanel({
  sessionId = null,
  presentation = "dialog",
  active = true,
}: Props) {
  const [open, setOpen] = useState(false);
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  const controller = useMcpConnectionsController(
    active && (presentation === "embedded" || open),
    sessionId,
  );
  const close = () => {
    controller.clearSensitiveState();
    setOpen(false);
  };

  const content = (
    <section className="extensions-mcp" aria-label="MCP 服务管理">
      <SettingsStatus
        notice={controller.notice}
        className="mcp-connections-notice"
      />

      <div className="mcp-connections-layout">
        <aside className="mcp-connection-list">
          <div>
            <strong>连接</strong>
            <button
              type="button"
              disabled={controller.loading}
              onClick={() => controller.createNew()}
            >
              <ProductIcon name="plus" />
              新建连接
            </button>
          </div>
          {controller.connections.length ? (
            controller.connections.map((connection) => (
              <article
                className={
                  controller.selectedId === connection.connection_id
                    ? "selected"
                    : ""
                }
                key={connection.connection_id}
              >
                <button
                  type="button"
                  onClick={() => controller.select(connection)}
                >
                  <span>
                    <i className={connection.enabled ? "enabled" : ""} />
                    <strong>{connection.name}</strong>
                  </span>
                  <small>
                    {transportLabel(connection.transport)} ·{" "}
                    {connection.connection_id}
                  </small>
                </button>
                <div>
                  <button
                    type="button"
                    disabled={controller.busy !== null}
                    onClick={() => void controller.toggle(connection)}
                  >
                    {connection.enabled ? "停用" : "启用"}
                  </button>
                  <button
                    type="button"
                    className="danger"
                    disabled={controller.busy !== null}
                    onClick={() => void controller.remove(connection)}
                  >
                    <ProductIcon name="trash" />
                    删除
                  </button>
                </div>
              </article>
            ))
          ) : (
            <p role="status">
              {controller.loading
                ? "正在读取 MCP 连接…"
                : "尚未配置 MCP 连接。"}
            </p>
          )}
        </aside>

        <div className="mcp-connection-workspace">
          <McpConnectionEditor
            draft={controller.draft}
            existing={controller.selectedConnection}
            busy={controller.busy !== null || controller.loading}
            onChange={controller.change}
            onSave={() => void controller.save()}
            onTest={() => void controller.probe()}
            onCapabilities={() => void controller.refreshCapabilities()}
            onClearToken={() => void controller.clearToken()}
          />
          <McpCapabilitiesBrowser
            capabilities={controller.capabilities}
            result={controller.capabilityResult}
            promptArguments={controller.promptArguments}
            busy={controller.busy !== null}
            onPromptArguments={controller.setPromptArguments}
            onReadResource={(uri) => void controller.readResource(uri)}
            onGetPrompt={(name) => void controller.fetchPrompt(name)}
          />
        </div>
      </div>
    </section>
  );
  if (presentation === "embedded") return content;
  return (
    <>
      <button
        type="button"
        onClick={(event) => {
          setOpener(event.currentTarget);
          setOpen(true);
        }}
      >
        <ProductIcon name="plugin" />
        MCP 连接
      </button>
      {open ? (
        <SettingsDialog
          title="MCP 连接"
          description="管理本地 stdio 与远程 MCP 服务，查看连接状态和服务提供的能力。"
          label="MCP 连接管理"
          closeLabel="关闭 MCP 连接管理"
          returnFocusTo={opener}
          onClose={close}
        >
          {content}
        </SettingsDialog>
      ) : null}
    </>
  );
}
