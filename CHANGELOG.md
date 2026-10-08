# Changelog

## Unreleased

- Add opt-in QQ free conversation: paired owner messages use model-selected
  respond/clarify/wait, and member groups consider mentions, ordinary text, scoped
  voice transcripts and bot-targeted poke events. Wait stays silent; permission
  changes cancel pending decisions. Verify group voice source identity before STT,
  preserve incoming grants, delivery fences and existing text/voice reply rules.

- Fix explicit QQ gesture requests such as “戳一戳我” producing only dialogue:
  require native tool selection and expose `send_poke` in the initial admitted
  QQ tool list. Keep definitions, quotes and negations on automatic selection;
  other account operations retain progressive discovery.

- Add an explicit owner-controlled QQ account mode with 158 version-pinned NapCat
  operations, including actual poke execution and recorded provider results. Existing
  admitted private/group turns can operate the same bot account without per-call
  desktop confirmation; live generation, scene, account and version checks remain.
  Keep incoming grants and group participation unchanged, with progressive capability
  activation and a separate permission setting from desktop and other services.

- Let a genuine QQ group mention without text invite a reply to recent admitted
  discussion, asking for a topic when none is available. Keep its explicit trigger
  marker outside listening/memory extraction and preserve speaker grants. Place
  current untrusted discussion after old history on the complete model wire, so
  older missing-image replies do not sit nearer a short follow-up question.

- Collect authorized fixed-group text in a volatile, fair bounded cache without
  replying or calling a model. A valid mention freezes recent attributed originals
  plus optional source-ID-only extractive compression; summary failures preserve
  recent context and lifecycle fences discard stale audiences. Keep cache outside
  history/memory and complete inputs within the admitted model budget. Group
  replies now remain text-only even when private sticker replies are enabled.

- Preserve complete sentence pauses in short instant-message replies instead of
  bypassing or merging them below the preferred length. Retain canonical text,
  atomic spans, technical/single-text bypass, three-part cadence and tail
  cancellation. Replay nine frozen live Gemini replies without new model calls.

- Accept a valid QQ group reply envelope together with a real bot mention and
  nonempty text. Preserve scoped group history, operator grants, deduplication,
  cancellation and delivery fences. Record two actual pre-admission drops;
  do not enable quote-only triggers or load arbitrary provider quote bodies.

- Scope short casual replies to external messaging origins while retaining the v7
  persona and existing local/Web/desktop/voice output contracts. Apply channel
  defaults of 30 preferred and 60 soft characters, with detailed/code bypass.
  Extend fixed QQ group delivery to lossless ordered text bubbles through SQLite
  migration 41, keeping authority, cancellation, cadence and send receipt fences.
  Record 24 fixed-context real Gemini samples without approving Q02 or handset UX.
  Deploy frozen product bd00970 to the primary server with migration/state/source
  verification. Retain earlier QQ platform login rejection 168, then verify original
  account recovery and restore the same fixed group using fresh matching audience
  and unchanged member links/authority. Handset short-reply UX remains pending.

- Merge the frozen QQ NapCat and Q02 v7 test branches for isolated server testing.
  Add a default-off host policy for current-owner public reads, recheck it before
  execution, preserve ordinary model-selected voice and shared-scene isolation,
  and complete source retrieval as text. Keep the Q02 quality gate unapproved.

- Select the frozen v7 persona on the local Q02 test branch at the user's request.
  Preserve v4 controls and separately verify v7 loading and section-budget clipping
  across three presentations. Retain the failed quality verdict and the independent
  server version; record the optimization checkpoint, rollback and remaining gates.

- Freeze the v44 Q02 conclusion with all attempts and four separate gates.
  Directed search/read controls and a real four-turn flow acquired the official
  missing conditions. An exact previously unsent review was admitted by a bounded
  operating allowance control, but complete-input replies still contain unsupported
  source dates and bibliography. Keep the version unapproved, the original full
  A/B gate unchanged, and valid delivery/playback evidence scoped and reused.
  No prompt rules, production model/budget changes, or AGY were introduced.

- Prefer the latest ordinary assistant history over optional older source bodies
  for explicit acquired-material follow-ups, and preserve full pre-reprojection
  estimates in successful and blocked guard reports. Network-forbidden controls
  reproduce the old actual wires and verify retained history under the same
  allowance. Answer fidelity remains unapproved; no prompt or production changes.

- Reproject whole prior source bodies against complete outgoing tool/review input
  after late overhead, preserving current results and withheld-draft fences.
  Exact offline replay, local/server 523 checks and a real four-turn flow validate
  the repair. A frozen two-budget control still exposes latest-answer selection
  and complete-input fidelity failures; Q02 remains unapproved, with no prompt
  change, production deployment or automatic window increase.

- Enforce the evaluator's selected DNS resolver in actual permissioned source
  invocations while preserving original model arguments and rejecting invalid
  values. A bounded production-service discovery flow recorded one model reply
  and three Runtime fallbacks; retrieval success does not approve Q02 quality.
- Recognize explicit acquired-material questions with no new retrieval and
  acquired-document checklists in existing routing. Retain successful READ and
  missing-body fences, affirmative operations and cancellation. Local/server
  regressions passed; no new prompts, budget increase or production deployment.

- Reuse the existing source review for explicit versioned provided material with
  natural text and source frames disabled. Keep the withheld draft bounded and
  unpublished, with truthful budget fallback and cancellation fences. Four actual
  four-turn flows completed, but full-text review still found condition and scope
  loss; v40 is not adopted or deployed and Q02 remains unapproved.

- Investigate Q02 fidelity with an isolated, bounded structured source-answer frame.
  Retain model-declared unresolved gaps across the same original URL/body identity
  and render typed list items; production behavior is unchanged. Ten server HTTP
  controls preserve gaps but expose condition ambiguity and an expanded prohibition;
  the prototype is not adopted and full Q02 acceptance remains open. Record ADR 0070,
  all raw/rendered replies, wire accounting and the source-condition dependency.

- Route explicit “this document” follow-up summaries through the existing retained-source
  revision when a successful original READ is available, preserving fresh-operation and
  permission boundaries. Q02 fidelity remains unapproved after actual four-turn validation.

- Preserve safe native-tool protocol error codes without exposing response bodies,
  and let the optional evaluation decision wrapper use its existing single
  correction allowance for unknown functions. Production execution still rejects
  unknown calls. Evaluator 1.32.0 fingerprints the final routing change.
- Keep an enabled, validated builtin source reader available alongside selected
  builtin search within the original tool count and schema limits. Preserve
  per-call permission and confirmation. A server-isolated native four-turn source
  flow completed; frozen-document review still found scope and checklist gaps,
  so Q02 and the full persona candidate remain unapproved.
- Preserve the positive meaning of the Chinese intensifier `特别`, and plan a
  current positive user signal as a happy celebration after higher-priority
  distress, boundary, interaction and question handling. Opted-in private stickers
  can now match that durable plan; background happiness on a generic answer stays
  undecorated. QQ regression checks use actual Character planning instead of a
  supplied fixture plan. Voice tool selection and group text policy are unchanged.

- Configure input limits, output reserve/cap, estimation margin and upstream
  section/history/memory/tool budgets per selected model route. Freeze budgets
  at admission, retain legacy configurations, and expose shared settings;
  migration 36, template v12, evaluator 1.14.0 and ADR 0062 record the change.
  Archive 156 controlled comparison replies plus 10 output probes: missing
  inputs recover, but Flash-Lite technical errors and proxy-ignored output caps
  remain. No automatic production window change or Q02 quality approval.

- Supply trusted public ChatWaifu NEXT architecture without guessing the selected
  provider's deployment or exposing private model configuration. Retain history,
  ownership and privacy rules within the unchanged full-source input budget;
  template v11/evaluator 1.13.0 preserve prior identities. Real identity/greeting
  quality remains to be verified, default persona v4 and Q02 unapproved.

- Require results for Chinese operation modifiers such as sequencing and repeated
  verification, preserving negated URL reads and objectless follow-up closure.
  Template v10/evaluator 1.12.0 retain old samples. Archive separate actual Gemini
  native create/read/cancel/read controls across three presentations; correct
  saved states do not prove future device delivery, and Q02 remains unapproved.

- Compact the instant-chat output contract and lossless source JSON so the six
  archived follow-up controls retain full originals within the same input budget.
  Keep default persona v4 and permission/source fences; template v9/evaluator
  1.11.0 prevent mixed resumes. Real style/source quality remains unapproved.
  Synchronize the proactive audit test on actual terminal events instead of a
  fixed two-second poll, retaining failed-generation and historical CI evidence.

- Preserve bounded actual source links when the whole body exceeds input budget,
  declare the reader HTTPS constraint in results, and honor explicit existing-content
  analysis without fresh tools. Compound external operations retain confirmation.
  Reader 1.3.0/template v8/evaluator 1.10.0 freeze the repair; the completed
  twelve-reply Gemini failure batch stays immutable and Q02 remains unapproved.

- Add opt-in bounded anchor links to public source reads, preserving actual
  schemes, final-page/base resolution and separate confirmation for each target.
  Skill 1.2.0/evaluator 1.9.2 retain compatibility and immutable batch identity.
  Actual index reads and Python HTTP 503 failures are recorded separately;
  model source quality and Q02 remain unapproved.

- Separate each initial image admission from Provider readiness in the burst
  overflow test, preserving stop and dispatch deadlines. Delayed-intake and
  suppressed-dispatch controls retain the historical Windows CI failure.
  Archive Gemini source-policy A/B (18 ties; candidate not adopted) and actual
  READ-to-native-WRITE denial across three presentations; Q02 remains unapproved.

- Preserve local writing, supplied-content review and explicitly negated URL reads
  without forcing an external operation; compound save/send/read requests retain
  the required path. Actual Conversation regressions cover all four prior
  fallbacks. Prompt identity v7/evaluator 1.9.1 record the policy follow-up. Archive
  two full-catalog Gemini three-presentation batches (144 visible replies); the
  usage-enabled batch has one source-condition error and Q02 remains unapproved.

- Separate relevant capabilities from required external operations. Ordinary
  dialogue uses native automatic tool decisions with the full character contract;
  explicit operations retain mandatory calls, permissions and truthful failure
  handling. Preserve the complete 288-reply Gemini v4/v7 review and the repaired
  source follow-up's 24 replies; Q02 remains unapproved. ADR 0061, prompt identity
  v6 and evaluator 1.9.0 record the changed policy without a schema migration.

- Format explicitly referenced, available prior public READ sources without forcing
  a new external operation. Fresh verification, supplied URLs, latest-rule requests
  and explicit mutations retain the normal tool and permission path. Preserve the
  incomplete 130/144 real-model batch and its 15 misgated source follow-ups;
  evaluator 1.8.2 prevents mixed resumes. Q02 quality remains unapproved.

- Keep bounded source generations eligible when old assistant prose is omitted by
  the conversation budget. Select from prepared/redacted history and retain route,
  completion, session and whole-source input guards; evaluator 1.8.1 fingerprints
  the same path. Actual two-turn and compiler privacy controls cover the gap.

- Wait for the embedding warning dialog's actual focus transfer before checking
  its keyboard trap. Keep exact text/focus/Tab assertions and the default deadline;
  a suppressed-production-focus control still fails. Preserve the Ubuntu CI
  failure separately from the successful local verification.

- Carry bounded prior public READ receipts through actual Conversation follow-ups,
  using completed generations, stable source routes and the existing ephemeral
  result store. Preserve whole originals or explicit budget/unavailable states,
  without adding audit plaintext or replaying tools. Evaluator 1.8.0 uses the same
  projection and guards source loading against concurrent/cancelled runs; ADR 0060
  records retention and scope. Q02 model quality remains unapproved.

- Apply frozen whole-input budgets to no-tool text replies before Provider
  dispatch, including disabled/unsupported tools, and count each initial quoted
  history candidate without converting tokens back to characters. Preserve
  source/task facts and cancellation; evaluator 1.7.1 rejects mixed resumes.

- Preserve the three-model v6/v7 source/checklist comparison: 72 synthetic replies,
  all 106 Provider attempts, primary masked-label review and safe Sonnet resume.
  V7 remains evaluation-only; source continuity and native input-budget gaps are
  recorded rather than treated as Q02 acceptance.

- Replace whole-tool-input half-character estimates with a bundled, checksum-pinned
  offline cl100k chat JSON reference. Recompute complete projections when omitting
  old history, preserve task/source/action facts, and tag estimates explicitly.
  Evaluation 1.7.0 fingerprints the vocabulary/dependency; three final-only real
  model replays fit the reserved allowance, while factual quality remains unapproved.

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
