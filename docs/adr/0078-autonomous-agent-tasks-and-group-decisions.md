# ADR 0078: Shared autonomous Agent, durable tasks and selected group evidence

- Status: Accepted for source implementation; production enablement and native receipt acceptance remain separate.
- Date: 2026-10-07
- Supersedes: ADR 0075's mention-only decision and no ordinary-message memory constraints **only in explicitly enabled routes**. Existing off-mode behavior is preserved.

## Decision

Desktop, QQ and subsequent channels use the existing character/model/memory Runtime. A second agent service is unnecessary. Model output selects structured actions and capabilities; trusted Runtime code controls identity, resources, versions, budgets and effects. Free-form model prose never grants authority.

`CapabilityCatalog` supplies permanent search, inspect and activate tools. Detailed schemas and product `SKILL.md` load on demand. Search misses can be followed by category browsing, signed pagination and reformulated queries. Activation changes model visibility, never permissions. Catalog statuses distinguish permission, configuration, adaptation and platform limitations. QQ metadata is pinned to official NapCat OpenAPI 4.18.33; imported actions remain unexecutable until their scope adapter is reviewed. Current message/group/file references are Runtime-issued, bound to live account, route, audience and member revisions. Private Calendar and owner workspace permissions never follow a group member.

`BehaviorDecisionService` emits wait/respond/clarify/task/defer/capability-gap decisions with bounded public reasons and original source references. Group observation is volatile, bounded and event-driven. Defaults are off; shadow records decisions without messages, tasks or memory writes. Member mode uses existing speaking grants, quiet hours and the specified 3/10/240/20/30 resource limits. Only selected, complete original messages enter the existing scoped memory extraction/privacy/dedup/correction/forget pipeline; compressed discussion and the entire transcript are never memory sources. Turning off a route revokes pending autonomous generations and task deliveries without deleting task, artifact or delivery facts.

`AgentTaskService` owns persisted goals and grants, not single tool implementations. `RuntimeSkillService` retains execution, confirmation, cancellation, timeout and plugin responsibilities. Every task is scoped, versioned, budgeted and checkpointed in SQLite. Explicit owner task grants bind selected skills, executor fingerprints, workspace roots, Calendar IDs, write permission and expiry. Background channel targets are issued by Runtime and revalidated before effects; HTTP task creation cannot inject them. Owner local sessions share task/artifact visibility. Group scenes remain isolated.

The durable loop executes, checks evidence, verifies completion and repairs within the task budget. Workspace writes require subsequent matching path/checksum readback. Legacy chat retains its single-turn write boundary. New chat or voice interruption does not cancel a goal; pause/cancel revokes and joins the worker and its skill runs. Restarted uncertain writes wait for owner evidence. Confirmed successful writes return recorded results; definitely rejected operations may retry; uncertain or owner-reconciled operations cannot blindly resubmit. Owner reconciliation records evidence rather than inventing provider receipts. Task authorization updates pause execution and require an explicit resume.

Immutable `ArtifactRef` binds scope, task, content hash and version. Runtime workspace paths cannot escape authorized roots. Word uses a Python worker; PPT uses Node/PptxGenJS. ZIP structure and PDF rendering/text are verified before a rendered artifact is published. Worker cancellation terminates and joins processes. Frozen desktop Runtime currently advertises document/material extraction as unavailable rather than invoking itself recursively; the source Runtime with declared dependencies is the executable location.

QQ files and background result text use the existing reliable delivery ledger and scheduler, with explicit schema 1.2 task source. Inbound 1.0 and proactive 1.1 sources retain their contracts. Autonomous task messages reserve actual speech slots atomically and respect spacing/hourly limits; explicit requested task replies are separate. Unknown transport outcomes remain unknown and never trigger an automatic second send. Artifact validation, platform acceptance and user receipt/openability remain distinct facts.

Candidate development is owner opt-in, one per day, isolated and network-denied. The initial executable candidate format is a stateless JSON-to-JSON Python MCP capability with schemas, tests and immutable package hash. It can repair test failures, deliver a disabled candidate for review, recover interrupted installation and enable only the exact owner-approved package. Existing plugin disable supplies rollback. Core Runtime patches, arbitrary dependency installation and deployment need their own reviewed release pipeline; these are not implicitly granted to candidates.

## Persistence and compatibility

Migrations 45 and 46 add Agent facts and desktop decision mode, then rebuild delivery tables while preserving original rows, indexes, constraints and durable event references. No old migration is rewritten. Protocol Python, JSON Schema, TypeScript, Zod and shared fixtures carry the same authority and checkpoint fields. API/UI controls use CAS and server permission checks.

## Consequences and acceptance

Capability discovery and behavior remain model-dependent and require repeated real-model acceptance, including misses and repair. Deterministic tests cover privacy, account/route/version revocation, cancellation, late results, restart, known/unknown effects and delivery duplication. Real browser checks exercise both products against a disposable real Runtime. Native QQ handset receipt and Calendar writes cannot be inferred from fixtures. Evidence and reproduction instructions live in `docs/agent-autonomy.md` and `docs/research/ningning-agent-2026-10-07/`.
