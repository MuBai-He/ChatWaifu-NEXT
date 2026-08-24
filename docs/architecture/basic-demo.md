# Basic usable demo architecture

## User-visible acceptance path

1. One command starts Runtime and Web on loopback addresses.
2. The client creates or resumes a character session.
3. The browser sends microphone audio over WebRTC; Pipecat and Silero VAD create a bounded utterance,
   and the isolated local STT worker supplies the same final-text command used by typed input.
4. The character responds incrementally through a real configured LLM or the clearly labelled demo
   provider.
5. The response is synthesized locally, returned over the WebRTC audio track, and drives
   speaking/lipsync Avatar state.
6. Interrupt immediately invalidates the generation, stops synthesis/playback, clears stale output,
   and returns the avatar to listening.
7. Explicit remember/forget requests persist with provenance and affect a later session.
8. Runtime Skills and local MCP plugins share schema validation, permissions, confirmation,
   cancellation, timeout, normalized error, and audit boundaries.
9. A read-only system-status Runtime Skill reports active providers and health truthfully.
10. Desktop history scrolls inside the conversation pane without moving the avatar off-screen.
11. A confirmed reset cancels active work and returns conversation, memory, event, audio, and
    avatar state to a clean demo baseline while keeping the ready session connected.

## Domain responsibilities

| Domain         | Owns                                                            | Explicitly excludes                              | Inputs and outputs                                  | Failure/cancellation                                             |
| -------------- | --------------------------------------------------------------- | ------------------------------------------------ | --------------------------------------------------- | ---------------------------------------------------------------- |
| Frontend       | session UI, WebRTC peer, device UI, AvatarController            | provider SDKs, SQL, model IDs                    | microphone/remote track, commands/events, AvatarCue | reconnect; generation guard; immediate remote playback stop      |
| Realtime media | Pipecat transport, VAD, utterance buffering, playback bridge    | character, memory, provider policy               | PCM frames in/out and typed speech events           | interruption frame; bounded buffers; connection teardown         |
| Runtime API    | lifecycle, auth-free loopback API, connection registry          | character policy and provider internals          | HTTP/WS request validation and normalized errors    | graceful shutdown; bounded client queues                         |
| Conversation   | turns, generations, segmentation, interruption                  | SQL and provider SDK objects                     | transcript/text in; deltas/audio/cues/events out    | cancellation token plus active-generation checks at every output |
| Providers      | LLM/STT/TTS-specific I/O                                        | sessions, memory truth, UI state                 | normalized streaming interfaces                     | timeout, cancellation, late-output rejection, health diagnostics |
| Persistence    | SQLite WAL migrations, repositories, event/outbox atomicity     | prompt construction and model routing            | domain records and page queries                     | transactions, busy timeout, startup recovery                     |
| Memory         | proposals, explicit policy, FTS retrieval, provenance, deletion | transcript-as-memory and direct SQL use by agent | policy-filtered context packets                     | no write on ambiguity; forget is durable                         |
| Runtime Skills | registry, policy broker, job executor, MCP adapter, audit       | Codex Development Skills and provider internals  | typed calls/results and confirmations               | timeout, process-group cancellation, normalized denial/errors    |
| Character      | persona, prompt budgets, response/Avatar planning               | provider and renderer identifiers                | context in; provider prompt and semantic cues out   | safe neutral/default persona fallback                            |

## Demo dependencies and migration path

- FastAPI/AnyIO provide the loopback Runtime and cancellation-aware task lifecycle.
- SQLite WAL + FTS5 is the only persistence target.
- HTTPX adapters call local OpenAI-compatible LLM services and the authenticated STT worker.
- Pipecat 1.7 SmallWebRTC owns full-duplex transport and Silero VAD behind ChatWaifu contracts.
- faster-whisper runs in an independently locked worker environment and returns versioned SDK models.
- `sherpa-onnx` and all model bundles are optional worker/runtime dependencies, never base CI inputs.
- MCP plugins run in fresh stdio child processes; the packaged OS sandbox remains future hardening.
- Tauri remains a thin host for Runtime lifecycle and shared Web assets.

## Release exclusions

The basic demo does not claim production sandboxing, public TURN/RTVI data-channel control, trained
custom voice weights, cloud realtime, multi-machine workers, proactive ambient behavior, or complete
long-term cognitive memory. Playback acknowledgements and long-running multi-turn voice stress tests
remain hardening work rather than hidden completed features.
