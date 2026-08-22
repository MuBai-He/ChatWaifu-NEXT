import type { AvatarHitResult, AvatarManifest } from "../types";
import type { CubismBridgeHit } from "./live2d-model-loader";

export function mapLive2DHits(
  hits: CubismBridgeHit[],
  manifest: AvatarManifest,
): AvatarHitResult[] {
  return hits.map((hit) => ({
    areaId: hit.areaId,
    semanticTarget:
      manifest.hitAreas?.find((area) => area.id === hit.areaId)
        ?.semanticTarget ?? `touched_${hit.areaId.toLowerCase()}`,
    confidence: hit.confidence ?? 1,
    modelX: hit.modelX,
    modelY: hit.modelY,
  }));
}
