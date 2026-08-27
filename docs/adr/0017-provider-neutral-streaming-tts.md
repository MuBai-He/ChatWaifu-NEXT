# ADR 0017: Provider-neutral streaming TTS delivery and cloud egress

- Status: Accepted
- Date: 2026-08-28

## Context

ADR 0014 normalized selectable local TTS providers but intentionally stopped at a complete WAV
asset boundary. The local Qwen engine already decodes incrementally, GPT-SoVITS can expose
fragments in supported configurations, and Aliyun Bailian Qwen-TTS-Realtime returns base64 PCM
deltas over WebSocket. Treating the cloud provider as a special browser API would bypass Runtime
identity, cancellation, privacy, playback acknowledgement, and fallback rules.

The old `native_streaming` capability described an engine property even though the worker protocol
still returned one base64 WAV. It therefore did not prove end-to-end streaming delivery.

## Decision

`TtsRouter.stream` is the provider-neutral Runtime boundary. It yields ordered PCM16 fragments and
one completed synthesis result. Native providers may produce fragments immediately; batch providers
are adapted by reading their validated WAV into ordered fragments. Every fragment remains scoped to
the request's session, turn, generation, and segment identity.

Runtime fans PCM fragments out through a separate ephemeral, bounded `AudioStreamHub`. Audio bytes
are never written to the durable event stream or SQLite outbox. A slow subscriber is cancelled on
overflow instead of dropping arbitrary middle fragments. The conversation pipeline still assembles
and validates a complete generation-scoped WAV asset. That asset is the reconnect and unsupported-
client fallback and remains the source for local history/debugging.

The Web client consumes schema-versioned stream messages and schedules PCM16 through Web Audio.
Playback receipts use the existing generation and segment identity. Playback rows may begin with a
provisional duration while synthesis is active and are finalized from the actual PCM byte count. A
cancelled stream keeps its partial duration long enough to audit what was played. Stale generation
fragments, out-of-order sequences, and late provider completion are rejected.

Aliyun Bailian is the first native provider. Its adapter owns WebSocket events, authentication,
voice/model pairing, PCM decoding, size limits, timeouts, and cancellation. The configured voice is
`qwen-tts-vc-bailian-voice-20260828030329088-e738` and the default compatible base model is
`qwen3-tts-vc-realtime-2026-01-15`. The voice identifier is not treated as a model identifier.

Selecting and enabling the cloud provider in the local settings UI is explicit egress consent. Only
the current committed TTS text segment leaves the device. Conversation history, memory, system
prompt, model credentials, and local voice assets are excluded. The API Key is write-only through
HTTP and stored in a mode-0600 ignored local secret file; it is never returned to Web or logged.

## Consequences

Aliyun audio can begin playing before the complete WAV exists, while all existing providers remain
compatible. Local Qwen and GPT-SoVITS can later expose their native fragments without changing the
conversation, playback, or Web contracts. Until their worker endpoints are upgraded, they retain
batch latency despite passing through the normalized stream adapter.

The Runtime now owns an additional ephemeral audio WebSocket and provisional playback duration
state. Clients must keep a bounded queue and preserve fragment order. A connected native stream
consumer suppresses the final WAV/WebRTC replay to prevent overlapping speech; when no consumer is
present, the complete WAV path remains active.

## Alternatives

Call DashScope directly from React; add an Aliyun-only callback beside the TTS contract; persist PCM
fragments as core events; remove the WAV asset path; claim local engine-internal streaming as
end-to-end streaming without changing delivery.
