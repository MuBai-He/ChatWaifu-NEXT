import type { AvatarCue } from "@chatwaifu/protocol";

import type { OfficialCubismBridge } from "./live2d-model-loader";

export class Live2DMotionLayer {
  private activeCueId: string | null = null;

  constructor(
    private readonly bridge: OfficialCubismBridge,
    private readonly onMotionEnded?: (cueId: string) => void,
  ) {}

  apply(cue: AvatarCue | undefined): void {
    if (!cue || cue.cue_id === this.activeCueId) return;
    this.activeCueId = cue.cue_id;
    this.bridge.playMotion(cue.name, cue.priority ?? 50, () => {
      if (this.activeCueId !== cue.cue_id) return;
      this.activeCueId = null;
      this.onMotionEnded?.(cue.cue_id);
    });
  }

  reset(): void {
    this.activeCueId = null;
  }
}
