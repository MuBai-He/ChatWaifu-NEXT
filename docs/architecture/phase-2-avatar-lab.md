# Phase 2 Avatar Lab

## Scope and result

Phase 2 adds an isolated browser laboratory at `/avatar-lab`. It proves the semantic avatar
contract, deterministic scheduling, renderer lifecycle, audio-driven mouth input, semantic hit
events, and observability without starting Runtime, Pipecat, Tauri, or an AI model SDK.

The Fake/CI path is implemented and locally validated. The real Cubism path has an official-SDK
adapter boundary and vendor diagnostics, but cannot be claimed as rendered until the owner supplies
Cubism Core, a bridge build, and a licensed model.

## Ownership

```text
React controls
  -> versioned semantic AvatarCue
  -> AvatarController
       -> CueScheduler + capability fallback
       -> lip-sync source + telemetry
       -> requestAnimationFrame loop
  -> AvatarRenderer
       -> FakeAvatarRenderer (default and CI)
       -> Live2DAvatarRenderer (optional local vendor path)
  -> versioned AvatarInteractionEvent
```

React renders low-frequency snapshots for diagnostics. It neither writes Cubism parameter IDs nor
drives frame-by-frame model updates. Model-specific identifiers, motion groups, expressions,
textures, and WebGL objects remain behind the bridge.

## Scheduler policy

- Cues resolve to attention, speech, emotion, gesture, gaze, or override layers.
- Same-layer replacement requires an interruptible current cue and sufficient incoming priority.
- Different layers may remain active together.
- `duration_ms` expires a cue; `after_current_motion` queues until the gesture layer is free.
- Queues and warning histories are bounded.
- Invalidated generations are removed from active and queued state; later stale cues are rejected.
- Unsupported capabilities emit a warning and fall back to a safe semantic value where available.
- Motion start/end callbacks are emitted for start, expiry, replacement, and invalidation.

## Audio and interaction

The lab provides deterministic sine/random envelopes, local WAV decoding, and a microphone
analyser. WAV and microphone nodes are disconnected and their AudioContext/stream resources are
closed when replaced or stopped. Mouth openness is clamped to `[0, 1]` and returns to zero when the
speech layer or source stops. `MotionSyncAdapter` remains an optional adapter interface.

Renderer hit results contain only area and model coordinates. `interactionFromHit` maps them to a
versioned semantic `AvatarInteractionEvent`; business code never receives raw Cubism parameters.

## Live2D vendor boundary

The repository pins the official public Cubism Web Framework `5-r.5` at commit
`198a3769c26ca3d7b600e932590433badd392edd`. `make setup-live2d-framework` fetches it into an ignored
directory and verifies the commit. Cubism Core, the browser bridge build, and character assets are
not committed. `make check-live2d-vendor` reports each missing item and the Web UI surfaces the same
condition without crashing.

Setup details and official source links are in `vendor/live2d/README.md`.

## Tests and release boundary

- Vitest covers scheduler ordering, duration, fallback, queue bounds, generation invalidation,
  controller pre-ready behavior, lip-sync clamping, official bridge mapping, hit mapping, missing
  Core, and 50 renderer load/unload cycles.
- Playwright covers the complete semantic acceptance sequence, hit interaction, screenshot output,
  and the missing-Core error path in Chromium.
- CI installs Chromium and uses only `FakeAvatarRenderer`; proprietary artifacts are not CI inputs.
- The real Live2D renderer remains `pending_vendor_validation` until all three local artifacts are
  supplied and an actual model load/render/unload cycle is recorded.
