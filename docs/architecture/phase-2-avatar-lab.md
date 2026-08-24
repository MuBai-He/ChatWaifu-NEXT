# Phase 2 Avatar Lab

## Scope and result

Phase 2 adds an isolated browser laboratory at `/avatar-lab`. It proves the semantic avatar
contract, deterministic scheduling, renderer lifecycle, audio-driven mouth input, semantic hit
events, and observability without starting Runtime, Pipecat, Tauri, or an AI model SDK.

The Fake/CI path and the real Cubism path are both implemented and locally validated. The real path
uses the user-supplied Cubism SDK for Web 5 R5. The current local Demo adapts the user-supplied
Ayachi Nene archive; all licensed inputs and generated artifacts remain Git-ignored and are not
remote-CI inputs. The official Natori sample remains the clean-room setup fallback.

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
`198a3769c26ca3d7b600e932590433badd392edd`. `make setup-live2d-vendor` detects the SDK in Downloads,
fetches and verifies the Framework, stages Core/sample inputs in ignored directories, builds the
browser bridge, installs Natori, and verifies the clean fallback. The separate local Nene installer
adapts the user archive into facial presets and the bounded semantic actions `headpat`, `stare`,
`flustered`, and `sing`. Runtime emits only semantic cues; raw Cubism parameter, expression-file, and
motion-group identifiers stay inside the renderer bridge.

Setup details and official source links are in `vendor/live2d/README.md`.

## Tests and release boundary

- Vitest covers scheduler ordering, duration, fallback, queue bounds, generation invalidation,
  controller pre-ready behavior, lip-sync clamping, official bridge mapping, hit mapping, missing
  Core, and 50 renderer load/unload cycles.
- Playwright covers the complete semantic acceptance sequence, hit interaction, screenshot output,
  the missing-Core error path, a real Natori render in Avatar Lab, and a real render in main chat.
- CI installs Chromium and uses only `FakeAvatarRenderer`; licensed vendor artifacts are not CI
  inputs, so real-render scenarios skip when those files are absent.
- Local Chromium records actual local model load, expression/motion changes, draw output, resource
  accounting, fallback canvas remounting, and renderer cleanup. Remote OS validation remains pending.
