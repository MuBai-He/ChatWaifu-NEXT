# Changelog

## Unreleased

- Strengthen the Runtime Skill event-failure check by registering a real terminal
  waiter before execution and separating durable setup from its existing signal
  deadline. A held-signal control and notification-suppression experiment verify
  the check; shutdown timing assertions are unchanged. Input-count investigation
  retains proxy/reference discrepancies without changing production budgets.

- Fail completed LLM responses that contain no visible answer text, preserving
  recorded tool results and avoiding automatic operation replay. Evaluation 1.6.2
  records empty answers as incomplete with actual usage retained. Real Opus
  controls confirm upstream empty string deltas; their cause and Q02 quality
  acceptance remain unresolved.

- Separate the initial required-tool decision from character expression in prompt
  template v4, quote prior assistant replies as bounded untrusted data, and restore
  the full context after a real tool exchange. Evaluation 1.5.2 records context and
  history fingerprints and preserves actual usage. Failed reads now produce an
  honest Runtime fallback instead of unverified model claims. Real probes still
  retain outdated-source and incomplete-answer failures; Q02 remains unapproved.

- Add one bounded correction for a missing required initial tool call and an
  explicit final-answer instruction when the tool phase closes. Evaluation 1.4.2
  counts up to six Provider rounds per tool turn and preserves discarded-round
  usage. Gemini still missed calls and Opus still promised another query in real
  probes; Q02 quality remains unapproved and persona v4 remains the default.

- Freeze the trusted Runtime turn time in prompt template v3, count it in the
  safety budget, and require date-aware original-source verification. Evaluation
  1.4.0 records an explicit UTC prompt time and preserves it across resume,
  separately from synthetic relationship-state time. Fix a Windows test's UTF-8
  journal read; Q02 quality acceptance remains pending.

- Add permissioned public source discovery and opt-in Runtime source evaluation
  1.3.0, with actual tool results, provider-round usage and persistent attempt
  limits across resume. Fix regulation recall and generic code-result false
  recall in public-reader metadata. Real three-model smoke tests preserve failed
  reads, missing calls and outdated sources; Q02 quality remains unapproved.

- Add permissioned public HTTPS source reads through the Runtime Skill gateway,
  with source URL/time/fingerprint and bounded excerpts. Address pins, public-only
  DNS, redirects, decompression and HTML limits protect the network boundary;
  explicit Cloudflare encrypted DNS supports proxy Fake-IP environments without
  changing system settings. Q02 model quality remains pending.

- Add explicit remote connection selection for Web and desktop: the native host starts no
  Runtime or model Worker until local mode is chosen. Remote clients use HTTPS, authenticated
  HTTP/audio and ticketed WebSockets, with server-provided STUN/TURN configuration for voice.
  Add frontend-only source commands and a split-deployment guide; target-network voice acceptance
  remains pending.

- Add a source server entrypoint with a separate durable state directory, create-once private
  configuration and management credentials, and a generated systemd user service. It reuses the
  Runtime lifecycle without desktop supervision, frontend tooling, or model-worker installation.
  Target Linux deployment and native WeChat credential-store acceptance remain pending.

- Add an opt-in OpenAI Realtime GA server adapter with verified session configuration, streaming
  PCM conversion, Runtime-owned turn identity, late-transcript correlation, bounded cancellation
  and sanitized errors. Loopback WebSocket/SQLite acceptance is covered; public cloud voice and
  native listening acceptance remain pending. See `docs/testing/openai-realtime.md`.

- Consume event-sequence allocation results within one SQLite worker operation so interrupting
  their delivery cannot strand a write cursor and reject the next event/outbox commit.

- Harden cloud realtime turns: order input commits after queued audio, flush local playback
  before remote interruption, cancel stale input/output handoffs, and bound network operations.
  Close EOF/error sessions once, join in-flight admission, and prevent a delayed old-session
  close from cancelling a replacement connection's turn.

- Reconnect an already-open desktop settings window when native Runtime restarts with a new
  address or token; cancel stale reads and preserve settings without replaying mutations.

- Reduce repeated learned stickers among equally suitable choices using recent confirmed deliveries;
  fall back to the original choice if history is unavailable. Fence interrupted channel turns before
  optional reply preparation can publish an old delivery plan.
- Add recent sticker-send history in settings with actual image delivery outcomes, retry counts and
  dates; deleted stickers and reset source conversations no longer appear in this bounded view.

- Add native WeChat typing, durable in-character failure recovery, and friendly memory-source UI.
- Add opt-in static sticker learning/reuse, photo retention/deletion, semantic description retrieval,
  manual embedding-index rebuild, photo capture metadata and user-statement association.
- Combine native consecutive image messages into one bounded multi-image reply (PR #28).
- Remember shared jokes from adjacent delivered exchanges, with natural recall, deduplication,
  and negation rejection (Phase 17.4A, PR #30).
- Reconcile Phase 17 progress and restore parseable status YAML. GIF/APNG remains Draft PR #29;
  shared-joke/sticker binding remains pending.

- Add opt-in Phase 17.2 preset sticker replies for the default character on WeChat: three original
  kitten images selected from durable Character ResponsePlan, encrypted native image transport,
  and an optional durable image tail that preserves delivered text on image failure.
  Local validation and real macOS WeChat image receipt/interruption acceptance passed.

- Initialize the Phase 0 monorepo and cross-language quality gates.
- Define the version 1 protocol package, schema generation, runtime validation, and
  golden contract fixtures.
- Add the isolated Phase 2 Avatar Lab, semantic cue scheduler, controller-owned render loop,
  lip-sync debug sources, hit testing, telemetry, and FakeAvatarRenderer.
- Pin the public Cubism Web Framework 5-r.5 vendor boundary and add actionable missing-Core
  diagnostics without committing proprietary SDK or model assets.
- Add deterministic Avatar SDK tests and Chromium Playwright interaction coverage.
- Build a local official Cubism bridge from SDK 5 R5, install the Natori sample, and render Live2D
  in Avatar Lab and the main chat with a safe Fake fallback.
- Bound the desktop chat to the display viewport, make transcript history independently scrollable,
  and add a confirmed reset for conversation, memory, events, generated audio, and avatar state.
- Accept the continuous basic-demo delivery scope and tiered local TTS architecture.
- Adapt the user-supplied local Ayachi Nene model to semantic facial presets and bounded character
  actions while keeping all character assets ignored.
- Implement Scheme A structured memory with review proposals, privacy policy, provenance, FTS5
  retrieval, dedupe, supersede, correction, pinning, tombstones, and a Web memory center.
- Reserve null `SemanticMemoryIndex` and `TemporalMemoryGraph` ports for optional Scheme B/C retrieval
  without changing SQLite Scheme A truth ownership.
- Center the structured-memory dialog close icon with geometry-stable SVG rendering.
- Smooth bursty assistant deltas through a bounded generation-scoped Web reveal queue while preserving
  lossless text, interruption, and stale-generation rejection.
