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

The model canvas uses semantic Live2D hit testing before window movement. Authored head and body
areas retain their semantic targets; visible drawable triangles provide a model-neutral silhouette
fallback for hair, arms, skirts, and legs that the source model omitted from those authored areas.
The fallback ignores hidden and effectively transparent drawables, so empty canvas space does not
become a drag handle. Pointer coordinates are mapped from the CSS-transformed canvas bounds back to
its untransformed layout space before Cubism hit testing. Pressing and moving from any of these
character hits crosses a small pointer threshold and calls Tauri's native window drag API directly
inside the pointer event; releasing without crossing the threshold remains a character touch. The
old online label and its decorative drag strip are not part of the HUD.

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
browser it records explicit pointer enter, move, and leave transitions. In Tauri it additionally
samples the global physical cursor position against the current physical overlay bounds every 80 ms,
with at most one sample in flight and a three-error cutoff. Physical coordinates are mapped into the
current CSS viewport, including negative-coordinate and scaled secondary displays. This window-level
presence alone reveals the bottom controls without taking mouse ownership.

When transparent-region pass-through is enabled, React requests native cursor capture only while the
sampled point intersects either the renderer's authored/visible Live2D mesh hit test or explicitly
marked UI chrome such as subtitles, the composer, menus, and action buttons. Empty canvas space keeps
native cursor events ignored even though the rail is visible. Entering a reserved bottom-control
rectangle captures it on the same bounded sampler, so a previously unfocused button becomes clickable
without first clicking the window. Active modal, avatar-drag, and push-to-talk guards temporarily
capture the window until their gesture ends. This reuses renderer geometry and does not read the WebGL framebuffer or
turn the canvas element's rectangle into a hit area. macOS still accepts the first mouse action in an
inactive interactive region.

The always-visible overlay disables WebView background throttling. This keeps streaming-text timers,
audio progress, Live2D animation, and whole-line subtitle paging active while another application has
focus; otherwise an inactive WKWebView may defer the visible update until the user clicks or selects
text. The ordinary control-center window retains the platform default throttling policy.

React shares one desktop-preference hook between the overlay and settings window. Tauri remains the
source of truth, persists UI/OS preferences atomically, and broadcasts a bounded
`desktop-preferences-changed` event so an open overlay updates immediately. Browser preview uses an
in-memory/local-storage fallback and does not claim OS integration.

Functional controls use the shared typed Lucide SVG boundary rather than font glyphs or one-off path
markup. Settings branding, the favicon, generated application assets, and the tray are derived from
the checked-in Web crescent-and-ribbon mark. The macOS tray declares its compact PNG as a native
template image and intentionally sets no text title so AppKit can recolor it for either menu-bar
appearance.

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

## Windows installed product boundary

ADR 0027 defines the Windows x64 release layout. The first installer is an NSIS current-user package;
it embeds the Desktop frontend, maps the complete PyInstaller onedir Runtime to
`$RESOURCE/runtime-sidecar/`, and maps the x64 AppContainer helper to
`$RESOURCE/bin/chatwaifu-appcontainer-host.exe`. Release startup resolves these paths through
Tauri's path API and fails explicitly when either component is absent. It never falls back to a
source checkout, system Python, `uv`, or PATH lookup.

Runtime receives Tauri-owned per-user config, local-data, and log roots before its Python modules are
imported. Built-in code, characters, Skills, VAD/tokenizer data, and frozen libraries stay immutable
under `$RESOURCE`; SQLite, provider settings and secrets, generated audio, installed plugin data,
and future model caches remain outside the installation tree. Ordinary uninstall removes product
resources but preserves these user roots. AppContainer profiles and ChatWaifu-owned ACL grants must
be reconciled without deleting plugin data before an installed build can pass release acceptance.

The redistributable base does not contain CUDA/PyTorch environments, local model weights, trained or
cloned voices, or private Live2D assets. It remains usable through Demo and configured cloud
providers and uses the safe avatar fallback when vendor assets are absent. A local operator may
explicitly overlay ignored Live2D assets into a private test build, but that output cannot enter CI,
tags, or a public release.

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
  releases temporary interactive-region capture before stopping.
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

The frozen Runtime and NSIS assembly are a separate packaging slice governed by ADR 0027. An
unsigned owner-only candidate has passed basic install, Runtime health, forced-exit cleanup,
uninstall, and user-root retention under Windows x64 emulation. Clean-account product UX, normal
exit, update/reinstall, installed AppContainer execution and reconciliation, native x64/CUDA
hardware, signed public delivery, automatic updates, autostart, and Store-compatible
non-transparent profiles remain outside the validated desktop-host slice.

## Verification

- Web unit tests cover route selection, avatar interaction, semantic and visible-mesh avatar dragging,
  CSS-transformed canvas coordinates, typed-message
  submission, subtitle visibility, interaction-rail persistence, pending-caret rendering, paragraph-gap folding,
  focus-free pointer presence, multi-display physical bounds, playback-paced whole-line subtitle
  turns, out-of-order playback metadata, hanging audio-unlock recovery, interruption, and single
  media ownership.
- Chromium checks hover-only interaction-rail reveal and layout, the dedicated settings layout,
  internal scrolling, functional HUD switch, absence of conversation controls in the settings
  window, and real-model transparent, head, authored-body, and leg hit points.
- Rust tests cover host responsibility, bootstrap parsing, bounded restart state, backward-compatible
  preference defaults, and interactive-region capture while transparent click-through remains enabled.
- Cargo check, Clippy, Rust tests, Web typecheck/lint/tests, and the no-bundle release build are gates.
- Local macOS smoke must show the transparent overlay with the real local Live2D model over another
  application; browser-only proof is insufficient.
