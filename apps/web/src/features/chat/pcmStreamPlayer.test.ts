import { beforeEach, describe, expect, it, vi } from "vitest";
import { decodePcmBase64, PcmStreamPlayer } from "./pcmStreamPlayer";

class FakeSource {
  buffer: AudioBuffer | null = null;
  onended: (() => void) | null = null;
  readonly connect = vi.fn();
  readonly start = vi.fn();
  readonly stop = vi.fn();

  finish(): void {
    this.onended?.();
  }
}

class FakeContext {
  currentTime = 0;
  state: AudioContextState = "running";
  readonly destination = {} as AudioDestinationNode;
  readonly sources: FakeSource[] = [];
  readonly resume = vi.fn(() => Promise.resolve());
  readonly close = vi.fn(() => Promise.resolve());

  createBuffer(channels: number, frames: number, sampleRate: number): AudioBuffer {
    const values = Array.from({ length: channels }, () => new Float32Array(frames));
    return {
      duration: frames / sampleRate,
      getChannelData: (channel: number) => values[channel],
    } as AudioBuffer;
  }

  createBufferSource(): AudioBufferSourceNode {
    const source = new FakeSource();
    this.sources.push(source);
    return source as unknown as AudioBufferSourceNode;
  }
}

describe("PCM stream player", () => {
  beforeEach(() => {
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  it("accepts ordered PCM16 chunks and completes one playback segment", () => {
    const context = new FakeContext();
    const started = vi.fn();
    const stopped = vi.fn();
    const failed = vi.fn();
    const player = new PcmStreamPlayer(
      () => context as unknown as AudioContext,
      {
        isGenerationActive: () => true,
        onStreamAccepted: vi.fn(),
        onPlaybackStart: started,
        onPlaybackProgress: vi.fn(),
        onPlaybackStop: stopped,
        onPlaybackError: failed,
      },
    );

    player.start({
      phase: "started",
      generationId: "generation-1",
      streamId: "stream-1",
      segmentId: "segment-1",
      segmentIndex: 0,
      text: "你好。",
      sampleRate: 24_000,
      channels: 1,
      nativeStreaming: true,
    });
    player.push("segment-1", 0, new Uint8Array([0, 0, 255, 127]));
    player.push("segment-1", 1, new Uint8Array([0, 128, 0, 0]));
    player.complete("segment-1", 10);
    context.currentTime = 1;
    for (const source of context.sources) source.finish();

    expect(started).toHaveBeenCalledTimes(1);
    expect(stopped).toHaveBeenCalledWith(
      expect.objectContaining({ segmentId: "segment-1", durationMs: 10 }),
      expect.objectContaining({ playedPtsMs: 10 }),
      "ended",
    );
    expect(failed).not.toHaveBeenCalled();
  });

  it("rejects an out-of-order fragment instead of playing stale audio", () => {
    const context = new FakeContext();
    const failed = vi.fn();
    const player = new PcmStreamPlayer(
      () => context as unknown as AudioContext,
      {
        isGenerationActive: () => true,
        onStreamAccepted: vi.fn(),
        onPlaybackStart: vi.fn(),
        onPlaybackProgress: vi.fn(),
        onPlaybackStop: vi.fn(),
        onPlaybackError: failed,
      },
    );
    player.start({
      phase: "started",
      generationId: "generation-1",
      streamId: "stream-1",
      segmentId: "segment-1",
      segmentIndex: 0,
      text: "测试",
      sampleRate: 24_000,
      channels: 1,
      nativeStreaming: true,
    });
    player.push("segment-1", 2, new Uint8Array([0, 0]));
    expect(failed).toHaveBeenCalledOnce();
    expect(context.sources).toHaveLength(0);
  });

  it("decodes little PCM payloads without changing bytes", () => {
    expect(Array.from(decodePcmBase64("AAH+/w=="))).toEqual([0, 1, 254, 255]);
  });
});
