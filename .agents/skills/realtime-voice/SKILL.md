---
name: realtime-voice
description: Implement or debug ChatWaifu realtime media, Pipecat, transport, VAD, STT, turn detection, LLM/TTS streaming, playback, interruption, cancellation, reconnection, buffering, or latency instrumentation.
---

# Realtime Voice

Treat interruption correctness as the primary invariant. Every assistant generation
has session, conversation, turn, and generation IDs plus an explicit lifecycle.
Only the active generation may reach playback.

On barge-in, mark the generation obsolete, cancel downstream LLM/TTS work, flush
pending playback, reject late chunks, and return to listening. Cancellation must
propagate through coordinator, model stream, segmentation, synthesis, queues, and
playback. Never use arbitrary sleeps for synchronization or swallow CancelledError.

Keep partial transcripts separate from final transcripts and committed turns. Keep
turn detection replaceable and provider objects inside adapters. Define bounded
buffer ownership and intentional backpressure. Disconnect teardown must cancel
tasks, close queues, release media, and prevent ghost output.

Instrument speech start/end, STT partial/final, turn commit, LLM request/first token,
TTS request/first audio, playback, and interruption. Test normal turns, cancellation
before audio and during TTS, late chunks, disconnects, provider errors, and slow
consumers.
