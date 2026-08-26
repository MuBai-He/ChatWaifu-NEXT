import type { MemoryProposal } from "@chatwaifu/protocol";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
});
