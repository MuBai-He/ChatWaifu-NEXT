import type { AudioPlaybackItem } from "./audioPlayer";
import type { PlaybackAckReceipt } from "./runtimeClient";

const MAX_PENDING_RECEIPTS = 32;
const MAX_TRACKED_SEGMENTS = 256;

export interface SubtitlePlaybackProgress {
  generationId: string;
  segmentIndex: number;
  playedTextUnits: number;
  phase: "waiting" | "playing" | "stopped";
}

interface SegmentProgress {
  item: AudioPlaybackItem;
  playedTextUnits: number;
  stopped: boolean;
}

/** Collapse paragraph gaps for the compact overlay without changing transcripts. */
export function normalizeDesktopSubtitle(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n(?:[ \t]*\n)+/g, "\n");
}

/** Unicode code points excluding layout whitespace approximate spoken progress. */
export function countSubtitleTextUnits(text: string): number {
  return Array.from(text).filter((character) => !/\s/u.test(character)).length;
}

export class SubtitlePlaybackTracker {
  private generationId: string | null = null;
  private readonly segments = new Map<string, SegmentProgress>();
  private readonly pendingReceipts = new Map<string, PlaybackAckReceipt>();
  private progress: SubtitlePlaybackProgress | null = null;

  start(generationId: string): SubtitlePlaybackProgress {
    this.generationId = generationId;
    this.segments.clear();
    this.pendingReceipts.clear();
    this.progress = {
      generationId,
      segmentIndex: -1,
      playedTextUnits: 0,
      phase: "waiting",
    };
    return this.progress;
  }

  reset(): void {
    this.generationId = null;
    this.segments.clear();
    this.pendingReceipts.clear();
    this.progress = null;
  }

  registerSegment(item: AudioPlaybackItem): SubtitlePlaybackProgress | null {
    if (item.generationId !== this.generationId) return null;
    if (!this.segments.has(item.segmentId)) {
      if (this.segments.size >= MAX_TRACKED_SEGMENTS) return null;
      this.segments.set(item.segmentId, {
        item,
        playedTextUnits: 0,
        stopped: false,
      });
    }
    const pending = this.pendingReceipts.get(item.segmentId);
    if (!pending) return null;
    this.pendingReceipts.delete(item.segmentId);
    return this.report(pending);
  }

  report(receipt: PlaybackAckReceipt): SubtitlePlaybackProgress | null {
    if (
      receipt.generationId !== this.generationId ||
      receipt.phase === "queue_cleared"
    )
      return null;

    const segment = this.segments.get(receipt.segmentId);
    if (!segment) {
      this.rememberPending(receipt);
      return null;
    }
    if (segment.stopped && receipt.phase !== "stopped") return null;

    const durationMs = Math.max(1, segment.item.durationMs);
    const fraction = Math.min(1, Math.max(0, receipt.playedPtsMs / durationMs));
    segment.playedTextUnits = Math.max(
      segment.playedTextUnits,
      countSubtitleTextUnits(segment.item.text) * fraction,
    );
    if (receipt.phase === "stopped") segment.stopped = true;

    const playedTextUnits = Array.from(this.segments.values()).reduce(
      (total, current) => total + current.playedTextUnits,
      0,
    );
    const previous = this.progress;
    const advancesActiveSegment =
      !previous || segment.item.segmentIndex >= previous.segmentIndex;
    const phase = advancesActiveSegment
      ? receipt.phase === "stopped"
        ? "stopped"
        : "playing"
      : previous.phase;
    const next: SubtitlePlaybackProgress = {
      generationId: receipt.generationId,
      segmentIndex: advancesActiveSegment
        ? segment.item.segmentIndex
        : (previous?.segmentIndex ?? segment.item.segmentIndex),
      playedTextUnits,
      phase,
    };
    if (this.progress && next.playedTextUnits < this.progress.playedTextUnits)
      return null;
    this.progress = next;
    return next;
  }

  private rememberPending(receipt: PlaybackAckReceipt): void {
    this.pendingReceipts.delete(receipt.segmentId);
    this.pendingReceipts.set(receipt.segmentId, receipt);
    while (this.pendingReceipts.size > MAX_PENDING_RECEIPTS) {
      const oldest = this.pendingReceipts.keys().next().value;
      if (typeof oldest !== "string") break;
      this.pendingReceipts.delete(oldest);
    }
  }
}
