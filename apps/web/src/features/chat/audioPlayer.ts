export const AUTOPLAY_BLOCKED_MESSAGE =
  "声音播放权限被浏览器阻止，请点击页面后再次发送，或在地址栏允许声音。";

export const AUDIO_PLAYBACK_FAILED_MESSAGE = "语音播放失败，文字回复仍然可用。";

const SILENT_WAV_URL =
  "data:audio/wav;base64,UklGRiUAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQEAAACA";

export interface AudioPlaybackItem {
  generationId: string;
  streamId: string;
  segmentId: string;
  durationMs: number;
  url: string;
}

export interface PlaybackPosition {
  playedPtsMs: number;
  bufferedMs: number;
  clientClockMs: number;
}

export type PlaybackStopReason =
  "ended" | "interrupted" | "error" | "queue_cleared";

export interface PlayableAudio {
  src: string;
  preload: string;
  onplay: ((event: Event) => void) | null;
  onended: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  ontimeupdate: ((event: Event) => void) | null;
  currentTime: number;
  duration: number;
  buffered: Pick<TimeRanges, "length" | "end">;
  play(): Promise<void>;
  pause(): void;
  removeAttribute(name: string): void;
  load(): void;
}

interface AudioPlayerCallbacks {
  isGenerationActive(generationId: string): boolean;
  onPlaybackStart(item: AudioPlaybackItem, position: PlaybackPosition): void;
  onPlaybackProgress(item: AudioPlaybackItem, position: PlaybackPosition): void;
  onPlaybackStop(
    item: AudioPlaybackItem,
    position: PlaybackPosition,
    reason: PlaybackStopReason,
  ): void;
  onQueueCleared(item: AudioPlaybackItem): void;
  onPlaybackError(message: string): void;
}

interface ActivePlayback {
  audio: PlayableAudio;
  epoch: number;
  reusable: boolean;
  item: AudioPlaybackItem;
  lastReportedMs: number;
}

type AudioFactory = (url: string) => PlayableAudio;

export class GenerationAudioPlayer {
  private readonly queue: AudioPlaybackItem[] = [];
  private active: ActivePlayback | null = null;
  private epoch = 0;
  private disposed = false;
  private primingAudio: PlayableAudio | null = null;
  private standbyAudio: PlayableAudio | null = null;
  private unlocked = false;

  constructor(
    private readonly createAudio: AudioFactory,
    private readonly callbacks: AudioPlayerCallbacks,
    private readonly maxQueueSize = 32,
  ) {}

  enqueue(item: AudioPlaybackItem): void {
    if (this.disposed || !this.callbacks.isGenerationActive(item.generationId))
      return;
    if (this.queue.length >= this.maxQueueSize) {
      this.stop();
      this.callbacks.onPlaybackError(
        "语音播放队列过长，已停止本轮语音以保持同步。",
      );
      return;
    }
    this.queue.push(item);
    this.playNext();
  }

  stop(): void {
    for (const item of this.queue.splice(0))
      this.callbacks.onQueueCleared(item);
    this.epoch += 1;
    const active = this.active;
    this.active = null;
    if (!active) return;
    this.callbacks.onPlaybackStop(
      active.item,
      playbackPosition(active.audio, active.item),
      "interrupted",
    );
    this.recycle(active.audio, active.reusable);
  }

  dispose(): void {
    if (this.disposed) return;
    this.stop();
    this.disposed = true;
    if (this.standbyAudio) cleanupAudio(this.standbyAudio);
    this.standbyAudio = null;
    if (this.primingAudio) cleanupAudio(this.primingAudio);
    this.primingAudio = null;
  }

  /**
   * Call from a user gesture. A successful silent play grants the page an
   * opportunity to use audible media after the asynchronous Runtime reply.
   */
  prime(): void {
    if (
      this.disposed ||
      this.unlocked ||
      this.primingAudio ||
      this.standbyAudio
    )
      return;
    const probe = this.createAudio(SILENT_WAV_URL);
    this.primingAudio = probe;
    probe.preload = "auto";
    let playResult: Promise<void>;
    try {
      playResult = probe.play();
    } catch {
      this.finishPriming(probe);
      return;
    }
    void playResult
      .then(() => {
        if (!this.disposed) this.unlocked = true;
      })
      .catch(() => undefined)
      .finally(() => {
        this.finishPriming(probe);
      });
  }

  private playNext(): void {
    if (this.disposed || this.active || this.primingAudio) return;

    let next = this.queue.shift();
    while (next && !this.callbacks.isGenerationActive(next.generationId)) {
      this.callbacks.onQueueCleared(next);
      next = this.queue.shift();
    }
    if (!next) return;

    const standby = this.standbyAudio;
    const reusable = standby !== null;
    const audio = standby ?? this.createAudio(next.url);
    if (reusable) {
      this.standbyAudio = null;
      audio.src = next.url;
      audio.load();
    }
    audio.preload = "auto";
    const epoch = ++this.epoch;
    this.active = { audio, epoch, reusable, item: next, lastReportedMs: 0 };

    audio.onplay = () => {
      if (!this.isCurrent(audio, epoch)) return;
      this.unlocked = true;
      const position = playbackPosition(audio, next);
      this.callbacks.onPlaybackStart(next, position);
      const active = this.active;
      if (active) active.lastReportedMs = position.playedPtsMs;
    };
    audio.ontimeupdate = () => {
      const active = this.active;
      if (!active || !this.isCurrent(audio, epoch)) return;
      const position = playbackPosition(audio, next);
      if (
        position.playedPtsMs < next.durationMs &&
        position.playedPtsMs - active.lastReportedMs < 250
      )
        return;
      active.lastReportedMs = position.playedPtsMs;
      this.callbacks.onPlaybackProgress(next, position);
    };
    audio.onended = () => {
      const position = finalPlaybackPosition(audio, next);
      if (!this.release(audio, epoch)) return;
      this.callbacks.onPlaybackStop(next, position, "ended");
      this.playNext();
    };
    audio.onerror = () => {
      const position = playbackPosition(audio, next);
      if (!this.release(audio, epoch)) return;
      this.callbacks.onPlaybackStop(next, position, "error");
      this.callbacks.onPlaybackError(AUDIO_PLAYBACK_FAILED_MESSAGE);
      this.playNext();
    };

    let playResult: Promise<void>;
    try {
      playResult = audio.play();
    } catch (error: unknown) {
      this.handlePlayRejection(audio, epoch, error);
      return;
    }
    void playResult.catch((error: unknown) => {
      this.handlePlayRejection(audio, epoch, error);
    });
  }

  private handlePlayRejection(
    audio: PlayableAudio,
    epoch: number,
    error: unknown,
  ): void {
    const item = this.itemFor(audio, epoch);
    const position = playbackPosition(audio, item);
    if (!this.release(audio, epoch)) return;
    if (isAbortRejection(error)) {
      if (item) this.callbacks.onPlaybackStop(item, position, "interrupted");
      this.playNext();
      return;
    }
    for (const queued of this.queue.splice(0))
      this.callbacks.onQueueCleared(queued);
    if (item) this.callbacks.onPlaybackStop(item, position, "error");
    this.callbacks.onPlaybackError(
      isAutoplayRejection(error)
        ? AUTOPLAY_BLOCKED_MESSAGE
        : AUDIO_PLAYBACK_FAILED_MESSAGE,
    );
  }

  private isCurrent(audio: PlayableAudio, epoch: number): boolean {
    return this.active?.audio === audio && this.active.epoch === epoch;
  }

  private release(audio: PlayableAudio, epoch: number): boolean {
    if (!this.isCurrent(audio, epoch)) return false;
    const reusable = this.active?.reusable ?? false;
    this.active = null;
    this.recycle(audio, reusable);
    return true;
  }

  private itemFor(
    audio: PlayableAudio,
    epoch: number,
  ): AudioPlaybackItem | null {
    return this.isCurrent(audio, epoch) ? (this.active?.item ?? null) : null;
  }

  private finishPriming(audio: PlayableAudio): void {
    if (this.primingAudio !== audio) return;
    this.primingAudio = null;
    cleanupAudio(audio);
    if (!this.disposed && !this.standbyAudio) this.standbyAudio = audio;
    this.playNext();
  }

  private recycle(audio: PlayableAudio, reusable: boolean): void {
    cleanupAudio(audio);
    if (reusable && !this.disposed && !this.standbyAudio)
      this.standbyAudio = audio;
  }
}

function cleanupAudio(audio: PlayableAudio): void {
  audio.onplay = null;
  audio.onended = null;
  audio.onerror = null;
  audio.ontimeupdate = null;
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
}

function playbackPosition(
  audio: PlayableAudio,
  item?: AudioPlaybackItem | null,
): PlaybackPosition {
  const durationMs = item?.durationMs ?? Number.POSITIVE_INFINITY;
  const playedPtsMs = Math.max(
    0,
    Math.min(durationMs, Math.round(finite(audio.currentTime) * 1000)),
  );
  let bufferedMs = 0;
  if (audio.buffered.length > 0) {
    try {
      bufferedMs = Math.max(
        0,
        Math.round(
          (audio.buffered.end(audio.buffered.length - 1) - audio.currentTime) *
            1000,
        ),
      );
    } catch {
      bufferedMs = 0;
    }
  }
  return {
    playedPtsMs,
    bufferedMs,
    clientClockMs: Math.max(0, Math.round(performance.now())),
  };
}

function finalPlaybackPosition(
  audio: PlayableAudio,
  item: AudioPlaybackItem,
): PlaybackPosition {
  return { ...playbackPosition(audio, item), playedPtsMs: item.durationMs };
}

function finite(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

function isAutoplayRejection(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "NotAllowedError"
  );
}

function isAbortRejection(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}
