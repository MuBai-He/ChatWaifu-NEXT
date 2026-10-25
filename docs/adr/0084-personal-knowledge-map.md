# ADR 0084: Owner-private personal knowledge with rebuildable temporal graph

- Status: Accepted for implementation by the owner on 2026-10-13

The phone remains an independent source archive and reminder executor. Runtime SQLite owns
committed synchronized object revisions, immutable source versions, policies, review decisions,
conflicts, operation receipts and the index outbox. Graphiti 0.30.2 with Neo4j provides a derived
temporal index. Graph output cannot overwrite human decisions. The main Runtime and its read-only
knowledge skill always validate references against canonical state and current source policy.

Sync is revision-based, idempotent per operation, and returns explicit conflicts with both values.
Clients retain offline edits until acknowledged. Policy changes precede ingestion and invalidate
in-flight indexing/query results. Exclusion retains local raw text and confirmed work, while hiding
source-derived graph knowledge and disallowing AI use. Forgetting suppresses future reconstruction.

Identity is owner/account/source scoped. A contact's stable source identity outranks its display
name; unbound mentions remain source-local until reviewed. Graphiti groups are conversation
scoped so excluded conversations cannot contaminate extraction for another source. Canonical
identities and the graph query layer connect proven identities across those groups.

Graph/model SDKs stay in adapters. A dedicated worker uses the persistent leased outbox; it does
not start voice, external channels or reminder schedulers. All model calls use existing Runtime
model roles. No provider secrets cross to browser or phone. The first release includes the real
Graphiti adapter, not a fake graph presented as production indexing.

The same React/Cytoscape knowledge surface is used in Web and a restricted Android WebView.
Graph access uses a short-lived scoped viewer credential, never a provider key or a Root bridge.
Knowledge search is owner-private; shared-channel invocations are rejected.

Rollout requires compatibility checks against actual structured generation and multilingual
embedding, migration/rollback evidence, source-policy/deletion propagation, offline conflict and
reminder reconciliation tests, browser and handset screenshots, and an end-to-end sourced answer.
