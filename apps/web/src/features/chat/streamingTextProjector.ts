const DEFAULT_TICK_MS = 24;
const DEFAULT_MAX_QUEUED_CODE_POINTS = 4_096;

interface StreamingTextProjectorCallbacks {
  onReveal(generationId: string, text: string): void;
  onComplete(generationId: string): void;
}

interface StreamingTextProjectorOptions {
  tickMs?: number;
  maxQueuedCodePoints?: number;
}

/**
 * Smooths provider/network bursts without changing the lossless Runtime event
 * stream. The queue is generation-scoped, bounded, and discarded on
 * interruption so stale text can never leak into the next reply.
 */
export class StreamingTextProjector {
  private readonly queue: string[] = [];
  private readonly tickMs: number;
  private readonly maxQueuedCodePoints: number;
  private generationId: string | null = null;
  private completionRequested = false;
  private timer: number | null = null;
  private disposed = false;

  constructor(
    private readonly callbacks: StreamingTextProjectorCallbacks,
    options: StreamingTextProjectorOptions = {},
  ) {
    this.tickMs = options.tickMs ?? DEFAULT_TICK_MS;
    this.maxQueuedCodePoints =
      options.maxQueuedCodePoints ?? DEFAULT_MAX_QUEUED_CODE_POINTS;
  }

  start(generationId: string): void {
    if (this.disposed) return;
    this.clearTimer();
    this.queue.length = 0;
    this.generationId = generationId;
    this.completionRequested = false;
  }

  push(generationId: string, text: string): void {
    if (
      this.disposed ||
      generationId !== this.generationId ||
      this.completionRequested ||
      !text
    )
      return;

    this.queue.push(...Array.from(text));
    const overflow = this.queue.length - this.maxQueuedCodePoints;
    if (overflow > 0) {
      this.callbacks.onReveal(
        generationId,
        this.queue.splice(0, overflow).join(""),
      );
    }
    this.schedule();
  }

  complete(generationId: string): void {
    if (this.disposed || generationId !== this.generationId) return;
    this.completionRequested = true;
    if (this.queue.length === 0) {
      this.finish(generationId);
      return;
    }
    this.schedule();
  }

  cancel(generationId: string): void {
    if (generationId !== this.generationId) return;
    this.clearTimer();
    this.queue.length = 0;
    this.generationId = null;
    this.completionRequested = false;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.clearTimer();
    this.queue.length = 0;
    this.generationId = null;
  }

  private schedule(): void {
    if (this.timer !== null || this.queue.length === 0) return;
    this.timer = window.setTimeout(() => this.revealNext(), this.tickMs);
  }

  private revealNext(): void {
    this.timer = null;
    const generationId = this.generationId;
    if (!generationId || this.queue.length === 0) return;

    const count = revealCount(this.queue.length);
    this.callbacks.onReveal(generationId, this.queue.splice(0, count).join(""));
    if (this.queue.length > 0) {
      this.schedule();
    } else if (this.completionRequested) {
      this.finish(generationId);
    }
  }

  private finish(generationId: string): void {
    if (generationId !== this.generationId) return;
    this.generationId = null;
    this.completionRequested = false;
    this.callbacks.onComplete(generationId);
  }

  private clearTimer(): void {
    if (this.timer === null) return;
    window.clearTimeout(this.timer);
    this.timer = null;
  }
}

function revealCount(backlog: number): number {
  if (backlog > 480) return 10;
  if (backlog > 180) return 6;
  if (backlog > 72) return 3;
  return 1;
}
