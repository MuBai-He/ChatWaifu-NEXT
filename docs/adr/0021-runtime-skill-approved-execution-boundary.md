# ADR 0021: Immutable Runtime Skill approvals and persistence boundaries

- Status: Accepted
- Date: 2026-08-29

## Context

ADR 0018 completed the MCP Host and loopback MCP Server, but the first implementation still had
three unsafe seams. A confirmation named a Skill and stored its arguments, then execution looked up
the mutable registry and MCP connection again. A plugin or connection could therefore change after
the user approved it. Runtime Skill domain services also issued SQLite statements directly, which
violated the repository boundary and made concurrency policy hard to test independently. Finally,
plugin code and writable plugin data shared one directory, audit rows retained arbitrary payloads,
and bearer-token file replacement could not be committed atomically with SQLite configuration.

These are security and lifecycle boundaries rather than UI details. Fixing only the confirmation
screen or adding more locks at HTTP routes would not protect calls originating from the loopback MCP
Server or future agent orchestration.

## Decision

Every invocation creates an immutable, versioned `ExecutionPlan` before permission evaluation. The
plan freezes the complete capability schemas and policy, adapter kind and target, interruptibility,
background request, plugin package digest, MCP connection identity and revision, and a runtime-keyed
argument HMAC. Durable argument summaries are schema-aware redacted and size-bounded; the original
arguments exist only in Runtime memory while the run is active. A restart expires the run instead of
reconstructing a secret-bearing invocation from audit storage.

Confirmation is valid for five minutes. Deciding it uses one conditional SQLite update inside the
same immediate transaction that creates any permission grants, so concurrent decisions have exactly
one winner. Before decision and immediately before adapter execution, Runtime recomputes the plan's
mutable identities. A changed manifest, package byte, capability schema, adapter target, plugin
policy, or MCP connection revision invalidates the approval with `approval_context_changed`; no
adapter executes and the caller must invoke again. Per-connection async leases serialize discovery,
resource/prompt access, tool execution, update, and deletion, while SQLite foreign keys remain the
final cross-process integrity boundary.

Reusable grants are keyed by a permission-subject fingerprint derived from the skill version,
complete capability policy/schema, adapter target, plugin identity and package fingerprint, and MCP
connection identity and revision. The corresponding explicit subject columns are retained for
diagnostics. Plugin lifecycle changes and every MCP connection revision change revoke matching
grants transactionally; legacy grants migrate as revoked. A matching permission name alone can
therefore never authorize upgraded or reinstalled executable content.

Runtime Skills depend on a `RuntimeSkillRepository` port. The SQLite adapter owns persistence for
runs, tool-call audit, permission requests and grants, plugins, and MCP connections. Domain services
do not import the database implementation or issue SQL. The repository also owns the atomic
permission-decision transition and expiry of its associated waiting run.

Audit serialization is deny-by-default: arbitrary arguments and results become a bounded structural
summary and byte count without a reusable content digest. Only a host-owned builtin schema may
retain an individual plaintext field with `x-chatwaifu-audit-public`; plugin and remote MCP schemas
cannot opt into plaintext audit. `writeOnly`, sensitive formats/annotations, conventional secret names,
unresolved references, and sensitive local `$ref`/`allOf` branches override that opt-in. Runtime
delivers the real result from a bounded in-memory cache while SQLite rows and persisted events keep
only the audit form. File bodies, prompt arguments, user PII, cards, avatar cues, spoken summaries,
and untrusted MCP error bodies are not copied into durable audit rows by default.

MCP `resources/read` and `prompts/get` are generated as typed Runtime Skill capabilities. Their
public routes require a real session and return an asynchronous `SkillRun`; they use the same plan,
permission/confirmation, revision lease, timeout, cancellation, audit, and normalized-error boundary
as MCP tool calls. The former direct connection routes no longer exist.

For network MCP, URL policy validation resolves DNS once and freezes the accepted global or loopback
addresses into the session transport. TCP dials only those pinned addresses and verifies the actual
peer, while the original URL hostname remains in HTTP Host and TLS SNI/certificate validation. This
closes DNS-rebinding time-of-check/time-of-use without weakening hostname authentication.

Installed plugin packages and mutable data use different roots. Package files are copied after
bounded regular-file validation and made owner-read-only; the package root is mounted read-only by
Seatbelt, bubblewrap, or OCI. On Windows, the ADR 0025 host grants its stable per-subject
AppContainer SID read/execute access to the package/runtime roots and write access only to the
separate per-plugin data root. Snapshot fields persist the backend that was actually planned or
used, including explicit `none`, and separately list only resource limits the backend really
enforces. The OCI supervisor contract enforces process-count, memory, and CPU limits; current
Seatbelt and bubblewrap launchers truthfully report no resource-limit enforcement instead of
claiming a portable limit they do not provide. Windows may report AppContainer/Job limits only
after its host confirms their application for that child.

MCP bearer tokens remain outside SQLite. A mode-0600 mutation journal records previous and intended
token state until the related SQLite create, update, or delete commits. Ordinary failures compensate
immediately. Startup resolves interrupted mutations from row existence and connection revision,
then reconciles the non-secret configured flag and removes orphaned tokens. Corrupt secret or journal
storage fails closed.

Declared execution contracts are enforced: unsupported background requests are rejected,
non-interruptible running work is not cancelled by client timeout or cancellation, interruptible work
propagates cancellation into the official MCP transport, initialization and operation share bounded
deadlines, and normalized/truncated MCP tool names receive deterministic collision-free suffixes.

## Consequences

An approval or reusable grant can no longer authorize a different package, schema, tool, or
connection configuration. Durable audit remains useful for identity, timing, state, explicit public
fields and structural summaries but is intentionally neither a result store nor a
replay log. Full successful results are process-local and disappear on Runtime restart; pending runs
also expire because their original arguments are intentionally unavailable.

Plugin authors must write through `CHATWAIFU_PLUGIN_DATA_DIR` or their working directory and treat
`CHATWAIFU_PLUGIN_PACKAGE_DIR` as immutable. ADR 0025 accepts and validates the Windows native
backend with real x64 filesystem, network, inherited-handle, Job, memory, MCP lifecycle,
cancellation, and reconciliation probes. The API still rejects untrusted required-sandbox stdio
whenever the sibling helper is missing or cannot enforce the immutable plan; it never silently
falls back to a cleaned environment. ADR 0027 now records frozen-sidecar packaging and basic
installed Runtime health/uninstall validation under Windows x64 emulation. Signed delivery and
execution plus profile/ACL reconciliation through the installed AppContainer helper remain separate
release gates.

The SQLite adapter remains replaceable behind the domain port. Moving to another durable store must
preserve conditional confirmation decisions, connection revisions, leases, secret-journal recovery,
and terminal-state semantics; it is not sufficient to implement CRUD methods independently.

## Alternatives

Trust the registry lookup performed after approval; store original arguments for restart replay;
hash only `plugin.json`; use one writable plugin directory; rely on route-level locks; treat file and
database writes as eventually consistent without a journal; label environment cleanup or process
termination as a sandbox; expose raw MCP server errors for debugging.
