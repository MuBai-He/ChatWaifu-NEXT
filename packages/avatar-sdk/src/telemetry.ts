import type { AvatarRenderer } from "./renderer";
import type { AvatarTelemetrySnapshot } from "./types";

const TARGET_FRAME_MS = 1000 / 60;

interface MemoryPerformance extends Performance {
  memory?: { usedJSHeapSize?: number };
}

export class AvatarTelemetryCollector {
  private readonly frameDurations: number[] = [];
  private renderedFrames = 0;
  private droppedFrames = 0;
  private lastFrameAt: number | null = null;

  recordFrame(nowMs: number): void {
    if (this.lastFrameAt !== null) {
      const duration = Math.max(0, nowMs - this.lastFrameAt);
      this.frameDurations.push(duration);
      if (this.frameDurations.length > 120) this.frameDurations.shift();
      this.droppedFrames += Math.max(
        0,
        Math.round(duration / TARGET_FRAME_MS) - 1,
      );
    }
    this.lastFrameAt = nowMs;
    this.renderedFrames += 1;
  }

  reset(): void {
    this.frameDurations.splice(0);
    this.renderedFrames = 0;
    this.droppedFrames = 0;
    this.lastFrameAt = null;
  }

  snapshot(
    renderer: AvatarRenderer,
    activeAudioNodes: number,
  ): AvatarTelemetrySnapshot {
    const total = this.frameDurations.reduce((sum, value) => sum + value, 0);
    const frameTimeMs = this.frameDurations.length
      ? total / this.frameDurations.length
      : 0;
    const diagnostics = renderer.diagnostics();
    const memory = (performance as MemoryPerformance).memory?.usedJSHeapSize;
    return {
      fps: frameTimeMs > 0 ? 1000 / frameTimeMs : 0,
      frameTimeMs,
      droppedFrames: this.droppedFrames,
      renderedFrames: this.renderedFrames,
      contextLosses: diagnostics.contextLosses,
      rendererResources: diagnostics.resourceCount,
      activeAudioNodes,
      heapUsedBytes: typeof memory === "number" ? memory : null,
    };
  }
}
