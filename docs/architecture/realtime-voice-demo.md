# Realtime voice demo slice

## Supported flow

```text
browser getUserMedia (mono, EC/NS/AGC)
  -> browser activation gate (push-to-talk by default; open mic is explicit)
  -> WebRTC offer/answer on loopback
  -> Pipecat SmallWebRTC input
  -> Silero VAD
  -> bounded PCM16 utterance buffer
  -> authenticated faster-whisper worker
  -> ConversationService final-text command
  -> configured LLM and local TTS
  -> generation-gated Pipecat output
  -> browser remote audio track + ordered segment markers
  -> browser output-media clock playback ACK
  -> Runtime spoken-text projection
```

The browser never calls STT, LLM, or TTS providers. Pipecat owns media transport, VAD frames,
interruption frames, and transport teardown; it does not own sessions, character policy, memory,
skills, or generation truth.

## Activation and VAD boundary

VAD answers “is this audio speech?” and “where did this utterance end?” It cannot answer “was this
speech addressed to ChatWaifu?” Threshold tuning alone therefore cannot distinguish a command from
a conversation with another person nearby.

The browser defaults to `push_to_talk`. Its outbound audio track is disabled before and after a
press and enabled only while the control is held; releasing it produces silence so Runtime VAD can
close the current utterance normally. `open_mic` is an explicit opt-in for quiet, single-user rooms
and warns that nearby speech may start a turn or interrupt current output. Both modes retain the
same WebRTC, VAD, STT, identity, and cancellation contracts.

A later hands-free attention layer may combine a local wake word, a short armed-conversation window,
and optional enrolled-speaker verification. Semantic addressee classification can be an additional
signal after STT, but it must not be the sole privacy or interruption gate. This attention layer is
not claimed as implemented in the current demo.

## Identity and cancellation

At VAD speech start the Runtime allocates `utterance_id`, `audio_stream_id`, `turn_id`, and
`generation_id`. Speech lifecycle and transcript events retain that identity. The STT worker request
also carries session, turn, generation, job, and request IDs through the versioned worker SDK.

Speech start first pushes a Pipecat `InterruptionFrame`, then requests conversation cancellation.
It cancels any superseded STT job and discards a result whose identity is no longer current. Runtime
TTS is forwarded only when its generation is the bridge's active output generation. Browser UI and
Avatar state apply the same generation invalidation rule.

## Playback truth

Each synthesized sentence is registered before delivery with a stable `generation_id`,
`audio_stream_id`, `segment_id`, segment index, duration, and text. The ordinary HTML audio path
reads `currentTime` and buffered ranges directly. The WebRTC path sends ordered `started` and
`buffered` markers over the `chatwaifu-runtime` data channel and measures progress against the
remote audio element's media clock. Both paths send typed, serial playback acknowledgements at most
every 250 ms; the browser queue is bounded and coalesces superseded progress updates.

Runtime validates acknowledgement identity and monotonic progress, stores segment snapshots and
audited playback events, and exposes the current projection at
`GET /v1/sessions/{session_id}/generations/{generation_id}/playback`. A sentence is appended to the
generation's `spoken_text` only after its segment reports `stopped` with reason `ended` and reaches
the registered duration tolerance. Interruption, errors, queue clearing, and partial progress do not
claim that text was heard. Duplicate commands and terminal retries are idempotent.

The projection is session/segment truth rather than per-browser analytics. If the same session is
open in several tabs, acknowledgements are merged monotonically and one completed playback commits
the segment once; the current protocol does not identify which tab completed it.

## Device loss and reconnect

The browser listens for both microphone-track `ended` and media-device `devicechange`. If the
selected device disappears, it chooses the first remaining input, updates the visible selection,
and rebuilds the peer connection. WebRTC `failed` reconnects immediately; `disconnected` gets a
750 ms grace period. Failed attempts use bounded 250/500/1000/2000/4000 ms backoff, preserve the
chosen activation mode, and tear down the old Runtime peer before a new offer. Explicit disconnect,
component disposal, or permission denial cancels recovery; after the retry limit the UI asks for a
manual retry.

The inference thread used by faster-whisper cannot be force-killed safely. Worker cancellation
cancels the request task and the Runtime rejects a late result; an in-progress native inference may
finish in the background before worker capacity is reclaimed.

## Process and privacy boundary

`make demo` keeps the ASR model out of the Runtime environment. It synchronizes the independently
locked worker, creates a random token and free loopback port for that run, starts the worker, waits
for its authenticated health response, and injects only the temporary endpoint/token into Runtime.
The token is redacted from public configuration. Model data is cached under the ignored
`.local/models/faster-whisper/` directory.

## Deliberate exclusions

- Web Demo only; Tauri sidecar packaging is not claimed.
- One browser peer per voice connection; public TURN and multi-machine media are not configured.
- The ordered data channel carries playback markers only. There is no generic RTVI control protocol;
  ChatWaifu HTTP and typed Domain Events remain the control plane.
- Final transcription only. The protocol supports partial events, but faster-whisper partial
  streaming is not presented as implemented.
- Playback ACK is segment-granular because the current TTS adapters do not expose word boundaries;
  it cannot prove which word inside a partially played sentence was heard.
- The `base` model favors footprint and startup over maximum Mandarin accuracy. A heavier model can
  replace the worker configuration without changing Runtime or frontend contracts.
