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
      parameters: {
        headYaw: { id: "ParamAngleX", blend: "add", scale: 16 },
        headPitch: { id: "ParamAngleY", blend: "add", scale: 12 },
        headRoll: { id: "ParamAngleZ", blend: "add", scale: 9 },
        bodyYaw: { id: "ParamBodyAngleX", blend: "add", scale: 6 },
        bodyPitch: { id: "ParamBodyAngleY", blend: "add", scale: 4 },
        bodyRoll: { id: "ParamBodyAngleZ", blend: "add", scale: 4 },
        eyeX: { id: "ParamEyeBallX", blend: "add", scale: 0.7 },
        eyeY: { id: "ParamEyeBallY", blend: "add", scale: 0.55 },
        eyeOpen: [
          { id: "ParamEyeLOpen", blend: "multiply" },
          { id: "ParamEyeROpen", blend: "multiply" },
        ],
        browLift: [
          { id: "ParamBrowLY", blend: "add", scale: 0.45 },
          { id: "ParamBrowRY", blend: "add", scale: 0.45 },
        ],
        mouthForm: { id: "ParamMouthForm", blend: "add", scale: 0.35 },
        breath: { id: "ParamBreath", blend: "add", scale: 0.18 },
      },
    },
  },
};
