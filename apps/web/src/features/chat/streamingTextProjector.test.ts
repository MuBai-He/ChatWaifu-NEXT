import { afterEach, describe, expect, it, vi } from "vitest";

import { StreamingTextProjector } from "./streamingTextProjector";

describe("StreamingTextProjector", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reveals a burst progressively and completes only after draining", () => {
    vi.useFakeTimers();
    let visible = "";
    const completed: string[] = [];
    const projector = new StreamingTextProjector({
      onReveal: (_generationId, text) => {
        visible += text;
      },
      onComplete: (generationId) => completed.push(generationId),
    });

    projector.start("generation-1");
    projector.push("generation-1", "这是一段突然抵达但应当逐步显示的回复。");
    projector.complete("generation-1");

    expect(visible).toBe("");
    expect(completed).toEqual([]);
    vi.advanceTimersByTime(24);
    expect(visible.length).toBeGreaterThan(0);
    expect(visible).not.toBe("这是一段突然抵达但应当逐步显示的回复。");

    vi.runAllTimers();
    expect(visible).toBe("这是一段突然抵达但应当逐步显示的回复。");
    expect(completed).toEqual(["generation-1"]);
  });

  it("drops queued stale text when a generation is cancelled or replaced", () => {
    vi.useFakeTimers();
    const revealed: string[] = [];
    const projector = new StreamingTextProjector({
      onReveal: (_generationId, text) => revealed.push(text),
      onComplete: vi.fn(),
    });

    projector.start("generation-1");
    projector.push("generation-1", "这段旧回复不能泄漏");
    projector.cancel("generation-1");
    projector.start("generation-2");
    projector.push("generation-1", "迟到旧文本");
    projector.push("generation-2", "新回复");
    vi.runAllTimers();

    expect(revealed.join("")).toBe("新回复");
  });

  it("bounds an extreme backlog without losing code points", () => {
    vi.useFakeTimers();
    let visible = "";
    const projector = new StreamingTextProjector(
      {
        onReveal: (_generationId, text) => {
          visible += text;
        },
        onComplete: vi.fn(),
      },
      { maxQueuedCodePoints: 16 },
    );
    const text = "宁".repeat(40);

    projector.start("generation-1");
    projector.push("generation-1", text);
    projector.complete("generation-1");
    vi.runAllTimers();

    expect(visible).toBe(text);
  });
});
