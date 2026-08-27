import type { AudioPlaybackItem, PlaybackPosition } from "./audioPlayer";

export interface PcmStreamStarted {
  phase: "started";
  generationId: string;
  streamId: string;
  segmentId: string;
  segmentIndex: number;
  text: string;
  sampleRate: number;
  channels: number;
  nativeStreaming: boolean;
}

interface ActiveStream {
  item: AudioPlaybackItem;
  sampleRate: number;
  channels: number;
  nextSequence: number;
  startTime: number | null;
  scheduledUntil: number;
  completed: boolean;
  sources: Set<AudioBufferSourceNode>;
  lastReportedMs: number;
}

interface StreamCallbacks {
  isGenerationActive(generationId: string): boolean;
  onStreamAccepted(item: AudioPlaybackItem, nativeStreaming: boolean): void;
  onPlaybackStart(item: AudioPlaybackItem, position: PlaybackPosition): void;
  onPlaybackProgress(item: AudioPlaybackItem, position: PlaybackPosition): void;
  onPlaybackStop(
    item: AudioPlaybackItem,
    position: PlaybackPosition,
    reason: "ended" | "interrupted" | "error",
  ): void;
  onPlaybackError(message: string): void;
}

type AudioContextFactory = () => AudioContext;

export class PcmStreamPlayer {
  private context: AudioContext | null = null;
  private active: ActiveStream | null = null;
  private readonly draining = new Set<ActiveStream>();
  private scheduleCursor = 0;
  private progressFrame: number | null = null;
  private disposed = false;

  constructor(
    private readonly createContext: AudioContextFactory,
    private readonly callbacks: StreamCallbacks,
  ) {}

  prime(): void {
    const context = this.ensureContext();
    if (context?.state === "suspended")
      void context.resume().catch(() => undefined);
  }

  start(message: PcmStreamStarted): void {
    if (
      this.disposed ||
      !this.callbacks.isGenerationActive(message.generationId)
    )
      return;
    const previous = this.active;
    if (previous) {
      if (!previous.completed) this.stop("interrupted");
      else {
        this.draining.add(previous);
        this.active = null;
      }
    }
    const item: AudioPlaybackItem = {
      generationId: message.generationId,
      streamId: message.streamId,
      segmentId: message.segmentId,
      segmentIndex: message.segmentIndex,
      text: message.text,
      durationMs: 0,
      url: "",
    };
    this.active = {
      item,
      sampleRate: message.sampleRate,
      channels: message.channels,
      nextSequence: 0,
      startTime: null,
      scheduledUntil: 0,
      completed: false,
      sources: new Set(),
      lastReportedMs: 0,
    };
    this.callbacks.onStreamAccepted(item, message.nativeStreaming);
  }

  push(segmentId: string, sequence: number, pcm16: Uint8Array): void {
    const active = this.active;
    const context = this.ensureContext();
    if (!active || active.item.segmentId !== segmentId || !context) return;
    if (
      sequence !== active.nextSequence ||
      pcm16.byteLength % (2 * active.channels)
    ) {
      this.fail("实时语音分片顺序异常，完成后将使用 WAV 回退播放。");
      return;
    }
    active.nextSequence += 1;
    const frameCount = pcm16.byteLength / (2 * active.channels);
    if (!frameCount) return;
    const buffer = context.createBuffer(
      active.channels,
      frameCount,
      active.sampleRate,
    );
    const view = new DataView(pcm16.buffer, pcm16.byteOffset, pcm16.byteLength);
    for (let channel = 0; channel < active.channels; channel += 1) {
      const samples = buffer.getChannelData(channel);
      for (let frame = 0; frame < frameCount; frame += 1) {
        samples[frame] =
          view.getInt16((frame * active.channels + channel) * 2, true) / 32768;
      }
    }
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const startsAt = Math.max(
      context.currentTime + 0.035,
      active.scheduledUntil,
      this.scheduleCursor,
    );
    if (active.startTime === null) {
      active.startTime = startsAt;
      this.callbacks.onPlaybackStart(active.item, this.position(active));
      this.startProgressLoop();
    }
    active.scheduledUntil = startsAt + buffer.duration;
    this.scheduleCursor = active.scheduledUntil;
    active.sources.add(source);
    source.onended = () => {
      active.sources.delete(source);
      this.finishIfDrained(active);
    };
    source.start(startsAt);
  }

  complete(segmentId: string, durationMs: number): void {
    const active = this.active;
    if (!active || active.item.segmentId !== segmentId) return;
    active.item.durationMs = Math.max(0, durationMs);
    active.completed = true;
    this.finishIfDrained(active);
  }

  cancel(segmentId?: string): void {
    if (segmentId) {
      const stream = this.findStream(segmentId);
      if (stream) this.stopStream(stream, "interrupted");
      return;
    }
    this.stop("interrupted");
  }

  dispose(): void {
    if (this.disposed) return;
    this.stop("interrupted");
    this.disposed = true;
    const context = this.context;
    this.context = null;
    if (context) void context.close().catch(() => undefined);
  }

  private ensureContext(): AudioContext | null {
    if (this.disposed) return null;
    if (!this.context) {
      try {
        this.context = this.createContext();
      } catch {
        this.callbacks.onPlaybackError(
          "当前浏览器不支持实时 PCM 播放，将使用 WAV 回退。",
        );
        return null;
      }
    }
    return this.context;
  }

  private startProgressLoop(): void {
    if (this.progressFrame !== null) return;
    const update = () => {
      this.progressFrame = null;
      const active = this.active;
      const streams = [...this.draining, ...(active ? [active] : [])];
      if (!streams.length) return;
      for (const stream of streams) {
        const position = this.position(stream);
        if (position.playedPtsMs - stream.lastReportedMs >= 250) {
          stream.lastReportedMs = position.playedPtsMs;
          this.callbacks.onPlaybackProgress(stream.item, position);
        }
        this.finishIfDrained(stream);
      }
      if (this.active || this.draining.size)
        this.progressFrame = requestAnimationFrame(update);
    };
    this.progressFrame = requestAnimationFrame(update);
  }

  private finishIfDrained(active: ActiveStream): void {
    const context = this.context;
    if (
      (this.active !== active && !this.draining.has(active)) ||
      !active.completed ||
      active.sources.size > 0 ||
      !context ||
      context.currentTime + 0.01 < active.scheduledUntil
    )
      return;
    const position = {
      ...this.position(active),
      playedPtsMs: active.item.durationMs,
    };
    if (this.active === active) this.active = null;
    this.draining.delete(active);
    if (!this.active && !this.draining.size) this.stopProgressLoop();
    this.callbacks.onPlaybackStop(active.item, position, "ended");
  }

  private position(active: ActiveStream): PlaybackPosition {
    const context = this.context;
    const start = active.startTime;
    const playedPtsMs =
      context && start !== null
        ? Math.max(0, Math.round((context.currentTime - start) * 1000))
        : 0;
    return {
      playedPtsMs: active.item.durationMs
        ? Math.min(active.item.durationMs, playedPtsMs)
        : playedPtsMs,
      bufferedMs: context
        ? Math.max(
            0,
            Math.round((active.scheduledUntil - context.currentTime) * 1000),
          )
        : 0,
      clientClockMs: Math.round(performance.now()),
    };
  }

  private fail(message: string): void {
    this.callbacks.onPlaybackError(message);
    this.stop("error");
  }

  private stop(reason: "interrupted" | "error"): void {
    const streams = [...this.draining, ...(this.active ? [this.active] : [])];
    this.active = null;
    this.draining.clear();
    this.scheduleCursor = this.context?.currentTime ?? 0;
    this.stopProgressLoop();
    for (const active of streams) this.stopStream(active, reason, false);
  }

  private stopStream(
    active: ActiveStream,
    reason: "interrupted" | "error",
    remove = true,
  ): void {
    if (remove) {
      if (this.active === active) this.active = null;
      this.draining.delete(active);
    }
    for (const source of active.sources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // A source that has already ended is harmless during cancellation.
      }
    }
    active.sources.clear();
    if (active.startTime !== null)
      this.callbacks.onPlaybackStop(active.item, this.position(active), reason);
  }

  private findStream(segmentId: string): ActiveStream | null {
    if (this.active?.item.segmentId === segmentId) return this.active;
    return (
      Array.from(this.draining).find(
        (stream) => stream.item.segmentId === segmentId,
      ) ?? null
    );
  }

  private stopProgressLoop(): void {
    if (this.progressFrame === null) return;
    cancelAnimationFrame(this.progressFrame);
    this.progressFrame = null;
  }
}

export function decodePcmBase64(value: string): Uint8Array {
  const raw = atob(value);
  const result = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1)
    result[index] = raw.charCodeAt(index);
  return result;
}
