# ADR 0005: React renderer with a thin Tauri host

- Status: Accepted
- Date: 2026-08-23

## Context

The product needs a web-rendered Live2D surface and desktop OS integration without duplicating
character behavior in the host shell.

## Decision

React owns application UI and semantic avatar rendering. A future Tauri host owns windows, tray,
permissions and sidecar lifecycle only. Live2D asset identifiers stay inside the avatar adapter;
domain code emits semantic `AvatarCue` values.

## Consequences

Web and desktop can share the renderer. Tauri commands remain small and testable. Live2D Core stays
outside version control and Tauri business implementation remains deferred until Phase 3.

Phase 2 implements this boundary as `AvatarCue -> CueScheduler/AvatarController ->
AvatarRenderer`. CI uses `FakeAvatarRenderer`; the optional official adapter targets the pinned
Cubism Web Framework `5-r.5` and fails with an actionable diagnostic when local vendor artifacts are
absent. React does not own the animation-frame loop.

## Alternatives

Native-only UI; placing character behavior in Rust; leaking model parameter IDs into the agent.
