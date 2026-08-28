# Basic usable demo architecture

## User-visible acceptance path

1. One command starts Runtime and Web on loopback addresses.
2. The client creates or resumes a character session.
3. The browser sends microphone audio over WebRTC; Pipecat and Silero VAD create a bounded utterance,
   and the isolated local STT worker supplies the same final-text command used by typed input.
4. The character responds incrementally through a real configured LLM or the clearly labelled demo
   provider. The Web client smooths bursty delta delivery through a bounded, generation-scoped reveal
   queue so text remains visibly progressive without delaying Runtime generation or TTS.
5. The response is synthesized locally, returned over the WebRTC audio track, and drives
   speaking/lipsync Avatar state.
6. Interrupt immediately invalidates the generation, stops synthesis/playback, clears stale output,
   and returns the avatar to listening.
7. Explicit normal memories commit immediately; implicit candidates enter a review inbox; accepted
   records persist with provenance, correction, supersede, pin, privacy, and tombstone semantics.
8. Runtime Skills and local or remote MCP servers share schema validation, permissions,
   confirmation, cancellation, timeout, normalized error, and audit boundaries. Runtime also
   exposes a policy-filtered loopback Streamable HTTP MCP server at `/mcp`.
9. A read-only system-status Runtime Skill reports active providers and health truthfully.
10. The visual-novel stage keeps Live2D and the current dialogue fixed while the optional backlog
    scrolls independently; voice and provider controls live in a bounded configuration panel.
11. A confirmed reset cancels active work and returns conversation, memory, event, audio, and
    avatar state to a clean demo baseline while keeping the ready session connected.

## Domain responsibilities

| Domain         | Owns                                                         | Explicitly excludes                                | Inputs and outputs                                  | Failure/cancellation                                             |
| -------------- | ------------------------------------------------------------ | -------------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------- |
| Frontend       | session UI, WebRTC peer, device UI, AvatarController         | provider SDKs, SQL, model IDs                      | microphone/remote track, commands/events, AvatarCue | reconnect; generation guard; immediate remote playback stop      |
| Realtime media | Pipecat transport, VAD, utterance buffering, playback bridge | character, memory, provider policy                 | PCM frames in/out and typed speech events           | interruption frame; bounded buffers; connection teardown         |
| Runtime API    | lifecycle, auth-free loopback API, connection registry       | character policy and provider internals            | HTTP/WS request validation and normalized errors    | graceful shutdown; bounded client queues                         |
| Conversation   | turns, generations, segmentation, interruption               | SQL and provider SDK objects                       | transcript/text in; deltas/audio/cues/events out    | cancellation token plus active-generation checks at every output |
| Providers      | LLM/STT/TTS-specific I/O                                     | sessions, memory truth, UI state                   | normalized streaming interfaces                     | timeout, cancellation, late-output rejection, health diagnostics |
| Persistence    | SQLite WAL migrations, repositories, event/outbox atomicity  | prompt construction and model routing              | domain records and page queries                     | transactions, busy timeout, startup recovery                     |
| Memory         | typed proposals/records, policy, FTS retrieval, provenance   | transcript archive, UI policy, direct SQL by agent | policy-filtered context packets                     | review on ambiguity; sensitive confirmation; durable tombstone   |
| Runtime Skills | registry, policy broker, job executor, MCP adapter, audit    | Codex Development Skills and provider internals    | typed calls/results and confirmations               | timeout, process-group cancellation, normalized denial/errors    |
| Character      | persona, prompt budgets, response/Avatar planning            | provider and renderer identifiers                  | context in; provider prompt and semantic cues out   | safe neutral/default persona fallback                            |

## Demo dependencies and migration path

- FastAPI/AnyIO provide the loopback Runtime and cancellation-aware task lifecycle.
- SQLite WAL + FTS5 structured records are the only memory truth; semantic-index and temporal-graph
  ports are disabled extension points rather than alternate stores.
- HTTPX adapters call local OpenAI-compatible LLM services and the authenticated STT worker.
- Pipecat 1.7 SmallWebRTC owns full-duplex transport and Silero VAD behind ChatWaifu contracts.
- faster-whisper runs in an independently locked worker environment and returns versioned SDK models.
- `sherpa-onnx` and all model bundles are optional worker/runtime dependencies, never base CI inputs.
- MCP Host connections use the official SDK over stdio, Streamable HTTP, or compatibility SSE.
  Untrusted local processes require an enforcing macOS Seatbelt or Linux bubblewrap sandbox and
  fail closed when one is unavailable; network transports are revalidated against SSRF policy.
- Tauri remains a thin host for Runtime lifecycle and shared Web assets.

## Release exclusions

The basic demo does not claim a Windows AppContainer sandbox, public TURN/RTVI data-channel control, trained
custom voice weights, cloud realtime, multi-machine workers, proactive ambient behavior, semantic
vector retrieval, temporal graph reasoning, or a completed LongMemEval gate. Playback acknowledgements
and long-running multi-turn voice stress tests remain hardening work rather than hidden completed features.
