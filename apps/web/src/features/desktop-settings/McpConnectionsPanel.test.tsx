import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
import type { McpConnectionSnapshot } from "../chat/runtimeClient";
import { McpConnectionsPanel } from "./McpConnectionsPanel";

vi.mock("../chat/runtimeClient", () => ({
  createMcpConnection: vi.fn(),
  deleteMcpConnection: vi.fn(),
  getMcpCapabilities: vi.fn(),
  getMcpConnections: vi.fn(),
  getMcpPrompt: vi.fn(),
  readMcpResource: vi.fn(),
  testMcpConnection: vi.fn(),
  updateMcpConnection: vi.fn(),
}));

const localConnection: McpConnectionSnapshot = {
  connection_id: "11111111-1111-4111-8111-111111111111",
  name: "本地笔记",
  transport: "stdio",
  command: ["python", "-I", "/opt/notes/server.py"],
  enabled: true,
  allow_remote: false,
  timeout_seconds: 30,
  trust_level: "untrusted",
  sandbox_mode: "required",
  network_policy: "deny",
  bearer_token_configured: false,
  status: "ready",
  sandbox_backend: "macos_seatbelt",
};

describe("McpConnectionsPanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getMcpConnections).mockResolvedValue([]);
    vi.mocked(deleteMcpConnection).mockResolvedValue();
    vi.mocked(testMcpConnection).mockResolvedValue({
      status: "ready",
      latency_ms: 18,
      protocol_version: "2025-11-25",
    });
    vi.mocked(getMcpCapabilities).mockResolvedValue({
      tools: [],
      resources: [],
      resource_templates: [],
      prompts: [],
    });
  });

  it("creates a remote connection without retaining or echoing its token", async () => {
    const connectionId = "22222222-2222-4222-8222-222222222222";
    const saved: McpConnectionSnapshot = {
      connection_id: connectionId,
      name: "宁宁远程工具",
      transport: "streamable_http",
      url: "https://mcp.example.com/v1",
      enabled: true,
      allow_remote: true,
      timeout_seconds: 30,
      trust_level: "untrusted",
      sandbox_mode: "disabled",
      network_policy: "allow",
      bearer_token_configured: true,
      status: "untested",
    };
    vi.mocked(createMcpConnection).mockResolvedValue(saved);

    render(<McpConnectionsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "MCP 连接" }));
    await screen.findByRole("dialog", { name: "MCP 连接管理" });

    fireEvent.change(screen.getByLabelText("MCP 连接名称"), {
      target: { value: "宁宁远程工具" },
    });
    expect(screen.getByLabelText<HTMLInputElement>("MCP 连接 ID").value).toBe(
      "保存后由 Runtime 生成",
    );
    fireEvent.change(screen.getByLabelText("MCP 传输类型"), {
      target: { value: "streamable_http" },
    });
    fireEvent.change(screen.getByLabelText("MCP 服务 URL"), {
      target: { value: "https://mcp.example.com/v1" },
    });
    fireEvent.change(screen.getByLabelText("MCP Bearer Token"), {
      target: { value: "secret-token" },
    });
    fireEvent.click(screen.getByRole("switch", { name: "允许远程网络连接" }));
    fireEvent.click(screen.getByRole("button", { name: "保存连接" }));

    await waitFor(() =>
      expect(createMcpConnection).toHaveBeenCalledWith({
        name: "宁宁远程工具",
        transport: "streamable_http",
        url: "https://mcp.example.com/v1",
        bearer_token: "secret-token",
        enabled: true,
        allow_remote: true,
        timeout_seconds: 30,
        trust_level: "untrusted",
        sandbox_mode: "disabled",
        network_policy: "allow",
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLInputElement>("MCP Bearer Token").value,
      ).toBe(""),
    );
    expect(screen.getByText("令牌已保存")).toBeTruthy();
    expect(screen.getByText("沙箱：已关闭")).toBeTruthy();
    expect(screen.queryByDisplayValue("secret-token")).toBeNull();
  });

  it("keeps stdio network policy truthful when sandbox mode changes", async () => {
    const saved: McpConnectionSnapshot = {
      ...localConnection,
      connection_id: "33333333-3333-4333-8333-333333333333",
      name: "无沙箱工具",
      sandbox_mode: "disabled",
      network_policy: "allow",
      sandbox_backend: null,
    };
    vi.mocked(createMcpConnection).mockResolvedValue(saved);

    render(<McpConnectionsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "MCP 连接" }));
    await screen.findByRole("dialog", { name: "MCP 连接管理" });

    fireEvent.change(screen.getByLabelText("MCP 连接名称"), {
      target: { value: "无沙箱工具" },
    });
    fireEvent.change(screen.getByLabelText("MCP 启动命令"), {
      target: { value: "python\nserver.py" },
    });

    const networkPolicy =
      screen.getByLabelText<HTMLSelectElement>("MCP 网络策略");
    expect(screen.queryByRole("option", { name: "仅允许本机回环" })).toBeNull();
    expect(networkPolicy.value).toBe("deny");

    fireEvent.change(screen.getByLabelText("MCP 沙箱策略"), {
      target: { value: "disabled" },
    });
    expect(networkPolicy.value).toBe("allow");
    expect(screen.getByLabelText<HTMLSelectElement>("MCP 信任等级").value).toBe(
      "trusted",
    );

    fireEvent.change(networkPolicy, { target: { value: "deny" } });
    fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
    expect(
      await screen.findByText(
        "关闭沙箱后无法保证网络隔离，stdio 网络策略必须设为允许网络。",
      ),
    ).toBeTruthy();
    expect(createMcpConnection).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("MCP 沙箱策略"), {
      target: { value: "preferred" },
    });
    expect(networkPolicy.value).toBe("deny");
    fireEvent.change(screen.getByLabelText("MCP 沙箱策略"), {
      target: { value: "disabled" },
    });
    expect(networkPolicy.value).toBe("allow");
    fireEvent.change(screen.getByLabelText("MCP 信任等级"), {
      target: { value: "untrusted" },
    });
    expect(screen.getByLabelText<HTMLSelectElement>("MCP 沙箱策略").value).toBe(
      "required",
    );
    expect(networkPolicy.value).toBe("deny");
    fireEvent.change(screen.getByLabelText("MCP 沙箱策略"), {
      target: { value: "disabled" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存连接" }));

    await waitFor(() =>
      expect(createMcpConnection).toHaveBeenCalledWith({
        name: "无沙箱工具",
        transport: "stdio",
        command: ["python", "server.py"],
        enabled: true,
        allow_remote: false,
        timeout_seconds: 30,
        trust_level: "trusted",
        sandbox_mode: "disabled",
        network_policy: "allow",
      }),
    );
    expect(await screen.findByText("沙箱：已关闭")).toBeTruthy();
  });

  it("rejects a legacy untrusted stdio connection with no sandbox", async () => {
    vi.mocked(getMcpConnections).mockResolvedValue([
      {
        ...localConnection,
        trust_level: "untrusted",
        sandbox_mode: "disabled",
        network_policy: "allow",
        sandbox_backend: null,
      },
    ]);

    render(<McpConnectionsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "MCP 连接" }));
    await screen.findByDisplayValue("本地笔记");
    fireEvent.click(screen.getByRole("button", { name: "保存连接" }));

    expect(
      await screen.findByText(
        "不受信任的 stdio 进程不能关闭沙箱，请启用沙箱或改为受信任。",
      ),
    ).toBeTruthy();
    expect(updateMcpConnection).not.toHaveBeenCalled();
  });

  it("shows an untested sandbox until a backend is reported", async () => {
    vi.mocked(getMcpConnections).mockResolvedValue([
      {
        ...localConnection,
        status: "untested",
        sandbox_backend: null,
      },
    ]);

    render(<McpConnectionsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "MCP 连接" }));
    expect(await screen.findByText("沙箱：尚未测试")).toBeTruthy();
  });

  it("tests, toggles and browses tools, resources and prompts", async () => {
    vi.mocked(getMcpConnections).mockResolvedValue([localConnection]);
    vi.mocked(updateMcpConnection).mockResolvedValue({
      ...localConnection,
      enabled: false,
    });
    vi.mocked(getMcpCapabilities).mockResolvedValue({
      tools: [
        {
          name: "notes.search",
          description: "搜索本地笔记",
          input_schema: { type: "object" },
        },
      ],
      resources: [
        {
          uri: "notes://profile/nene",
          name: "宁宁资料",
          description: "本地角色资料",
        },
      ],
      resource_templates: [
        {
          uri_template: "notes://daily/{date}",
          name: "daily-note",
          title: "每日笔记",
          description: "按日期读取本地笔记",
        },
      ],
      prompts: [
        {
          name: "greeting",
          description: "生成问候",
          arguments: [{ name: "name", required: true }],
        },
      ],
    });
    vi.mocked(readMcpResource).mockResolvedValue({
      contents: [{ text: "角色资料内容" }],
    });
    vi.mocked(getMcpPrompt).mockResolvedValue({
      messages: [{ role: "user", content: "向木白问好" }],
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<McpConnectionsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "MCP 连接" }));
    expect(await screen.findByDisplayValue("本地笔记")).toBeTruthy();
    expect(screen.getByText("沙箱：macos_seatbelt")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    await waitFor(() =>
      expect(testMcpConnection).toHaveBeenCalledWith(
        localConnection.connection_id,
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "刷新能力" }));
    expect(await screen.findByText("notes.search")).toBeTruthy();
    expect(screen.getByText("宁宁资料")).toBeTruthy();
    expect(screen.getByText("每日笔记")).toBeTruthy();
    expect(screen.getByText("notes://daily/{date}")).toBeTruthy();
    expect(screen.getByText("URI 模板；填充参数后才能读取")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /读取.*notes:\/\/daily/ }),
    ).toBeNull();
    expect(screen.getByText("greeting")).toBeTruthy();
    expect(
      await screen.findByText(
        "已发现 1 个工具、1 个资源、1 个资源模板、1 个提示模板",
      ),
    ).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: "读取资源 notes://profile/nene" }),
    );
    await waitFor(() =>
      expect(readMcpResource).toHaveBeenCalledWith(
        localConnection.connection_id,
        "notes://profile/nene",
      ),
    );
    expect(await screen.findByText(/角色资料内容/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("MCP Prompt 参数"), {
      target: { value: '{"name":"木白"}' },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "获取 Prompt greeting" }),
    );
    await waitFor(() =>
      expect(getMcpPrompt).toHaveBeenCalledWith(
        localConnection.connection_id,
        "greeting",
        { name: "木白" },
      ),
    );
    expect(await screen.findByText(/向木白问好/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "停用" }));
    await waitFor(() =>
      expect(updateMcpConnection).toHaveBeenCalledWith(
        localConnection.connection_id,
        {
          name: "本地笔记",
          transport: "stdio",
          command: ["python", "-I", "/opt/notes/server.py"],
          enabled: false,
          allow_remote: false,
          timeout_seconds: 30,
          trust_level: "untrusted",
          sandbox_mode: "required",
          network_policy: "deny",
        },
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() =>
      expect(deleteMcpConnection).toHaveBeenCalledWith(
        localConnection.connection_id,
      ),
    );
  });
});
