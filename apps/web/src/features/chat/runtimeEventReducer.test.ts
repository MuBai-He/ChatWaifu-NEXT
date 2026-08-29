import { describe, expect, it } from "vitest";
import type { DomainEvent } from "@chatwaifu/protocol";

import {
  initialRuntimeViewState,
  runtimeEventReducer,
} from "./runtimeEventReducer";

describe("runtimeEventReducer", () => {
  it("projects streamed text only into its matching generation", () => {
    const state = {
      ...initialRuntimeViewState,
      messages: [
        {
          id: "generation-a",
          role: "assistant" as const,
          text: "宁",
          generationId: "generation-a",
          pending: true,
        },
        {
          id: "generation-b",
          role: "assistant" as const,
          text: "旧回复",
          generationId: "generation-b",
          pending: false,
        },
      ],
    };

    const revealed = runtimeEventReducer(state, {
      type: "text_revealed",
      generationId: "generation-a",
      text: "宁",
    });
    const completed = runtimeEventReducer(revealed, {
      type: "text_completed",
      generationId: "generation-a",
    });

    expect(completed.messages).toEqual([
      expect.objectContaining({ text: "宁宁", pending: false }),
      expect.objectContaining({ text: "旧回复", pending: false }),
    ]);
  });

  it("resets all projected UI state without retaining stale arrays", () => {
    const reset = runtimeEventReducer(
      {
        messages: [{ id: "turn", role: "user", text: "会被当前会话重置清除" }],
        voiceActivity: "thinking",
        voiceTranscript: "处理中",
        error: "旧错误",
      },
      { type: "reset" },
    );

    expect(reset).toEqual(initialRuntimeViewState);
    expect(reset).not.toBe(initialRuntimeViewState);
  });

  it("applies a durable reset event received from another window", () => {
    const reset = runtimeEventReducer(
      {
        messages: [{ id: "turn", role: "assistant", text: "旧对话" }],
        voiceActivity: "thinking",
        voiceTranscript: "旧转写",
        error: "旧错误",
      },
      {
        type: "runtime_event",
        event: {
          event_id: "00000000-0000-4000-8000-000000000101",
          schema_version: "1.0",
          event_type: "session.data_reset",
          session_id: "00000000-0000-4000-8000-000000000201",
          sequence: 42,
          occurred_at: "2026-08-29T00:00:00Z",
          source: "runtime.conversation",
          privacy: "private",
          payload: {
            character_id: "default",
            user_scope: "local",
            conversation: "current_session",
            audio: "current_session",
            memory: "current_character_user",
            character_state: "current_character_user",
          },
        } as DomainEvent,
      },
    );

    expect(reset).toEqual(initialRuntimeViewState);
  });
});
