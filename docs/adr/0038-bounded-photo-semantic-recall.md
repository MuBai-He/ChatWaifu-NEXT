# ADR 0038: Bounded semantic photo recall

Status: Accepted design; implementation and isolated Runtime acceptance complete; owner real photo WeChat acceptance succeeded on 2026-09-06

## Context

Phase 17.3C introduced opt-in photo retention, conservative structured photo classification,
metadata-free local copies, authenticated gallery preview/deletion, and scoped lexical and
recent-photo recall under [ADR 0037](0037-opt-in-photo-memory.md).

While lexical search effectively handles queries sharing exact vocabulary with generated
descriptions and tags (e.g. "红屋顶", "猫咪"), users naturally describe retained photos with
synonyms, related concepts, or abstract summaries (e.g. "夕阳下的小洋房", "宠物"). Lexical search
alone misses these semantic paraphrases, while falling back unconditionally to the latest photo
violates user trust by returning an unrelated image when a specific photo query fails.

Furthermore, photos must remain distinct from text facts: photo descriptions are visual evidence,
not personal memory records. Therefore, semantic photo recall requires a dedicated, bounded
embedding projection that integrates cleanly with the existing photo retention lifecycle without
polluting `MemoryRecord` or `memory_embeddings`.

## Decision

1. **Separation from Memory Records**:
   Photos never enter `MemoryRecord` or `memory_embeddings`. Photo embeddings are disposable
   vector projections stored in a dedicated `photo_embeddings` table with a cascading foreign key
   to `photo_assets(photo_id)`. If an embedding is lost or stale, it can be regenerated at any time
   from the authoritative photo asset description and keywords.

2. **Domain and Persistence Boundary**:
   - `PhotoSemanticPersistencePort` and `SQLitePhotoSemanticAdapter` handle purely transactional
     SQL operations: scoped index reads, unindexed candidate queries, stale fingerprint purges,
     and authoritative inserts. Persistence adapters never invoke remote embedding models or
     calculate cosine similarity rankings.
   - `PhotoSemanticService` (domain service) owns model invocation, vector validation (finite,
     non-zero, matching dimensions), similarity calculation, ambiguity detection, bounded incremental indexing, and query budget enforcement.
   - Authoritative insert validation uses `INSERT INTO photo_embeddings ... SELECT ... FROM photo_assets p WHERE p.photo_id = ? AND p.principal_scope = ? AND p.character_id = ? AND p.sha256 = ?`
     to guarantee that concurrent deletion or scope reassignment prevents orphaned projections.

3. **Conservative Recall Policy**:
   - **Lexical Priority**: Lexical search runs first. Semantic search is only invoked on a lexical
     miss when the user query contains an explicit photo reference (`has_explicit_photo_reference`).
   - **Similarity Threshold**: Semantic candidates must achieve a minimum cosine similarity of 0.55
     (`MIN_SEMANTIC_COSINE_SIMILARITY`).
   - **Ambiguity Suppression**: If the top two semantic candidates have a cosine similarity margin
     smaller than 0.08 (`SEMANTIC_AMBIGUITY_GAP`), both photo descriptions are retained for conversational
     clarification, but image attachment is suppressed (`attached_photo = None`). Ningning asks the
     user for clarification rather than hallucinating visual confirmation on an ambiguous match.
   - **Narrow Recency Fallback**: Content-specific requests (e.g. "之前那张红屋顶的照片") never
     fall back silently to the latest photo on search misses. Only pure recency requests (e.g.
     "刚才那张照片", "刚才发的图") lacking content qualifiers may retrieve the latest photo.
   - **Local Hash Stub Safety**: The deterministic `local_hash` embedding provider produces hash
     projections unsuitable for semantic distance comparison. `PhotoSemanticService` returns an
     empty semantic candidate set when `local_hash` is active, cleanly falling back to lexical search.

4. **Concurrency and Query Budget**:
   - Semantic retrieval is bounded by a strict query budget of 1.5 seconds (`SEMANTIC_QUERY_BUDGET_SECONDS`).
     Model latency spikes or network timeouts abort semantic retrieval and allow the chat turn to
     proceed with lexical results or honest misses, never blocking chat generation.
   - Route and fingerprint changes snapshot the model fingerprint before and after each `await`.
     In-flight embeddings computed under an obsolete model route are discarded safely.

5. **Stale Vector Policy and Degraded Retrieval Tradeoff**:
   - For the current TEXT representation (`photo_description_v1`), old-model embeddings of the SAME
     dimension participate in semantic search rather than being excluded or paused.
   - The user explicitly accepted the degraded retrieval tradeoff: comparing vectors from different
     embedding models may cause lower similarity accuracy, false positives, or false negatives, but
     preserves immediate recall availability without forced downtime.
   - Embeddings with mismatched vector dimensions or non-finite values are safely skipped during cosine
     ranking without crashing the search.
   - Future multimodal expansion is reserved via typed discriminated `PhotoEmbeddingInput` (TEXT vs IMAGE);
     only TEXT is supported currently, and IMAGE modality fails fast before network calls.

6. **Owner-Approved Manual Rebuild Orchestration**:
   - No automatic rebuild or backfill occurs on embedding route save, runtime startup, or query miss.
   - Next to embedding settings in `ModelSettingsPanel`, a manual "重建索引" button triggers background
     re-indexing across both structured memory (`memory_records`) and retained photos (`photo_assets`).
   - Upon saving changes to embedding `provider`, `model`, or `base_url`, a warning modal is presented with
     EXACT text:
     `embedding 模型已更换，现有索引仍由旧模型生成。不重建可能导致漏检、错误匹配或相关度下降；向量维度不兼容的条目将无法参与语义检索。建议重建索引。`
   - Modal actions provide "重建索引" and "稍后". Choosing "稍后" or dismissing via Escape closes the modal
     with zero rebuild calls, leaving normal retrieval enabled.
   - Modifying non-route attributes (`api_key`, `timeout_seconds`, `context_window`), saving unchanged
     configurations, failed saves, or modifying other roles (`chat`, `memory_extraction`, `memory_summary`)
     never triggers the warning modal.
   - Rebuilding is singleflight, cancellable on model switch or server shutdown, and reports truthful
     per-domain running, completed, and failed counts. Records or photos deleted during rebuild cannot be
     resurrected.

## Consequences

- Semantic search enables natural paraphrased recall of retained photos with conservative attachment selection.
- Ambiguity and miss policies reduce false recall and incorrect image grounding.
- Decoupling embeddings from core photo storage ensures zero data loss or database corruption if
  an embedding model provider is swapped or unavailable.
- Stale vector participation avoids artificial retrieval pause while giving users full manual control
  over index rebuild timing.
- Automated tests pass across backend and web UI; live real-model WeChat acceptance remains pending
  (Issue #24 deferred).

## Validation on 2026-09-06

Independent verification passed 983 Python tests (46 platform-dependent skips), 229 Web tests,
Ruff, Pyright, frontend lint/type checks, and Web/desktop UI builds. A real configured text
embedding endpoint with isolated SQLite fixtures passed five paraphrased photo queries and five
unsupported-photo negatives through the actual recall service. These synthetic fixtures do not
replace owner WeChat photo acceptance.

The actual browser and isolated Runtime displayed the exact warning after a successful model save;
choosing Later left rebuild status idle. Clicking Rebuild started a real job and displayed separate
memory/photo completion states. Focus defaults to Later, Escape dismisses without a rebuild, and
status requests are serialized and cancelled on unmount.

Manual memory rebuild traverses bounded cursor pages beyond the management API's 500-record limit;
a 505-record regression checks intermediate and final counts. Photo write epochs synchronize with
persisted projections after restart. Incremental photo work is capped at two tasks and five seconds
per task; it never implicitly rebuilds the existing library.
