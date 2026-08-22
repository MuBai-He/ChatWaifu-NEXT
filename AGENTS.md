# ChatWaifu NEXT Engineering Instructions

## Mission

Build a maintainable, local-first realtime AI character runtime with replaceable
voice/model providers, durable character state, memory, runtime skills, semantic
avatar cues, and shared web/desktop clients.

Correctness, architecture consistency, cancellation safety, testability, privacy,
and observability matter more than minimizing file count.

## Before modifying code

1. Read `CODEX_HANDOFF.md`, then the task-relevant architecture and implementation-plan sections.
2. Inspect `docs/implementation-status.yaml` and accepted ADRs.
3. Identify the affected domains and read only the relevant repository skill.
4. Inspect existing contracts, producers, and consumers before adding new ones.
5. State the explicit scope, exclusions, and smallest complete slice.

## Architecture boundaries

Keep these domains separate:

- realtime media and transport
- session and conversation coordination
- model provider adapters
- agent reasoning and character context
- memory
- product runtime skills and permissions
- avatar planning and rendering
- frontend application state
- persistence and observability

Provider SDK objects stay inside adapters. Frontend code never calls STT, TTS,
or LLM providers directly. Memory and runtime skills never access database
implementations directly. The agent emits semantic avatar cues; only a renderer
adapter knows Live2D parameter or asset identifiers.

Use typed, versioned contracts at lifecycle, asynchronous, and cross-domain
boundaries. Ordinary local logic may remain direct function calls; do not turn
every internal operation into an event.

## Realtime invariants

Every generation carries `session_id`, `turn_id`, and `generation_id`. Only the
active generation may reach playback.

Every streaming change must account for interruption, task cancellation, late or
out-of-order chunks, bounded buffering, reconnection, and teardown. Do not use
arbitrary sleeps for synchronization and do not swallow `CancelledError`.

## Memory and runtime skill invariants

Long-term memory is not a transcript archive. Writes pass through extraction,
policy, deduplication, provenance, and privacy checks.

Product Runtime Skills live under `skills/`; Codex Development Skills live under
`.agents/skills/`. Runtime capabilities require versioned schemas, permissions,
side-effect classification, timeouts, normalized errors, and cancellation.

## Development discipline

Prefer complete vertical slices: contract, domain behavior, adapter or integration,
tests, observability, and documentation. Avoid speculative abstractions, unrelated
refactors, unbounded queues, global mutable session state, provider keys in clients,
and parsing arbitrary model prose to control system state.

Significant hard-to-reverse architecture decisions require an ADR under `docs/adr/`.
SQLite WAL + FTS5 is the first persistence target; replacing it requires an ADR.

## Completion gate

Run the relevant formatter, linter, type checks, unit tests, integration tests, and
frontend build. Realtime changes require cancellation and stale-output tests.
Report changed behavior, architecture decisions, checks run, and remaining risks.
