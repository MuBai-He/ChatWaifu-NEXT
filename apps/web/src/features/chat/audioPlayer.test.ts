import { describe, expect, it, vi } from "vitest";

import {
  AUDIO_PLAYBACK_FAILED_MESSAGE,
  AUTOPLAY_BLOCKED_MESSAGE,
  GenerationAudioPlayer,
} from "./audioPlayer";
import type { PlayableAudio } from "./audioPlayer";

class FakeAudio implements PlayableAudio {
  src: string;
  preload = "";
  onplay: ((event: Event) => void) | null = null;
  onended: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  ontimeupdate: ((event: Event) => void) | null = null;
  currentTime = 0;
  duration = 1;
  buffered = { length: 0, end: () => 0 };
  readonly play = vi.fn(() => this.playback.promise);
  readonly pause = vi.fn();
  readonly removeAttribute = vi.fn();
  readonly load = vi.fn();
  private readonly playback = deferred<void>();

  constructor(url: string) {
    this.src = url;
  }

  resolvePlay(): void {
    this.onplay?.(new Event("play"));
    this.playback.resolve();
  }

  rejectPlay(error: unknown): void {
    this.playback.reject(error);
  }

  finish(): void {
    this.onended?.(new Event("ended"));
  }

  fail(): void {
    this.onerror?.(new Event("error"));
  }

  tick(currentTime: number): void {
    this.currentTime = currentTime;
    this.ontimeupdate?.(new Event("timeupdate"));
  }
}

describe("GenerationAudioPlayer", () => {
  it("keeps a stale play rejection from releasing the new audio", async () => {
    let activeGeneration = "generation-1";
    const harness = createHarness(() => activeGeneration);
    harness.player.enqueue(playbackItem(activeGeneration, "/one.wav"));
    const first = harness.audios[0];
    const staleEnded = first?.onended;
    expect(first).toBeDefined();

    harness.player.stop();
    activeGeneration = "generation-2";
    harness.player.enqueue(playbackItem(activeGeneration, "/two.wav"));
    const second = harness.audios[1];
    expect(second).toBeDefined();

    first?.rejectPlay(autoplayError());
    staleEnded?.(new Event("ended"));
    await flushPromises();
    harness.player.enqueue(playbackItem(activeGeneration, "/three.wav"));

    expect(harness.audios).toHaveLength(2);
    expect(harness.errors).toEqual([]);
    expect(harness.onPlaybackStop).toHaveBeenCalledOnce();

    second?.finish();
    expect(harness.audios.map((audio) => audio.src)).toEqual([
      "/one.wav",
      "/two.wav",
      "/three.wav",
    ]);
  });

  it("clears queued chunks when the current audio is blocked", async () => {
    const harness = createHarness(() => "generation-1");
    harness.player.enqueue(playbackItem("generation-1", "/one.wav"));
    harness.player.enqueue(playbackItem("generation-1", "/two.wav"));

    harness.audios[0]?.rejectPlay(autoplayError());
    await flushPromises();

    expect(harness.audios).toHaveLength(1);
    expect(harness.errors).toEqual([AUTOPLAY_BLOCKED_MESSAGE]);
    expect(harness.onPlaybackStop).toHaveBeenCalledOnce();
  });

  it("reports decode failures without claiming autoplay was blocked", () => {
    const harness = createHarness(() => "generation-1");
    harness.player.enqueue(playbackItem("generation-1", "/one.wav"));

    harness.audios[0]?.fail();

    expect(harness.errors).toEqual([AUDIO_PLAYBACK_FAILED_MESSAGE]);
  });

  it("treats an interrupted play promise as cancellation", async () => {
    const harness = createHarness(() => "generation-1");
    harness.player.enqueue(playbackItem("generation-1", "/one.wav"));
    harness.player.enqueue(playbackItem("generation-1", "/two.wav"));

    const error = new Error("play() was interrupted by pause()");
    error.name = "AbortError";
    harness.audios[0]?.rejectPlay(error);
    await flushPromises();

    expect(harness.errors).toEqual([]);
    expect(harness.audios.map((audio) => audio.src)).toEqual([
      "/one.wav",
      "/two.wav",
    ]);
  });

  it("reuses the user-gesture audio element for queued speech", async () => {
    const harness = createHarness(() => "generation-1");

    harness.player.prime();
    const probe = harness.audios[0];
    probe?.resolvePlay();
    await flushPromises();
    harness.player.enqueue(playbackItem("generation-1", "/one.wav"));

    expect(harness.audios).toHaveLength(1);
    expect(probe?.src).toBe("/one.wav");
    expect(probe?.play).toHaveBeenCalledTimes(2);
  });

  it("promotes a hanging user-gesture probe when real speech arrives", async () => {
    const harness = createHarness(() => "generation-1");

    harness.player.prime();
    const probe = harness.audios[0];
    harness.player.enqueue(playbackItem("generation-1", "/one.wav"));

    expect(harness.audios).toHaveLength(1);
    expect(probe?.pause).toHaveBeenCalledOnce();
    expect(probe?.src).toBe("/one.wav");
    expect(probe?.play).toHaveBeenCalledTimes(2);

    probe?.resolvePlay();
    await flushPromises();
    expect(harness.starts).toHaveLength(1);
  });

  it("drops stale generation chunks before creating audio", () => {
    const harness = createHarness(() => "generation-2");

    harness.player.enqueue(playbackItem("generation-1", "/old.wav"));

    expect(harness.audios).toEqual([]);
  });

  it("reports measured progress and commits the registered duration on ended", () => {
    const harness = createHarness(() => "generation-1");
    const item = playbackItem("generation-1", "/one.wav");
    harness.player.enqueue(item);
    const audio = harness.audios[0];

    audio?.resolvePlay();
    audio?.tick(0.2);
    audio?.tick(0.4);
    audio?.finish();

    expect(harness.starts[0]).toMatchObject({ item, playedPtsMs: 0 });
    expect(harness.progress.map((receipt) => receipt.playedPtsMs)).toEqual([
      400,
    ]);
    expect(harness.stops[0]).toMatchObject({
      item,
      playedPtsMs: 1000,
      reason: "ended",
    });
  });

  it("marks queued segments cleared and active audio interrupted on stop", () => {
    const harness = createHarness(() => "generation-1");
    const first = playbackItem("generation-1", "/one.wav");
    const second = playbackItem("generation-1", "/two.wav");
    harness.player.enqueue(first);
    harness.player.enqueue(second);
    harness.audios[0]?.resolvePlay();
    harness.audios[0]?.tick(0.35);

    harness.player.stop();

    expect(harness.stops.at(-1)).toMatchObject({
      item: first,
      playedPtsMs: 350,
      reason: "interrupted",
    });
    expect(harness.cleared).toEqual([second]);
  });
});

function createHarness(activeGeneration: () => string) {
  const audios: FakeAudio[] = [];
  const errors: string[] = [];
  const starts: Array<{
    item: ReturnType<typeof playbackItem>;
    playedPtsMs: number;
  }> = [];
  const progress: Array<{
    item: ReturnType<typeof playbackItem>;
    playedPtsMs: number;
  }> = [];
  const stops: Array<{
    item: ReturnType<typeof playbackItem>;
    playedPtsMs: number;
    reason: string;
  }> = [];
  const cleared: ReturnType<typeof playbackItem>[] = [];
  const onPlaybackStop = vi.fn();
  const player = new GenerationAudioPlayer(
    (url) => {
      const audio = new FakeAudio(url);
      audios.push(audio);
      return audio;
    },
    {
      isGenerationActive: (generationId) => generationId === activeGeneration(),
      onPlaybackStart: (item, position) =>
        starts.push({ item, playedPtsMs: position.playedPtsMs }),
      onPlaybackProgress: (item, position) =>
        progress.push({ item, playedPtsMs: position.playedPtsMs }),
      onPlaybackStop: (item, position, reason) => {
        onPlaybackStop();
        stops.push({ item, playedPtsMs: position.playedPtsMs, reason });
      },
      onQueueCleared: (item) => cleared.push(item),
      onPlaybackError: (message) => errors.push(message),
    },
  );
  return {
    player,
    audios,
    errors,
    onPlaybackStop,
    starts,
    progress,
    stops,
    cleared,
  };
}

function playbackItem(generationId: string, url: string) {
  const suffix = url.replace(/\W/g, "") || "audio";
  return {
    generationId,
    streamId: `00000000-0000-4000-8000-${suffix.padEnd(12, "0").slice(0, 12)}`,
    segmentId: `00000000-0000-4000-8001-${suffix.padEnd(12, "0").slice(0, 12)}`,
    segmentIndex: 0,
    text: "测试语音",
    durationMs: 1000,
    url,
  };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function autoplayError(): Error {
  const error = new Error("play() failed because autoplay is disabled");
  error.name = "NotAllowedError";
  return error;
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
