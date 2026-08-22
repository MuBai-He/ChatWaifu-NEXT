import type { AvatarCue } from "@chatwaifu/protocol";

let cueSequence = 1;

export function cue(
  kind: AvatarCue["kind"],
  name: string,
  overrides: Partial<AvatarCue> = {},
): AvatarCue {
  const suffix = String(cueSequence++).padStart(12, "0");
  return {
    cue_id: `00000000-0000-4000-8000-${suffix}`,
    kind,
    name,
    ...overrides,
  };
}
