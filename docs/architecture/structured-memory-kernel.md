# Structured memory kernel

## Delivered Scheme A

Scheme A is the only durable memory truth in the current Runtime. A committed user turn supplies a
persisted source event id; deterministic extraction creates typed candidates, policy chooses commit,
review, or reject, and the repository projects accepted records into SQLite WAL and FTS5. Agent code
receives only a bounded `MemoryContextPacket`, never repository rows or a transcript archive.

```text
committed user event
  -> candidate extractor
  -> privacy/write policy
  -> proposal review or commit
  -> MemoryRepository
  -> records + sources + FTS5

current turn
  -> FTS/recent candidates
  + SemanticMemoryIndex candidates (Scheme B port; null by default)
  + TemporalMemoryGraph candidates (Scheme C port; null by default)
  -> state/namespace/privacy filters
  -> lexical/semantic/entity/temporal + importance/confidence ranking
  -> token budget
  -> MemoryContextPacket with provenance
```

The write policy is intentionally asymmetric:

- explicit normal `remember` requests commit immediately;
- implicit facts, preferences, procedures, and commitments create review proposals;
- sensitive content always requires an individual confirmation;
- exact duplicates are no-ops;
- a new value for the same subject and predicate supersedes the prior active record;
- correction creates a replacement record rather than overwriting history;
- correction preserves a record's explicit pinned/core status;
- forgetting tombstones the record and excludes it from retrieval.

Sensitive records remain excluded from agent retrieval by default even after storage. A source-event
foreign key and source API make each accepted fact inspectable without exposing all conversation
history in the prompt. Content is not written to operational logs.

## Persistence and ownership

`MemoryService` depends on the `MemoryRepository` protocol. `SQLiteMemoryRepository` owns SQL and
FTS query details; conversation coordination only supplies committed event identity and consumes a
context packet. The migration keeps legacy explicit memory as structured records and retains the
existing reset contract.

The Web memory center exposes pending proposals and active records in separate scrolling columns.
It supports kind/privacy filters, explicit sensitive confirmation, provenance lookup, correction,
pinning, and auditable forgetting. These operations call Runtime APIs; the browser never accesses
SQLite or makes policy decisions.

## Reserved Scheme B/C boundaries

`SemanticMemoryIndex` and `TemporalMemoryGraph` are optional retrieval ports with null implementations.
They may contribute candidate ids and scores to the common ranker, but they cannot commit records,
change lifecycle state, weaken privacy filters, or become a second source of truth. A future semantic
index must be rebuildable from Scheme A records and version its embedding model. A future temporal
graph must preserve record/source identity and deletion propagation.

No embedding model, vector extension, graph database, or graph extraction model is part of the
current Demo. Enabling either port requires fixed evaluation evidence, migration/teardown tests, and
an adapter whose absence leaves Scheme A fully functional.

## Remaining evaluation work

The next gate is a Chinese character-memory suite covering extraction precision, false recall,
cross-session lookup, corrections, temporal questions, sensitive-data non-recall, and latency. Scheme
B is justified only if paraphrase misses dominate after FTS tuning; Scheme C is justified only if
multi-entity temporal reasoning becomes a demonstrated product requirement.
