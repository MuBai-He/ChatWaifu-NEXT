import { useState } from "react";

import { ModalPortal } from "../chat/ModalPortal";
import { SettingsStatus } from "../settings/SettingsFields";
import {
  McpCapabilitiesBrowser,
  McpConnectionEditor,
} from "./mcp/McpConnectionView";
import { transportLabel } from "./mcp/mcpConnectionPolicy";
import { useMcpConnectionsController } from "./mcp/useMcpConnectionsController";

import "./mcp-connections.css";

export function McpConnectionsPanel({
  sessionId = null,
}: {
  sessionId?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const controller = useMcpConnectionsController(open, sessionId);
  const close = () => {
    controller.clearSensitiveState();
    setOpen(false);
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
            if (event.target === event.currentTarget) close();
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
                onClick={close}
              >
                ×
              </button>
            </header>

            <SettingsStatus
              notice={controller.notice}
              className="mcp-connections-notice"
            />

            <div className="mcp-connections-layout">
              <aside className="mcp-connection-list">
                <div>
                  <strong>连接</strong>
                  <button type="button" onClick={() => controller.createNew()}>
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
                <McpConnectionEditor
                  draft={controller.draft}
                  existing={controller.selectedConnection}
                  busy={controller.busy !== null}
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
              </main>
            </div>
          </section>
        </ModalPortal>
      ) : null}
    </>
  );
}
