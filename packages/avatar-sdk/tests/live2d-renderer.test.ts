import { describe, expect, it, vi } from "vitest";

import { LIVE2D_LAB_MANIFEST } from "../src/default-manifest";
import { neutralProceduralFrame } from "../src/behavior-state-machine";
import type {
  CubismBridgeHit,
  OfficialCubismBridge,
} from "../src/live2d/live2d-model-loader";
import { Live2DModelLoader } from "../src/live2d/live2d-model-loader";
import { Live2DAvatarRenderer } from "../src/live2d/live2d-renderer";
import type { AvatarManifest, AvatarRuntimeState } from "../src/types";
import { cue } from "./fixtures";

class StubBridge implements OfficialCubismBridge {
  readonly deltas: number[] = [];
  readonly motions: string[] = [];
  readonly expressions: Array<[string, number]> = [];
  readonly gazes: string[] = [];
  readonly mouthValues: number[] = [];
  readonly proceduralFrames: AvatarRuntimeState["procedural"][] = [];
  readonly sizes: Array<[number, number, number]> = [];
  draws = 0;
  unloaded = false;
  disposed = false;
  stoppedMotions = 0;
  private motionComplete: (() => void) | null = null;

  async load(): Promise<void> {}

  update(deltaSeconds: number): void {
    this.deltas.push(deltaSeconds);
  }

  draw(): void {
    this.draws += 1;
  }

  playMotion(name: string, _priority: number, onComplete: () => void): void {
    this.motions.push(name);
    this.motionComplete = onComplete;
  }

  stopMotion(): void {
    this.stoppedMotions += 1;
  }

  setExpression(name: string, intensity: number): void {
    this.expressions.push([name, intensity]);
  }

  setGaze(target: string): void {
    this.gazes.push(target);
  }

  setMouthOpen(value: number): void {
    this.mouthValues.push(value);
  }

  setProceduralParameters(frame: AvatarRuntimeState["procedural"]): void {
    this.proceduralFrames.push(frame);
  }

  hitTest(): CubismBridgeHit[] {
    return [{ areaId: "head", modelX: 0.1, modelY: 0.2 }];
  }

  resize(width: number, height: number, dpr: number): void {
    this.sizes.push([width, height, dpr]);
  }

  async unload(): Promise<void> {
    this.unloaded = true;
  }

  dispose(): void {
    this.disposed = true;
  }

  resourceCount(): number {
    return 4;
  }

  completeMotion(): void {
    this.motionComplete?.();
  }
}

class StubLoader extends Live2DModelLoader {
  constructor(private readonly bridge: OfficialCubismBridge) {
    super();
  }

  override async load(
    canvas: HTMLCanvasElement,
    manifest: AvatarManifest,
  ): Promise<OfficialCubismBridge> {
    void canvas;
    void manifest;
    return this.bridge;
  }
}

function stubCanvas(): HTMLCanvasElement {
  return {
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as HTMLCanvasElement;
}

describe("Live2DAvatarRenderer adapter", () => {
  it("maps semantic state to the bridge and maps hits back to semantic events", async () => {
    const bridge = new StubBridge();
    const onMotionEnded = vi.fn();
    const renderer = new Live2DAvatarRenderer(stubCanvas(), {
      modelLoader: new StubLoader(bridge),
      onMotionEnded,
    });
    renderer.resize(800, 600, 2);
    await renderer.load(LIVE2D_LAB_MANIFEST);

    const motion = cue("motion", "headpat");
    const expression = cue("expression", "happy", { intensity: 0.7 });
    const state: AvatarRuntimeState = {
      revision: 1,
      state: "speaking",
      expression: "happy",
      motion: "headpat",
      gaze: "pointer",
      speaking: true,
      interrupted: false,
      mouthOpen: 0.6,
      procedural: {
        ...neutralProceduralFrame("speaking"),
        headPitch: 0.2,
      },
      activeCues: { gesture: motion, emotion: expression },
    };

    renderer.render(state, 100);
    renderer.render(state, 116);
    bridge.completeMotion();

    expect(bridge.sizes).toEqual([[800, 600, 2]]);
    expect(bridge.motions).toEqual(["headpat"]);
    expect(bridge.expressions).toEqual([["happy", 0.7]]);
    expect(bridge.gazes).toEqual(["pointer"]);
    expect(bridge.mouthValues).toEqual([0.6]);
    expect(bridge.proceduralFrames).toHaveLength(2);
    expect(bridge.proceduralFrames[0]).toMatchObject({
      mode: "speaking",
      headPitch: 0.2,
    });
    expect(bridge.deltas).toEqual([0, 0.016]);
    expect(bridge.draws).toBe(2);
    expect(onMotionEnded).toHaveBeenCalledWith(motion.cue_id);
    expect(renderer.hitTest(10, 20)[0]).toMatchObject({
      areaId: "head",
      semanticTarget: "touched_head",
      modelX: 0.1,
      modelY: 0.2,
    });
    expect(renderer.diagnostics().resourceCount).toBe(4);

    await renderer.unload();
    expect(bridge.unloaded).toBe(true);
    expect(bridge.disposed).toBe(true);
    expect(renderer.diagnostics()).toMatchObject({
      status: "idle",
      resourceCount: 0,
    });
    renderer.dispose();
  });

  it("stops a bounded Cubism motion when its semantic cue expires", async () => {
    const bridge = new StubBridge();
    const renderer = new Live2DAvatarRenderer(stubCanvas(), {
      modelLoader: new StubLoader(bridge),
    });
    await renderer.load(LIVE2D_LAB_MANIFEST);
    const motion = cue("motion", "sing");
    const active: AvatarRuntimeState = {
      revision: 1,
      state: "idle",
      expression: "neutral",
      motion: "sing",
      gaze: "center",
      speaking: false,
      interrupted: false,
      mouthOpen: 0,
      procedural: neutralProceduralFrame(),
      activeCues: { gesture: motion },
    };
    renderer.render(active, 0);
    renderer.render({ ...active, motion: null, activeCues: {} }, 1_000);

    expect(bridge.motions).toEqual(["sing"]);
    expect(bridge.stoppedMotions).toBe(1);
    renderer.dispose();
  });

  it("releases a bridge that finishes loading after the renderer is disposed", async () => {
    const bridge = new StubBridge();
    let finishLoading: (value: OfficialCubismBridge) => void = () => undefined;
    const loading = new Promise<OfficialCubismBridge>((resolve) => {
      finishLoading = resolve;
    });
    const loader = new (class extends Live2DModelLoader {
      override async load(): Promise<OfficialCubismBridge> {
        return loading;
      }
    })();
    const renderer = new Live2DAvatarRenderer(stubCanvas(), {
      modelLoader: loader,
    });

    const pending = renderer.load(LIVE2D_LAB_MANIFEST);
    renderer.dispose();
    finishLoading(bridge);

    await expect(pending).rejects.toThrow("disposed during model loading");
    expect(bridge.disposed).toBe(true);
    expect(renderer.diagnostics().status).toBe("disposed");
  });
});
