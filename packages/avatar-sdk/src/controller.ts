import type { AvatarCue, AvatarInteractionEvent } from "@chatwaifu/protocol";
import { parseAvatarCue } from "@chatwaifu/protocol";

import { BrowserAnimationClock, type AnimationClock } from "./audio-clock";
import { AvatarCapabilityRegistry } from "./capability-registry";
import {
  AvatarBehaviorStateMachine,
  neutralProceduralFrame,
} from "./behavior-state-machine";
import { CueScheduler } from "./cue-scheduler";
import { interactionFromHit } from "./interaction";
import { SilentLipSyncSource, type LipSyncSource } from "./lip-sync";
import { AvatarRendererError, type AvatarRenderer } from "./renderer";
import { AvatarTelemetryCollector } from "./telemetry";
import type {
  AvatarControllerSnapshot,
  AvatarInteractionListener,
  AvatarLayer,
  AvatarManifest,
  AvatarRuntimeState,
  AvatarWarning,
  CueSchedulerSnapshot,
  MotionLifecycleEvent,
} from "./types";

export interface AvatarControllerOptions {
  clock?: AnimationClock;
  maxPreReadyCues?: number;
  telemetryIntervalMs?: number;
  onMotionLifecycle?: (event: MotionLifecycleEvent) => void;
}

export class AvatarController {
  private readonly clock: AnimationClock;
  private readonly scheduler: CueScheduler;
  private readonly telemetry = new AvatarTelemetryCollector();
  private readonly behavior = new AvatarBehaviorStateMachine();
  private readonly maxPreReadyCues: number;
  private readonly telemetryIntervalMs: number;
  private readonly preReadyCues: AvatarCue[] = [];
  private readonly stateListeners = new Set<
    (snapshot: AvatarControllerSnapshot) => void
  >();
  private readonly interactionListeners = new Set<AvatarInteractionListener>();
  private readonly warnings: AvatarWarning[] = [];
  private lipSync: LipSyncSource = new SilentLipSyncSource();
  private status: AvatarControllerSnapshot["status"] = "idle";
  private frameHandle: number | null = null;
  private lastPublishedRevision = -1;
  private lastTelemetryPublishedAt = 0;
  private runtime: AvatarRuntimeState = initialRuntimeState();

  constructor(
    private readonly renderer: AvatarRenderer,
    private readonly manifest: AvatarManifest,
    options: AvatarControllerOptions = {},
  ) {
    this.clock = options.clock ?? new BrowserAnimationClock();
    this.maxPreReadyCues = options.maxPreReadyCues ?? 32;
    this.telemetryIntervalMs = options.telemetryIntervalMs ?? 500;
    this.scheduler = new CueScheduler(
      new AvatarCapabilityRegistry(manifest.capabilities),
      {
        onMotionLifecycle: options.onMotionLifecycle,
      },
    );
  }

  async load(): Promise<void> {
    if (this.status === "disposed") throw new Error("controller is disposed");
    this.status = "loading";
    this.publish();
    try {
      await this.renderer.load(this.manifest);
      this.status = "ready";
      const nowMs = this.clock.now();
      for (const cue of this.preReadyCues.splice(0))
        this.scheduler.schedule(cue, nowMs);
      this.startRenderLoop();
      this.publish();
    } catch (error: unknown) {
      this.status = "error";
      const rendererError =
        error instanceof AvatarRendererError
          ? error
          : new AvatarRendererError(
              "avatar.renderer_load_failed",
              error instanceof Error
                ? error.message
                : "Avatar renderer failed to load.",
              "Check the avatar manifest and renderer diagnostics.",
              { cause: error },
            );
      this.warn(rendererError.toWarning());
      this.publish();
      throw rendererError;
    }
  }

  applyCue(input: unknown): AvatarControllerSnapshot {
    const cue = parseAvatarCue(input);
    if (this.status !== "ready") {
      if (this.preReadyCues.length >= this.maxPreReadyCues) {
        const dropped = this.preReadyCues.shift();
        this.warn({
          code: "avatar.pre_ready_queue_full",
          message: `Pre-ready cue queue is bounded at ${this.maxPreReadyCues}; oldest cue was dropped.`,
          cueId: dropped?.cue_id,
        });
      }
      this.preReadyCues.push(cue);
      this.publish();
      return this.snapshot();
    }
    this.scheduler.schedule(cue, this.clock.now());
    this.publishSemanticState();
    return this.snapshot();
  }

  invalidateGeneration(generationId: string): AvatarControllerSnapshot {
    this.scheduler.invalidateGeneration(generationId, this.clock.now());
    this.publishSemanticState();
    return this.snapshot();
  }

  clearLayer(layer: AvatarLayer): AvatarControllerSnapshot {
    this.scheduler.clearLayer(layer, this.clock.now());
    this.publishSemanticState();
    return this.snapshot();
  }

  notifyMotionEnded(cueId: string): AvatarControllerSnapshot {
    this.scheduler.notifyMotionEnded(cueId, this.clock.now());
    this.publishSemanticState();
    return this.snapshot();
  }

  reset(): AvatarControllerSnapshot {
    this.scheduler.reset();
    this.behavior.reset();
    this.publishSemanticState();
    return this.snapshot();
  }

  setLipSyncSource(source: LipSyncSource): void {
    if (source === this.lipSync) return;
    this.lipSync.dispose();
    this.lipSync = source;
    this.publish();
  }

  resize(width: number, height: number, dpr = 1): void {
    this.renderer.resize(width, height, dpr);
  }

  hitTest(x: number, y: number): AvatarInteractionEvent[] {
    return this.renderer
      .hitTest(x, y)
      .map((hit) => interactionFromHit(this.manifest, hit));
  }

  handlePointer(x: number, y: number): AvatarInteractionEvent[] {
    const events = this.hitTest(x, y);
    for (const event of events) {
      for (const listener of this.interactionListeners) listener(event);
    }
    return events;
  }

  subscribe(
    listener: (snapshot: AvatarControllerSnapshot) => void,
  ): () => void {
    this.stateListeners.add(listener);
    listener(this.snapshot());
    return () => this.stateListeners.delete(listener);
  }

  onInteraction(listener: AvatarInteractionListener): () => void {
    this.interactionListeners.add(listener);
    return () => this.interactionListeners.delete(listener);
  }

  snapshot(): AvatarControllerSnapshot {
    return {
      status: this.status,
      scheduler: this.scheduler.snapshot(),
      runtime: this.runtime,
      telemetry: this.telemetry.snapshot(
        this.renderer,
        this.lipSync.activeNodeCount,
      ),
      preReadyQueueSize: this.preReadyCues.length,
      warnings: [...this.warnings, ...this.scheduler.snapshot().warnings],
    };
  }

  async unload(): Promise<void> {
    this.stopRenderLoop();
    this.lipSync.dispose();
    this.lipSync = new SilentLipSyncSource();
    this.scheduler.reset();
    this.behavior.reset();
    this.preReadyCues.splice(0);
    await this.renderer.unload();
    this.telemetry.reset();
    this.runtime = initialRuntimeState();
    this.status = "idle";
    this.publish();
  }

  dispose(): void {
    if (this.status === "disposed") return;
    this.stopRenderLoop();
    this.lipSync.dispose();
    this.renderer.dispose();
    this.preReadyCues.splice(0);
    this.stateListeners.clear();
    this.interactionListeners.clear();
    this.status = "disposed";
  }

  private startRenderLoop(): void {
    if (this.frameHandle !== null) return;
    const renderFrame: FrameRequestCallback = (nowMs) => {
      if (this.status !== "ready") {
        this.frameHandle = null;
        return;
      }
      const scheduler = this.scheduler.tick(nowMs);
      const semantic = runtimeFromScheduler(
        scheduler,
        this.lipSync.sample(nowMs),
        this.runtime.procedural,
      );
      this.runtime = {
        ...semantic,
        procedural: this.behavior.step(behaviorInput(semantic), nowMs),
      };
      this.renderer.render(this.runtime, nowMs);
      this.telemetry.recordFrame(nowMs);
      if (scheduler.revision !== this.lastPublishedRevision)
        this.publishSemanticState();
      if (nowMs - this.lastTelemetryPublishedAt >= this.telemetryIntervalMs) {
        this.lastTelemetryPublishedAt = nowMs;
        this.publish();
      }
      this.frameHandle = this.clock.requestFrame(renderFrame);
    };
    this.frameHandle = this.clock.requestFrame(renderFrame);
  }

  private stopRenderLoop(): void {
    if (this.frameHandle !== null) this.clock.cancelFrame(this.frameHandle);
    this.frameHandle = null;
  }

  private publishSemanticState(): void {
    const scheduler = this.scheduler.snapshot();
    this.runtime = runtimeFromScheduler(
      scheduler,
      this.runtime.mouthOpen,
      this.runtime.procedural,
    );
    this.lastPublishedRevision = scheduler.revision;
    this.publish();
  }

  private publish(): void {
    const snapshot = this.snapshot();
    for (const listener of this.stateListeners) listener(snapshot);
  }

  private warn(warning: AvatarWarning): void {
    this.warnings.push(warning);
    if (this.warnings.length > 32) this.warnings.shift();
  }
}

function initialRuntimeState(): AvatarRuntimeState {
  return {
    revision: 0,
    state: "idle",
    expression: "neutral",
    motion: null,
    gaze: "center",
    speaking: false,
    interrupted: false,
    mouthOpen: 0,
    procedural: neutralProceduralFrame(),
    activeCues: {},
  };
}

function runtimeFromScheduler(
  scheduler: CueSchedulerSnapshot,
  sampledMouthOpen: number,
  procedural: AvatarRuntimeState["procedural"],
): AvatarRuntimeState {
  const activeCues = Object.fromEntries(
    Object.entries(scheduler.active).map(([layer, scheduled]) => [
      layer,
      scheduled?.cue,
    ]),
  );
  const speaking = scheduler.active.speech?.cue.name === "speaking";
  return {
    revision: scheduler.revision,
    state: scheduler.active.attention?.cue.name ?? "idle",
    expression: scheduler.active.emotion?.cue.name ?? "neutral",
    motion: scheduler.active.gesture?.cue.name ?? null,
    gaze: scheduler.active.gaze?.cue.name ?? "center",
    speaking,
    interrupted: scheduler.active.override?.cue.name === "interrupt",
    mouthOpen: speaking ? sampledMouthOpen : 0,
    procedural,
    activeCues,
  };
}

function behaviorInput(runtime: AvatarRuntimeState) {
  return {
    state: runtime.state,
    expression: runtime.expression,
    gaze: runtime.gaze,
    speaking: runtime.speaking,
    interrupted: runtime.interrupted,
    speechEnergy: runtime.mouthOpen,
  };
}
