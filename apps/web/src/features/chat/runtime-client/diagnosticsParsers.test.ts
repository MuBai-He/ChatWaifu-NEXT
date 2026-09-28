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

  it("accepts the runtime's tool digest and full model ID limits", () => {
    const parsed = parseInteractionTraceDetail({
      ...detailFixture,
      prompt_identity: {
        schema_version: "1.0",
        identity_hash: "a".repeat(64),
        character_id: "default",
        character_package_hash: "b".repeat(64),
        prompt_template_version: "default-v1",
        presentation_profile: "default",
        chat_route: {
          role: "chat",
          provider: "demo",
          model: "m".repeat(256),
          endpoint_digest: "c".repeat(64),
          context_window: 8192,
        },
        memory_summary_route: {
          role: "memory_summary",
          provider: "demo",
          model: "deterministic-summary-v1",
          endpoint_digest: null,
          context_window: 8192,
        },
        tools_digest: "d".repeat(32),
      },
    });
    expect(parsed.prompt_identity?.tools_digest).toHaveLength(32);
    expect(parsed.prompt_identity?.chat_route.model).toHaveLength(256);
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
