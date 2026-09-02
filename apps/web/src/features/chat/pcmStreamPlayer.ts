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
  accepted: boolean;
  nativeStreaming: boolean;
  pendingChunks: Array<{ sequence: number; pcm16: Uint8Array }>;
  pendingBytes: number;
  resumePending: boolean;
}

interface StreamCallbacks {
  isGenerationActive(generationId: string): boolean;
  onStreamAccepted(item: AudioPlaybackItem, nativeStreaming: boolean): void;
  onStreamRejected(item: AudioPlaybackItem): void;
  onStreamActivity(item: AudioPlaybackItem, bufferedMs: number): void;
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

  get hasAcceptedPlayback(): boolean {
    return (
      Boolean(this.active?.accepted) ||
      Array.from(this.draining).some((stream) => stream.accepted)
    );
  }

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
    const item: AudioPlaybackItem = {
      generationId: message.generationId,
      streamId: message.streamId,
      segmentId: message.segmentId,
      segmentIndex: message.segmentIndex,
      text: message.text,
      durationMs: 0,
      url: "",
    };
    const previous = this.active;
    if (previous) {
      if (previous.item.segmentId === item.segmentId) return;
      if (!previous.completed) {
        if (previous.item.generationId === item.generationId) {
          // A reconnect can lose the preceding completion envelope. Preserve
          // audio already accepted for that generation and route the later
          // segment to its complete-WAV fallback instead of cutting speech.
          this.callbacks.onStreamRejected(item);
          return;
        }
        this.stop("interrupted");
      } else {
        this.draining.add(previous);
        this.active = null;
      }
    }
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
      accepted: false,
      nativeStreaming: message.nativeStreaming,
      pendingChunks: [],
      pendingBytes: 0,
      resumePending: false,
    };
    // A start envelope only reserves the segment.  Do not claim the stream
    // until Web Audio has actually accepted its first PCM buffer; otherwise a
    // failed AudioContext would suppress the complete-WAV fallback.
  }

  push(segmentId: string, sequence: number, pcm16: Uint8Array): void {
    const active = this.active;
    if (!active || active.item.segmentId !== segmentId) return;
    const context = this.ensureContext();
    if (!context) {
      this.reject(active);
      return;
    }
    if (context.state !== "running") {
      this.queueUntilContextRuns(active, context, sequence, pcm16);
      return;
    }
    this.pushRunning(active, context, sequence, pcm16);
  }

  private pushRunning(
    active: ActiveStream,
    context: AudioContext,
    sequence: number,
    pcm16: Uint8Array,
  ): void {
    if (this.active !== active || context.state !== "running") return;
    if (
      sequence !== active.nextSequence ||
      pcm16.byteLength % (2 * active.channels)
    ) {
      this.fail("实时语音分片顺序异常，完成后将使用 WAV 回退播放。");
      return;
    }
    const frameCount = pcm16.byteLength / (2 * active.channels);
    if (!frameCount) return;
    try {
      const buffer = context.createBuffer(
        active.channels,
        frameCount,
        active.sampleRate,
      );
      const view = new DataView(
        pcm16.buffer,
        pcm16.byteOffset,
        pcm16.byteLength,
      );
      for (let channel = 0; channel < active.channels; channel += 1) {
        const samples = buffer.getChannelData(channel);
        for (let frame = 0; frame < frameCount; frame += 1) {
          samples[frame] =
            view.getInt16((frame * active.channels + channel) * 2, true) /
            32768;
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
      source.onended = () => {
        active.sources.delete(source);
        this.finishIfDrained(active);
      };
      active.sources.add(source);
      try {
        source.start(startsAt);
      } catch (error) {
        active.sources.delete(source);
        source.onended = null;
        throw error;
      }

      active.nextSequence += 1;
      active.scheduledUntil = startsAt + buffer.duration;
      this.scheduleCursor = active.scheduledUntil;
      if (!active.accepted) {
        active.accepted = true;
        active.startTime = startsAt;
        this.callbacks.onStreamAccepted(active.item, active.nativeStreaming);
        this.callbacks.onPlaybackStart(active.item, this.position(active));
        this.startProgressLoop();
      }
      this.callbacks.onStreamActivity(
        active.item,
        Math.max(
          0,
          Math.round((active.scheduledUntil - context.currentTime) * 1_000),
        ),
      );
    } catch {
      this.fail("实时语音无法开始播放，完成后将使用 WAV 回退播放。");
    }
  }

  complete(segmentId: string, durationMs: number): void {
    const active = this.active;
    if (!active || active.item.segmentId !== segmentId) return;
    active.item.durationMs = Math.max(0, durationMs);
    active.completed = true;
    if (!active.accepted && !active.pendingChunks.length) {
      this.reject(active);
      return;
    }
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

  private queueUntilContextRuns(
    active: ActiveStream,
    context: AudioContext,
    sequence: number,
    pcm16: Uint8Array,
  ): void {
    const expectedSequence = active.nextSequence + active.pendingChunks.length;
    if (
      sequence !== expectedSequence ||
      pcm16.byteLength % (2 * active.channels)
    ) {
      this.fail("实时语音分片顺序异常，完成后将使用 WAV 回退播放。");
      return;
    }
    if (!pcm16.byteLength) return;
    if (
      active.pendingChunks.length >= 32 ||
      active.pendingBytes + pcm16.byteLength > 2 * 1024 * 1024
    ) {
      this.fail("实时语音在等待浏览器音频权限时缓冲过多，将使用 WAV 回退。");
      return;
    }
    active.pendingChunks.push({ sequence, pcm16: pcm16.slice() });
    active.pendingBytes += pcm16.byteLength;
    if (active.resumePending) return;
    active.resumePending = true;
    void context
      .resume()
      .then(() => {
        active.resumePending = false;
        if (this.active !== active) return;
        if (context.state !== "running") {
          this.callbacks.onPlaybackError(
            "浏览器音频上下文仍未恢复，将使用 WAV 回退。",
          );
          this.reject(active);
          return;
        }
        const pending = active.pendingChunks.splice(0);
        active.pendingBytes = 0;
        for (const chunk of pending) {
          if (this.active !== active) break;
          this.pushRunning(active, context, chunk.sequence, chunk.pcm16);
        }
      })
      .catch(() => {
        active.resumePending = false;
        if (this.active === active) {
          this.callbacks.onPlaybackError(
            "浏览器未允许实时音频播放，将使用 WAV 回退。",
          );
          this.reject(active);
        }
      });
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
      !active.accepted ||
      active.pendingChunks.length > 0 ||
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
    const active = this.active;
    this.callbacks.onPlaybackError(message);
    if (!active) return;
    if (!active.accepted) this.callbacks.onStreamRejected(active.item);
    this.stopStream(active, "error");
  }

  private reject(active: ActiveStream): void {
    if (this.active !== active) return;
    this.callbacks.onStreamRejected(active.item);
    this.stopStream(active, "error");
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
    active.pendingChunks.length = 0;
    active.pendingBytes = 0;
    if (active.startTime !== null)
      this.callbacks.onPlaybackStop(active.item, this.position(active), reason);
    this.recalculateScheduleCursor();
    if (!this.active && !this.draining.size) {
      this.stopProgressLoop();
    }
  }

  private recalculateScheduleCursor(): void {
    const contextTime = this.context?.currentTime ?? 0;
    const remaining = [...this.draining, ...(this.active ? [this.active] : [])];
    this.scheduleCursor = remaining.reduce(
      (cursor, stream) => Math.max(cursor, stream.scheduledUntil),
      contextTime,
    );
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
