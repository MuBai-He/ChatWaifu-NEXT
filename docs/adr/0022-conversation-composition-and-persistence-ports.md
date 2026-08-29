# ADR 0022: Conversation orchestration depends on domain ports and a speech pipeline

- Status: Accepted
- Date: 2026-08-29

## Context

`ConversationService` previously mixed generation coordination with SQLite statements, event
persistence, TTS provider details, PCM forwarding, audio asset storage, and playback registration.
That made a provider or playback change touch the central turn state machine and made stale-audio
regressions difficult to isolate. It also violated the repository boundary required for product
domains.

## Decision

- The conversation domain depends on a typed `ConversationRepository` port. SQLite transaction and
  event-store details live in `SQLiteConversationRepository` under persistence.
- Commit, completion, cancellation, and failure mutations remain atomic conversation-repository
  operations; the service publishes only events that the repository has already persisted.
- User-confirmed reset uses a separate `ExperienceResetRepository`: session conversation truth,
  the selected character/user memory namespace, and Affect/Relationship truth are deleted in one
  SQLite transaction. Generated WAVs are first moved to a recoverable same-filesystem quarantine,
  restored if the transaction fails, and purged only after commit.
- TTS synthesis, native PCM forwarding, fallback audio publication, playback registration, and
  per-segment avatar speech cues live in `ConversationSpeechPipeline`.
- `ConversationService` retains only turn coordination: active-generation ownership, character and
  memory context, prompt compilation, LLM streaming, semantic avatar planning, and lifecycle
  decisions.
- Runtime composition creates concrete repository and speech dependencies. Domain modules do not
  import the database implementation.

## Consequences

Persistence can be tested with transaction-level fault cases without constructing the full
conversation pipeline. Fault injection verifies that a reset cannot leave memory deleted while
turns or audio survive (or the reverse). Voice transports and providers can evolve without adding
SQL or playback branches to the generation reducer. `ConversationService` remains an application
orchestrator, but its infrastructure-heavy responsibilities have explicit seams and cancellation
tests.
