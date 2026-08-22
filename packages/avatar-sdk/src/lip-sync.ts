export interface LipSyncSource {
  readonly id: string;
  readonly activeNodeCount: number;
  sample(nowMs: number): number;
  dispose(): void;
}

export class SilentLipSyncSource implements LipSyncSource {
  readonly id = "silent";
  readonly activeNodeCount = 0;

  sample(): number {
    return 0;
  }

  dispose(): void {}
}

export type SyntheticLipSyncMode = "sine" | "random";

export class SyntheticLipSyncSource implements LipSyncSource {
  readonly id: string;
  readonly activeNodeCount = 0;
  private seed: number;

  constructor(
    readonly mode: SyntheticLipSyncMode = "sine",
    seed = 0x5eed,
  ) {
    this.id = `synthetic:${mode}`;
    this.seed = seed >>> 0;
  }

  sample(nowMs: number): number {
    if (this.mode === "sine") {
      return clamp01((Math.sin(nowMs / 95) + 1) * 0.42);
    }
    this.seed ^= this.seed << 13;
    this.seed ^= this.seed >>> 17;
    this.seed ^= this.seed << 5;
    return clamp01((this.seed >>> 0) / 0xffff_ffff);
  }

  dispose(): void {}
}

export interface AnalyserLipSyncOptions {
  gain?: number;
  smoothing?: number;
  nodesToDisconnect?: AudioNode[];
  onDispose?: () => void;
}

export class AnalyserLipSyncSource implements LipSyncSource {
  readonly id = "web-audio-analyser";
  readonly activeNodeCount: number;
  private readonly samples: Float32Array<ArrayBuffer>;
  private readonly gain: number;
  private readonly smoothing: number;
  private readonly nodesToDisconnect: AudioNode[];
  private readonly onDispose?: () => void;
  private previous = 0;
  private disposed = false;

  constructor(
    private readonly analyser: AnalyserNode,
    options: AnalyserLipSyncOptions = {},
  ) {
    this.samples = new Float32Array(analyser.fftSize);
    this.gain = options.gain ?? 4.5;
    this.smoothing = options.smoothing ?? 0.68;
    this.nodesToDisconnect = options.nodesToDisconnect ?? [analyser];
    this.activeNodeCount = this.nodesToDisconnect.length;
    this.onDispose = options.onDispose;
  }

  sample(): number {
    if (this.disposed) return 0;
    this.analyser.getFloatTimeDomainData(this.samples);
    let sum = 0;
    for (const value of this.samples) sum += value * value;
    const rms = Math.sqrt(sum / this.samples.length);
    const current = clamp01(rms * this.gain);
    this.previous =
      this.previous * this.smoothing + current * (1 - this.smoothing);
    return this.previous;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const node of this.nodesToDisconnect) node.disconnect();
    this.onDispose?.();
    this.previous = 0;
  }
}

export interface MotionSyncAdapter {
  sampleMouthOpen(nowMs: number): number;
  dispose(): void;
}

export class MotionSyncLipSyncSource implements LipSyncSource {
  readonly id = "live2d-motion-sync";
  readonly activeNodeCount = 0;

  constructor(private readonly adapter: MotionSyncAdapter) {}

  sample(nowMs: number): number {
    return clamp01(this.adapter.sampleMouthOpen(nowMs));
  }

  dispose(): void {
    this.adapter.dispose();
  }
}

export function clamp01(value: number): number {
  return Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
}
