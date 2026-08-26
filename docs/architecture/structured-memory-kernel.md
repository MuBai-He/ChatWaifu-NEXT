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
  + SemanticMemoryIndex candidates (rebuildable SQLite projection)
  + TemporalMemoryGraph candidates (Scheme C port; null by default)
  -> state/namespace/privacy filters
  -> lexical/semantic/entity/temporal + importance/confidence ranking
  -> token budget
  -> MemoryContextPacket with provenance
```

The write policy is intentionally asymmetric:

- explicit normal `remember` requests commit immediately;
- implicit facts, preferences, procedures, and commitments create review proposals;
- a user-initiated conversation topic becomes a sourced episodic observation, never an inferred
  stable preference;
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

## Activated lightweight Scheme B and reserved Scheme C boundaries

`SemanticMemoryIndex` and `TemporalMemoryGraph` remain optional retrieval ports. The current semantic
adapter stores vectors in SQLite, fingerprints the independently configured embedding route, and is
fully rebuildable from active Scheme A records. The offline default is a small lexical hash vector;
an OpenAI-compatible multilingual embedding model can be selected in Web without changing memory
truth. Semantic candidates contribute only ids and scores to the common ranker. They cannot commit
records, change lifecycle state, weaken privacy filters, or become a second source of truth.

The temporal graph remains a null Scheme C port. Enabling it requires fixed evaluation evidence,
migration/teardown tests, deletion propagation, and an adapter whose absence leaves Scheme A fully
functional.

## Model-assisted extraction and heard-response evidence

Normal user turns first pass the deterministic extractor, then may pass through the separately
configured memory-extraction model. Its output is strict JSON validated into typed drafts; policy,
deduplication, contradiction handling, privacy, provenance, and user review still run afterwards.
The model cannot directly write or delete records. Secrets and obvious sensitive identifiers are not
sent to this extractor.

Assistant text is eligible for shared-event proposals only after all generation segments have
completed playback acknowledgement. The source must be an `assistant.spoken_text_committed` event;
generated but unheard text never becomes shared memory.

## Remaining evaluation work

The next gate is a Chinese character-memory suite covering extraction precision, false recall,
cross-session lookup, corrections, temporal questions, sensitive-data non-recall, and latency. The
next gate compares the configured multilingual embedding route against FTS and the local hash
fallback. Scheme C is justified only if multi-entity temporal reasoning becomes a demonstrated
product requirement.
