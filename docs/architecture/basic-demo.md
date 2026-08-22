# Basic usable demo architecture

## User-visible acceptance path

1. One command starts Runtime and Web on loopback addresses.
2. The client creates or resumes a character session.
3. The user sends text; optional local ASR may supply the same final-text command.
4. The character responds incrementally through a real configured LLM or the clearly labelled demo
   provider.
5. The response is synthesized locally, played, and drives speaking/lipsync Avatar state.
6. Interrupt immediately invalidates the generation, stops synthesis/playback, clears stale output,
   and returns the avatar to listening.
7. Explicit remember/forget requests persist with provenance and affect a later session.
8. A read-only system-status Runtime Skill reports active providers and health truthfully.

## Domain responsibilities

| Domain         | Owns                                                            | Explicitly excludes                              | Inputs and outputs                                | Failure/cancellation                                             |
| -------------- | --------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------- | ---------------------------------------------------------------- |
| Frontend       | session UI, playback queue, AvatarController, user interaction  | provider SDKs, SQL, model IDs                    | versioned commands/events, audio URLs, AvatarCue  | reconnect; generation guard; immediate local queue clear         |
| Runtime API    | lifecycle, auth-free loopback API, connection registry          | character policy and provider internals          | HTTP/WS request validation and normalized errors  | graceful shutdown; bounded client queues                         |
| Conversation   | turns, generations, segmentation, interruption                  | SQL and provider SDK objects                     | transcript/text in; deltas/audio/cues/events out  | cancellation token plus active-generation checks at every output |
| Providers      | LLM/STT/TTS-specific I/O                                        | sessions, memory truth, UI state                 | normalized streaming interfaces                   | timeout, cancellation, late-output rejection, health diagnostics |
| Persistence    | SQLite WAL migrations, repositories, event/outbox atomicity     | prompt construction and model routing            | domain records and page queries                   | transactions, busy timeout, startup recovery                     |
| Memory         | proposals, explicit policy, FTS retrieval, provenance, deletion | transcript-as-memory and direct SQL use by agent | policy-filtered context packets                   | no write on ambiguity; forget is durable                         |
| Runtime Skills | registry, schema, permission, execution, status skill           | Codex Development Skills and name-based dispatch | typed calls/results                               | timeout, cancellation, normalized denial/errors                  |
| Character      | persona, prompt budgets, response/Avatar planning               | provider and renderer identifiers                | context in; provider prompt and semantic cues out | safe neutral/default persona fallback                            |

## Demo dependencies and migration path

- FastAPI/AnyIO provide the loopback Runtime and cancellation-aware task lifecycle.
- SQLite WAL + FTS5 is the only persistence target.
- HTTPX adapters call local OpenAI-compatible LLM services and optional TTS workers.
- `sherpa-onnx` and all model bundles are optional worker/runtime dependencies, never base CI inputs.
- Pipecat will later replace the segment transport, not the domain contracts or generation registry.
- Tauri remains a thin host for Runtime lifecycle and shared Web assets.

## Release exclusions

The basic demo does not claim production sandboxing, full-duplex WebRTC, natural barge-in VAD,
trained custom voice weights, cloud realtime, multi-machine workers, proactive ambient behavior, or
complete long-term cognitive memory. Those capabilities remain compatible extension points rather
than hidden stubs presented as finished.
