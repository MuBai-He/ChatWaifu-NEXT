# AIRI motion experiment

Opt-in Avatar Lab experiment for CW2. The normal character client does not import
the algorithm or its datasets. Enable it at `/avatar-lab?motion=airi`, compare the
two profiles, adjust intensity, and select the original motion to return to CW2.

## Provenance

Source: https://github.com/moeru-ai/airi/tree/836941fda819c81038f09a45c873782e27dd704e

- `vendor/`: `packages/motion-driver-magic/src/var.ts`, `types.ts`, and the four
  modules under `shared/` from that revision. Copyright 2024-PRESENT Neko Ayaka,
  MIT; full terms are in `LICENSE` alongside this file.
- `idle-calm.json` and `speaking-excited.json`: derived from the corresponding
  files in `packages/stage-ui/src/features/motions/live2d/assets/`. Only the
  `source` recording is used, sampled by linear interpolation at 30 Hz and
  rounded to six decimal places. Column order: headX/Y/Z, bodyX/Y/Z. Unused
  eye, mouth, model translation and editor tracks are omitted.
- The original VAR algorithm is retained. Local vendor changes replace the
  es-toolkit clamp with equivalent arithmetic, adapt indexed accesses to CW2's
  strict TypeScript settings, and apply repository formatting.

## Adapter

The six AIRI axes map to normalized CW2 head yaw/pitch/roll and body yaw/pitch/roll.
VAR uses order 20, ridge 0.001, seed `0x4e454e45`, and noise scale 1.15. The adapter
uses AIRI's default cutoff 0.0575 and EMA history weight 0.8, plus interpolation
between fixed-rate frames. Per-frame catchup is capped at 100 ms. Fits are cached
per driver and profile; they happen on explicit selection, outside the frame loop.

Face expression, eye opening, gaze, mouth and breath retain their existing owner.
Gesture and interrupt take immediate priority; generated motion fades back in
after release. Original mode fades its weight to zero. Reset replays the seed.

This is an adapted six-axis VAR experiment, not the complete AIRI renderer or
AR-HMM implementation. It does not change character prompts, Runtime behavior,
audio providers, persisted settings, or the Phase 17 implementation status.
