# Realtime voice demo slice

## Supported flow

```text
browser getUserMedia (mono, EC/NS/AGC)
  -> WebRTC offer/answer on loopback
  -> Pipecat SmallWebRTC input
  -> Silero VAD
  -> bounded PCM16 utterance buffer
  -> authenticated faster-whisper worker
  -> ConversationService final-text command
  -> configured LLM and local TTS
  -> generation-gated Pipecat output
  -> browser remote audio track
```

The browser never calls STT, LLM, or TTS providers. Pipecat owns media transport, VAD frames,
interruption frames, and transport teardown; it does not own sessions, character policy, memory,
skills, or generation truth.

## Identity and cancellation

At VAD speech start the Runtime allocates `utterance_id`, `audio_stream_id`, `turn_id`, and
`generation_id`. Speech lifecycle and transcript events retain that identity. The STT worker request
also carries session, turn, generation, job, and request IDs through the versioned worker SDK.

Speech start first pushes a Pipecat `InterruptionFrame`, then requests conversation cancellation.
It cancels any superseded STT job and discards a result whose identity is no longer current. Runtime
TTS is forwarded only when its generation is the bridge's active output generation. Browser UI and
Avatar state apply the same generation invalidation rule.

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
- No RTVI data/control channel yet; ChatWaifu HTTP and typed Domain Events remain the control plane.
- Final transcription only. The protocol supports partial events, but faster-whisper partial
  streaming is not presented as implemented.
- Playback progress and queue-clear acknowledgements are pending hardening. Generation gating and
  Pipecat interruption already prevent superseded chunks from being newly enqueued.
- The `base` model favors footprint and startup over maximum Mandarin accuracy. A heavier model can
  replace the worker configuration without changing Runtime or frontend contracts.
