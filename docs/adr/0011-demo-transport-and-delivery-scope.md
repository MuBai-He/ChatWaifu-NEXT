# ADR 0011: Basic demo transport and continuous delivery scope

- Status: Accepted
- Date: 2026-08-23

## Context

The project needs a usable local demo before the complete realtime media roadmap is finished. The
demo must exercise actual session, provider, persistence, memory, Runtime Skill, avatar, playback,
and interruption boundaries without making proprietary models or GPU hardware mandatory. The owner
has authorized continuous delivery through the remaining roadmap and requested frequent local Git
commits instead of per-phase approval gates.

## Decision

The first usable demo is one local modular-monolith vertical slice:

```text
Web client
  -> versioned WebSocket commands/events + HTTP audio assets
  -> Runtime session/conversation coordinator
  -> replaceable LLM, STT, and TTS provider interfaces
  -> SQLite WAL event/session/turn/generation/memory/skill state
  -> semantic AvatarCue + AvatarInteractionEvent
```

WebSocket transports typed control and low-bandwidth conversation events. Synthesized WAV assets
are served over loopback HTTP and played by the client. This does not replace accepted Pipecat and
WebRTC architecture: the provider/coordinator contracts and generation cancellation semantics are
the migration boundary for a later full-duplex media transport.

The demo acceptance path includes text input, optional microphone transcription when a local ASR
adapter is available, streaming assistant text, audible TTS, Avatar cues and lipsync, interruption,
explicit remember/forget behavior, and a read-only system-status Runtime Skill.

## Consequences

The demo can run without a GPU or cloud key and remains testable with fake providers. Audio latency
is segment-oriented rather than full-duplex. WebRTC echo, production VAD/turn detection, arbitrary
third-party plugins, proactive behavior, and cloud realtime remain outside the basic-demo release
gate. Every subsequent slice is committed locally after its focused tests pass; no remote push is
implied.

## Alternatives

Block all product behavior until Pipecat/WebRTC is complete; call providers directly from React;
bundle model weights in Git; require phase-by-phase user approval.
