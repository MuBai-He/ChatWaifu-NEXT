import { LAppGlManager } from "@cubismsdksamples/lappglmanager";
import { LAppModel } from "@cubismsdksamples/lappmodel";
import { LAppPal } from "@cubismsdksamples/lapppal";
import { LAppTextureManager } from "@cubismsdksamples/lapptexturemanager";
import type { CubismIdHandle } from "@framework/id/cubismid";
import {
  CubismFramework,
  LogLevel,
  Option,
} from "@framework/live2dcubismframework";
import { CubismMatrix44 } from "@framework/math/cubismmatrix44";
import { CubismWebGLOffscreenManager } from "@framework/rendering/cubismoffscreenmanager";

const FRAMEWORK_VERSION = "5-r.5";
const LOAD_TIMEOUT_MS = 20_000;
const FORCE_MOTION_PRIORITY = 3;

const DEFAULT_EXPRESSION_MAP: Record<string, string> = {
  neutral: "Normal",
  happy: "Smile",
  curious: "Surprised",
};

const DEFAULT_MOTION_MAP: Record<string, MotionTarget> = {
  headpat: { group: "TapBody", index: 0 },
  stare: { group: "TapBody", index: 1 },
};

interface MotionTarget {
  group: string;
  index: number;
}

interface SemanticMapping {
  expressions: Record<string, string>;
  motions: Record<string, MotionTarget>;
}

interface BridgeOptions {
  canvas: HTMLCanvasElement;
  frameworkVersion: string;
}

interface BridgeHit {
  areaId: string;
  confidence?: number;
  modelX: number;
  modelY: number;
}

interface ModelInternals {
  _lipSyncIds: CubismIdHandle[];
  _motionManager: { stopAllMotions(): void };
  _modelSetting: { getTextureCount(): number } | null;
  _textureCount: number;
}

class BridgeSubdelegate {
  readonly glManager = new LAppGlManager();
  readonly textureManager = new LAppTextureManager();
  private readonly frameBuffer: WebGLFramebuffer | null;

  constructor(readonly canvas: HTMLCanvasElement) {
    if (!this.glManager.initialize(canvas)) {
      throw new Error(
        "This browser could not create a WebGL2 context for Live2D.",
      );
    }
    this.textureManager.setGlManager(this.glManager);
    this.frameBuffer = this.glManager
      .getGl()
      .getParameter(
        this.glManager.getGl().FRAMEBUFFER_BINDING,
      ) as WebGLFramebuffer | null;
  }

  getCanvas(): HTMLCanvasElement {
    return this.canvas;
  }

  getGlManager(): LAppGlManager {
    return this.glManager;
  }

  getTextureManager(): LAppTextureManager {
    return this.textureManager;
  }

  getFrameBuffer(): WebGLFramebuffer | null {
    return this.frameBuffer;
  }
}

class ChatWaifuCubismBridge {
  private readonly subdelegate: BridgeSubdelegate;
  private model: LAppModel | null = null;
  private loaded = false;
  private disposed = false;
  private mouthOpen = 0;
  private gazeTarget = "center";
  private expressionMap = DEFAULT_EXPRESSION_MAP;
  private motionMap = DEFAULT_MOTION_MAP;

  constructor(private readonly canvas: HTMLCanvasElement) {
    acquireFramework();
    try {
      this.subdelegate = new BridgeSubdelegate(canvas);
    } catch (error: unknown) {
      releaseFramework();
      throw error;
    }
  }

  async load(
    modelJsonUrl: string,
    semanticMapping?: SemanticMapping,
  ): Promise<void> {
    this.assertUsable();
    if (this.model) await this.unloadModel();

    const modelUrl = new URL(modelJsonUrl, window.location.href);
    const fileName = modelUrl.pathname.split("/").pop();
    if (!fileName) throw new Error(`Invalid Live2D model URL: ${modelJsonUrl}`);

    const model = new LAppModel();
    model.setSubdelegate(
      this.subdelegate as unknown as Parameters<LAppModel["setSubdelegate"]>[0],
    );
    this.model = model;
    this.expressionMap = semanticMapping?.expressions ?? DEFAULT_EXPRESSION_MAP;
    this.motionMap = semanticMapping?.motions ?? DEFAULT_MOTION_MAP;
    LAppPal.updateTime();
    model.loadAssets(new URL(".", modelUrl).href, fileName);
    await waitForModel(model, LOAD_TIMEOUT_MS);
    this.loaded = true;
    this.resize(
      this.canvas.clientWidth || this.canvas.width || 420,
      this.canvas.clientHeight || this.canvas.height || 420,
      window.devicePixelRatio || 1,
    );
  }

  update(deltaSeconds: number): void {
    if (!this.loaded || !this.model) return;
    LAppPal.deltaTime = Math.min(Math.max(deltaSeconds, 0), 0.1);
    this.model.setDragging(this.gazeTarget === "pointer" ? 0.22 : 0, 0);
    this.model.update();
    const coreModel = this.model.getModel();
    const internals = this.model as unknown as ModelInternals;
    for (const parameterId of internals._lipSyncIds) {
      coreModel.setParameterValueById(parameterId, this.mouthOpen);
    }
    coreModel.update();
  }

  draw(): void {
    if (!this.loaded || !this.model) return;
    const gl = this.subdelegate.glManager.getGl();
    const offscreen = CubismWebGLOffscreenManager.getInstance();
    const { width, height } = this.canvas;
    if (width <= 0 || height <= 0) return;

    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
    gl.clearColor(0, 0, 0, 0);
    gl.clearDepth(1);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    offscreen.beginFrameProcess(gl);
    const projection = new CubismMatrix44();
    const coreModel = this.model.getModel();
    if (coreModel.getCanvasWidth() > 1 && width < height) {
      this.model.getModelMatrix().setWidth(2);
      projection.scale(1, width / height);
    } else {
      projection.scale(height / width, 1);
    }
    this.model.draw(projection);
    offscreen.endFrameProcess(gl);
    offscreen.releaseStaleRenderTextures(gl);
    gl.flush();
  }

  playMotion(name: string, _priority: number, onComplete: () => void): void {
    if (!this.loaded || !this.model) return;
    const target = this.motionMap[name];
    if (target === undefined) {
      queueMicrotask(onComplete);
      return;
    }
    this.model.startMotion(
      target.group,
      target.index,
      FORCE_MOTION_PRIORITY,
      () => onComplete(),
    );
  }

  stopMotion(): void {
    if (!this.loaded || !this.model) return;
    (this.model as unknown as ModelInternals)._motionManager.stopAllMotions();
  }

  setExpression(name: string, _intensity: number): void {
    if (!this.loaded || !this.model) return;
    const fallback = this.expressionMap.neutral ?? "Normal";
    this.model.setExpression(this.expressionMap[name] ?? fallback);
  }

  setGaze(target: string): void {
    this.gazeTarget = target;
  }

  setMouthOpen(value: number): void {
    this.mouthOpen = Math.min(1, Math.max(0, value));
  }

  hitTest(x: number, y: number): BridgeHit[] {
    if (!this.loaded || !this.model) return [];
    const height = this.canvas.clientHeight || this.canvas.height || 1;
    const width = this.canvas.clientWidth || this.canvas.width || 1;
    const modelX = (2 * x - width) / height;
    const modelY = (height - 2 * y) / height;
    const hits: BridgeHit[] = [];
    if (this.model.hitTest("Head", modelX, modelY)) {
      hits.push({ areaId: "head", confidence: 1, modelX, modelY });
    }
    if (this.model.hitTest("Body", modelX, modelY)) {
      hits.push({ areaId: "body", confidence: 1, modelX, modelY });
    }
    return hits;
  }

  resize(width: number, height: number, dpr: number): void {
    const pixelWidth = Math.max(1, Math.round(width * dpr));
    const pixelHeight = Math.max(1, Math.round(height * dpr));
    if (this.canvas.width !== pixelWidth) this.canvas.width = pixelWidth;
    if (this.canvas.height !== pixelHeight) this.canvas.height = pixelHeight;
    const gl = this.subdelegate.glManager.getGl();
    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
    this.model?.setRenderTargetSize(pixelWidth, pixelHeight);
  }

  async unload(): Promise<void> {
    await this.unloadModel();
  }

  dispose(): void {
    if (this.disposed) return;
    void this.unloadModel();
    this.subdelegate.textureManager.release();
    CubismWebGLOffscreenManager.getInstance().removeContext(
      this.subdelegate.glManager.getGl(),
    );
    this.disposed = true;
    releaseFramework();
  }

  resourceCount(): number {
    if (!this.loaded) return 0;
    return 1 + this.subdelegate.textureManager._textures.length;
  }

  private async unloadModel(): Promise<void> {
    this.loaded = false;
    if (this.model) {
      this.model.release();
      this.model = null;
    }
    this.mouthOpen = 0;
    await Promise.resolve();
  }

  private assertUsable(): void {
    if (this.disposed) throw new Error("Live2D bridge is disposed.");
  }
}

let frameworkUsers = 0;

function acquireFramework(): void {
  if (frameworkUsers === 0) {
    const option = new Option();
    option.loggingLevel = LogLevel.LogLevel_Warning;
    option.logFunction = (message: string) =>
      console.warn(`[Live2D] ${message}`);
    if (!CubismFramework.startUp(option)) {
      throw new Error("Live2D Cubism Framework failed to start.");
    }
    CubismFramework.initialize();
  }
  frameworkUsers += 1;
}

function releaseFramework(): void {
  frameworkUsers = Math.max(0, frameworkUsers - 1);
  if (frameworkUsers === 0 && CubismFramework.isInitialized()) {
    CubismFramework.dispose();
  }
}

async function waitForModel(
  model: LAppModel,
  timeoutMs: number,
): Promise<void> {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    const internals = model as unknown as ModelInternals;
    const textureCount = internals._modelSetting?.getTextureCount();
    if (
      model.getModel() &&
      model.getRenderer() &&
      textureCount !== undefined &&
      internals._textureCount >= textureCount
    ) {
      return;
    }
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => resolve()),
    );
  }
  throw new Error(`Live2D model did not become ready within ${timeoutMs} ms.`);
}

export function createChatWaifuCubismBridge(
  options: BridgeOptions,
): ChatWaifuCubismBridge {
  if (options.frameworkVersion !== FRAMEWORK_VERSION) {
    throw new Error(
      `Live2D Framework mismatch: expected ${FRAMEWORK_VERSION}, got ${options.frameworkVersion}`,
    );
  }
  return new ChatWaifuCubismBridge(options.canvas);
}

Reflect.set(globalThis, "__chatwaifuCubismBridgeModule", {
  createChatWaifuCubismBridge,
});
