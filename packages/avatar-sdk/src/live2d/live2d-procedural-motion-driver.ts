import { neutralProceduralFrame } from "../behavior-state-machine";
import type { AvatarProceduralFrame } from "../types";
import type { OfficialCubismBridge } from "./live2d-model-loader";

export class Live2DProceduralMotionDriver {
  constructor(private readonly bridge: OfficialCubismBridge) {}

  apply(frame: AvatarProceduralFrame): void {
    this.bridge.setProceduralParameters(frame);
  }

  reset(): void {
    this.bridge.setProceduralParameters(neutralProceduralFrame());
  }
}
