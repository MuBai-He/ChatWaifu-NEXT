# Changelog

## Unreleased

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
