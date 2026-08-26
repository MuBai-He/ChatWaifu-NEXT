# Desktop pet host

## Scope

The first desktop slice turns the existing React and Live2D application path into a real macOS
desktop pet. Tauri owns OS integration only:

- a transparent `avatar-overlay` window;
- a lazily created normal `control-center` window;
- tray actions, click-through, always-on-top, visibility, position, size, and HUD visibility;
- atomic persistence of those OS-level preferences.

Character behavior, model calls, memory, voice routing, Runtime events, and Live2D asset identifiers
remain outside Rust. ADR 0005 remains the governing decision; this slice does not introduce a new
architecture direction.

## Inputs and outputs

The overlay consumes the same loopback Runtime HTTP/WebSocket contracts and semantic `AvatarCue`
stream as the browser Demo. It renders a transparent Live2D canvas, the latest assistant subtitle,
connection state, avatar touch, microphone connection, and push-to-talk controls. Subtitle and
connection-state visibility are independent presentation preferences; hiding either does not stop
generation, playback, lip sync, or avatar motion. The always-visible HUD action restores both
controls. Tauri commands only expose bounded window and presentation-preference operations.

The control center uses `/control-center` and is not created at startup. Opening it creates or shows
the native window; closing it hides the window so the pet and Runtime remain alive.

## Media ownership and cancellation

Only the overlay is a media owner. It receives generation-scoped audio, playback acknowledgements,
microphone capture, interruption, and stale-generation rejection through `useChatSession`. The
control center can send text and change product settings but its microphone and local playback are
disabled. This prevents two WebViews from playing the same TTS segment.

All existing generation, cancellation, late-output, and playback-ACK invariants stay in the Web and
Runtime layers. Tauri does not reinterpret conversation events.

## Failure and lifecycle behavior

- Missing Runtime renders an offline notice while Live2D remains safely interactive.
- Missing proprietary Live2D assets uses the existing deterministic renderer fallback.
- Click-through can always be disabled again from the tray.
- Subtitle and online-state visibility persist independently, with visible defaults for old files.
- Invalid or old preference files fall back to safe interactive defaults.
- `make desktop` owns all local worker, Runtime, Web, and Tauri development process groups; terminal
  interruption tears them down together.

Rust-owned Runtime bootstrap handshakes, automatic Runtime crash restart, signed installers,
automatic updates, autostart, and Store-compatible non-transparent profiles are intentionally
excluded from this slice.

## Verification

- Web unit tests cover route selection, avatar interaction, independent HUD visibility, and single
  media ownership.
- Rust tests cover host responsibility and backward-compatible preference defaults.
- Cargo check, Clippy, Rust tests, Web typecheck/lint/tests, and the no-bundle release build are gates.
- Local macOS smoke must show the transparent overlay with the real local Live2D model over another
  application; browser-only proof is insufficient.
