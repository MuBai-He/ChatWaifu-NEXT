# ADR 0071: Bounded source-answer coverage candidate

Status: Proposed; default-off acceptance candidate

## Evidence and decision

Q02 v36 completes real source flows but loses an earlier unresolved time scope.
Frame 1.1 can preserve explicitly declared gaps in isolation. Quote-only frame 2.0
preserves selected source wording but fails relevance, readability and language
conversion. Neither prototype is adopted as a product response or quality gate.

Implement the complete lifecycle for **natural-language** frame 1.1 as a default-off
candidate. Do not introduce another persona or domain-specific prompt rule. The
existing frame instruction describes serialization only. Free prose and headings
remain unverified model output; source relations, conditions and applicability
remain full-text acceptance questions. Source keys are audit references, not truth.

## Responsibilities and boundaries

- Provider: a neutral immutable JSON Schema request contract, OpenAI-compatible
  mapping, explicit unsupported-format errors, and whole-wire reference counting.
  Do not silently retry structured output as prose. Existing transport retry limits
  remain; tools and their permissions retain their current owner.
- Agent: collect and validate an entire bounded terminal frame before exposing
  rendered natural-language text. Buffer at most the existing 64KiB frame bound;
  cancellation/stale-generation checks own and close the provider iterator. Actual
  available READ results establish input provenance only. Explicit supplied
  documents use a separate versioned untrusted context block, never a READ receipt.
- Source identity: actual URL and reader-body hash plus a fingerprint of the visible
  text, offset, truncation and origin. Different excerpts or unavailable/budget-
  omitted originals cannot reuse coverage as if they were the same snapshot.
- Conversation: retain only declared gaps and source keys from a successful
  completed generation, not raw envelopes or source bodies. Load only generation
  IDs from prepared, unredacted same-route history which the repository confirms
  completed in the same session. Reuse the existing eight-generation limit and a
  bounded 128-entry process-local store. Bind character/package, local principal
  and nonsecret provider/model/endpoint identity; presentation/tools/budget changes
  do not silently erase known scope. Reset/stop clear the store; eligibility pruning
  removes redacted/other-route/stale records. Restart/eviction are not recovery.
- Publication: a validated frame is still provisional until Conversation commits
  completion. Cancelled/failed/late frames cannot become next-turn state. A safe
  optional completion marker distinguishes provider-frame rendering from Runtime
  fallback; gap text is not added to a new durable table or diagnostic log.

When an already buffered optional answer is source-informed, one source-format
pass replaces/uses the existing source revision path; it may add one call where
that path previously published plain text. Record all such calls and latency. No
unbounded retry, recursive formatting, new external operation or scope expansion.

## Scope, failures and gates

Production configuration, default persona, model route, budget, databases, services
and channels remain unchanged. The candidate is enabled explicitly by the isolated
Conversation/evaluation harness. Ordinary replies and default-off requests remain
on the existing path. Generic supplied material does not grant permission to read
its URL. Unsupported schema, invalid/nonterminal/oversized frames and transport
failures remain distinct; budget failures do not claim that supplied material was
fetched. No model gap is promoted to memory, truth or operation authority.

First add failing software checks for schema immutability/counting, unsupported
formats, retained gaps, snapshot identity, generation completion and cancellation,
route/principal/redaction isolation, reset/stop and bounds. Independently review
the actual diff and run local/server checks. Then run complete four-turn frozen
ICAO/AirChina flows with actual rendered history and classify every attempt and
reply origin. Preserve payload, omissions, reported usage, latency and author
full-text review. Do not infer quality from JSON validity or state retention.

Only a stable complete-source candidate advances to autonomous retrieval, role
directional tests and the original 864-reply A/B under identical frozen Runtime
configuration. Existing accepted delivery/playback evidence is not repeated.
If this candidate fails, freeze its whole result without continuing format/prompt
variants or lowering the original quality standard. Rollback removes the opt-in
candidate and its process-local store; no migration or production rollback occurs.

## v38 evidence and candidate serialization responsibility

The v37/v38 frame1.1 results remain frozen and rejected. In all four v38 third
turns, the precise unresolved time/operator scope was written in ordinary body
text while `new_gaps` was empty. Three fourth-turn lists then lost that scope even
though every earlier generic gap was retained and the full actual history and
originals were sent. A second optional textual representation cannot prove that
all unresolved body content was declared.

The next default-off candidate uses frame1.2: each natural-language block must
explicitly classify `unresolved_scope` as a boolean. A true block's exact text is
the retained statement, bound to its actual source indices. There is no separate
`new_gaps` statement list. Prior declared scopes remain monotonic; a literal
source-bound restatement preserves the prior identity without a duplicate printed
copy. Existing block organization, source identity, state lifecycle and budget
owners remain unchanged. Missing/nonboolean classifications, legacy envelopes,
unbound or oversized scope blocks are rejected before publication, never clipped.

This consolidates serialization/state representation, not source verification.
The model can still mark a limitation false or produce a false true block; Runtime
must not infer flags from prose, promote statements to truth, or overwrite old
unknowns. Full-text review must check classification completeness, scope fidelity,
conditions and readability. Persona and existing source-review policy do not change.
No production enablement or durable migration occurs. One bounded same-source
complete-flow gate precedes independent retrieval/role/full A/B; failure freezes
this entire version and does not authorize further layout/persona experiments.
