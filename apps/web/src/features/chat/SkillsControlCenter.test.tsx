import type { SkillDefinition } from "@chatwaifu/protocol";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  act,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getPlugins,
  getSkillConfirmations,
  getSkillRuns,
  getSkills,
  installExamplePlugin,
  invokeSkill,
} from "./runtimeClient";
import { SkillsControlCenter } from "./SkillsControlCenter";

vi.mock("./runtimeClient", () => ({
  cancelSkillRun: vi.fn(),
  decideSkillConfirmation: vi.fn(),
  getPlugins: vi.fn(),
  getSkillConfirmations: vi.fn(),
  getSkillInstructions: vi.fn().mockResolvedValue("# Runtime Status"),
  getSkillRuns: vi.fn(),
  getSkills: vi.fn(),
  installExamplePlugin: vi.fn(),
  installLocalPlugin: vi.fn(),
  invokeSkill: vi.fn(),
  setPluginEnabled: vi.fn(),
  uninstallPlugin: vi.fn(),
}));

const runtimeSkill = {
  skill_id: "runtime.status",
  version: "1.2.0",
  name: "Runtime Status",
  description: "Read Runtime status",
  enabled: true,
  source: "builtin",
  capabilities: [
    {
      name: "read",
      description: "Read status",
      input_schema: { type: "object" },
      output_schema: { type: "object" },
      side_effect: "read",
      required_permissions: [],
      confirmation_required: false,
      timeout_seconds: 5,
    },
  ],
} as SkillDefinition;

describe("SkillsControlCenter", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSkills).mockResolvedValue([runtimeSkill]);
    vi.mocked(getPlugins).mockResolvedValue([]);
    vi.mocked(getSkillRuns).mockResolvedValue([]);
    vi.mocked(getSkillConfirmations).mockResolvedValue([]);
    vi.mocked(installExamplePlugin).mockResolvedValue({} as never);
    vi.mocked(invokeSkill).mockResolvedValue({} as never);
  });

  it("discovers metadata and invokes a selected capability", async () => {
    render(<SkillsControlCenter sessionId="session-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Skills & 插件" }));

    const dialog = screen.getByRole("dialog", {
      name: "Skills 与插件控制中心",
    });
    expect(dialog.parentElement?.parentElement).toBe(document.body);
    expect(
      await screen.findByRole("heading", { name: "Runtime Status" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /read/ }));
    fireEvent.click(screen.getByRole("button", { name: "运行 Skill" }));

    await waitFor(() =>
      expect(invokeSkill).toHaveBeenCalledWith(
        "session-1",
        "runtime.status",
        "read",
        {},
      ),
    );
  });

  it("installs the bundled MCP example through the control center", async () => {
    render(<SkillsControlCenter sessionId="session-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Skills & 插件" }));
    fireEvent.click(screen.getByRole("tab", { name: "插件" }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: "安装 Local Echo 测试插件",
      }),
    );
    await waitFor(() => expect(installExamplePlugin).toHaveBeenCalledOnce());
  });

  it("shows the sandbox backend reported by Runtime", async () => {
    vi.mocked(getPlugins).mockResolvedValue([
      {
        plugin_id: "local.echo",
        version: "1.0.0",
        name: "Local Echo",
        description: "测试插件",
        enabled: true,
        install_path: "/plugins/local.echo",
        trust_level: "untrusted",
        sandbox_mode: "required",
        network_policy: "deny",
        sandbox_backend: "macos_seatbelt",
        installed_at: "2026-08-29T00:00:00Z",
        updated_at: "2026-08-29T00:00:00Z",
      },
    ]);

    render(<SkillsControlCenter sessionId="session-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Skills & 插件" }));

    fireEvent.click(screen.getByRole("tab", { name: "插件" }));
    expect(await screen.findByText("隔离：macos_seatbelt")).toBeTruthy();
    expect(screen.getByText(/隔离：macos_seatbelt · 网络：deny/)).toBeTruthy();
  });
  it("searches metadata and keeps the selected invocation next to its details", async () => {
    render(
      <SkillsControlCenter sessionId="session-1" presentation="embedded" />,
    );
    await screen.findByRole("heading", { name: "Runtime Status" });
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.change(screen.getByRole("searchbox", { name: "查找 Skill" }), {
      target: { value: "runtime.status" },
    });
    expect(
      screen.getByRole("button", { name: "选择 Runtime Status" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /read\s*只读/ }));
    const input = screen.getByRole<HTMLTextAreaElement>("textbox", {
      name: "Skill JSON 参数",
    });
    expect(input.closest('[aria-label="Skill 详情"]')).toBeTruthy();
    fireEvent.change(input, { target: { value: "[]" } });
    fireEvent.click(screen.getByRole("button", { name: "运行 Skill" }));
    expect(screen.getByText("参数顶层必须是 JSON 对象。")).toBeTruthy();
    expect(invokeSkill).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("searchbox", { name: "查找 Skill" }), {
      target: { value: "missing" },
    });
    expect(screen.getByText("没有匹配的 Skill")).toBeTruthy();
    expect(screen.getByText("还没有运行记录")).toBeTruthy();
  });

  it("stops polling while hidden and rejects a late catalog response", async () => {
    let resolve!: (items: SkillDefinition[]) => void;
    vi.mocked(getSkills).mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const { rerender } = render(
      <SkillsControlCenter sessionId="session-1" presentation="embedded" />,
    );
    await waitFor(() => expect(getSkills).toHaveBeenCalledOnce());
    rerender(
      <SkillsControlCenter
        sessionId="session-1"
        presentation="embedded"
        active={false}
      />,
    );
    await act(async () => {
      resolve([runtimeSkill]);
      await Promise.resolve();
    });
    expect(
      screen.queryByRole("button", { name: "选择 Runtime Status" }),
    ).toBeNull();
    vi.useFakeTimers();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45_000);
    });
    expect(getSkills).toHaveBeenCalledOnce();
    vi.useRealTimers();
    rerender(
      <SkillsControlCenter sessionId="session-1" presentation="embedded" />,
    );
    await screen.findByRole("heading", { name: "Runtime Status" });
    expect(getSkills).toHaveBeenCalledTimes(2);
  });

  it("resets selection and arguments when Runtime session identity changes", async () => {
    const { rerender } = render(
      <SkillsControlCenter sessionId="session-1" presentation="embedded" />,
    );
    await screen.findByRole("heading", { name: "Runtime Status" });
    fireEvent.click(screen.getByRole("button", { name: /read\s*只读/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Skill JSON 参数" }), {
      target: { value: '{"old":true}' },
    });
    rerender(
      <SkillsControlCenter sessionId="session-2" presentation="embedded" />,
    );
    await screen.findByRole("heading", { name: "Runtime Status" });
    expect(
      screen.queryByRole("textbox", { name: "Skill JSON 参数" }),
    ).toBeNull();
    expect(getSkillRuns).toHaveBeenLastCalledWith("session-2");
  });

  it("closes with Escape and restores the trigger and background accessibility", async () => {
    render(<SkillsControlCenter sessionId="session-1" />);
    const trigger = screen.getByRole<HTMLButtonElement>("button", {
      name: "Skills & 插件",
    });
    trigger.focus();
    fireEvent.click(trigger);
    await screen.findByRole("heading", { name: "Runtime Status" });
    expect(trigger.closest("div")?.inert).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
    expect(trigger.closest("div")?.inert).not.toBe(true);
  });
});
