import type { AvatarCue } from "@chatwaifu/protocol";

import type { OfficialCubismBridge } from "./live2d-model-loader";

export class Live2DExpressionMixer {
  private activeName = "neutral";
  private activeIntensity = 0;

  constructor(private readonly bridge: OfficialCubismBridge) {}

  apply(cue: AvatarCue | undefined): void {
    const name = cue?.name ?? "neutral";
    const intensity = cue?.intensity ?? 1;
    if (name === this.activeName && intensity === this.activeIntensity) return;
    this.activeName = name;
    this.activeIntensity = intensity;
    this.bridge.setExpression(name, intensity);
  }
}
