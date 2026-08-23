# Live2D vendor boundary

ChatWaifu NEXT pins the public **Live2D Cubism Web Framework 5-r.5**. Cubism Core,
official sample sources, generated bridge output, and character models remain in ignored local
directories because their distribution terms require a separate release review.

## Install from the official Web SDK

The default command detects the extracted SDK at
`~/Downloads/CubismSdkForWeb-5-r.5`, installs the official Natori sample as the local test model,
builds the ChatWaifu bridge, and checks every required artifact:

```bash
make setup-live2d-vendor
```

Use an explicit SDK path or another official sample model when needed:

```bash
make setup-live2d-vendor \
  LIVE2D_SETUP_ARGS="--sdk-dir /absolute/path/to/CubismSdkForWeb-5-r.5 --model Natori"
```

The installer performs these local-only steps:

- clones and verifies the official Framework `5-r.5` checkout;
- copies the redistributable Web Core file into `apps/web/public/vendor/live2d/`;
- copies the official Demo sources needed to build the adapter;
- builds `chatwaifu-live2d-bridge.js` against the pinned Framework;
- copies the selected sample model and aliases its model file to `avatar.model3.json`.

All generated/vendor paths are ignored by Git. Do not force-add the SDK, Core, sample source,
generated bridge, or model files to this repository.

## Architecture boundary

The browser bridge exports:

```ts
createChatWaifuCubismBridge({ canvas, frameworkVersion });
```

The bridge is the only layer allowed to know Cubism parameter IDs, motion groups, expression files,
textures and WebGL model objects. Product and React code continue to use semantic `AvatarCue` values.

## Verify and run

```bash
make check-live2d-vendor
make demo
```

The main chat selects Live2D automatically when the local vendor set is ready and remounts a safe
Fake renderer when it is unavailable. `/avatar-lab` can exercise both renderers explicitly.

Natori is an official SDK sample model used here only for local testing. Its files retain Live2D's
sample-model and Free Material License terms. Review the current SDK license files before any
redistribution or commercial release.

Official sources:

- <https://github.com/Live2D/CubismWebFramework/releases/tag/5-r.5>
- <https://docs.live2d.com/en/cubism-sdk-manual/cubism-sdk-for-web/>
- <https://www.live2d.com/download/cubism-sdk/download-web/>
