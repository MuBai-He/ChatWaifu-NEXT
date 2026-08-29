import { describe, expect, it, vi } from "vitest";

import { PlaybackAckReporter } from "./playbackAckReporter";
import type { PlaybackAckReceipt } from "./runtimeClient";

describe("PlaybackAckReporter", () => {
  it("keeps terminal receipts ordered behind progress", async () => {
    const first = deferred<void>();
    const sent: PlaybackAckReceipt[] = [];
    const send = vi.fn(async (receipt: PlaybackAckReceipt) => {
      sent.push(receipt);
      if (sent.length === 1) await first.promise;
    });
    const reporter = new PlaybackAckReporter({ send, onError: vi.fn() });

    reporter.report(receipt("started", 0));
    reporter.report(receipt("progress", 200));
    reporter.report(receipt("progress", 450));
    reporter.report(receipt("stopped", 1000));
    expect(sent.map((item) => item.phase)).toEqual(["started"]);

    first.resolve();
    await flushPromises();

    expect(sent.map((item) => [item.phase, item.playedPtsMs])).toEqual([
      ["started", 0],
      ["progress", 450],
      ["stopped", 1000],
    ]);
  });

  it("retries a transient failure without letting the terminal receipt overtake it", async () => {
    vi.useFakeTimers();
    const sent: string[] = [];
    const onError = vi.fn();
    let startedAttempts = 0;
    const reporter = new PlaybackAckReporter(
      {
        send: (item) => {
          sent.push(item.phase);
          if (item.phase === "started" && ++startedAttempts < 3)
            return Promise.reject(new Error("offline"));
          return Promise.resolve();
        },
        onError,
      },
      64,
      [10, 20],
    );

    reporter.report(receipt("started", 0));
    reporter.report(receipt("stopped", 1000));
    await flushPromises();
    expect(sent).toEqual(["started"]);
    await vi.advanceTimersByTimeAsync(10);
    expect(sent).toEqual(["started", "started"]);
    await vi.advanceTimersByTimeAsync(20);
    await flushPromises();

    expect(sent).toEqual(["started", "started", "started", "stopped"]);
    expect(onError).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});

function receipt(
  phase: PlaybackAckReceipt["phase"],
  playedPtsMs: number,
): PlaybackAckReceipt {
  return {
    phase,
    generationId: "generation-1",
    streamId: "stream-1",
    segmentId: "segment-1",
    playedPtsMs,
    bufferedMs: 0,
    clientClockMs: playedPtsMs,
    transport: "audio_element",
    reason: phase === "stopped" ? "ended" : undefined,
  };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}
