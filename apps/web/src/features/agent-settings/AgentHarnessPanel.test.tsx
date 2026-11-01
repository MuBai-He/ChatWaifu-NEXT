import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
} from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import { AgentHarnessPanel } from "./AgentHarnessPanel";
const client = vi.hoisted(() => ({
  getStandingIntents: vi.fn(),
  getHarnessWork: vi.fn(),
  getHarnessActivitySnapshot: vi.fn(),
  getHarnessSources: vi.fn(),
  getHarnessReview: vi.fn(),
  decideHarnessReview: vi.fn(),
  createStandingIntent: vi.fn(),
  reviewHarnessWork: vi.fn(),
  getHarnessActivity: vi.fn(),
  updateStandingIntent: vi.fn(),
  actStandingIntent: vi.fn(),
  linkHarnessWork: vi.fn(),
  readHarnessEvidence: vi.fn(),
}));
vi.mock("../chat/runtime-client/harnessClient", () => client);
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup() {
  client.getStandingIntents.mockResolvedValue([]);
  client.getHarnessWork.mockResolvedValue([]);
  client.getHarnessActivitySnapshot.mockResolvedValue({
    items: [],
    context_epoch: "one",
    quiet: false,
    next_cursor: 0,
  });
  client.getHarnessSources.mockResolvedValue({
    rooms: [],
    skills: [{ skill_id: "work.manage", name: "管理事项" }],
    resources: [],
    devices: [],
  });
  client.getHarnessReview.mockResolvedValue({ items: [] });
}
it("permits a scheduled intent without inventing a source room", async () => {
  setup();
  client.createStandingIntent.mockResolvedValue({});
  render(<AgentHarnessPanel sessionId="session" />);
  await screen.findByText("管理事项");
  fireEvent.change(screen.getByLabelText("希望宁宁持续负责什么"), {
    target: { value: "检查明天的事项" },
  });
  fireEvent.click(screen.getByLabelText("管理事项"));
  fireEvent.change(screen.getByLabelText("定时检查（可选）"), {
    target: { value: "2027-01-01T09:00" },
  });
  const save = screen.getByRole("button", { name: "保存委托" });
  expect((save as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(save);
  await waitFor(() =>
    expect(client.createStandingIntent).toHaveBeenCalledOnce(),
  );
  expect(client.createStandingIntent.mock.calls[0][0]).toMatchObject({
    room_ids: [],
    schedule: { repeat: "none", kind: "run" },
    shadow: true,
  });
});
it("routes a memory review to its existing owner and shows evidence", async () => {
  setup();
  const entry = {
    kind: "memory",
    reference: "p",
    title: "共同记忆核对",
    text: "主人喜欢清茶",
    reason: "需要确认",
    revision: 0,
    evidence: [{ text: "我喜欢清茶", occurred_at: "2026-10-31T01:00:00Z" }],
  };
  client.getHarnessReview.mockResolvedValue({ items: [entry] });
  client.decideHarnessReview.mockResolvedValue({});
  render(<AgentHarnessPanel sessionId="session" />);
  await screen.findByText("主人喜欢清茶");
  expect(screen.getByText(/我喜欢清茶/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "确认保存" }));
  await waitFor(() =>
    expect(client.decideHarnessReview).toHaveBeenCalledWith(
      "session",
      entry,
      "accept",
    ),
  );
});

it("reads the exact source revision under the trusted owner session", async () => {
  setup();
  client.getHarnessWork.mockResolvedValue([
    {
      work_item_id: "work:one",
      kind: "memory",
      title: "需核对的事实",
      details: "报告的偏好",
      state: "proposed",
      acceptance: "pending",
      revision: 1,
      ingestion_mode: "historical",
      updated_at: "2026-10-31T01:00:00Z",
      evidence: [{ kind: "knowledge", reference: "source:exact", revision: 7 }],
      bindings: [],
    },
  ]);
  client.readHarnessEvidence.mockResolvedValue({
    text: "第七版原文",
    occurred_at: 0,
    sender_name: "小林",
  });
  render(<AgentHarnessPanel sessionId="session" />);
  fireEvent.click(await screen.findByRole("button", { name: "查看依据" }));
  await waitFor(() =>
    expect(client.readHarnessEvidence).toHaveBeenCalledWith(
      "session",
      "source:exact",
      7,
    ),
  );
  expect(await screen.findByText(/第七版原文/)).toBeTruthy();
});

it("requires an explicit current-relevance decision for historical work", async () => {
  setup();
  client.getHarnessWork.mockResolvedValue([
    {
      work_item_id: "work:old",
      kind: "todo",
      title: "历史请求",
      details: "当时提出的合成任务",
      state: "proposed",
      acceptance: "pending",
      revision: 3,
      ingestion_mode: "historical",
      temporal_status: "uncertain",
      temporal_reason: "缺少近期佐证。",
      evidence: [],
      bindings: [],
    },
  ]);
  client.reviewHarnessWork.mockResolvedValue({});
  render(<AgentHarnessPanel sessionId="session" />);
  expect(await screen.findByText(/历史核对：缺少近期佐证/)).toBeTruthy();
  expect(client.reviewHarnessWork).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认仍需处理" }));
  await waitFor(() =>
    expect(client.reviewHarnessWork).toHaveBeenCalledWith(
      "session",
      "work:old",
      3,
      "confirm",
    ),
  );
});

it("offers a bounded retry for failed understanding instead of sync-conflict choices", async () => {
  setup();
  const failed = {
    kind: "observation",
    reference: "failed-source",
    title: "资料处理失败",
    text: "原文仍保留",
    reason: "提取结果不符合契约",
    revision: 6,
    evidence: [
      { kind: "knowledge", reference: "original-source", revision: 1 },
    ],
  };
  client.getHarnessReview.mockResolvedValue({ items: [failed] });
  client.decideHarnessReview.mockResolvedValue({ accepted: true });
  render(<AgentHarnessPanel sessionId="session" />);
  fireEvent.click(await screen.findByText(/需要核对/));
  const retry = await screen.findByRole("button", { name: "重新处理" });
  expect(screen.queryByRole("button", { name: "保留本机修改" })).toBeNull();
  fireEvent.click(retry);
  await waitFor(() =>
    expect(client.decideHarnessReview).toHaveBeenCalledWith(
      "session",
      failed,
      "retry",
    ),
  );
});

it("saves scoped reminder defaults only with a selected device and write permission", async () => {
  setup();
  const device = "9b632f82-642f-48d7-9ee6-31bdcecc4f42";
  client.getHarnessSources.mockResolvedValue({
    rooms: [{ room_id: "source-room", label: "合成来源" }],
    skills: [{ skill_id: "work.manage", name: "管理事项" }],
    resources: [],
    calendar_ids: [],
    tasklist_ids: [],
    devices: [{ device_id: device, label: "测试手机" }],
  });
  client.createStandingIntent.mockResolvedValue({});
  render(<AgentHarnessPanel sessionId="session" />);
  await screen.findByText("管理事项");
  fireEvent.change(screen.getByLabelText("希望宁宁持续负责什么"), {
    target: { value: "负责这些事项" },
  });
  fireEvent.click(screen.getByLabelText("合成来源"));
  fireEvent.click(screen.getByLabelText("管理事项"));
  fireEvent.change(screen.getByLabelText("新日程提前多少分钟"), {
    target: { value: "5" },
  });
  const save = screen.getByRole("button", { name: "保存委托" });
  expect((save as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("主提醒设备"), {
    target: { value: device },
  });
  expect((save as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText(/允许在所选日历和待办列表中/));
  fireEvent.click(save);
  await waitFor(() =>
    expect(client.createStandingIntent).toHaveBeenCalledOnce(),
  );
  expect(client.createStandingIntent.mock.calls[0][0]).toMatchObject({
    room_ids: ["source-room"],
    allow_writes: true,
    reminder_defaults: {
      event_offset_seconds: 300,
      todo_offset_seconds: null,
      primary_device_id: device,
    },
  });
});
