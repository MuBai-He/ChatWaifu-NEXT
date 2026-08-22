import { clamp01 } from "../lip-sync";
import type { OfficialCubismBridge } from "./live2d-model-loader";

export class Live2DLipSyncDriver {
  private previous = 0;

  constructor(private readonly bridge: OfficialCubismBridge) {}

  apply(value: number): void {
    const bounded = clamp01(value);
    if (Math.abs(bounded - this.previous) < 0.002) return;
    this.previous = bounded;
    this.bridge.setMouthOpen(bounded);
  }

  reset(): void {
    this.previous = 0;
    this.bridge.setMouthOpen(0);
  }
}
