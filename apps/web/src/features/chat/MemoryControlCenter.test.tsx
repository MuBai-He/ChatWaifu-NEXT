import type { MemoryProposal, MemorySource } from "@chatwaifu/protocol";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryControlCenter } from "./MemoryControlCenter";
import * as runtimeClient from "./runtimeClient";
import type { MemoryItem } from "./types";

vi.mock("./runtimeClient", () => ({
  correctMemory: vi.fn(),
  decideMemoryProposal: vi.fn(),
  forgetMemory: vi.fn(),
  getMemoryProposals: vi.fn(),
  getMemoryRecords: vi.fn(),
  getMemorySources: vi.fn(),
  setMemoryPinned: vi.fn(),
}));

const record = {
  memory_id: "00000000-0000-4000-8000-000000000101",
  namespace: "character/default/user/local",
  kind: "semantic.preference",
  subject_id: "user",
  predicate: "preference.like.蓝色",
  value: true,
  text: "我喜欢蓝色",
  content: "我喜欢蓝色",
  source_event_ids: ["00000000-0000-4000-8000-000000000201"],
  observed_at: "2026-08-24T12:00:00Z",
  confidence: 0.86,
  importance: 0.72,
  sensitivity: "private",
  state: "active",
  pinned: false,
  created_at: "2026-08-24T12:00:00Z",
  updated_at: "2026-08-24T12:00:00Z",
} as MemoryItem;

const proposal = {
  proposal_id: "00000000-0000-4000-8000-000000000301",
  operation: "add",
  candidate: {
    namespace: "character/default/user/local",
    kind: "semantic.preference",
    subject_id: "user",
    predicate: "preference.like.紫色",
    value: true,
    text: "我喜欢紫色",
    observed_at: "2026-08-24T12:00:00Z",
    confidence: 0.86,
    importance: 0.72,
    sensitivity: "private",
  },
  evidence_event_ids: ["00000000-0000-4000-8000-000000000401"],
  confidence: 0.86,
  rationale: "explicitly phrased user preference",
  status: "pending",
  created_at: "2026-08-24T12:00:00Z",
} as MemoryProposal;

describe("MemoryControlCenter", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(runtimeClient.getMemoryRecords).mockResolvedValue([record]);
    vi.mocked(runtimeClient.getMemoryProposals).mockResolvedValue([proposal]);
    vi.mocked(runtimeClient.decideMemoryProposal).mockResolvedValue({
      ...proposal,
      status: "accepted",
    });
    vi.mocked(runtimeClient.getMemorySources).mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
  });

  it("reviews proposals and exposes structured record management", async () => {
    const onChanged = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryControlCenter
        sessionId="00000000-0000-4000-8000-000000000501"
        onChanged={onChanged}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "记忆中心" }));
    const dialog = screen.getByRole("dialog", { name: "结构化记忆中心" });
    expect(dialog.parentElement?.parentElement).toBe(document.body);
    expect(await screen.findByText("我喜欢紫色")).toBeTruthy();
    expect(screen.getByText("我喜欢蓝色")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "接受" }));
    await waitFor(() =>
      expect(runtimeClient.decideMemoryProposal).toHaveBeenCalledWith(
        "00000000-0000-4000-8000-000000000501",
        proposal.proposal_id,
        "accept",
      ),
    );
    expect(onChanged).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "查看来源" }));
    await waitFor(() =>
      expect(runtimeClient.getMemorySources).toHaveBeenCalledWith(
        record.memory_id,
      ),
    );
  });

  it("shows friendly channel provenance without exposing routing identifiers", async () => {
    const receivedAt = "2026-08-31T08:00:00Z";
    const expectedTime = new Date(receivedAt).toLocaleString("zh-CN");

    const channelSource: MemorySource = {
      source_id: "00000000-0000-4000-8000-000000000601",
      memory_id: record.memory_id,
      session_id: "00000000-0000-4000-8000-000000000501",
      source_event_id: "00000000-0000-4000-8000-000000000602",
      turn_id: "00000000-0000-4000-8000-000000000603",
      source_kind: "user_turn",
      created_at: "2026-08-31T08:00:01Z",
      channel_attribution: {
        schema_version: "1.0",
        provider_id: "weixin_ilink",
        connection_id: "00000000-0000-4000-8000-000000000604",
        account_key: "private-account-key",
        principal_scope: "private-principal-scope",
        chat_type: "direct",
        conversation_key: "private-conversation-key",
        sender_key: "private-sender-key",
        received_at: receivedAt,
        conversation_label: "不应优先显示的会话名",
        sender_display_name: "<b>木白</b>\n  私聊",
      },
    };

    const nonChannelSource: MemorySource = {
      source_id: "00000000-0000-4000-8000-000000000611",
      memory_id: record.memory_id,
      session_id: "00000000-0000-4000-8000-000000000501",
      source_event_id: "12345678-0000-4000-8000-000000000612",
      turn_id: "87654321-0000-4000-8000-000000000613",
      source_kind: "user_turn",
      created_at: "2026-08-31T08:00:01Z",
    };

    const groupSource: MemorySource = {
      ...channelSource,
      source_id: "00000000-0000-4000-8000-000000000621",
      source_event_id: "00000000-0000-4000-8000-000000000622",
      turn_id: "00000000-0000-4000-8000-000000000623",
      channel_attribution: {
        ...channelSource.channel_attribution!,
        chat_type: "group",
        sender_display_name: null,
        conversation_label: "粉丝群",
      },
    };

    const unknownProviderSource: MemorySource = {
      ...channelSource,
      source_id: "00000000-0000-4000-8000-000000000631",
      source_event_id: "00000000-0000-4000-8000-000000000632",
      turn_id: "00000000-0000-4000-8000-000000000633",
      channel_attribution: {
        ...channelSource.channel_attribution!,
        provider_id: "future_provider",
        sender_display_name: null,
        conversation_label: null,
      },
    };

    const prototypeProviderSource: MemorySource = {
      ...unknownProviderSource,
      source_id: "00000000-0000-4000-8000-000000000641",
      source_event_id: "00000000-0000-4000-8000-000000000642",
      turn_id: "00000000-0000-4000-8000-000000000643",
      channel_attribution: {
        ...unknownProviderSource.channel_attribution!,
        provider_id: "constructor",
      },
    };

    vi.mocked(runtimeClient.getMemorySources).mockResolvedValue([
      channelSource,
      groupSource,
      unknownProviderSource,
      prototypeProviderSource,
      nonChannelSource,
    ]);

    render(
      <MemoryControlCenter
        sessionId="00000000-0000-4000-8000-000000000501"
        onChanged={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "记忆中心" }));
    const dialog = screen.getByRole("dialog", { name: "结构化记忆中心" });

    fireEvent.click(await screen.findByRole("button", { name: "查看来源" }));

    const friendlyLabel = `微信 · 私聊 · ${expectedTime} · <b>木白</b> 私聊`;
    const friendlySource = await screen.findByText(friendlyLabel);
    expect(friendlySource.querySelector("b")).toBeNull();
    expect(
      screen.getByText(`微信 · 群聊 · ${expectedTime} · 粉丝群`),
    ).toBeTruthy();
    expect(
      screen.getAllByText(`外部渠道 · 私聊 · ${expectedTime}`),
    ).toHaveLength(2);
    expect(
      screen.getByText("user_turn · event 12345678 · turn 87654321"),
    ).toBeTruthy();

    const forbiddenIdentifiers = [
      "00000000-0000-4000-8000-000000000604",
      "private-account-key",
      "private-principal-scope",
      "private-conversation-key",
      "private-sender-key",
      "00000000-0000-4000-8000-000000000602",
      "00000000-0000-4000-8000-000000000603",
      "00000000-0000-4000-8000-000000000622",
      "00000000-0000-4000-8000-000000000623",
      "00000000-0000-4000-8000-000000000632",
      "00000000-0000-4000-8000-000000000633",
      "00000000-0000-4000-8000-000000000642",
      "00000000-0000-4000-8000-000000000643",
      "不应优先显示的会话名",
      "future_provider",
      "constructor",
    ];
    for (const identifier of forbiddenIdentifiers) {
      expect(dialog.textContent).not.toContain(identifier);
    }
  });
});
