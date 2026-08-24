import type { AvatarManifest } from "./types";

export const AVATAR_LAB_MANIFEST: AvatarManifest = {
  avatarId: "avatar-lab",
  displayName: "Avatar Lab",
  rendererKind: "fake",
  capabilities: {
    avatar_id: "avatar-lab",
    renderer_kind: "fake",
    states: ["idle", "listening", "thinking", "speaking"],
    expressions: [
      "neutral",
      "happy",
      "curious",
      "shy",
      "sad",
      "angry",
      "surprised",
    ],
    motions: ["headpat", "stare", "flustered", "sing"],
    gaze_targets: ["center", "pointer"],
    hit_areas: ["head", "body"],
    supports_lipsync: true,
  },
  hitAreas: [
    { id: "head", semanticTarget: "touched_head" },
    { id: "body", semanticTarget: "touched_body" },
  ],
};

export const LIVE2D_LAB_MANIFEST: AvatarManifest = {
  ...AVATAR_LAB_MANIFEST,
  rendererKind: "live2d",
  capabilities: {
    ...AVATAR_LAB_MANIFEST.capabilities,
    renderer_kind: "live2d",
  },
  live2d: {
    modelJsonUrl: "/vendor/live2d/model/avatar.model3.json",
    coreScriptUrl: "/vendor/live2d/live2dcubismcore.min.js",
    bridgeModuleUrl: "/vendor/live2d/chatwaifu-live2d-bridge.js",
    semanticMapping: {
      expressions: {
        neutral: "Neutral",
        happy: "Happy",
        curious: "Curious",
        shy: "Shy",
        sad: "Sad",
        angry: "Angry",
        surprised: "Surprised",
      },
      motions: {
        headpat: { group: "ChatWaifuAction", index: 0 },
        stare: { group: "ChatWaifuAction", index: 1 },
        flustered: { group: "ChatWaifuAction", index: 2 },
        sing: { group: "ChatWaifuAction", index: 3 },
      },
    },
  },
};
