# ADR 0015: Persistent deterministic Character Kernel

- Status: Accepted
- Date: 2026-08-26

## Context

The first Demo loaded one `character.json` and passed one large `system_prompt` directly to the
selected LLM. Avatar actions were inferred primarily from user keywords. That made relationship
continuity, emotional inertia, prompt budgets, and model-to-model personality consistency implicit
and difficult to test. It also allowed character presentation to drift whenever the chat provider
or context window changed.

## Decision

Runtime owns a provider-independent Character Kernel. A character is a validated package containing
`character.yaml`, `persona.md`, `voice.yaml`, `avatar.yaml`, `relationship-policy.yaml`, and
`lexicon.yaml`; the previous JSON manifest remains a read-only compatibility fallback.

SQLite stores one local user scope of bounded Affect and Relationship state per character. Only
deterministic reducers may change numeric state. User turns, elapsed time, and acknowledged avatar
touches produce small clamped deltas; a single model response cannot set scores or skip relationship
stages. Reset deletes both state projections and recreates the policy defaults on demand.

Before generation, a Response Planner derives a high-level intent, tone, expression, optional
gesture, and response length from the current event plus durable state. The avatar planner intersects
that plan with the character manifest and suppresses repetitive motions. Neither the LLM nor the
Character Kernel emits Live2D parameter ids.

A Prompt Compiler always constructs ordered safety, canon, affect, relationship, response-plan,
memory, and conversation sections. Each section has a bounded budget derived from the independently
configured chat context window. Older history is summarized through the memory-summary role instead
of truncating the beginning of the system policy. The compiler emits a budget report but never logs
prompt contents or private memory.

## Consequences

The same character policy now reaches Demo and OpenAI-compatible chat providers, relationship and
affect survive new sessions, and avatar cues represent planned response semantics instead of parsing
arbitrary assistant prose. The initial reducer intentionally uses a conservative lexical/dialogue-act
classifier; richer classifiers may replace it behind the same state and response-plan contracts.

The current local user scope is single-user. Multi-profile ownership, scene state, skill-result
effects, calibrated sentiment models, and proactive behavior require separate vertical slices and
must preserve the reducer and provenance boundaries.

## Alternatives

Keep all personality and relationship rules in one system prompt; allow the chat model to return
arbitrary state numbers; parse generated prose for Live2D commands; persist character state in the
browser; let each model adapter build its own prompt.
