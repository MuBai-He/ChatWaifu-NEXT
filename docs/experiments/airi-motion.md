# AIRI motion experiment

This opt-in experiment was originally developed from `899402e` in the isolated
`codex/airi-motion-experiment` branch. It is now integrated against the current
mainline avatar interfaces without advancing Phase 17 or enabling desktop-pet
motion by default.

## Try it

Run from this checkout:

```sh
pnpm install --frozen-lockfile
pnpm --filter @chatwaifu/web exec vite --mode web --host 127.0.0.1 --port 5187 --strictPort
```

Open <http://127.0.0.1:5187/avatar-lab?motion=airi>. It selects the official Live2D
renderer and calm AIRI motion at 65% intensity. The local licensed Nene/Core/bridge
assets must exist under `apps/web/public/vendor/live2d`; these assets are not
committed. Licensed assets stay outside Git. The lab can run without Runtime, model workers
or external channels.

Compare original, calm and excited motion. Change intensity with the slider.
Use **Sine envelope** to test synthetic speaking, **headpat** for gesture ownership,
**interrupt** for override priority, and **Stop / neutral** to end synthetic speech.
**从头播放** resets cues and the generator to its deterministic seed.

The algorithm and datasets are lazy-loaded only for the lab experiment. See
[source and adaptation notes](../../packages/avatar-sdk/src/experimental/airi-motion/README.md)
and the adjacent MIT license. This is six-axis VAR, not AIRI's full avatar stack
or AR-HMM. Nothing is enabled in the desktop pet by default.

## Acceptance

- Avatar SDK: deterministic replay, five-minute finite/bounded runs of both
  profiles, 30/60 FPS equivalence, background catchup, suspension and release,
  preservation of face channels, controller gesture/interrupt/disposal integration.
- Web: late renderer load rejection cannot overwrite the replacement renderer;
  active load failures still display normally.
- Historical acceptance in the original experiment checkout: Nene rendered through the official bridge; both profiles,
  original mode, 100% intensity, synthetic speaking, headpat and interruption were
  exercised. Startup emitted Cubism shader initialization warnings; no persistent
  load-error banner remained after the lifecycle fix. This does not measure real
  conversational speech or native desktop performance.

The user should judge whether the movement suits Nene. Retain this as an optional
experiment until visual preference and longer interaction are accepted.
