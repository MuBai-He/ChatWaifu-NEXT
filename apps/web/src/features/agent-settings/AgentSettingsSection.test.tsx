import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { parseAgentTask } from "@chatwaifu/protocol";
import fixtures from "../../../../../tests/fixtures/protocol/v1/agent-contracts.json";
import { AgentSettingsSection } from "./AgentSettingsSection";

const reads = vi.hoisted(() => vi.fn());
const capabilities = vi.hoisted(() => vi.fn().mockResolvedValue({ items: [] }));
vi.mock("../chat/runtime-client/agentClient", async (original) => ({
  ...(await original<typeof import("../chat/runtime-client/agentClient")>()),
  getAgentCapabilities: capabilities,
  getAgentArtifacts: vi.fn().mockResolvedValue([]),
  getAgentTasks: reads,
}));
vi.mock("../chat/SkillConfirmationPrompt", () => ({
  SkillConfirmationPrompt: () => null,
}));
vi.mock("./AgentDevelopmentPanel", () => ({
  AgentDevelopmentPanel: () => null,
}));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  reads.mockReset();
  capabilities.mockReset().mockResolvedValue({ items: [] });
});

it("keeps a newer search when the initial catalog finishes later and retains grant options", async () => {
  const all = fixtures.capabilities;
  const filtered = {
    ...all,
    items: [
      {
        ...all.items[0],
        capability_id: "documents.create/word",
        skill_id: "documents.create",
        name: "word",
      },
    ],
  };
  let finishInitial: (page: typeof all) => void = () => undefined;
  const initial = new Promise<typeof all>((resolve) => {
    finishInitial = resolve;
  });
  capabilities.mockImplementation((_session: string, query: string) =>
    query === "Word" ? Promise.resolve(filtered) : initial,
  );
  reads.mockResolvedValue({ items: [] });
  await act(async () => {
    render(
      <AgentSettingsSection
        context={{ sessionId: fixtures.task.session_id }}
      />,
    );
    await Promise.resolve();
  });
  await act(async () => {
    fireEvent.change(screen.getByRole("textbox", { name: "查找能力" }), {
      target: { value: "Word" },
    });
    await Promise.resolve();
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await Promise.resolve();
  });
  expect(screen.getByText("documents.create / word")).toBeTruthy();
  await act(async () => {
    finishInitial(all);
    await Promise.resolve();
  });
  expect(screen.getByText("documents.create / word")).toBeTruthy();
  expect(screen.queryByText("workspace.files / read")).toBeNull();
  expect(
    screen.getByLabelText("workspace.files", { selector: "input" }),
  ).toBeTruthy();
});

it.each(["waiting_event", "waiting_input"])(
  "shows externally completed %s tasks and stops polling after completion",
  async (state) => {
    vi.useFakeTimers();
    let task = parseAgentTask({ ...fixtures.task, state });
    reads.mockImplementation(() => Promise.resolve({ items: [task] }));
    await act(async () => {
      render(<AgentSettingsSection context={{ sessionId: task.session_id }} />);
      await Promise.resolve();
    });
    expect(screen.getByText(task.goal)).toBeTruthy();
    task = parseAgentTask({
      ...task,
      state: "succeeded",
      revision: (task.revision ?? 0) + 1,
      result_text: "异步任务已完成并核实",
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(screen.getByText("异步任务已完成并核实")).toBeTruthy();
    const settledReads = reads.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(reads).toHaveBeenCalledTimes(settledReads);
  },
);
