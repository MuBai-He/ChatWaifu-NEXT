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
}

describe("GenerationAudioPlayer", () => {
  it("keeps a stale play rejection from releasing the new audio", async () => {
    let activeGeneration = "generation-1";
    const harness = createHarness(() => activeGeneration);
    harness.player.enqueue({ generationId: activeGeneration, url: "/one.wav" });
    const first = harness.audios[0];
    const staleEnded = first?.onended;
    expect(first).toBeDefined();

    harness.player.stop();
    activeGeneration = "generation-2";
    harness.player.enqueue({ generationId: activeGeneration, url: "/two.wav" });
    const second = harness.audios[1];
    expect(second).toBeDefined();

    first?.rejectPlay(autoplayError());
    staleEnded?.(new Event("ended"));
    await flushPromises();
    harness.player.enqueue({
      generationId: activeGeneration,
      url: "/three.wav",
    });

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
    harness.player.enqueue({ generationId: "generation-1", url: "/one.wav" });
    harness.player.enqueue({ generationId: "generation-1", url: "/two.wav" });

    harness.audios[0]?.rejectPlay(autoplayError());
    await flushPromises();

    expect(harness.audios).toHaveLength(1);
    expect(harness.errors).toEqual([AUTOPLAY_BLOCKED_MESSAGE]);
    expect(harness.onPlaybackStop).toHaveBeenCalledOnce();
  });

  it("reports decode failures without claiming autoplay was blocked", () => {
    const harness = createHarness(() => "generation-1");
    harness.player.enqueue({ generationId: "generation-1", url: "/one.wav" });

    harness.audios[0]?.fail();

    expect(harness.errors).toEqual([AUDIO_PLAYBACK_FAILED_MESSAGE]);
  });

  it("treats an interrupted play promise as cancellation", async () => {
    const harness = createHarness(() => "generation-1");
    harness.player.enqueue({ generationId: "generation-1", url: "/one.wav" });
    harness.player.enqueue({ generationId: "generation-1", url: "/two.wav" });

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
    harness.player.enqueue({ generationId: "generation-1", url: "/one.wav" });

    expect(harness.audios).toHaveLength(1);
    expect(probe?.src).toBe("/one.wav");
    expect(probe?.play).toHaveBeenCalledTimes(2);
  });

  it("drops stale generation chunks before creating audio", () => {
    const harness = createHarness(() => "generation-2");

    harness.player.enqueue({ generationId: "generation-1", url: "/old.wav" });

    expect(harness.audios).toEqual([]);
  });
});

function createHarness(activeGeneration: () => string) {
  const audios: FakeAudio[] = [];
  const errors: string[] = [];
  const onPlaybackStop = vi.fn();
  const player = new GenerationAudioPlayer(
    (url) => {
      const audio = new FakeAudio(url);
      audios.push(audio);
      return audio;
    },
    {
      isGenerationActive: (generationId) => generationId === activeGeneration(),
      onPlaybackStart: vi.fn(),
      onPlaybackStop,
      onPlaybackError: (message) => errors.push(message),
    },
  );
  return { player, audios, errors, onPlaybackStop };
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
