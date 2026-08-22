import type { AvatarManifest } from "./types";

export const AVATAR_LAB_MANIFEST: AvatarManifest = {
  avatarId: "avatar-lab",
  displayName: "Avatar Lab",
  rendererKind: "fake",
  capabilities: {
    avatar_id: "avatar-lab",
    renderer_kind: "fake",
    states: ["idle", "listening", "thinking", "speaking"],
    expressions: ["neutral", "happy", "curious"],
    motions: ["nod", "wave"],
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
  },
};
