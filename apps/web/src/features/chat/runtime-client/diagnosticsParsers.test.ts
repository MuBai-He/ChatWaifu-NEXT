import { describe, expect, it } from "vitest";

import {
  parseInteractionTraceDetail,
  parseInteractionTracePage,
} from "./diagnosticsParsers";

const summary = {
  schema_version: "1.0",
  interaction_id: "00000000-0000-4000-8000-000000000901",
  session_id: "00000000-0000-4000-8000-000000000201",
  turn_id: "00000000-0000-4000-8000-000000000902",
  generation_id: "00000000-0000-4000-8000-000000000901",
  occurred_at: "2026-08-23T08:00:00Z",
  trigger: "user",
  reason: null,
  generation_state: "completed",
};
const pageFixture = {
  schema_version: "1.0",
  items: [summary],
  has_more: false,
  next_cursor: null,
};
const detailFixture = {
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

describe("interaction diagnostics wire parser", () => {
  it("reads versioned page and detail shapes", () => {
    const page = parseInteractionTracePage(pageFixture);
    const detail = parseInteractionTraceDetail(detailFixture);
    expect(page.items?.[0]?.interaction_id).toBe(detail.summary.interaction_id);
    expect(detail.selected_memory_ids).toBeNull();
  });

  it("strips unapproved content fields and rejects unbounded pages", () => {
    const parsed = parseInteractionTraceDetail({
      ...detailFixture,
      raw_prompt: "must not reach UI",
    });
    expect("raw_prompt" in parsed).toBe(false);
    const page = { ...pageFixture, items: Array(51).fill(summary) };
    expect(() => parseInteractionTracePage(page)).toThrow();
  });
});
