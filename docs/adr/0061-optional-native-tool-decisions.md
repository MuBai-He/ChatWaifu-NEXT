# ADR 0061: Optional native tool decisions for ordinary dialogue

- Status: Accepted
- Date: 2026-10-01

## Context

The bounded Skill router projects capabilities by lexical relevance. That does
not establish that a user requested an operation. Q02's completed 288-reply Gemini
batch misgated six ordinary character questions and six goodbyes into mandatory
source-tool decisions. An actual Conversation control with the full registered
catalog reproduced the same problem after a successful permissioned READ. The
normal reply was discarded and an unnecessary correction produced a Runtime
fallback. Adding one closing-phrase exception would leave the shared cause intact.

## Decision

Keep capability projection, operation necessity and execution authority separate.
The existing provider-neutral `LlmRequest.tool_choice` carries the distinction;
no public protocol, database migration or additional model classifier is added.

Conversation and the Runtime evaluator use the same bounded current-user intent
policy. Explicit operations, supplied URLs, externally verifiable questions about
current rules/data, natural reminder requests and whole-utterance read corrections
after a trusted local operation use `required`. Ordinary dialogue and wording
outside that policy use native `auto`. The language policy does not route by
capability IDs, consult generated prose, source bodies or recalled memories, or
grant permission. Objectless requests to stop conversational follow-ups do not
cancel saved reminders; dated/named reminder requests retain the operation path.

A subsequent actual Conversation control reproduced mandatory-tool fallbacks for
local text composition, review of supplied calculation/code, and an explicitly
negated URL read. These bounded local-content requests now use auto. A compound
save/send/read request still requires results; English and/then/also commands are
recognized alongside the existing Chinese clause boundaries. A URL explicitly
introduced by a negated read command does not itself demand fetching. This does
not suppress affirmative URLs or operations elsewhere in the same request.

Required decisions retain the smaller trusted safety/time planner, one bounded
missing-call correction and the existing refusal to publish unverified completion
claims. Optional decisions retain the full character/context/history contract and
state that available capabilities are not requests. If a native function is
returned, its preamble remains buffered and execution follows the existing
registry, permission, confirmation, cancellation and result loop. A completed
text-only optional decision is published without requiring a gratuitous operation.
An empty answer or invalid/missing terminal fails explicitly. Cancellation and
generation validity are checked before dispatch and before each published chunk.

All request phases retain the frozen whole-input budget, bounded schema/call
projection and read-only prior-source projection. No tools are replayed or
background reminders created because conversation ends. Nonsecret logs include
generation, decision mode and schema count. The evaluator advances to 1.9.0 and
fingerprints the intent implementation, preventing mixed-code resumes.
The prompt identity template tag advances from v5 to v6 so a subsequent
generation records this prompt-policy change. Existing identity schema 1.0 and
historical version values remain compatible.
The local-content follow-up advances the evaluator to 1.9.1 and prompt identity to
v7; older 4a80465 paid samples retain their original 1.9.0/v6 identity.

An actual source-index consumption batch also reproduced mandatory decisions for
questions restricted to existing pages with an explicit no-new-tools instruction.
Both scope signals now suppress fresh-fact necessity and projected schemas; an
affirmative compound operation still takes precedence. Missing originals remain
an evidence gap, not authority to fetch again. Bounded actual link metadata can
remain when a whole source body is omitted, without representing a target read or
inheriting permission. Reader results preserve the accepted-scheme limitation even
after function schemas close. This follow-up uses template v8/evaluator 1.10.0;
prior paid batches retain their captured identities.

The instant-chat output contract subsequently merges repeated wording while
retaining task, boundary, relationship and brevity priorities. Prior-source JSON
uses compact punctuation whitespace with exactly the same parsed data. Whole
results and the latest user facts still pass the unchanged input guard; neither
compression permits clipping a body, replaying a tool or increasing permissions.
Template v9/evaluator 1.11.0 identify this representation change. Full source
availability in captured inputs is budget evidence, not a behavioral quality gate.

A completed actual native reminder round trip exposed another bounded intent gap:
Chinese sequencing and verification modifiers such as 先/再次/实际/直接 made explicit
queries optional. The shared command prefix now recognizes these modifiers and
also applies to negated URL reads and objectless conversational follow-up closure.
Local composition, supplied-content review and method explanations remain local;
affirmative compound operations and dated/named reminders still require results.
Before/after publisher controls reject an initial unverified completion claim and
allow the final answer after actual READ and WRITE results. Template v10/evaluator
1.12.0 identify the repair. Completed v9/1.11.0 native auto samples remain unchanged
and do not validate the subsequent required-mode behavior. Required selection is
not a guarantee that every requested write follows a successful read.

Shared trusted safety context also supplies the public ChatWaifu NEXT architecture:
a local-first character Runtime with replaceable local/remote model and voice
providers. This design is not evidence of the selected provider's deployment.
Neither private configuration nor a character's guess supplies these facts.
Existing history/ownership/redaction rules remain, with repeated wording merged
to preserve full-source input budgets. Template v11/evaluator 1.13.0 record this
context repair; compiler assertions and archived input fits do not approve identity
quality or absence of greeting regressions. Persona and operation authority remain
unchanged.

## Consequences and validation

This is a bounded language policy, not an exhaustive semantic intent classifier.
An unfamiliar operation can still use native automatic function selection; it
cannot bypass the permission gateway. A model's optional text is not proof of an
external fact or action. Its truthful behavior remains a quality gate. Optional
schema decisions buffer until completion, which can increase first-answer latency;
turns without schemas retain their direct streaming path.

Before/after controls cover actual Conversation with the full registered Skill
catalog, a successful allow-once READ, ordinary teasing and goodbye, original-source
preservation, fresh verification and explicit reminder requests. Further controls
cover explicit operations versus mentions/explanations, contextual READ correction,
native optional calls, denied results, empty/invalid responses, stale output and
input overflow. Existing write confirmation, read-then-write and cancellation
checks remain required. Real Gemini quality and all three presentations remain
separate acceptance work; this ADR does not approve Q02 or change default persona.

## Rollback

Restore required initial decisions in Conversation and the evaluator, and remove
the optional decision branch and intent policy. Existing permission grants, action
receipts, model adapters, history and source storage remain compatible.
