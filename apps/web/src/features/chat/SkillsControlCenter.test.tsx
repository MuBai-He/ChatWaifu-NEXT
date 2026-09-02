import type { SkillDefinition } from "@chatwaifu/protocol";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
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
  afterEach(cleanup);

  beforeEach(() => {
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
    expect(await screen.findByText("Runtime Status")).toBeTruthy();
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

    expect(await screen.findByText("隔离：macos_seatbelt")).toBeTruthy();
    expect(screen.getByText(/隔离：macos_seatbelt · 网络：deny/)).toBeTruthy();
  });
});
