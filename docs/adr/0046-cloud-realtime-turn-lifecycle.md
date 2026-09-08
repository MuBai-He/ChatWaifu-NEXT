# ADR 0046: Cloud realtime turn ordering and terminal ownership

- Status: Accepted
- Date: 2026-09-08
- Scope: Phase 13.4A lifecycle hardening, before real provider rollout

## Context

The cloud bridge sends audio on a background task. Calling `commit_input` directly from VAD stop
can overtake that task. Waiting for remote interruption before clearing local playback delays
barge-in. A pending output handoff can also resume after a playback flush. Finally, a provider
stream can end without a close event, or finish closing after its replacement has begun a new
turn under the same Runtime session ID.

## Decision

1. Audio and a generation-scoped commit barrier share one bounded FIFO. Audio keeps the existing
   oldest-frame drop policy; a reserved slot protects the commit barrier. Repeated VAD stop is
   idempotent, and audio after stop is ignored until the next admitted utterance.
2. Barge-in tombstones the old generation, cancels/joins a pending output handoff, and flushes
   downstream playback before any provider interrupt await. It cancels/joins the old input sender
   and drains its queued audio/commit before sending provider cancel/input-clear commands. New
   input may use the connection only after that interrupt call completes. Output cancellation
   does not cancel the provider event pump; cancellation of the pump itself still propagates.
3. Input send, commit and interrupt each have a 5-second deadline. Timeout or another media error
   seals the bridge, records a terminal generation outcome, drains input, stops local output and
   closes the provider. Adapters must propagate cancellation and release socket-write ownership;
   interrupt completion means commands were serialized, not that the server acknowledged them.
4. EOF, provider close, stream failure and teardown share one shielded cleanup task. Close has a
   2-second deadline. Cancelling an individual stop waiter does not abandon cleanup, and repeated
   stop calls join the same task. Closing rejects new admission and late provider events. An
   admission already writing to storage is retained in an owned task and joined/cancelled through
   the admission port, even when its caller is cancelled before receiving the committed identity.
5. Terminal writes use the coordinator's owned generation identities and the existing Runtime
   CAS. `session_closed` is observational: it must not terminate whichever generation happens to
   be active under the Runtime session ID when a delayed old provider close finishes.

## Validation and limits

Controlled Event barriers exercise audio/commit order, full queues, pending output, barge-in,
stalled operations, EOF/error, and cancellation of close waiters. RuntimeContainer/SQLite tests
verify exactly one cancellation for admission interrupted by closure and preserve a replacement
connection's active generation while an old close finishes.

No schema changes or provider SDKs are introduced. The default Cascade voice configuration is
unchanged. This does not implement automatic reconnect, context restoration, provider adapters,
shadow transcription, cloud tool execution, or real network/microphone acceptance. A fresh
bridge still goes through the existing egress gateway; old PCM is not replayed.
