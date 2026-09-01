import type { PlaybackAckReceipt } from "./runtimeClient";
import type { TtsStreamMessage } from "./types";
import {
  GenerationAudioPlayer,
  type AudioPlaybackItem,
  type PlaybackPosition,
  type PlaybackStopReason,
  type PlayableAudio,
} from "./audioPlayer";
import { PlaybackAckReporter } from "./playbackAckReporter";
import { decodePcmBase64, PcmStreamPlayer } from "./pcmStreamPlayer";
import {
  SubtitlePlaybackTracker,
  type SubtitlePlaybackProgress,
} from "./subtitlePlayback";

export type PlaybackOwner = "idle" | "audio_element" | "pcm_stream" | "webrtc";

interface PlaybackCoordinatorOptions {
  enabled: boolean;
  isGenerationActive: (generationId: string) => boolean;
  sendReceipt: (receipt: PlaybackAckReceipt) => Promise<void>;
  stopRemotePlayback: (generationId?: string) => void;
  onSubtitle: (progress: SubtitlePlaybackProgress | null) => void;
  onError: (message: string) => void;
  onLipSyncStart: () => void;
  onLipSyncStop: () => void;
  createAudio?: (url: string) => PlayableAudio;
  createAudioContext?: () => AudioContext;
  streamFallbackGraceMs?: number;
  streamStallMs?: number;
}

/**
 * Owns all browser playout paths. Exactly one transport owns audible output at
 * a time: WebRTC when connected, otherwise live PCM, otherwise WAV fallback.
 */
export class PlaybackCoordinator {
  private owner: PlaybackOwner = "idle";
  private remoteConnected = false;
  private audioPlayer: GenerationAudioPlayer | null = null;
  private pcmPlayer: PcmStreamPlayer | null = null;
  private wavGenerationId: string | null = null;
  private readonly streamedSegments = new Map<string, AudioPlaybackItem>();
  private readonly fallbackItems = new Map<string, AudioPlaybackItem>();
  private readonly fallbackTimers = new Map<string, number>();
  private readonly pcmStallTimers = new Map<string, number>();
  private readonly stalledSegments = new Set<string>();
  private readonly subtitle = new SubtitlePlaybackTracker();
  private readonly reporter: PlaybackAckReporter;

  constructor(private readonly options: PlaybackCoordinatorOptions) {
    this.reporter = new PlaybackAckReporter({
      send: options.sendReceipt,
      onError: () =>
        options.onError("播放进度同步失败；本次已听内容可能不完整。"),
    });
  }

  get currentOwner(): PlaybackOwner {
    return this.owner;
  }

  setRemoteConnected(connected: boolean): void {
    this.remoteConnected = connected;
    if (connected) {
      this.clearFallbackTimers();
      this.clearPcmStallTimers();
      this.audioPlayer?.stop();
      this.pcmPlayer?.cancel();
      this.wavGenerationId = null;
      this.owner = "webrtc";
    } else if (this.owner === "webrtc") {
      this.owner = "idle";
      this.options.onLipSyncStop();
    }
  }

  startGeneration(generationId: string): void {
    if (
      this.wavGenerationId !== null &&
      this.wavGenerationId !== generationId
    ) {
      this.audioPlayer?.stop();
      this.wavGenerationId = null;
    }
    this.clearFallbackTimers();
    this.clearPcmStallTimers();
    this.stalledSegments.clear();
    this.streamedSegments.clear();
    this.fallbackItems.clear();
    this.options.onSubtitle(this.subtitle.start(generationId));
  }

  registerQueuedAudio(item: AudioPlaybackItem, streamedLive = false): void {
    const progress = this.subtitle.registerSegment(item);
    if (progress) this.options.onSubtitle(progress);
    if (!this.options.enabled || this.remoteConnected) return;
    this.fallbackItems.set(item.segmentId, item);
    if (this.streamedSegments.has(item.segmentId)) return;
    if (streamedLive) {
      this.scheduleFallback(item.segmentId);
      return;
    }
    if (this.owner === "pcm_stream") return;
    const player = this.getAudioPlayer();
    if (!player) return;
    this.wavGenerationId = item.generationId;
    this.owner = "audio_element";
    player.enqueue(item);
  }

  consumePcm(message: TtsStreamMessage): void {
    if (
      !this.options.enabled ||
      this.remoteConnected ||
      !this.options.isGenerationActive(message.generation_id)
    )
      return;
    // Batch providers publish their already-complete WAV as a burst of
    // pseudo-stream packets. The durable WAV event is the reliable path for
    // those providers and avoids overrunning the bounded live-stream fan-out.
    if (
      !message.native_streaming ||
      this.wavGenerationId === message.generation_id
    )
      return;
    const player = this.getPcmPlayer();
    if (!player) return;
    if (message.phase === "started") {
      player.start({
        phase: "started",
        generationId: message.generation_id,
        streamId: message.stream_id,
        segmentId: message.segment_id,
        segmentIndex: message.segment_index,
        text: message.text,
        sampleRate: message.sample_rate,
        channels: message.channels,
        nativeStreaming: message.native_streaming,
      });
    } else if (message.phase === "chunk" && message.pcm16_base64) {
      player.push(
        message.segment_id,
        message.sequence,
        decodePcmBase64(message.pcm16_base64),
      );
    } else if (message.phase === "completed") {
      this.clearPcmStallTimer(message.segment_id);
      const item = this.streamedSegments.get(message.segment_id);
      if (item) {
        item.durationMs = message.duration_ms;
        const progress = this.subtitle.registerSegment(item);
        if (progress) this.options.onSubtitle(progress);
      }
      player.complete(message.segment_id, message.duration_ms);
    } else if (message.phase === "cancelled") {
      this.clearPcmStallTimer(message.segment_id);
      this.stalledSegments.delete(message.segment_id);
      player.cancel(message.segment_id);
    }
  }

  reportRemoteReceipt(receipt: PlaybackAckReceipt): void {
    if (
      !this.options.enabled ||
      !this.remoteConnected ||
      !this.options.isGenerationActive(receipt.generationId)
    )
      return;
    this.owner = "webrtc";
    this.report(receipt);
    if (receipt.phase === "started") this.options.onLipSyncStart();
    if (receipt.phase === "stopped" || receipt.phase === "queue_cleared") {
      this.options.onLipSyncStop();
      if (receipt.phase === "stopped") this.owner = "idle";
    }
  }

  prime(): void {
    if (!this.options.enabled || this.remoteConnected) return;
    this.getAudioPlayer()?.prime();
    this.getPcmPlayer()?.prime();
  }

  stop(generationId?: string): void {
    this.audioPlayer?.stop();
    this.pcmPlayer?.cancel();
    this.options.stopRemotePlayback(generationId);
    this.options.onLipSyncStop();
    this.wavGenerationId = null;
    this.owner = "idle";
    this.clearFallbackTimers();
    this.clearPcmStallTimers();
    this.stalledSegments.clear();
    this.fallbackItems.clear();
    this.streamedSegments.clear();
  }

  resetSubtitles(): void {
    this.subtitle.reset();
    this.options.onSubtitle(null);
  }

  dispose(): void {
    this.stop();
    this.audioPlayer?.dispose();
    this.audioPlayer = null;
    this.pcmPlayer?.dispose();
    this.pcmPlayer = null;
    this.streamedSegments.clear();
    this.fallbackItems.clear();
    this.clearFallbackTimers();
    this.clearPcmStallTimers();
    this.stalledSegments.clear();
    this.reporter.dispose();
  }

  private getAudioPlayer(): GenerationAudioPlayer | null {
    const createAudio =
      this.options.createAudio ??
      (typeof Audio === "undefined" ? null : (url: string) => new Audio(url));
    if (!this.options.enabled || !createAudio) return null;
    if (!this.audioPlayer) {
      this.audioPlayer = new GenerationAudioPlayer(createAudio, {
        isGenerationActive: this.options.isGenerationActive,
        onPlaybackStart: (item, position) => {
          this.owner = "audio_element";
          this.options.onLipSyncStart();
          this.reportElement(item, "started", position);
        },
        onPlaybackProgress: (item, position) =>
          this.reportElement(item, "progress", position),
        onPlaybackStop: (item, position, reason) => {
          this.options.onLipSyncStop();
          this.owner = "idle";
          this.reportElement(item, "stopped", position, reason);
        },
        onQueueCleared: (item) =>
          this.reportElement(
            item,
            "queue_cleared",
            {
              playedPtsMs: 0,
              bufferedMs: 0,
              clientClockMs: Math.round(performance.now()),
            },
            "queue_cleared",
          ),
        onPlaybackError: this.options.onError,
      });
    }
    return this.audioPlayer;
  }

  private getPcmPlayer(): PcmStreamPlayer | null {
    const createContext =
      this.options.createAudioContext ??
      (typeof AudioContext === "undefined" ? null : () => new AudioContext());
    if (!this.options.enabled || !createContext) return null;
    if (!this.pcmPlayer) {
      this.pcmPlayer = new PcmStreamPlayer(createContext, {
        isGenerationActive: this.options.isGenerationActive,
        onStreamAccepted: (item) => {
          this.clearFallbackTimer(item.segmentId);
          this.owner = "pcm_stream";
          this.streamedSegments.set(item.segmentId, item);
          this.armPcmStallTimer(item.segmentId);
        },
        onStreamRejected: (item) => this.playFallback(item.segmentId),
        onStreamActivity: (item, bufferedMs) =>
          this.armPcmStallTimer(item.segmentId, bufferedMs),
        onPlaybackStart: (item, position) => {
          this.owner = "pcm_stream";
          this.options.onLipSyncStart();
          this.reportElement(item, "started", position);
        },
        onPlaybackProgress: (item, position) =>
          this.reportElement(item, "progress", position),
        onPlaybackStop: (item, position, reason) => {
          this.clearPcmStallTimer(item.segmentId);
          const pcmStillPlaying = this.pcmPlayer?.hasAcceptedPlayback ?? false;
          if (!pcmStillPlaying) {
            this.options.onLipSyncStop();
            this.owner = "idle";
          }
          this.reportElement(item, "stopped", position, reason);
          const stalled = this.stalledSegments.delete(item.segmentId);
          if (reason === "error" || stalled) {
            this.streamedSegments.delete(item.segmentId);
            this.playFallback(item.segmentId);
          } else if (reason === "ended") {
            this.clearFallbackTimer(item.segmentId);
            this.fallbackItems.delete(item.segmentId);
            this.playNextDeferredFallback();
          }
        },
        onPlaybackError: this.options.onError,
      });
    }
    return this.pcmPlayer;
  }

  private playFallback(segmentId: string): void {
    if (this.remoteConnected || !this.options.enabled) return;
    this.clearFallbackTimer(segmentId);
    if (this.streamedSegments.has(segmentId)) {
      this.fallbackItems.delete(segmentId);
      return;
    }
    if (this.owner === "pcm_stream") return;
    const fallback = this.fallbackItems.get(segmentId);
    if (!fallback || !this.options.isGenerationActive(fallback.generationId))
      return;
    const player = this.getAudioPlayer();
    if (!player) return;
    this.fallbackItems.delete(segmentId);
    // Runtime audio packets and durable WAV events use separate sockets. Once
    // a generation has entered the ordered WAV queue, later PCM for another
    // sentence must not take over and interrupt audio that is already playing.
    this.wavGenerationId = fallback.generationId;
    this.owner = "audio_element";
    player.enqueue(fallback);
  }

  private playNextDeferredFallback(): void {
    for (const [segmentId] of this.fallbackItems) {
      if (this.streamedSegments.has(segmentId)) continue;
      this.playFallback(segmentId);
      return;
    }
  }

  private scheduleFallback(segmentId: string): void {
    this.clearFallbackTimer(segmentId);
    const delay = this.options.streamFallbackGraceMs ?? 1_200;
    this.fallbackTimers.set(
      segmentId,
      window.setTimeout(() => {
        this.fallbackTimers.delete(segmentId);
        this.playFallback(segmentId);
      }, delay),
    );
  }

  private clearFallbackTimer(segmentId: string): void {
    const timer = this.fallbackTimers.get(segmentId);
    if (timer === undefined) return;
    window.clearTimeout(timer);
    this.fallbackTimers.delete(segmentId);
  }

  private clearFallbackTimers(): void {
    for (const timer of this.fallbackTimers.values())
      window.clearTimeout(timer);
    this.fallbackTimers.clear();
  }

  private armPcmStallTimer(segmentId: string, bufferedMs = 0): void {
    this.clearPcmStallTimer(segmentId);
    const delay = (this.options.streamStallMs ?? 2_500) + bufferedMs;
    this.pcmStallTimers.set(
      segmentId,
      window.setTimeout(() => {
        this.pcmStallTimers.delete(segmentId);
        const item = this.streamedSegments.get(segmentId);
        if (!item || !this.options.isGenerationActive(item.generationId))
          return;
        this.options.onError("实时语音流已中断，正在切换到完整音频。");
        this.stalledSegments.add(segmentId);
        this.streamedSegments.delete(segmentId);
        this.pcmPlayer?.cancel(segmentId);
        if (this.stalledSegments.delete(segmentId)) {
          this.owner = "idle";
          this.options.onLipSyncStop();
          this.playFallback(segmentId);
        }
      }, delay),
    );
  }

  private clearPcmStallTimer(segmentId: string): void {
    const timer = this.pcmStallTimers.get(segmentId);
    if (timer === undefined) return;
    window.clearTimeout(timer);
    this.pcmStallTimers.delete(segmentId);
  }

  private clearPcmStallTimers(): void {
    for (const timer of this.pcmStallTimers.values())
      window.clearTimeout(timer);
    this.pcmStallTimers.clear();
  }

  private reportElement(
    item: AudioPlaybackItem,
    phase: PlaybackAckReceipt["phase"],
    position: PlaybackPosition,
    reason?: PlaybackStopReason,
  ): void {
    this.report({
      phase,
      generationId: item.generationId,
      streamId: item.streamId,
      segmentId: item.segmentId,
      playedPtsMs: position.playedPtsMs,
      bufferedMs: position.bufferedMs,
      clientClockMs: position.clientClockMs,
      transport: "audio_element",
      reason,
    });
  }

  private report(receipt: PlaybackAckReceipt): void {
    const progress = this.subtitle.report(receipt);
    if (progress) this.options.onSubtitle(progress);
    this.reporter.report(receipt);
  }
}
