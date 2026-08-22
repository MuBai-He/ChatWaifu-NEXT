import type { AvatarCue } from "@chatwaifu/protocol";

import type {
  AvatarHitResult,
  AvatarManifest,
  AvatarRendererDiagnostics,
  AvatarRuntimeState,
  AvatarWarning,
} from "./types";

export interface AvatarRenderer {
  readonly kind: "fake" | "live2d";

  load(manifest: AvatarManifest): Promise<void>;
  unload(): Promise<void>;
  render(state: AvatarRuntimeState, nowMs: number): void;
  hitTest(x: number, y: number): AvatarHitResult[];
  resize(width: number, height: number, dpr: number): void;
  diagnostics(): AvatarRendererDiagnostics;
  dispose(): void;
}

export interface AvatarRendererFactory {
  create(kind: AvatarManifest["rendererKind"]): AvatarRenderer;
}

export class AvatarRendererError extends Error {
  readonly code: string;
  readonly action: string;

  constructor(
    code: string,
    message: string,
    action: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "AvatarRendererError";
    this.code = code;
    this.action = action;
  }

  toWarning(cue?: AvatarCue): AvatarWarning {
    return {
      code: this.code,
      message: this.message,
      cueId: cue?.cue_id,
      action: this.action,
    };
  }
}
