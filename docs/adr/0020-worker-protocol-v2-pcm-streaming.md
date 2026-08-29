# ADR 0020: Worker Protocol v2 streams identity-scoped PCM frames

- Status: Accepted
- Date: 2026-08-29

## Context

The local Qwen3-TTS and GPT-SoVITS engines could decode incrementally, but the
worker v1 boundary returned one complete base64 WAV. Runtime then split that WAV
into 100 ms pieces, which looked like streaming to downstream clients while
preserving the full model-generation delay. Cancellation also had no framed
chunk identity with which to reject late audio.

## Decision

Local neural TTS workers expose authenticated WebSocket endpoint
`/v2/stream/tts` in addition to the existing v1 complete-WAV endpoint.

- The client sends a `tts.stream.start` envelope containing the immutable v1
  synthesis request and all five request identities.
- The worker acknowledges with `tts.stream.ready` before sending audio.
- Audio uses versioned binary PCM16 frames. Every frame carries
  `generation_id`, `job_id`, monotonically increasing `sequence`, sample rate,
  and channel count.
- A typed terminal envelope repeats the full identity and declares chunk count,
  format, duration, provider, and model.
- Worker production crosses a bounded queue. Cancellation closes the stream,
  signals the native engine, and invalidates the generation before late chunks
  can reach playback.
- Runtime validates identity, order, format stability, and a maximum audio size;
  it forwards PCM immediately while accumulating the same bounded asset into a
  WAV for playback fallback and history.
- `/v1/synthesize` remains available for non-streaming workers and compatibility.

## Consequences

Qwen MLX and GPT-SoVITS now provide real first-chunk latency rather than
post-hoc WAV slicing. The protocol has a small custom binary header, but it is
owned by the dependency-light worker SDK and covered by round-trip, mismatch,
ordering, cancellation, and WebSocket tests. Other workers can remain on v1 by
not advertising `pcm.v2`.
