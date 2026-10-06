import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConversationScopeGate } from "./ConversationScopeGate";
import {
  getParticipants,
  getScenes,
  readConversationScope,
  renameParticipant,
  type Participant,
} from "./conversationScope";
import { RuntimeRequestError } from "./runtime-client/http";

vi.mock("./conversationScope", async (original) => ({
  ...(await original<typeof import("./conversationScope")>()),
  getParticipants: vi.fn(),
  getScenes: vi.fn(),
  readConversationScope: vi.fn(),
  scopeStorageKey: vi.fn().mockResolvedValue("scope-test"),
  renameParticipant: vi.fn(),
}));

const people: Participant[] = [
  { participant_id: "local", display_name: "主人" },
  { participant_id: "registered-qq-member", display_name: "占位名" },
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getParticipants).mockResolvedValue(people);
  vi.mocked(getScenes).mockResolvedValue([]);
  vi.mocked(readConversationScope).mockResolvedValue({
    participant_id: "local",
    scene_id: null,
  });
});
afterEach(cleanup);

async function openMemberEditor() {
  render(
    <ConversationScopeGate>
      <button>对话页面</button>
    </ConversationScopeGate>,
  );
  fireEvent.click(screen.getByRole("button", { name: "切换参与者与场景" }));
  await screen.findByRole("option", { name: "占位名" });
  fireEvent.change(screen.getByRole("combobox", { name: "当前说话者" }), {
    target: { value: "registered-qq-member" },
  });
  fireEvent.click(screen.getByText("修改当前参与者名称"));
}

describe("ConversationScopeGate participant labels", () => {
  it("updates options and audience labels using the same participant ID", async () => {
    vi.mocked(renameParticipant).mockResolvedValue({
      ...people[1],
      display_name: "小林",
    });
    await openMemberEditor();
    fireEvent.change(screen.getByLabelText("显示名称"), {
      target: { value: " 小林 " },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    await screen.findByText("参与者名称已保存");
    expect(renameParticipant).toHaveBeenCalledWith(
      people[1],
      "小林",
      expect.any(AbortSignal),
    );
    const option = screen.getByRole("option", {
      name: "小林",
    });
    expect(option.getAttribute("value")).toBe(people[1].participant_id);
    fireEvent.click(screen.getByText("创建共享场景"));
    expect(screen.getByRole("checkbox", { name: "小林" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "主人" })).toBeTruthy();
  });

  it("keeps the old label and shows a conflict when the name changed elsewhere", async () => {
    vi.mocked(renameParticipant).mockRejectedValue(
      new RuntimeRequestError("stale name", 409),
    );
    await openMemberEditor();
    fireEvent.change(screen.getByLabelText("显示名称"), {
      target: { value: "覆盖" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain(
      "名称已被其他操作修改",
    );
    expect(screen.getByRole("option", { name: "占位名" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "覆盖" })).toBeNull();
  });

  it("ignores a late rename response after the dialog is closed", async () => {
    let complete: (participant: Participant) => void = () => undefined;
    vi.mocked(renameParticipant).mockImplementation(
      () =>
        new Promise<Participant>((resolve) => {
          complete = resolve;
        }),
    );
    await openMemberEditor();
    fireEvent.change(screen.getByLabelText("显示名称"), {
      target: { value: "过期结果" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    const signal = vi.mocked(renameParticipant).mock.calls[0][2]!;
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(signal.aborted).toBe(true);
    complete({ ...people[1], display_name: "过期结果" });
    fireEvent.click(screen.getByRole("button", { name: "切换参与者与场景" }));
    await waitFor(() => expect(getParticipants).toHaveBeenCalledTimes(2));
    await screen.findByRole("option", { name: "占位名" });
    expect(screen.queryByRole("option", { name: "过期结果" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
