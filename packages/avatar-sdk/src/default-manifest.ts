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
    hit_areas: ["head", "body", "avatar"],
    supports_lipsync: true,
  },
  hitAreas: [
    { id: "head", semanticTarget: "touched_head" },
    { id: "body", semanticTarget: "touched_body" },
    { id: "avatar", semanticTarget: "touched_avatar" },
  ],
};

export const LIVE2D_LAB_MANIFEST: AvatarManifest = {
  ...AVATAR_LAB_MANIFEST,
  avatarId: "ayachi-nene-local",
  displayName: "绫地宁宁 · 本地 Live2D",
  rendererKind: "live2d",
  attribution: {
    modelAuthor: "涂抹一画",
    sourceLabel: "[Live2D模型免费分享] 拥有全服装的Live2D宁宁！",
    sourceUrl: "https://www.bilibili.com/video/BV1MLgYzmEz9",
    rightsNotice: "绫地宁宁与《サノバウィッチ》相关权利归 YUZUSOFT/JUNOS",
    usageNotice: "仅限私人研究与本机验证，不属于可公开分发资产",
  },
  capabilities: {
    ...AVATAR_LAB_MANIFEST.capabilities,
    avatar_id: "ayachi-nene-local",
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
