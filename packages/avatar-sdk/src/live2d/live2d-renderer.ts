import type { AvatarRenderer } from "../renderer";
import type {
  AvatarHitResult,
  AvatarManifest,
  AvatarRendererDiagnostics,
  AvatarRuntimeState,
  AvatarWarning,
} from "../types";
import { Live2DExpressionMixer } from "./live2d-expression-mixer";
import { Live2DGazeController } from "./live2d-gaze-controller";
import { mapLive2DHits } from "./live2d-hit-test";
import { Live2DLipSyncDriver } from "./live2d-lip-sync-driver";
import {
  Live2DModelLoader,
  type OfficialCubismBridge,
} from "./live2d-model-loader";
import { Live2DMotionLayer } from "./live2d-motion-layer";

export interface Live2DAvatarRendererOptions {
  modelLoader?: Live2DModelLoader;
  onMotionEnded?: (cueId: string) => void;
  onWarning?: (warning: AvatarWarning) => void;
}

export class Live2DAvatarRenderer implements AvatarRenderer {
  readonly kind = "live2d" as const;
  private readonly modelLoader: Live2DModelLoader;
  private readonly onMotionEnded?: (cueId: string) => void;
  private readonly onWarning?: (warning: AvatarWarning) => void;
  private status: AvatarRendererDiagnostics["status"] = "idle";
  private bridge: OfficialCubismBridge | null = null;
  private manifest: AvatarManifest | null = null;
  private motionLayer: Live2DMotionLayer | null = null;
  private expressionMixer: Live2DExpressionMixer | null = null;
  private gazeController: Live2DGazeController | null = null;
  private lipSyncDriver: Live2DLipSyncDriver | null = null;
  private contextLosses = 0;
  private lastFrameAt: number | null = null;
  private lastError: AvatarWarning | undefined;
  private requestedSize: [number, number, number] | null = null;
  private disposed = false;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    options: Live2DAvatarRendererOptions = {},
  ) {
    this.modelLoader = options.modelLoader ?? new Live2DModelLoader();
    this.onMotionEnded = options.onMotionEnded;
    this.onWarning = options.onWarning;
    canvas.addEventListener("webglcontextlost", this.handleContextLost);
    canvas.addEventListener("webglcontextrestored", this.handleContextRestored);
  }

  async load(manifest: AvatarManifest): Promise<void> {
    if (this.disposed) throw new Error("Live2D renderer is disposed.");
    this.status = "loading";
    this.manifest = manifest;
    try {
      const bridge = await this.modelLoader.load(this.canvas, manifest);
      if (this.disposed) {
        bridge.dispose();
        throw new Error("Live2D renderer was disposed during model loading.");
      }
      this.bridge = bridge;
      if (this.requestedSize) this.bridge.resize(...this.requestedSize);
      this.motionLayer = new Live2DMotionLayer(this.bridge, this.onMotionEnded);
      this.expressionMixer = new Live2DExpressionMixer(this.bridge);
      this.gazeController = new Live2DGazeController(this.bridge);
      this.lipSyncDriver = new Live2DLipSyncDriver(this.bridge);
      this.lastFrameAt = null;
      this.status = "ready";
    } catch (error: unknown) {
      if (!this.disposed) this.status = "error";
      throw error;
    }
  }

  async unload(): Promise<void> {
    this.lipSyncDriver?.reset();
    this.motionLayer?.reset();
    await this.bridge?.unload();
    this.bridge?.dispose();
    this.clearBridge();
    this.status = "idle";
  }

  render(state: AvatarRuntimeState, nowMs: number): void {
    if (this.status !== "ready" || !this.bridge) return;
    this.motionLayer?.apply(state.activeCues.gesture);
    this.expressionMixer?.apply(state.activeCues.emotion);
    this.gazeController?.apply(state.gaze);
    this.lipSyncDriver?.apply(state.mouthOpen);
    const deltaSeconds =
      this.lastFrameAt === null
        ? 0
        : Math.max(0, nowMs - this.lastFrameAt) / 1000;
    this.lastFrameAt = nowMs;
    this.bridge.update(deltaSeconds);
    this.bridge.draw();
  }

  hitTest(x: number, y: number): AvatarHitResult[] {
    if (!this.bridge || !this.manifest) return [];
    return mapLive2DHits(this.bridge.hitTest(x, y), this.manifest);
  }

  resize(width: number, height: number, dpr: number): void {
    this.requestedSize = [width, height, dpr];
    this.bridge?.resize(width, height, dpr);
  }

  diagnostics(): AvatarRendererDiagnostics {
    return {
      status: this.status,
      resourceCount: this.bridge?.resourceCount() ?? 0,
      contextLosses: this.contextLosses,
      lastError: this.lastError,
    };
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.lipSyncDriver?.reset();
    this.bridge?.dispose();
    this.clearBridge();
    this.canvas.removeEventListener("webglcontextlost", this.handleContextLost);
    this.canvas.removeEventListener(
      "webglcontextrestored",
      this.handleContextRestored,
    );
    this.status = "disposed";
  }

  private readonly handleContextLost = (event: Event): void => {
    event.preventDefault();
    this.contextLosses += 1;
    this.lastError = {
      code: "avatar.webgl_context_lost",
      message: "The Live2D WebGL context was lost.",
      action: "Wait for context restoration or reload the avatar.",
    };
    this.onWarning?.(this.lastError);
  };

  private readonly handleContextRestored = (): void => {
    this.lastError = undefined;
  };

  private clearBridge(): void {
    this.bridge = null;
    this.manifest = null;
    this.motionLayer = null;
    this.expressionMixer = null;
    this.gazeController = null;
    this.lipSyncDriver = null;
    this.lastFrameAt = null;
  }
}
