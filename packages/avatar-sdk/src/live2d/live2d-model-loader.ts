import { AvatarRendererError } from "../renderer";
import type {
  AvatarManifest,
  AvatarProceduralFrame,
  Live2DSemanticMapping,
} from "../types";

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
  load(
    modelJsonUrl: string,
    semanticMapping?: Live2DSemanticMapping,
  ): Promise<void>;
  update(deltaSeconds: number): void;
  draw(): void;
  playMotion(name: string, priority: number, onComplete: () => void): void;
  stopMotion(): void;
  setExpression(name: string, intensity: number): void;
  setGaze(target: string): void;
  setMouthOpen(value: number): void;
  setProceduralParameters(frame: AvatarProceduralFrame): void;
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

const BRIDGE_REGISTRY_KEY = "__chatwaifuCubismBridgeModule";
const bridgeModuleLoads = new Map<string, Promise<void>>();

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
    try {
      await bridge.load(source.modelJsonUrl, source.semanticMapping);
      return bridge;
    } catch (error: unknown) {
      bridge.dispose();
      throw error;
    }
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
  const registered = Reflect.get(globalThis, BRIDGE_REGISTRY_KEY) as unknown;
  if (isCubismBridgeModule(registered)) return registered;
  if (typeof document === "undefined") throw bridgeMissingError(moduleUrl);

  let pending = bridgeModuleLoads.get(moduleUrl);
  if (!pending) {
    pending = new Promise<void>((resolve, reject) => {
      const script = document.createElement("script");
      script.type = "module";
      script.src = moduleUrl;
      script.dataset.chatwaifuLive2dBridge = moduleUrl;
      script.addEventListener("load", () => resolve(), { once: true });
      script.addEventListener(
        "error",
        () => {
          script.remove();
          reject(bridgeMissingError(moduleUrl));
        },
        { once: true },
      );
      document.head.append(script);
    });
    bridgeModuleLoads.set(moduleUrl, pending);
  }

  try {
    await pending;
  } catch (error: unknown) {
    bridgeModuleLoads.delete(moduleUrl);
    if (error instanceof AvatarRendererError) throw error;
    throw new AvatarRendererError(
      "avatar.live2d_bridge_missing",
      `The official Cubism bridge could not be loaded from ${moduleUrl}.`,
      "Run make setup-live2d-framework, build the vendor bridge, and reload Avatar Lab.",
      { cause: error },
    );
  }
  const imported = Reflect.get(globalThis, BRIDGE_REGISTRY_KEY) as unknown;
  if (!isCubismBridgeModule(imported)) {
    throw new AvatarRendererError(
      "avatar.live2d_bridge_invalid",
      "The vendor bridge does not export createChatWaifuCubismBridge().",
      "Rebuild the bridge against the pinned official Cubism Framework version.",
    );
  }
  return imported;
}

function bridgeMissingError(moduleUrl: string): AvatarRendererError {
  return new AvatarRendererError(
    "avatar.live2d_bridge_missing",
    `The official Cubism bridge could not be loaded from ${moduleUrl}.`,
    "Run make setup-live2d-vendor, then reload Avatar Lab.",
  );
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
