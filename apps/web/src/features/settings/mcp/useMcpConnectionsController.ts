import { useEffect, useMemo, useState } from "react";

import {
  createMcpConnection,
  deleteMcpConnection,
  getMcpCapabilities,
  getMcpConnections,
  getMcpPrompt,
  readMcpResource,
  testMcpConnection,
  updateMcpConnection,
  type McpCapabilitiesSnapshot,
  type McpConnectionSnapshot,
} from "../../chat/runtimeClient";
import { useSettingsOperation } from "../../settings/useSettingsOperation";
import {
  draftFromConnection,
  emptyConnectionDraft,
  formatMcpResult,
  payloadFromConnection,
  upsertConnection,
  validateConnectionDraft,
  type ConnectionDraft,
} from "./mcpConnectionPolicy";

export function useMcpConnectionsController(
  open: boolean,
  sessionId: string | null,
) {
  const [connections, setConnections] = useState<McpConnectionSnapshot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ConnectionDraft>(emptyConnectionDraft);
  const [capabilities, setCapabilities] =
    useState<McpCapabilitiesSnapshot | null>(null);
  const [capabilityResult, setCapabilityResult] = useState<string | null>(null);
  const [promptArguments, setPromptArguments] = useState("{}");
  const { busy, notice, setNotice, run } = useSettingsOperation<string>();
  const selectedConnection = useMemo(
    () =>
      connections.find(
        (connection) => connection.connection_id === selectedId,
      ) ?? null,
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
        setSelectedId(first?.connection_id ?? null);
        setDraft(first ? draftFromConnection(first) : emptyConnectionDraft());
        setCapabilities(first?.capabilities ?? null);
        setCapabilityResult(null);
        setPromptArguments("{}");
        setNotice(null);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setNotice({ tone: "error", text: message(error, "读取 MCP 连接失败") });
      });
    return () => {
      active = false;
    };
  }, [open, setNotice]);

  const select = (connection: McpConnectionSnapshot, clearNotice = true) => {
    setSelectedId(connection.connection_id);
    setDraft(draftFromConnection(connection));
    setCapabilities(connection.capabilities ?? null);
    setCapabilityResult(null);
    setPromptArguments("{}");
    if (clearNotice) setNotice(null);
  };

  const createNew = (clearNotice = true) => {
    setSelectedId(null);
    setDraft(emptyConnectionDraft());
    setCapabilities(null);
    setCapabilityResult(null);
    setPromptArguments("{}");
    if (clearNotice) setNotice(null);
  };

  const change = <Key extends keyof ConnectionDraft>(
    key: Key,
    value: ConnectionDraft[Key],
  ) => setDraft((current) => ({ ...current, [key]: value }));

  const clearSensitiveState = () => {
    setCapabilityResult(null);
    setPromptArguments("{}");
  };

  const save = async () => {
    const validation = validateConnectionDraft(draft);
    if (!validation.ok) {
      setNotice({ tone: "error", text: validation.error });
      return;
    }
    const wasNew = selectedId === null;
    const saved = await run(
      "save",
      () =>
        wasNew
          ? createMcpConnection(validation.payload)
          : updateMcpConnection(selectedId, validation.payload),
      {
        pending: "正在保存 MCP 连接…",
        success: wasNew ? "MCP 连接已创建" : "MCP 连接已保存",
        error: "保存 MCP 连接失败",
      },
    );
    if (!saved) return;
    setConnections((current) => upsertConnection(current, saved));
    select(saved, false);
  };

  const toggle = async (connection: McpConnectionSnapshot) => {
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
    if (selectedId === updated.connection_id) select(updated, false);
  };

  const clearToken = async () => {
    if (!selectedConnection) return;
    const updated = await run(
      `clear-token:${selectedConnection.connection_id}`,
      () =>
        updateMcpConnection(selectedConnection.connection_id, {
          ...payloadFromConnection(
            selectedConnection,
            selectedConnection.enabled,
          ),
          clear_bearer_token: true,
        }),
      { success: "Bearer Token 已移除", error: "移除 Bearer Token 失败" },
    );
    if (!updated) return;
    setConnections((current) => upsertConnection(current, updated));
    select(updated, false);
  };

  const remove = async (connection: McpConnectionSnapshot) => {
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
      (candidate) => candidate.connection_id !== removedId,
    );
    setConnections(remaining);
    const next = remaining[0];
    if (next) select(next, false);
    else createNew(false);
  };

  const probe = async () => {
    if (!selectedConnection) {
      setNotice({ tone: "info", text: "请先保存连接，再测试握手。" });
      return;
    }
    const tested = await run(
      `test:${selectedConnection.connection_id}`,
      () => testMcpConnection(selectedConnection.connection_id),
      {
        pending: "正在执行 MCP initialize 与能力探测…",
        success: (result) => {
          const version = result.capabilities.protocol_version
            ? `，协议 ${result.capabilities.protocol_version}`
            : "";
          return `连接测试 ${result.status}${version}`;
        },
        error: "MCP 连接测试失败",
      },
    );
    if (!tested) return;
    setConnections((current) => upsertConnection(current, tested));
    select(tested, false);
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
    if (!sessionId) {
      setNotice({
        tone: "info",
        text: "Runtime 会话尚未就绪，无法申请资源读取权限。",
      });
      return;
    }
    const result = await run(
      `resource:${uri}`,
      () => readMcpResource(sessionId, selectedConnection.connection_id, uri),
      {
        success: "已创建资源读取任务，请在 Skills 中确认权限",
        error: "读取 MCP 资源失败",
      },
    );
    if (result !== undefined) setCapabilityResult(formatMcpResult(result));
  };

  const fetchPrompt = async (name: string) => {
    if (!selectedConnection) return;
    if (!sessionId) {
      setNotice({
        tone: "info",
        text: "Runtime 会话尚未就绪，无法申请 Prompt 读取权限。",
      });
      return;
    }
    const args = parsePromptArguments(promptArguments);
    if (typeof args === "string") {
      setNotice({ tone: "error", text: args });
      return;
    }
    const result = await run(
      `prompt:${name}`,
      () =>
        getMcpPrompt(sessionId, selectedConnection.connection_id, name, args),
      {
        success: "已创建 Prompt 读取任务，请在 Skills 中确认权限",
        error: "获取 MCP Prompt 失败",
      },
    );
    if (result !== undefined) setCapabilityResult(formatMcpResult(result));
  };

  return {
    connections,
    selectedId,
    selectedConnection,
    draft,
    capabilities,
    capabilityResult,
    promptArguments,
    busy,
    notice,
    change,
    select,
    createNew,
    save,
    toggle,
    clearToken,
    remove,
    probe,
    refreshCapabilities,
    readResource,
    fetchPrompt,
    setPromptArguments,
    clearSensitiveState,
  };
}

function parsePromptArguments(source: string): Record<string, string> | string {
  let value: unknown;
  try {
    value = JSON.parse(source);
  } catch {
    return "Prompt 参数必须是合法 JSON。";
  }
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return "Prompt 参数顶层必须是 JSON 对象。";
  if (Object.values(value).some((item) => typeof item !== "string"))
    return "Prompt 参数值必须全部是字符串。";
  return value as Record<string, string>;
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
