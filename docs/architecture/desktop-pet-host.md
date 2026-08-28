# Desktop pet host

## Scope

The first desktop slice turns the existing React and Live2D application path into a real macOS
desktop pet. Tauri owns OS integration only:

- a transparent `avatar-overlay` window;
- a lazily created normal `control-center` window mounting the desktop-settings surface;
- tray actions, click-through, always-on-top, visibility, position, size, and subtitle visibility;
- atomic persistence of those OS-level preferences.

Character behavior, model calls, memory, voice routing, Runtime events, and Live2D asset identifiers
remain outside Rust. ADR 0005 remains the governing decision; this slice does not introduce a new
architecture direction.

## Inputs and outputs

The overlay consumes the same loopback Runtime HTTP/WebSocket contracts and semantic `AvatarCue`
stream as the browser Demo. It renders a transparent Live2D canvas, the latest assistant subtitle,
avatar touch, a compact typed-message composer, microphone connection, and push-to-talk controls.
The composer calls the same generation-safe `useChatSession.send` path as the main visual-novel
page; the overlay does not own a parallel chat protocol. Hiding subtitles does not stop generation,
playback, lip sync, or avatar motion. The bottom interaction rail stays visually hidden
until the pointer enters the pet window, keyboard focus reaches a control, or a draft, active HUD,
send, or push-to-talk interaction needs it to remain available. Touch-only previews keep the rail
visible. Tauri commands only expose bounded window and presentation-preference operations.

The model canvas uses semantic Live2D hit testing before window movement. Pressing and moving from
the mapped head area crosses a small pointer threshold and calls Tauri's native window drag API;
releasing without crossing the threshold remains a character touch. The old online label and its
decorative drag strip are not part of the HUD.

The character greeting is shown only before any assistant message exists. Once a generation has
started, its empty pre-token state renders only the typing caret and never falls back to the greeting.
Subtitle text keeps a bounded three-line viewport without line clamping or ellipsis; progressive text
retains all earlier content for manual review. The compact overlay collapses consecutive blank lines
without changing the stored transcript. Automatic upward movement follows generation-scoped audio
playback acknowledgements and measured playback time rather than token arrival, so a fast model
cannot reveal ahead of the voice. Playback progress is quantized to rendered line boundaries: text
stays still while the voice remains on the same line, then the three-line viewport immediately turns
forward by a whole line without smooth scrolling. Audio-element and WebRTC playback share the same
projection; late metadata and receipts are reconciled through bounded state, while cleared queues
and stale generations never advance the subtitle.

The control center uses `/desktop-settings` (`/control-center` remains a Web compatibility alias)
and is not created at startup. It is a dedicated app-like settings surface with desktop-pet,
voice-provider, model-routing, companion-policy, and local-data sections; it does not render the
visual-novel stage or composer. Opening it creates or shows the native window; closing it hides the
window so the pet and Runtime remain alive.

The native host declares the control center's surface identity before page scripts execute, so
Windows WebView2 cannot accidentally mount the pet when its path or JavaScript window label is stale.
The host-injected `desktop-settings` marker is authoritative in packaged builds; development URLs
carry the same typed marker so an already-open hot-reload window can recover on navigation. Immutable
Tauri labels and browser paths remain compatibility fallbacks: `avatar-overlay` maps to the pet and
`control-center` maps to settings. The lazily created control center has one native Rust builder
definition rather than a dormant configuration entry plus a second copy. In development that builder
loads the configured Vite `devUrl` directly; packaged builds load the embedded `index.html`.

An always-on-top avatar can otherwise cover the ordinary control-center window, especially after a
large persisted overlay resize. While the control center has focus, the host temporarily promotes
settings and demotes the avatar within the topmost window band. Losing focus or closing settings
returns the control center to an ordinary window and restores the avatar's persisted always-on-top
preference. Changing that preference inside focused settings updates persistence but keeps the
control center unobscured until the user leaves it.

The overlay does not rely on WebView `:hover` or window focus to reveal its interaction rail. In the
browser it records explicit pointer enter and leave transitions. In Tauri it additionally samples the
global physical cursor position against the current physical overlay bounds every 80 ms, with at most
one sample in flight and a three-error cutoff. This also works on negative-coordinate secondary
displays. macOS accepts the first mouse action in an inactive overlay. When persisted click-through is
enabled, entering the overlay temporarily captures cursor events so the newly revealed controls can
be clicked; leaving or disposing the overlay restores click-through without changing the persisted
preference. This is rectangular window hit testing, not per-pixel Live2D alpha hit testing.

The always-visible overlay disables WebView background throttling. This keeps streaming-text timers,
audio progress, Live2D animation, and whole-line subtitle paging active while another application has
focus; otherwise an inactive WKWebView may defer the visible update until the user clicks or selects
text. The ordinary control-center window retains the platform default throttling policy.

React shares one desktop-preference hook between the overlay and settings window. Tauri remains the
source of truth, persists UI/OS preferences atomically, and broadcasts a bounded
`desktop-preferences-changed` event so an open overlay updates immediately. Browser preview uses an
in-memory/local-storage fallback and does not claim OS integration.

Settings navigation uses local inline SVG symbols rather than font glyphs or network icon assets.
The desktop application icon is generated from the checked-in moon-and-spark source SVG. The macOS
tray loads a separate monochrome PNG generated from its checked-in template SVG, declares it as a
native template image, and intentionally sets no text title so AppKit can recolor it for either menu
bar appearance.

## Media ownership and cancellation

Only the overlay is a media owner. It receives generation-scoped audio, playback acknowledgements,
microphone capture, interruption, and stale-generation rejection through `useChatSession`. The
settings window can change product configuration but has no conversation composer, microphone
control, or local playback. This prevents two WebViews from playing the same TTS segment.

The macOS host declares its microphone usage in the bundled `Info.plist`; the OS remains the final
permission authority and prompts on first capture. A WebView missing `getUserMedia` or WebRTC stays
outside the media path, but its microphone control remains actionable long enough to explain the
specific restart or system-update recovery instead of presenting an inert disabled button.

All existing generation, cancellation, late-output, and playback-ACK invariants stay in the Web and
Runtime layers. Tauri does not reinterpret conversation events.

The audio-element fallback may create a silent user-gesture probe before asynchronous TTS is ready.
That probe is never allowed to hold the playback queue indefinitely: arrival of the first real,
active-generation segment immediately promotes the probe element to real playback. A late probe
resolution is ignored, interruption still clears every queued segment, and only actual playback
emits progress acknowledgements.

## Failure and lifecycle behavior

- Missing Runtime renders an offline notice while Live2D remains safely interactive.
- Missing proprietary Live2D assets uses the existing deterministic renderer fallback.
- Windows x64 development bounds oversized local Live2D textures to 4096 pixels before launch,
  preserving the source beside the ignored local asset. This keeps model-specific optimization out
  of the renderer contract while avoiding virtual-GPU decode timeouts.
- Click-through can always be disabled again from the tray.
- Native cursor sampling falls back to ordinary Web pointer events after repeated read failures and
  restores persisted click-through before stopping.
- Inactive-window scheduling stays enabled for the overlay so streaming subtitles never require a
  focus or text-selection repaint.
- Subtitle and online-state visibility persist independently, with visible defaults for old files.
- Invalid or old preference files fall back to safe interactive defaults.
- Tauri starts one supervised Python service-stack child. A versioned, prefix-delimited bootstrap
  handshake publishes its dynamic Runtime URL without logging worker tokens.
- Unexpected service-stack exit enters bounded exponential backoff. Five consecutive failed restarts
  open a circuit visible in settings and tray; manual restart closes it and begins a fresh attempt.
- Application exit terminates and waits for the child process group, while ordinary settings-window
  close leaves the Runtime and pet alive.
- The service stack also watches the owning Rust PID, so an abrupt development hot reload still
  tears down workers even when Tauri cannot deliver its normal exit callback.

Frozen release sidecars, signed installers, automatic updates, autostart, and Store-compatible
non-transparent profiles are intentionally excluded from this slice.

## Verification

- Web unit tests cover route selection, avatar interaction, semantic avatar dragging, typed-message
  submission, subtitle visibility, interaction-rail persistence, pending-caret rendering, paragraph-gap folding,
  focus-free pointer presence, multi-display physical bounds, playback-paced whole-line subtitle
  turns, out-of-order playback metadata, hanging audio-unlock recovery, interruption, and single
  media ownership.
- Chromium checks hover-only interaction-rail reveal and layout, the dedicated settings layout,
  internal scrolling, functional HUD switch, and absence of conversation controls in the settings
  window.
- Rust tests cover host responsibility, bootstrap parsing, bounded restart state, backward-compatible
  preference defaults, and temporary cursor capture while persisted click-through remains enabled.
- Cargo check, Clippy, Rust tests, Web typecheck/lint/tests, and the no-bundle release build are gates.
- Local macOS smoke must show the transparent overlay with the real local Live2D model over another
  application; browser-only proof is insufficient.
