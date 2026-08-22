# Live2D vendor boundary

ChatWaifu NEXT pins the public **Live2D Cubism Web Framework 5-r.5**. Cubism Core,
MotionSync Core and character models are not committed because their distribution and commercial
terms require separate review.

## 1. Fetch the public Framework

```bash
make setup-live2d-framework
```

This clones the official Framework at tag `5-r.5` into
`vendor/live2d/CubismWebFramework/`. The directory is ignored by Git.

## 2. Supply Cubism Core

Download the official Cubism SDK for Web from Live2D. Copy the Web Core file to:

```text
apps/web/public/vendor/live2d/live2dcubismcore.min.js
```

Do not copy the entire SDK or proprietary license bundle into this repository.

## 3. Build the bridge

Build a browser ES module against the pinned official Framework and export:

```ts
createChatWaifuCubismBridge({ canvas, frameworkVersion });
```

The returned object must satisfy `OfficialCubismBridge` from
`@chatwaifu/avatar-sdk/live2d-model-loader`. Put the output at:

```text
apps/web/public/vendor/live2d/chatwaifu-live2d-bridge.js
```

The bridge is the only layer allowed to know Cubism parameter IDs, motion groups, expression files,
textures and WebGL model objects. Product and React code continue to use semantic `AvatarCue` values.

## 4. Supply a licensed test model

Place a model and its referenced files under:

```text
apps/web/public/vendor/live2d/model/
```

The default lab manifest expects `avatar.model3.json`. Confirm the model's redistribution terms
before committing or distributing it.

## 5. Verify

```bash
make check-live2d-vendor
make dev-avatar-lab
```

Without these files, Avatar Lab remains fully usable through `FakeAvatarRenderer`; selecting the
Live2D renderer shows an actionable `avatar.live2d_core_missing` or
`avatar.live2d_bridge_missing` error instead of crashing.

Official sources:

- <https://github.com/Live2D/CubismWebFramework/releases/tag/5-r.5>
- <https://docs.live2d.com/en/cubism-sdk-manual/cubism-sdk-for-web/>
- <https://www.live2d.com/download/cubism-sdk/download-web/>
