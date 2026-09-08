# ADR 0047: First cloud voice adapter uses the OpenAI Realtime GA protocol

- Status: Accepted
- Date: 2026-09-08
- Scope: Phase 13.4B; opt-in server configuration and a single provider

## Decision

The Runtime implements `OpenAIRealtimeBackend` behind the existing cloud session contracts.
`CloudEgressGateway` persists its receipt before the backend opens a WebSocket. The endpoint is
the OpenAI Realtime service; credentials stay in the Runtime, and the native Cascade configuration
remains the default. The model must be explicitly configured. This development slice does not add
a desktop credential editor or a new database schema.

The adapter uses the GA `session.audio` configuration and `response.output_audio.*` events.
It waits for and validates the effective `session.updated` configuration before returning a ready
session. Open plus handshake has one deadline. Input/output are mono signed 16-bit PCM at 24 kHz;
other supported local input rates use one streaming SoXR instance per utterance, flushed before
commit and discarded on interruption. No rate conversion state crosses generations.

Local VAD retains turn ownership: server turn detection is disabled, and one input commit precedes
one explicit response request. Each request carries the locally admitted generation and session IDs
as metadata. The first response event must echo that identity; subsequent response events use its
immutable provider response ID. Commit acknowledgments bind provider input items through the ordered
local commit queue. Input transcription may finish after the response or during a later utterance,
but stays attached to its original input item. No event falls back to the current generation.

Connection state is bounded: at most eight opening/open sessions per backend, 64 retained turn
bindings, 2,048 replay IDs, 16 assistant items per turn, 64,000 transcript characters per role/turn,
1 MiB wire messages and a 16-frame transport receive high-water mark. Pending commit correlations
are never evicted to make room: exhaustion closes the session instead of shifting acknowledgments.
Raw PCM travels through Pipecat and the socket only. Provider payloads, authentication headers and
private error messages are excluded from logs and normalized errors.

Interruption cancels the old response and clears its input buffer before new input can be written.
It also deletes the interrupted assistant item from the provider's temporary conversation, including
items first announced after cancellation. This conservatively removes the entire interrupted item;
there is no invented playback offset. Repeated interruption is idempotent and cannot cancel a newer
response. Every socket write, including late-item cleanup, has a deadline. An uncertain/cancelled
write seals the socket. EOF, protocol errors and failed/incomplete responses terminate the connection
through the existing coordinator cleanup. Closing waiters share owned cleanup tasks.

Cloud tool execution is not implemented in this slice. The adapter explicitly advertises no tools,
sends an empty tool list, and the factory excludes unavailable skill signatures from its context.
Unexpected tool output fails closed. Input transcription failures degrade subtitles without blocking
native audio. Response token usage is normalized through the existing usage contract; this is not an
invoice reconciliation of every cancelled response or separate transcription charge.

## Verification

Controlled transport tests cover handshake rejection/deadlines, resampling/flush, commit ordering,
late response and transcription identity, duplicate events, interruption before response creation,
cancelled writes/close waiters, malformed/oversized messages and bounded correlation. A real loopback
WebSocket passes through the configured Runtime factory and SQLite, including late transcription,
mid-response disconnect, terminal cleanup, egress receipts and absence of stored PCM. Deny, ungranted
ask and failed audit persistence all produce zero connector calls.

## Remaining Phase 13 work

- Public OpenAI endpoint, microphone, listening and native avatar acceptance require separately
  configured Realtime API access; loopback tests do not establish these outcomes.
- Automatic reconnect, fresh Runtime context restoration and Cascade fallback remain pending.
- The existing cloud path still needs playback acknowledgment integration: provider completion is
  not proof that all audio was heard. Precisely trimming an interrupted spoken prefix, including a
  response completed by the provider while local playback is still pending, belongs to that work.
- User-visible mode/voice selection must distinguish the provider voice from Nene's character TTS.
- Cloud tool execution, broader turn-by-turn memory recall/context sync and independent shadow ASR
  are not completed by this adapter. Phase 17.4C remains queued after the agreed Phase 13 work.

## References

- [ADR 0031: Runtime-owned cloud realtime](0031-runtime-owned-cloud-realtime.md)
- [ADR 0046: Turn ordering and terminal ownership](0046-cloud-realtime-turn-lifecycle.md)
- [OpenAI WebSocket connection guide](https://developers.openai.com/api/docs/guides/realtime-websocket)
- [OpenAI Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations)
- [OpenAI client events](https://developers.openai.com/api/reference/resources/realtime/client-events)
- [OpenAI server events](https://developers.openai.com/api/reference/resources/realtime/server-events)
