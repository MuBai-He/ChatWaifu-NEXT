import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PlayableAudio } from "./audioPlayer";
import { PlaybackCoordinator } from "./playbackCoordinator";

class FakeAudio implements PlayableAudio {
  src = "";
  preload = "";
  onplay: ((event: Event) => void) | null = null;
  onended: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  ontimeupdate: ((event: Event) => void) | null = null;
  currentTime = 0;
  duration = 1;
  buffered = { length: 0, end: () => 0 };
  readonly play = vi.fn(() => {
    this.onplay?.(new Event("play"));
    return Promise.resolve();
  });
  readonly pause = vi.fn();
  readonly removeAttribute = vi.fn();
  readonly load = vi.fn();
}

class FakeSource {
  buffer: AudioBuffer | null = null;
  onended: (() => void) | null = null;
  connect(): void {}
  start(): void {}
  stop(): void {}
  finish(): void {
    this.onended?.();
  }
}

class FakeContext {
  currentTime = 0;
  state: AudioContextState = "running";
  destination = {} as AudioDestinationNode;
  readonly sources: FakeSource[] = [];
  createBuffer(_channels: number, frames: number, rate: number): AudioBuffer {
    return {
      duration: frames / rate,
      getChannelData: () => new Float32Array(frames),
    } as unknown as AudioBuffer;
  }
  createBufferSource(): AudioBufferSourceNode {
    const source = new FakeSource();
    this.sources.push(source);
    return source as unknown as AudioBufferSourceNode;
  }
  resume(): Promise<void> {
    return Promise.resolve();
  }
  close(): Promise<void> {
    return Promise.resolve();
  }
}

function coordinator() {
  const sendReceipt = vi.fn().mockResolvedValue(undefined);
  const instance = new PlaybackCoordinator({
    enabled: true,
    isGenerationActive: () => true,
    sendReceipt,
    stopRemotePlayback: vi.fn(),
    onSubtitle: vi.fn(),
    onError: vi.fn(),
    onLipSyncStart: vi.fn(),
    onLipSyncStop: vi.fn(),
  });
  return { instance, sendReceipt };
}

describe("PlaybackCoordinator", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn(() => 1),
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps WebRTC as the sole owner while the remote media path is connected", () => {
    const { instance } = coordinator();
    instance.setRemoteConnected(true);
    instance.registerQueuedAudio({
      generationId: "00000000-0000-4000-8000-000000000901",
      streamId: "00000000-0000-4000-8000-000000000902",
      segmentId: "00000000-0000-4000-8000-000000000903",
      segmentIndex: 0,
      text: "不会由本地播放器重复播放",
      durationMs: 1_000,
      url: "/audio/test.wav",
    });
    instance.consumePcm({
      type: "chatwaifu.tts_stream",
      schema_version: "1.0",
      phase: "started",
      session_id: "00000000-0000-4000-8000-000000000904",
      turn_id: "00000000-0000-4000-8000-000000000905",
      generation_id: "00000000-0000-4000-8000-000000000901",
      stream_id: "00000000-0000-4000-8000-000000000902",
      segment_id: "00000000-0000-4000-8000-000000000903",
      segment_index: 0,
      text: "不会由本地播放器重复播放",
      sequence: 0,
      sample_rate: 24_000,
      channels: 1,
      native_streaming: true,
      pcm16_base64: "",
      duration_ms: 0,
      provider_id: "fake",
      model: "fake",
      reason: null,
    });
    expect(instance.currentOwner).toBe("webrtc");
    instance.dispose();
  });

  it("forwards the selected transport receipt exactly once", () => {
    const { instance, sendReceipt } = coordinator();
    instance.setRemoteConnected(true);
    instance.reportRemoteReceipt({
      phase: "started",
      generationId: "00000000-0000-4000-8000-000000000911",
      streamId: "00000000-0000-4000-8000-000000000912",
      segmentId: "00000000-0000-4000-8000-000000000913",
      playedPtsMs: 0,
      bufferedMs: 100,
      clientClockMs: 5,
      transport: "webrtc",
    });
    expect(sendReceipt).toHaveBeenCalledOnce();
    expect(instance.currentOwner).toBe("webrtc");
    instance.dispose();
  });

  it("ignores a late WebRTC receipt from an inactive generation", () => {
    const sendReceipt = vi.fn().mockResolvedValue(undefined);
    const instance = new PlaybackCoordinator({
      enabled: true,
      isGenerationActive: (generationId) => generationId === "generation-new",
      sendReceipt,
      stopRemotePlayback: vi.fn(),
      onSubtitle: vi.fn(),
      onError: vi.fn(),
      onLipSyncStart: vi.fn(),
      onLipSyncStop: vi.fn(),
    });
    instance.setRemoteConnected(true);

    instance.reportRemoteReceipt({
      phase: "started",
      generationId: "generation-old",
      streamId: "stream-old",
      segmentId: "segment-old",
      playedPtsMs: 0,
      bufferedMs: 100,
      clientClockMs: 5,
      transport: "webrtc",
    });

    expect(sendReceipt).not.toHaveBeenCalled();
    instance.dispose();
  });

  it("does not start the WAV fallback when queued arrives before first PCM", () => {
    const audio = new FakeAudio();
    const instance = new PlaybackCoordinator({
      enabled: true,
      isGenerationActive: () => true,
      sendReceipt: vi.fn().mockResolvedValue(undefined),
      stopRemotePlayback: vi.fn(),
      onSubtitle: vi.fn(),
      onError: vi.fn(),
      onLipSyncStart: vi.fn(),
      onLipSyncStop: vi.fn(),
      createAudio: () => audio,
      createAudioContext: () => new FakeContext() as unknown as AudioContext,
      streamFallbackGraceMs: 50,
    });
    instance.startGeneration("generation-1");
    instance.registerQueuedAudio(
      {
        generationId: "generation-1",
        streamId: "stream-1",
        segmentId: "segment-1",
        segmentIndex: 0,
        text: "实时语音",
        durationMs: 1_000,
        url: "/audio/fallback.wav",
      },
      true,
    );
    expect(audio.play).not.toHaveBeenCalled();
    instance.consumePcm(streamMessage("started"));
    instance.consumePcm({
      ...streamMessage("chunk"),
      pcm16_base64: "AAA=",
    });
    vi.advanceTimersByTime(100);

    expect(instance.currentOwner).toBe("pcm_stream");
    expect(audio.play).not.toHaveBeenCalled();
    instance.dispose();
  });

  it("plays the bounded WAV fallback when promised PCM never arrives", async () => {
    const audio = new FakeAudio();
    const instance = new PlaybackCoordinator({
      enabled: true,
      isGenerationActive: () => true,
      sendReceipt: vi.fn().mockResolvedValue(undefined),
      stopRemotePlayback: vi.fn(),
      onSubtitle: vi.fn(),
      onError: vi.fn(),
      onLipSyncStart: vi.fn(),
      onLipSyncStop: vi.fn(),
      createAudio: () => audio,
      streamFallbackGraceMs: 50,
    });
    instance.startGeneration("generation-1");
    instance.registerQueuedAudio(
      {
        generationId: "generation-1",
        streamId: "stream-1",
        segmentId: "segment-1",
        segmentIndex: 0,
        text: "回退语音",
        durationMs: 1_000,
        url: "/audio/fallback.wav",
      },
      true,
    );
    await vi.advanceTimersByTimeAsync(50);

    expect(audio.play).toHaveBeenCalledOnce();
    expect(instance.currentOwner).toBe("audio_element");
    instance.dispose();
  });

  it("cancels a stalled accepted PCM stream before starting its WAV fallback", async () => {
    const audio = new FakeAudio();
    const context = new FakeContext();
    const onError = vi.fn();
    const instance = new PlaybackCoordinator({
      enabled: true,
      isGenerationActive: () => true,
      sendReceipt: vi.fn().mockResolvedValue(undefined),
      stopRemotePlayback: vi.fn(),
      onSubtitle: vi.fn(),
      onError,
      onLipSyncStart: vi.fn(),
      onLipSyncStop: vi.fn(),
      createAudio: () => audio,
      createAudioContext: () => context as unknown as AudioContext,
      streamFallbackGraceMs: 500,
      streamStallMs: 50,
    });
    instance.startGeneration("generation-1");
    instance.registerQueuedAudio(
      {
        generationId: "generation-1",
        streamId: "stream-1",
        segmentId: "segment-1",
        segmentIndex: 0,
        text: "实时语音",
        durationMs: 1_000,
        url: "/audio/fallback.wav",
      },
      true,
    );
    instance.consumePcm(streamMessage("started"));
    instance.consumePcm({
      ...streamMessage("chunk"),
      pcm16_base64: "AAA=",
    });
    expect(instance.currentOwner).toBe("pcm_stream");

    await vi.advanceTimersByTimeAsync(100);

    expect(context.sources[0]).toBeDefined();
    expect(audio.play).toHaveBeenCalledOnce();
    expect(instance.currentOwner).toBe("audio_element");
    expect(onError).toHaveBeenCalledWith(
      "实时语音流已中断，正在切换到完整音频。",
    );
    instance.dispose();
  });

  it("keeps lipsync and PCM ownership when an older draining segment ends", () => {
    const context = new FakeContext();
    const onLipSyncStop = vi.fn();
    const instance = new PlaybackCoordinator({
      enabled: true,
      isGenerationActive: () => true,
      sendReceipt: vi.fn().mockResolvedValue(undefined),
      stopRemotePlayback: vi.fn(),
      onSubtitle: vi.fn(),
      onError: vi.fn(),
      onLipSyncStart: vi.fn(),
      onLipSyncStop,
      createAudioContext: () => context as unknown as AudioContext,
      streamStallMs: 5_000,
    });
    instance.startGeneration("generation-1");
    instance.consumePcm(streamMessage("started"));
    instance.consumePcm({
      ...streamMessage("chunk"),
      pcm16_base64: "AAA=",
    });
    instance.consumePcm({
      ...streamMessage("started"),
      phase: "completed",
      duration_ms: 20,
    });
    instance.consumePcm({
      ...streamMessage("started"),
      segment_id: "segment-2",
      segment_index: 1,
      text: "第二句",
    });
    instance.consumePcm({
      ...streamMessage("chunk"),
      segment_id: "segment-2",
      segment_index: 1,
      text: "第二句",
      pcm16_base64: "AAA=",
    });
    context.currentTime = 1;

    context.sources[0].finish();

    expect(instance.currentOwner).toBe("pcm_stream");
    expect(onLipSyncStop).not.toHaveBeenCalled();
    instance.dispose();
  });
});

function streamMessage(phase: "started" | "chunk") {
  return {
    type: "chatwaifu.tts_stream" as const,
    schema_version: "1.0" as const,
    phase,
    session_id: "00000000-0000-4000-8000-000000000904",
    turn_id: "00000000-0000-4000-8000-000000000905",
    generation_id: "generation-1",
    stream_id: "stream-1",
    segment_id: "segment-1",
    segment_index: 0,
    text: "实时语音",
    sequence: 0,
    sample_rate: 24_000,
    channels: 1,
    native_streaming: true,
    pcm16_base64: "",
    duration_ms: 0,
    provider_id: "fake",
    model: "fake",
    reason: null,
  };
}
