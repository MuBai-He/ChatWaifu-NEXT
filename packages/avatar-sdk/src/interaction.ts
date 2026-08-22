import type { AvatarInteractionEvent } from "@chatwaifu/protocol";

import type { AvatarHitResult, AvatarManifest } from "./types";

export function interactionFromHit(
  manifest: AvatarManifest,
  hit: AvatarHitResult,
): AvatarInteractionEvent {
  return {
    interaction_id: crypto.randomUUID(),
    avatar_id: manifest.avatarId,
    kind: "touch",
    target: hit.semanticTarget,
    x: hit.modelX,
    y: hit.modelY,
    metadata: {
      area_id: hit.areaId,
      confidence: hit.confidence,
    },
  };
}
