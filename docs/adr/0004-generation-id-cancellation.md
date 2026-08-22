# ADR 0004: Generation identity controls cancellation

- Status: Accepted
- Date: 2026-08-23

## Context

Cancellation alone cannot prevent already-buffered or late model/audio chunks from reaching
playback after an interruption.

## Decision

Every assistant output and media frame carries a `generation_id`. Only the active generation may
be queued or played. Cancellation marks the generation inactive before downstream teardown; stale
output is dropped at every boundary.

## Consequences

Interruption can be tested deterministically and late output cannot corrupt the next turn. Future
pipelines must propagate identity without substitution.

## Alternatives

Task cancellation only; timestamps; clearing the final audio queue only.
