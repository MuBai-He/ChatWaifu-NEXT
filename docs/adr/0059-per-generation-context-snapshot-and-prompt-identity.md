# ADR 0059: Per-Generation Context Snapshot and Prompt Context Identity

- Status: Accepted
- Date: 2026-09-29

## Context

ChatWaifu NEXT requires deterministic turn execution across asynchronous boundaries and reliable
traceability of character behavior changes. In multi-turn interactions, runtime configuration updates
(such as chat model selection, memory summary model, context window size, or base URL changes),
credential rotations, character package updates, and runtime skill permission changes can occur
concurrently with active conversation turns.

Previously, `PromptCompiler` queried live configuration from `ModelConfigurationService` during prompt
construction, while streaming and tool execution queried the live provider and router later during the
turn. If a configuration update occurred while a turn was in flight, the prompt compilation budget could
diverge from the actual provider capabilities and token limits, causing unexpected truncation or runtime
errors. Furthermore, compiled prompts did not persist identity metadata; when personality or response
characteristics shifted, it was impossible to determine whether the change resulted from a character
package modification, prompt template change, model route swap, or tool visibility change.

QQ Agent Plus attempted to address this with a parallel thread/lease database and dynamic token refresh
mechanisms. However, that design introduced mutable shared state across threads, leaky abstraction
boundaries, and complex rollback overhead incompatible with ChatWaifu NEXT's local-first architecture
(ADR 0015, ADR 0022).

## Decision

### 1. Snapshot Ownership and Capture Moment

`ConversationService` owns an immutable per-generation configuration and context snapshot
(`GenerationContextSnapshot`). This snapshot is captured strictly at turn admission inside
`ConversationService._start_lock` (within `_submit` and `submit_proactive`), before any asynchronous
generation background task (`asyncio.create_task`) is spawned.

The snapshot freezes:

- The `chat` route configuration (`ModelRoleConfig`), including provider, model, context window, and timeout.
- The `memory_summary` route configuration (`ModelRoleConfig`).
- The character package identity, including character ID and a deterministic SHA-256 hash of the six-file
  character package (`character.yaml`, `persona.md`, `voice.yaml`, `avatar.yaml`, `relationship-policy.yaml`, `lexicon.yaml`).
- The presentation profile (e.g. `instant_message` or `default`).
- The prompt template version (initially `v2`; `v3` adds the trusted admission time).
- The generation's trusted admission time from its persisted Runtime turn event, normalized to UTC
  by the compiler. Delayed retrieval or a midnight boundary cannot change this time within a turn.
- The frozen provider adapter (`LlmProvider`) configured for the captured chat route.
- The bounded tool schemas (`ProjectedAgentTool`) visible to the generation, along with their deterministic digest.
- The typed, versioned `PromptContextIdentity`.

Neither global `ContextVar` nor parallel provider-thread/lease databases are introduced.

### 2. Frozen vs. Live Control Plane

The system enforces a strict boundary between frozen generation parameters and the live control plane:

- **Frozen parameters**: Route, model, context window, compiler budget, and tool schemas visible to the LLM
  are frozen at admission. `PromptCompiler` uses the frozen context window to establish safety, persona,
  memory, and conversation token budgets, and uses the frozen summary route for history summarization.
  The provider streaming request uses the frozen adapter. Configuration updates made during turn execution
  affect only subsequent admitted generations.
- **Live control plane**: Explicit cancellation, superseded generation stops, and skill permission checks
  remain strictly live. When a tool call is executed during a turn, `AgentTurnOrchestrator` invokes the
  skill through `RuntimeSkillService.invoke`, which evaluates live permission grants, session scopes, and
  cancellation. If a skill's permission was revoked after admission, execution is denied immediately,
  ensuring revocation cannot authorize an unintended side effect. Cancellation checks (`ensure_current()`)
  continue to suppress stale or interrupted generation output immediately.

### 3. Nonsecret Identity and Credential Rotation

`PromptContextIdentity` is versioned (`1.0`) and typed. It contains:

- `identity_hash`: Deterministic SHA-256 digest of canonical nonsecret fields.
- `character_id`: Stable identifier of the active character.
- `character_package_hash`: Deterministic digest of the six-file character package.
- `prompt_template_version`: Template revision string (`v3` for the admission-time template).
- `presentation_profile`: Active presentation surface mode.
- `chat_route`: Nonsecret route identity (provider, model, hashed endpoint route, context window).
- `memory_summary_route`: Nonsecret route identity for summarization.
- `tools_digest`: Deterministic digest of visible tool schemas.

The identity strictly excludes secret API keys, URL queries/tokens/paths, user input text, memory bodies,
prompt text, dynamic token counts, and the admission timestamp. Time is dynamic context, not static
configuration identity; it can be recovered from the existing generation/turn events without adding
a timestamp to the public identity contract.

Credential rotation (updating an API key in local secret storage) does not alter the nonsecret route
identity or its hash. The provider adapter accesses current credentials live via the local secret store,
allowing seamless credential rotation without invalidating prompt context identity.

### 4. Event Persistence and Compatibility

- `character.prompt_compiled` persists `PromptContextIdentity` as an additive `identity` field alongside
  the existing `report` in `EventStore` JSON payload.
- No database DDL or migrations: events are stored directly within existing SQLite JSON columns.
- Historical events and prior turns lacking `identity` remain valid and are explicitly treated as `unknown`
  (`None`), never backfilled or fabricated.
- Character package updates take effect only upon runtime restart when `CharacterService` reloads manifests.
  A new `character_id` continues to follow existing local user scope isolation rules.

### 5. Historical Stylistic Precedence

Current persona, safety rules, and output contracts explicitly outrank stylistic patterns or quirks in
historical assistant turns. Historical user facts and source attribution contexts are preserved, while
obsolete assistant phrasing, verbose formulas, or persona drift in earlier history are not mirrored
or summarized into character personality.

### 6. Tool Digest Granularity

The `tools_digest` is computed deterministically from the canonical JSON serialization of sorted tool
names, descriptions, and input schemas visible to that turn. Turns with no tools use a stable empty
digest.

## Consequences

- Configuration changes made mid-turn cannot cause budget or token limit mismatches within an active turn.
- Prompt context identity enables deterministic diagnostic tracing without leaking credentials or private text.
- Tool schemas remain bounded and frozen to what was admitted, while permission revocation remains immediate.
- Full backwards compatibility with existing event history and zero SQLite schema migrations.
