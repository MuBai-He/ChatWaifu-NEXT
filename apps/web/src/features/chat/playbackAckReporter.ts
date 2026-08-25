import type { PlaybackAckReceipt } from "./runtimeClient";

interface PlaybackAckReporterCallbacks {
  send(receipt: PlaybackAckReceipt): Promise<void>;
  onError(): void;
}

/** Serializes receipts so a terminal ACK cannot overtake its final progress ACK. */
export class PlaybackAckReporter {
  private readonly queue: PlaybackAckReceipt[] = [];
  private sending = false;
  private disposed = false;

  constructor(
    private readonly callbacks: PlaybackAckReporterCallbacks,
    private readonly maxQueueSize = 64,
  ) {}

  report(receipt: PlaybackAckReceipt): void {
    if (this.disposed) return;
    if (receipt.phase === "progress" && this.coalesceProgress(receipt)) return;
    if (this.queue.length >= this.maxQueueSize) {
      const progressIndex = this.queue.findIndex(
        (queued) => queued.phase === "progress",
      );
      if (progressIndex >= 0) this.queue.splice(progressIndex, 1);
      else if (receipt.phase === "progress") return;
      else {
        this.callbacks.onError();
        return;
      }
    }
    this.queue.push(receipt);
    void this.drain();
  }

  dispose(): void {
    this.disposed = true;
    this.queue.length = 0;
  }

  private coalesceProgress(receipt: PlaybackAckReceipt): boolean {
    for (let index = this.queue.length - 1; index >= 0; index -= 1) {
      const queued = this.queue[index];
      if (queued?.segmentId !== receipt.segmentId) continue;
      if (queued.phase !== "progress") return false;
      this.queue[index] = receipt;
      return true;
    }
    return false;
  }

  private async drain(): Promise<void> {
    if (this.sending || this.disposed) return;
    this.sending = true;
    try {
      while (!this.disposed) {
        const receipt = this.queue.shift();
        if (!receipt) break;
        try {
          await this.callbacks.send(receipt);
        } catch {
          this.callbacks.onError();
        }
      }
    } finally {
      this.sending = false;
      if (!this.disposed && this.queue.length > 0) void this.drain();
    }
  }
}
