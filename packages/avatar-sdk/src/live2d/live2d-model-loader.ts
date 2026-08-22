import { AvatarRendererError } from "../renderer";
import type { AvatarManifest } from "../types";

export const LIVE2D_FRAMEWORK_VERSION = "5-r.5";
export const LIVE2D_FRAMEWORK_REPOSITORY =
  "https://github.com/Live2D/CubismWebFramework";

export interface CubismBridgeHit {
  areaId: string;
  confidence?: number;
  modelX: number;
  modelY: number;
}

export interface OfficialCubismBridge {
  load(modelJsonUrl: string): Promise<void>;
  update(deltaSeconds: number): void;
  draw(): void;
  playMotion(name: string, priority: number, onComplete: () => void): void;
  setExpression(name: string, intensity: number): void;
  setGaze(target: string): void;
  setMouthOpen(value: number): void;
  hitTest(x: number, y: number): CubismBridgeHit[];
  resize(width: number, height: number, dpr: number): void;
  unload(): Promise<void>;
  dispose(): void;
  resourceCount(): number;
}

interface CubismBridgeModule {
  createChatWaifuCubismBridge(options: {
    canvas: HTMLCanvasElement;
    frameworkVersion: string;
  }): OfficialCubismBridge;
}

export class Live2DModelLoader {
  async load(
    canvas: HTMLCanvasElement,
    manifest: AvatarManifest,
  ): Promise<OfficialCubismBridge> {
    const source = manifest.live2d;
    if (!source) {
      throw new AvatarRendererError(
        "avatar.live2d_manifest_missing",
        "The avatar manifest does not define a Live2D model source.",
        "Add live2d.modelJsonUrl and vendor URLs to the avatar manifest.",
      );
    }

    await ensureCubismCore(
      source.coreScriptUrl ?? "/vendor/live2d/live2dcubismcore.min.js",
    );
    const module = await importBridgeModule(
      source.bridgeModuleUrl ?? "/vendor/live2d/chatwaifu-live2d-bridge.js",
    );
    const bridge = module.createChatWaifuCubismBridge({
      canvas,
      frameworkVersion: LIVE2D_FRAMEWORK_VERSION,
    });
    await bridge.load(source.modelJsonUrl);
    return bridge;
  }
}

async function ensureCubismCore(scriptUrl: string): Promise<void> {
  if (Reflect.has(globalThis, "Live2DCubismCore")) return;
  if (typeof document === "undefined") {
    throw coreMissingError(scriptUrl);
  }

  await new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[data-chatwaifu-live2d-core="${CSS.escape(scriptUrl)}"]`,
    );
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(coreMissingError(scriptUrl)),
        {
          once: true,
        },
      );
      return;
    }

    const script = document.createElement("script");
    script.src = scriptUrl;
    script.async = true;
    script.dataset.chatwaifuLive2dCore = scriptUrl;
    script.addEventListener("load", () => resolve(), { once: true });
    script.addEventListener(
      "error",
      () => {
        script.remove();
        reject(coreMissingError(scriptUrl));
      },
      { once: true },
    );
    document.head.append(script);
  });

  if (!Reflect.has(globalThis, "Live2DCubismCore"))
    throw coreMissingError(scriptUrl);
}

async function importBridgeModule(
  moduleUrl: string,
): Promise<CubismBridgeModule> {
  let imported: unknown;
  try {
    imported = (await import(/* @vite-ignore */ moduleUrl)) as unknown;
  } catch (error: unknown) {
    throw new AvatarRendererError(
      "avatar.live2d_bridge_missing",
      `The official Cubism bridge could not be loaded from ${moduleUrl}.`,
      "Run make setup-live2d-framework, build the vendor bridge, and reload Avatar Lab.",
      { cause: error },
    );
  }
  if (!isCubismBridgeModule(imported)) {
    throw new AvatarRendererError(
      "avatar.live2d_bridge_invalid",
      "The vendor bridge does not export createChatWaifuCubismBridge().",
      "Rebuild the bridge against the pinned official Cubism Framework version.",
    );
  }
  return imported;
}

function isCubismBridgeModule(value: unknown): value is CubismBridgeModule {
  return (
    typeof value === "object" &&
    value !== null &&
    "createChatWaifuCubismBridge" in value &&
    typeof value.createChatWaifuCubismBridge === "function"
  );
}

function coreMissingError(scriptUrl: string): AvatarRendererError {
  return new AvatarRendererError(
    "avatar.live2d_core_missing",
    `Live2D Cubism Core is unavailable at ${scriptUrl}.`,
    "Download Cubism SDK for Web from Live2D, then follow vendor/live2d/README.md.",
  );
}
