import type { AvatarCue } from "@chatwaifu/protocol";

import { AvatarCapabilityRegistry } from "./capability-registry";
import type {
  AvatarLayer,
  AvatarWarning,
  CueSchedulerSnapshot,
  MotionLifecycleEvent,
  ScheduledCue,
} from "./types";

const DEFAULT_DURATIONS: Record<AvatarCue["kind"], number | null> = {
  state: null,
  expression: 1_500,
  motion: 1_200,
  gaze: null,
  speech: null,
  override: 350,
};

export interface CueSchedulerOptions {
  maxQueueSize?: number;
  maxWarnings?: number;
  onMotionLifecycle?: (event: MotionLifecycleEvent) => void;
}

export class CueScheduler {
  private readonly capabilities: AvatarCapabilityRegistry;
  private readonly maxQueueSize: number;
  private readonly maxWarnings: number;
  private readonly onMotionLifecycle?: (event: MotionLifecycleEvent) => void;
  private readonly active = new Map<AvatarLayer, ScheduledCue>();
  private readonly queued: ScheduledCue[] = [];
  private readonly invalidGenerations = new Set<string>();
  private readonly warnings: AvatarWarning[] = [];
  private revision = 0;
  private sequence = 0;

  constructor(
    capabilities: AvatarCapabilityRegistry,
    options: CueSchedulerOptions = {},
  ) {
    this.capabilities = capabilities;
    this.maxQueueSize = options.maxQueueSize ?? 64;
    this.maxWarnings = options.maxWarnings ?? 32;
    this.onMotionLifecycle = options.onMotionLifecycle;
  }

  schedule(cue: AvatarCue, nowMs: number): CueSchedulerSnapshot {
    if (cue.generation_id && this.invalidGenerations.has(cue.generation_id)) {
      this.warn({
        code: "avatar.stale_generation_cue",
        message: `Ignored cue from invalid generation ${cue.generation_id}.`,
        cueId: cue.cue_id,
      });
      return this.snapshot();
    }

    const resolution = this.capabilities.resolve(cue);
    if (resolution.warning) this.warn(resolution.warning);
    const scheduled = this.createScheduled(
      resolution.cue,
      resolution.layer,
      nowMs,
    );

    if (
      scheduled.cue.kind === "override" &&
      scheduled.cue.name === "interrupt"
    ) {
      this.interruptActiveCues();
    }

    if (
      this.mustWaitForMotion(scheduled) ||
      !this.canReplaceActive(scheduled)
    ) {
      this.enqueue(scheduled);
    } else {
      this.activate(scheduled);
    }
    this.revision += 1;
    return this.snapshot();
  }

  tick(nowMs: number): CueSchedulerSnapshot {
    let changed = false;
    for (const [layer, scheduled] of this.active) {
      if (scheduled.expiresAtMs !== null && scheduled.expiresAtMs <= nowMs) {
        this.active.delete(layer);
        this.emitMotionEnded(scheduled, "expired");
        changed = true;
      }
    }
    changed = this.promoteQueued(nowMs) || changed;
    if (changed) this.revision += 1;
    return this.snapshot();
  }

  invalidateGeneration(
    generationId: string,
    nowMs: number,
  ): CueSchedulerSnapshot {
    this.invalidGenerations.add(generationId);
    let changed = false;
    for (const [layer, scheduled] of this.active) {
      if (scheduled.cue.generation_id === generationId) {
        this.active.delete(layer);
        this.emitMotionEnded(scheduled, "invalidated");
        changed = true;
      }
    }
    for (let index = this.queued.length - 1; index >= 0; index -= 1) {
      if (this.queued[index]?.cue.generation_id === generationId) {
        this.queued.splice(index, 1);
        changed = true;
      }
    }
    changed = this.promoteQueued(nowMs) || changed;
    if (changed) this.revision += 1;
    return this.snapshot();
  }

  notifyMotionEnded(cueId: string, nowMs: number): CueSchedulerSnapshot {
    const motion = this.active.get("gesture");
    if (!motion || motion.cue.cue_id !== cueId) return this.snapshot();
    this.active.delete("gesture");
    this.emitMotionEnded(motion, "expired");
    this.promoteQueued(nowMs);
    this.revision += 1;
    return this.snapshot();
  }

  clearLayer(layer: AvatarLayer, nowMs: number): CueSchedulerSnapshot {
    const active = this.active.get(layer);
    if (!active) return this.snapshot();
    this.active.delete(layer);
    this.emitMotionEnded(active, "invalidated");
    this.promoteQueued(nowMs);
    this.revision += 1;
    return this.snapshot();
  }

  reset(): CueSchedulerSnapshot {
    for (const scheduled of this.active.values()) {
      this.emitMotionEnded(scheduled, "invalidated");
    }
    this.active.clear();
    this.queued.splice(0);
    this.invalidGenerations.clear();
    this.revision += 1;
    return this.snapshot();
  }

  snapshot(): CueSchedulerSnapshot {
    return {
      revision: this.revision,
      active: Object.fromEntries(this.active) as Partial<
        Record<AvatarLayer, ScheduledCue>
      >,
      queued: [...this.queued],
      warnings: [...this.warnings],
    };
  }

  private createScheduled(
    cue: AvatarCue,
    layer: AvatarLayer,
    nowMs: number,
  ): ScheduledCue {
    const duration = cue.duration_ms ?? DEFAULT_DURATIONS[cue.kind];
    return {
      cue: {
        ...cue,
        intensity: cue.intensity ?? 1,
        interruptible: cue.interruptible ?? true,
        priority: cue.priority ?? 50,
        start_anchor: cue.start_anchor ?? "immediate",
      },
      layer,
      sequence: this.sequence++,
      startsAtMs: nowMs,
      expiresAtMs: duration === null ? null : nowMs + duration,
    };
  }

  private canReplaceActive(incoming: ScheduledCue): boolean {
    const current = this.active.get(incoming.layer);
    if (!current) return true;
    const currentPriority = current.cue.priority ?? 50;
    const incomingPriority = incoming.cue.priority ?? 50;
    return (
      current.cue.interruptible !== false && incomingPriority >= currentPriority
    );
  }

  private mustWaitForMotion(incoming: ScheduledCue): boolean {
    return (
      incoming.cue.start_anchor === "after_current_motion" &&
      this.active.has("gesture")
    );
  }

  private enqueue(scheduled: ScheduledCue): void {
    if (this.queued.length >= this.maxQueueSize) {
      this.warn({
        code: "avatar.cue_queue_full",
        message: `Cue queue is bounded at ${this.maxQueueSize}; cue was dropped.`,
        cueId: scheduled.cue.cue_id,
      });
      return;
    }
    this.queued.push(scheduled);
    this.queued.sort((left, right) => {
      const priority = (right.cue.priority ?? 50) - (left.cue.priority ?? 50);
      return priority || left.sequence - right.sequence;
    });
  }

  private activate(scheduled: ScheduledCue): void {
    const replaced = this.active.get(scheduled.layer);
    if (replaced) this.emitMotionEnded(replaced, "replaced");
    this.active.set(scheduled.layer, scheduled);
    if (scheduled.layer === "gesture") {
      this.onMotionLifecycle?.({ type: "motion-started", cue: scheduled });
    }
  }

  private promoteQueued(nowMs: number): boolean {
    let changed = false;
    for (let index = 0; index < this.queued.length;) {
      const candidate = this.queued[index];
      if (!candidate) break;
      if (
        this.mustWaitForMotion(candidate) ||
        !this.canReplaceActive(candidate)
      ) {
        index += 1;
        continue;
      }
      this.queued.splice(index, 1);
      this.activate({
        ...candidate,
        startsAtMs: nowMs,
        expiresAtMs:
          candidate.expiresAtMs === null
            ? null
            : nowMs + Math.max(0, candidate.expiresAtMs - candidate.startsAtMs),
      });
      changed = true;
    }
    return changed;
  }

  private interruptActiveCues(): void {
    for (const [layer, scheduled] of this.active) {
      if (scheduled.cue.interruptible !== false) {
        this.active.delete(layer);
        this.emitMotionEnded(scheduled, "invalidated");
      }
    }
    for (let index = this.queued.length - 1; index >= 0; index -= 1) {
      if (this.queued[index]?.cue.interruptible !== false)
        this.queued.splice(index, 1);
    }
  }

  private emitMotionEnded(
    scheduled: ScheduledCue,
    reason: "expired" | "replaced" | "invalidated",
  ): void {
    if (scheduled.layer === "gesture") {
      this.onMotionLifecycle?.({
        type: "motion-ended",
        cue: scheduled,
        reason,
      });
    }
  }

  private warn(warning: AvatarWarning): void {
    this.warnings.push(warning);
    if (this.warnings.length > this.maxWarnings) this.warnings.shift();
  }
}
