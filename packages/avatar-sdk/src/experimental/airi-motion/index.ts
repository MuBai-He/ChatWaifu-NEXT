import type {
  AvatarProceduralFrame,
  AvatarProceduralMotionSource,
} from "../../types";
import type { Generator } from "./vendor/types";
import { createVarModel, type VarModel } from "./vendor/var";
import idleCalm from "./idle-calm.json";
import speakingExcited from "./speaking-excited.json";

export type AiriMotionProfile = "idle-calm" | "speaking-excited";
export interface AiriMotionOptions {
  profile: AiriMotionProfile;
  intensity: number;
}

const CHANNELS = [
  "headYaw",
  "headPitch",
  "headRoll",
  "bodyYaw",
  "bodyPitch",
  "bodyRoll",
] as const;
const STEP_MS = 1000 / 30;
const SEED = 0x4e454e45;
const clamp = (value: number, low: number, high: number) =>
  Math.min(high, Math.max(low, value));

/** Lab-only AIRI VAR adapter. Owns six normalized pose axes, never face or speech. */
export class AiriMotionDriver implements AvatarProceduralMotionSource {
  private readonly models = new Map<AiriMotionProfile, VarModel>();
  private options: AiriMotionOptions;
  private generator: Generator;
  private previous = CHANNELS.map(() => 0);
  private current = CHANNELS.map(() => 0);
  private accepted = CHANNELS.map(() => 0);
  private accumulatedMs = 0;
  private lastNowMs: number | null = null;
  private blend = 0;

  constructor(options: AiriMotionOptions) {
    this.options = normalizeOptions(options);
    this.generator = this.getModel(this.options.profile).toGenerator({
      seed: SEED,
    });
  }

  configure(options: AiriMotionOptions): void {
    const next = normalizeOptions(options);
    if (next.profile !== this.options.profile) {
      // Fit on explicit selection, never inside requestAnimationFrame.
      this.generator = this.getModel(next.profile).toGenerator({ seed: SEED });
      this.accumulatedMs = 0;
      this.accepted = [...this.current];
    }
    this.options = next;
  }

  reset(): void {
    this.generator = this.getModel(this.options.profile).toGenerator({
      seed: SEED,
    });
    this.previous.fill(0);
    this.current.fill(0);
    this.accepted.fill(0);
    this.accumulatedMs = 0;
    this.lastNowMs = null;
    this.blend = 0;
  }

  apply(
    base: AvatarProceduralFrame,
    nowMs: number,
    suspended: boolean,
  ): AvatarProceduralFrame {
    if (!Number.isFinite(nowMs)) return base;
    const elapsed =
      this.lastNowMs === null ? 0 : clamp(nowMs - this.lastNowMs, 0, 100);
    this.lastNowMs = nowMs;
    // A gesture or interrupt releases all six axes immediately. Resume fades in.
    if (suspended) {
      this.blend = 0;
      this.accumulatedMs = 0;
      return base;
    }
    const targetBlend = this.options.intensity;
    this.blend += (targetBlend - this.blend) * (1 - Math.exp(-elapsed / 180));
    if (Math.abs(this.blend - targetBlend) < 0.0001) this.blend = targetBlend;
    if (this.blend === 0 && targetBlend === 0) return base;
    this.accumulatedMs += elapsed;
    while (this.accumulatedMs + 1e-7 >= STEP_MS) {
      this.accumulatedMs = Math.max(0, this.accumulatedMs - STEP_MS);
      const frame = this.generator.next({ noiseScale: 1.15 }).values;
      this.previous = [...this.current];
      this.current = CHANNELS.map((_, index) => {
        const raw = frame[index] ?? 0;
        const value = Number.isFinite(raw) ? clamp(raw, -1, 1) : 0;
        const accepted = this.accepted[index] ?? 0;
        // AIRI's default cutoff + EMA at the fixed model cadence.
        if (Math.abs(value - accepted) >= 0.0575) this.accepted[index] = value;
        return (
          (this.current[index] ?? 0) * 0.8 + (this.accepted[index] ?? 0) * 0.2
        );
      });
    }
    const result = { ...base };
    const fraction = this.accumulatedMs / STEP_MS;
    CHANNELS.forEach((channel, index) => {
      const previous = this.previous[index] ?? 0;
      const generated =
        previous + ((this.current[index] ?? 0) - previous) * fraction;
      result[channel] = clamp(
        base[channel] * (1 - this.blend) + generated * this.blend,
        -1,
        1,
      );
    });
    return result;
  }

  private getModel(profile: AiriMotionProfile): VarModel {
    let model = this.models.get(profile);
    if (!model) {
      model = createVarModel(
        profile === "idle-calm" ? idleCalm : speakingExcited,
        { order: 20, ridge: 0.001 },
      );
      this.models.set(profile, model);
    }
    return model;
  }
}

function normalizeOptions(options: AiriMotionOptions): AiriMotionOptions {
  if (
    options.profile !== "idle-calm" &&
    options.profile !== "speaking-excited"
  ) {
    throw new Error("Unknown AIRI motion profile");
  }
  return {
    profile: options.profile,
    intensity: Number.isFinite(options.intensity)
      ? clamp(options.intensity, 0, 1)
      : 0,
  };
}
