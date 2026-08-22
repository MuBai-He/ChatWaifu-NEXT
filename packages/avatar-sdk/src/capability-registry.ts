import type { AvatarCapabilityManifest, AvatarCue } from "@chatwaifu/protocol";

import type { AvatarLayer, AvatarWarning } from "./types";

const CUE_LAYERS: Record<AvatarCue["kind"], AvatarLayer> = {
  state: "attention",
  expression: "emotion",
  motion: "gesture",
  gaze: "gaze",
  speech: "speech",
  override: "override",
};

export interface CapabilityResolution {
  cue: AvatarCue;
  layer: AvatarLayer;
  warning?: AvatarWarning;
}

export class AvatarCapabilityRegistry {
  readonly manifest: AvatarCapabilityManifest;

  private readonly states: Set<string>;
  private readonly expressions: Set<string>;
  private readonly motions: Set<string>;
  private readonly gazeTargets: Set<string>;

  constructor(manifest: AvatarCapabilityManifest) {
    this.manifest = manifest;
    this.states = new Set(manifest.states ?? []);
    this.expressions = new Set(manifest.expressions ?? []);
    this.motions = new Set(manifest.motions ?? []);
    this.gazeTargets = new Set(manifest.gaze_targets ?? []);
  }

  resolve(cue: AvatarCue): CapabilityResolution {
    if (this.supports(cue)) {
      return { cue, layer: CUE_LAYERS[cue.kind] };
    }

    const fallback: AvatarCue = {
      ...cue,
      kind: "state",
      name: this.states.has("idle") ? "idle" : "neutral",
      duration_ms: cue.duration_ms ?? 800,
    };
    return {
      cue: fallback,
      layer: "attention",
      warning: {
        code: "avatar.capability_missing",
        message: `Cue ${cue.kind}:${cue.name} is unavailable; falling back to ${fallback.name}.`,
        cueId: cue.cue_id,
      },
    };
  }

  private supports(cue: AvatarCue): boolean {
    switch (cue.kind) {
      case "state":
        return this.states.has(cue.name);
      case "expression":
        return this.expressions.has(cue.name);
      case "motion":
        return this.motions.has(cue.name);
      case "gaze":
        return this.gazeTargets.has(cue.name);
      case "speech":
        return (
          cue.name === "speaking" && Boolean(this.manifest.supports_lipsync)
        );
      case "override":
        return cue.name === "interrupt";
    }
  }
}

export function cueLayer(cue: AvatarCue): AvatarLayer {
  return CUE_LAYERS[cue.kind];
}
