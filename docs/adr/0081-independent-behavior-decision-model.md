# ADR 0081: Independent behavior decision model

Status: Accepted for the owner's 2026-10-07 request.

## Decision

Add `behavior_decision` to the existing persisted model roles. Its default provider
is explicit `inherit_chat`; it resolves the current chat route and credential on
each decision. Independent `openai_compatible` and native `typesafe` routes own their
endpoint, model, timeout and write-only credential. OpenAI-compatible selection
reuses the streaming adapter; Jev uses its actual System One API through a separate
typed `BehaviorDecisionProvider` port. Independent failures never silently switch
to chat. Disabled and demo decision routes are rejected.

The shared `BehaviorDecisionService` resolves this role for group/private free
conversation, ambient participation and task wake decisions. Formal reply generation
and task execution continue to use chat; extraction, summary and embedding keep their
own roles. Save changes affect the next decision, without restarting Runtime. Existing
30-second outer cancellation, source validation, permission and delivery fences remain.
The independent adapter timeout is at most 30 seconds. Decision evidence remains
bounded by the existing behavior contract; chat context budget controls are not shown
for this role.

The shared Web/desktop model routing panel offers inheritance, OpenAI-compatible
selection and TypeSafe Jev. Selecting Jev fills its official `/v1` base URL, `jev-latest`
and a 10-second timeout; credentials are independently write-only. The decision test
endpoint executes the actual selected adapter with synthetic evidence and validates
the resulting `DecisionRecord`; ordinary text is not a successful test. The probe
performs no chat ingress, task execution or external delivery. Users save before
testing the persisted configuration.

New clients explicitly request `include_behavior_decision=true` when listing model
configurations. The default list retains the original four roles so older installed
clients with closed role/provider enums can still open their model settings. Direct
decision-role configuration and testing remain available. A new desktop build is needed
to show its selector; the deployed Web UI opts in immediately.

Jev receives bounded character instructions, structured situation and Choice questions
for an allowed action and, when needed, the exact triggering source. Strictly validate
answer types, allowed options, probability distributions, finite confidence and
original source identifiers. Runtime supplies observable fixed reasons; it does not
parse generated prose or invent task goals. Task/defer goals are copied only from the
selected original message or the already authorized task; defer uses a bounded
30-second wake. Jev proposes no long-term memory writes. Existing deployed group
memory policy remains disabled. Code owns permissions, execution and timing.

The HTTP adapter has a bounded body, response and deadline, preserves cancellation,
rejects redirects, and retries 429/529 at most twice within that deadline. Provider
bodies, endpoints and credentials do not cross error boundaries. Log only actual
model version, action, confidence and duration. Confidence is observable, with no
uncalibrated hard-coded threshold that would override voluntary conversation.

Request/response and model naming follow the official
[TypeSafe API](https://docs.typesafe.ai/api) and [model documentation](https://docs.typesafe.ai/models).

Migration 47 rebuilds only the model-role table to extend its role CHECK constraint.
All nine existing column values and earlier immutable migration checksums are retained;
startup inserts the new inherited role without copying chat credentials. Rollback to
schema-46 software requires the stopped-service pre-upgrade database backup, since
older software deliberately rejects newer migration ledgers. Back up the database
before deployment; do not modify already applied migration scripts.

## Validation

Upgrade/restart tests preserve populated rows, budgets, old checksums and private key
bytes. Native HTTP adapter tests prove separate model/endpoint/credential routing,
live saves, explicit inheritance, chat preservation and rejection of prose-only
decisions. Native Jev tests cover typed actions/sources, extractive goals, private
credentials, malformed responses, bounded retries, timeouts and actual cancellation.
UI tests cover selecting Jev, editing/saving an independent route and structured testing.
Production acceptance distinguishes adapter completion from actual QQ behavior.
