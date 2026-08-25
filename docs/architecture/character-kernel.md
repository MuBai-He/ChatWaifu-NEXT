# Character Kernel and role-routed context

The Character Kernel is the Runtime-owned boundary between character identity and replaceable model
or renderer adapters. It loads the six-file character package, persists bounded Affect and
Relationship projections, reduces observed interactions, plans response semantics, compiles a
budgeted prompt, and emits renderer-independent cues.

```text
committed user turn / acknowledged avatar interaction / elapsed time
  -> deterministic signal classifier
  -> Affect Reducer + Relationship Reducer
  -> SQLite character/relationship projections
  -> ResponsePlan(intent, tone, expression, motion, length)
  -> manifest capability intersection + repetition guard
  -> AvatarCue

Character Canon + Affect + Relationship + ResponsePlan
  + policy-filtered MemoryContextPacket
  + recent committed conversation
  -> Prompt Compiler budgets
  -> role-routed chat adapter
```

## State ownership

State uses the `local` user scope in the current single-user Demo. Reducers clamp every value and
apply small deltas, relationship stages require both interaction counts and score thresholds, and
Affect decays toward policy defaults over time. The LLM can express the planned state naturally but
cannot write scores. Avatar touch is acknowledged through Runtime before affecting state. Reset
clears conversation, memory, generated audio, Affect, and Relationship state together.

## Stable prompt order

The Prompt Compiler always preserves product safety and character canon before dynamic context. It
allocates separate budgets for persona, memory, conversation, relationship/state, and response
scene. FTS/semantic-ranked memory enters only through a provenance-carrying `MemoryContextPacket`.
If older conversation does not fit, only the dropped prefix is sent to the independently configured
memory-summary route. Prompt budget events contain counts, not private prompt text.

## Model roles

The Web manages four independent Runtime routes:

| Role                | Purpose                                       | Offline default                              |
| ------------------- | --------------------------------------------- | -------------------------------------------- |
| `chat`              | stream the character reply                    | deterministic Demo                           |
| `memory_extraction` | propose structured durable memories           | deterministic rules and empty model fallback |
| `memory_summary`    | compress dropped history                      | deterministic compact summary                |
| `embedding`         | build/search a disposable semantic projection | local 64-dimensional hash                    |

Saved keys are never returned to Web. Changing the embedding route rebuilds only
`memory_embeddings`; structured records, sources, lifecycle, FTS5, and tombstones remain unchanged.

## Failure behavior

Extraction, summary, and semantic indexing failures do not block normal chat or remove Scheme A
records. FTS5 and deterministic extraction remain available. Chat route errors use the existing
generation failure/cancellation path. Unknown avatar capabilities fall back to `neutral`; stale
generation cues are still rejected by the avatar scheduler.
