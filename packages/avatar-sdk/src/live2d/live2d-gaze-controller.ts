import type { OfficialCubismBridge } from "./live2d-model-loader";

export class Live2DGazeController {
  private activeTarget = "center";

  constructor(private readonly bridge: OfficialCubismBridge) {}

  apply(target: string): void {
    if (target === this.activeTarget) return;
    this.activeTarget = target;
    this.bridge.setGaze(target);
  }
}
