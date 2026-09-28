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

  it("does not promise a cursor for bounded non-timeline metadata", async () => {
    vi.mocked(runtimeClient.getInteractionTraceDetail).mockResolvedValue({
      ...detail(first),
      truncated: true,
      next_cursor: null,
    });
    render(
      <InteractionDiagnosticsPanel
        sessionId={first.session_id}
        runtimeOnline
      />,
    );
    fireEvent.click(screen.getByText("高级诊断：单次互动"));
    const list = await screen.findByRole("list", { name: "互动诊断列表" });
    fireEvent.click(list.querySelectorAll("button")[0]);
    expect(
      await screen.findByText("部分元数据超过上限，已截断。"),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "继续读取事件" })).toBeNull();
  });

  it("returns to recent interactions when refreshed from an older page", async () => {
    const older = { ...page, items: [second], has_more: false };
    vi.mocked(runtimeClient.getInteractionTraces).mockImplementation(
      (_session, options) =>
        Promise.resolve(
          options?.cursor
            ? older
            : { ...page, items: [first], has_more: true, next_cursor: "older" },
        ),
    );
    render(
      <InteractionDiagnosticsPanel
        sessionId={first.session_id}
        runtimeOnline
      />,
    );
    fireEvent.click(screen.getByText("高级诊断：单次互动"));
    fireEvent.click(
      await screen.findByRole("button", { name: "查看更早互动" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("list", { name: "互动诊断列表" }).textContent,
      ).toContain("cancelled"),
    );
    fireEvent.click(screen.getByRole("button", { name: "返回最新互动" }));
    await waitFor(() =>
      expect(
        screen.getByRole("list", { name: "互动诊断列表" }).textContent,
      ).toContain("completed"),
    );
    fireEvent.click(screen.getByRole("button", { name: "查看更早互动" }));
    await waitFor(() =>
      expect(
        screen.getByRole("list", { name: "互动诊断列表" }).textContent,
      ).toContain("cancelled"),
    );
    fireEvent.click(screen.getByRole("button", { name: "刷新诊断" }));
    await waitFor(() =>
      expect(runtimeClient.getInteractionTraces).toHaveBeenLastCalledWith(
        first.session_id,
        expect.objectContaining({ cursor: null }),
      ),
    );
    expect(screen.queryByRole("button", { name: "返回最新互动" })).toBeNull();
  });

  it("keeps earlier timeline events when continuing with a cursor", async () => {
    vi.mocked(runtimeClient.getInteractionTraceDetail).mockImplementation(
      (_session, _id, options) =>
        Promise.resolve({
          ...detail(first),
          timeline: [
            {
              schema_version: "1.0",
              sequence: options?.afterSequence ? 3 : 2,
              event_type: options?.afterSequence
                ? "tool.call_finished"
                : "memory.recalled",
              occurred_at: first.occurred_at,
              reason: null,
              status: null,
            },
          ],
          truncated: !options?.afterSequence,
          next_cursor: options?.afterSequence ? null : 2,
        }),
    );
    render(
      <InteractionDiagnosticsPanel
        sessionId={first.session_id}
        runtimeOnline
      />,
    );
    fireEvent.click(screen.getByText("高级诊断：单次互动"));
    const list = await screen.findByRole("list", { name: "互动诊断列表" });
    fireEvent.click(list.querySelectorAll("button")[0]);
    fireEvent.click(
      await screen.findByRole("button", { name: "继续读取事件" }),
    );
    await screen.findByText(/3 · tool.call_finished/);
    expect(screen.getByText(/2 · memory.recalled/)).toBeTruthy();
  });

  it("distinguishes legacy memory selection and queued audio from confirmed playback", async () => {
    vi.mocked(runtimeClient.getInteractionTraceDetail).mockResolvedValue({
      ...detail(first),
      memory_candidates: [
        {
          schema_version: "1.0",
          memory_id: "00000000-0000-4000-8000-000000000904",
          score: 0.9,
          selected_for_prompt: null,
          currently_visible: true,
        },
      ],
      playback_segments: [
        {
          schema_version: "1.0",
          segment_id: "00000000-0000-4000-8000-000000000905",
          segment_index: 0,
          state: "queued",
          played_pts_ms: 0,
          transport: "audio_element",
        },
      ],
    });
    render(
      <InteractionDiagnosticsPanel
        sessionId={first.session_id}
        runtimeOnline
      />,
    );
    fireEvent.click(screen.getByText("高级诊断：单次互动"));
    const list = await screen.findByRole("list", { name: "互动诊断列表" });
    fireEvent.click(list.querySelectorAll("button")[0]);
    expect(await screen.findByText(/是否注入未知/)).toBeTruthy();
    expect(screen.getByText(/播放确认：已排队，尚无客户端确认/)).toBeTruthy();
    expect(
      screen.getByText(/分段状态：queued · 尚无客户端播放确认/),
    ).toBeTruthy();
    expect(screen.queryByText(/客户端 ACK：queued/)).toBeNull();
  });

  it("marks an ignored voice event as having no prompt and a playback ACK as progress", async () => {
    const ignored: InteractionTraceSummary = {
      ...first,
      generation_id: null,
      generation_state: null,
      trigger: "ignored_voice",
    };
    vi.mocked(runtimeClient.getInteractionTraces).mockResolvedValue({
      ...page,
      items: [ignored, second],
    });
    vi.mocked(runtimeClient.getInteractionTraceDetail).mockImplementation(
      (_session, id) =>
        Promise.resolve(
          id === ignored.interaction_id
            ? detail(ignored)
            : {
                ...detail(second),
                playback_segments: [
                  {
                    schema_version: "1.0",
                    segment_id: "00000000-0000-4000-8000-000000000905",
                    segment_index: 0,
                    state: "playing",
                    played_pts_ms: 120,
                    transport: "audio_element",
                  },
                ],
              },
        ),
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
    expect(await screen.findByText(/实际选中：未生成提示词/)).toBeTruthy();
    fireEvent.click(buttons[1]);
    expect(
      await screen.findByText(/播放确认：有客户端确认，见分段状态/),
    ).toBeTruthy();
    expect(screen.getByText(/客户端确认进度 120 ms/)).toBeTruthy();
  });
});
