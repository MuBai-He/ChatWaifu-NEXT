import type {
  InteractionTraceDetail,
  InteractionTracePage,
  InteractionTraceSummary,
} from "@chatwaifu/protocol";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as runtimeClient from "../chat/runtimeClient";
import { InteractionDiagnosticsPanel } from "./InteractionDiagnosticsPanel";

vi.mock("../chat/runtimeClient", () => ({
  getInteractionTraces: vi.fn(),
  getInteractionTraceDetail: vi.fn(),
}));

const first: InteractionTraceSummary = {
  schema_version: "1.0",
  interaction_id: "00000000-0000-4000-8000-000000000901",
  session_id: "00000000-0000-4000-8000-000000000201",
  turn_id: "00000000-0000-4000-8000-000000000902",
  generation_id: "00000000-0000-4000-8000-000000000901",
  occurred_at: "2026-09-29T00:00:00+08:00",
  trigger: "user",
  reason: null,
  generation_state: "completed",
};
const second: InteractionTraceSummary = {
  ...first,
  interaction_id: "00000000-0000-4000-8000-000000000903",
  generation_id: "00000000-0000-4000-8000-000000000903",
  generation_state: "cancelled",
};
const page: InteractionTracePage = {
  schema_version: "1.0",
  items: [first, second],
  has_more: false,
  next_cursor: null,
};

function detail(summary: InteractionTraceSummary): InteractionTraceDetail {
  return {
    schema_version: "1.0",
    summary,
    prompt_identity: null,
    response_plan: null,
    prompt_budget: null,
    memory_candidates: [],
    selected_memory_ids: null,
    tool_calls: [],
    delivery_status: null,
    delivery_parts: [],
    playback_segments: [],
    timeline: [],
    truncated: false,
    next_cursor: null,
  };
}

describe("InteractionDiagnosticsPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(runtimeClient.getInteractionTraces).mockResolvedValue(page);
    vi.mocked(runtimeClient.getInteractionTraceDetail).mockImplementation(
      (_session, id) =>
        Promise.resolve(detail(id === first.interaction_id ? first : second)),
    );
  });
  afterEach(cleanup);

  it("stays collapsed and lazy until opened, then distinguishes generation from delivery", async () => {
    render(
      <InteractionDiagnosticsPanel
        sessionId={first.session_id}
        runtimeOnline
      />,
    );
    expect(runtimeClient.getInteractionTraces).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("高级诊断：单次互动"));
    const list = await screen.findByRole("list", { name: "互动诊断列表" });
    expect(list.querySelectorAll("button")).toHaveLength(2);
    fireEvent.click(list.querySelectorAll("button")[0]);
    expect(await screen.findByText(/渠道交付：未知/)).toBeTruthy();
    expect(screen.getByText(/实际选中：未知（旧事件）/)).toBeTruthy();
    expect(screen.queryByText("秘密测试正文")).toBeNull();
  });

  it("does not let a stale detail response replace a newer selection", async () => {
    let resolveFirst: ((value: InteractionTraceDetail) => void) | undefined;
    const pendingFirst = new Promise<InteractionTraceDetail>((resolve) => {
      resolveFirst = resolve;
    });
    vi.mocked(runtimeClient.getInteractionTraceDetail).mockImplementation(
      (_session, id) =>
        id === first.interaction_id
          ? pendingFirst
          : Promise.resolve(detail(second)),
    );
    render(
      <InteractionDiagnosticsPanel
        sessionId={first.session_id}
        runtimeOnline
      />,
    );
    fireEvent.click(screen.getByText("高级诊断：单次互动"));
    const list = await screen.findByRole("list", { name: "互动诊断列表" });
    const buttons = list.querySelectorAll("button");
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    expect(await screen.findByText("互动 00000000")).toBeTruthy();
    expect(screen.getByText(/生成：cancelled/)).toBeTruthy();
    await act(async () => {
      resolveFirst?.(detail(first));
      await pendingFirst;
    });
    await waitFor(() =>
      expect(screen.getByText(/生成：cancelled/)).toBeTruthy(),
    );
  });
});
