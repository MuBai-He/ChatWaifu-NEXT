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
  it("completes with final terminal text matching delta stream", () => {
    vi.useFakeTimers();
    let visible = "";
    let completedWith: string | undefined;
    const projector = new StreamingTextProjector({
      onReveal: (_generationId, text) => {
        visible += text;
      },
      onComplete: (_generationId, finalText) => {
        completedWith = finalText;
      },
    });

    const fullText = "宁宁的第一句话和第二句话。";
    projector.start("generation-1");
    projector.push("generation-1", "宁宁的第一句话");
    projector.push("generation-1", "和第二句话。");
    projector.complete("generation-1", fullText);
    vi.runAllTimers();

    expect(visible).toBe(fullText);
    expect(completedWith).toBe(fullText);
  });

  it("recovers missing suffix when a delta is dropped before completion", () => {
    vi.useFakeTimers();
    let visible = "";
    let completedWith: string | undefined;
    const projector = new StreamingTextProjector({
      onReveal: (_generationId, text) => {
        visible += text;
      },
      onComplete: (_generationId, finalText) => {
        completedWith = finalText;
      },
    });

    const fullText = "宁宁的前半句与丢失的后半句。";
    projector.start("generation-1");
    projector.push("generation-1", "宁宁的前半句");
    // Missing delta: '与丢失的后半句。' was not received over websocket
    projector.complete("generation-1", fullText);
    vi.runAllTimers();

    expect(visible).toBe(fullText);
    expect(completedWith).toBe(fullText);
  });
  it("never produces duplicate appended prefix when completion arrives before any delta", () => {
    vi.useFakeTimers();
    let messageText = "";
    const snapshots: string[] = [];

    const projector = new StreamingTextProjector({
      onReveal: (_generationId, text) => {
        messageText += text;
        snapshots.push(messageText);
      },
      onComplete: (_generationId, finalText) => {
        if (typeof finalText === "string" && finalText.length > 0) {
          messageText = finalText;
        }
        snapshots.push(messageText);
      },
    });

    const fullText = "绫地宁宁的完整终态文本";
    projector.start("generation-1");
    projector.complete("generation-1", fullText);

    while (vi.getTimerCount() > 0) {
      vi.advanceTimersByTime(24);
    }

    expect(snapshots.length).toBeGreaterThan(0);
    for (const text of snapshots) {
      expect(text).toBe(fullText.slice(0, text.length));
      expect(text.startsWith(fullText + fullText.slice(0, 2))).toBe(false);
    }
    expect(messageText).toBe(fullText);
  });
});
