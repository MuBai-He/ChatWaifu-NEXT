import type { AvatarCue } from "@chatwaifu/protocol";

import type { OfficialCubismBridge } from "./live2d-model-loader";

export class Live2DMotionLayer {
  private activeCueId: string | null = null;

  constructor(
    private readonly bridge: OfficialCubismBridge,
    private readonly onMotionEnded?: (cueId: string) => void,
  ) {}

  apply(cue: AvatarCue | undefined): void {
    if (!cue) {
      if (this.activeCueId !== null) {
        this.activeCueId = null;
        this.bridge.stopMotion();
      }
      return;
    }
    if (cue.cue_id === this.activeCueId) return;
    if (this.activeCueId !== null) this.bridge.stopMotion();
    this.activeCueId = cue.cue_id;
    this.bridge.playMotion(cue.name, cue.priority ?? 50, () => {
      if (this.activeCueId !== cue.cue_id) return;
      this.activeCueId = null;
      this.onMotionEnded?.(cue.cue_id);
    });
  }

  reset(): void {
    if (this.activeCueId !== null) this.bridge.stopMotion();
    this.activeCueId = null;
  }
}
