# Personal knowledge map delivery ledger

Approved scope: unexcluded text and confirmed memories/work/events; recent 30 days first then
history, new material first; Web and Android editing; visible unverified relations; cited graph QA.

## Implementation and deployment, 2026-10-13

- [x] P0: versioned contracts, ADR 0082, Graphiti 0.30.2 / Neo4j Community, real Chinese
      structured generation and Voyage multilingual embedding compatibility.
- [x] P1: canonical revisions, immutable operation retries, bidirectional sync, offline drafts,
      explicit conflict resolution, exclusion epochs and per-record phone application receipts.
- [x] P2: durable single-consumer queue, provenance, temporal invalidation, review/correction,
      identity suggestions, merge and split, exclusion/forgetting fences, rebuildable derived index.
- [x] P3: responsive React/Cytoscape map, desktop/mobile layout, scoped Android WebView,
      exact source versions and a controlled jump to the latest local original.
- [x] P4: bounded lexical + semantic + graph retrieval, cited model answers, private read-only
      `knowledge.search` Runtime Skill and confirmed formal-memory graph contribution.
- [x] Production Runtime and Web deployment with database/configuration backups, schema 48,
      preserved model credentials, independent worker and private Neo4j.
- [x] Existing Xiaomi upgrade from Android database schema 4 to 5; exact preservation checks
      for original memories, work items and six exclusions; actual Root source intake and HTTPS sync.
- [x] Physical reminder create/reschedule/cancel/forget roundtrip with per-record application
      acknowledgement, using synthetic fixtures only. No original user task was edited.
- [x] Actual Web graph, zoom/filter, responsive layout, exact source revision and cited answer;
      actual handset map, touch zoom/filter and original-source dialog inspected in screenshots.
- [x] Physical WebView edit persisted a synthetic memory as revision 2; fixture forgotten after
      verification. Signed release APK installed in-place, without debug instrumentation.

## Evidence and corrections

Runtime regression gate: 83 passing tests including three added lease-cancellation scenarios.
The focused indexer gate has 12 passing graph tests. Formatter/linter
and strict production-code type checking passed. Android has 39 passing JVM/native SQLite tests,
with debug/release build and lint gates. Web has five passing Playwright scenarios.

Actual configured models, rather than fixtures replacing model calls, passed the isolated
Chinese extraction, temporal change, cancellation, canonical rebuild and exclusion flow.
A transient provider ReadTimeout was retried. The final acceptance required cancellation to
be published in the graph, not merely mentioned in raw-source QA.

Deployment: `/home/mubai/chatwaifu-server/releases/personal-knowledge-20261013-v1`.
Backup: `/home/mubai/chatwaifu-server/backups/personal-knowledge-20261013-v1`.
Map shell: `https://mubai.website:18443/knowledge`. Data remains authenticated.
Migration 48 is deployed and frozen. Later schema changes require a new migration.

Physical acceptance found and corrected four integration issues:

1. First-time remote work import calculated its native fingerprint before installing the
   canonical identity template, causing an echo revision. A regression now captures local
   changes immediately after first import and after rescheduling.
2. Android edge-to-edge rendering overlapped the native map actions with the status bar.
   The map now applies the same system safe insets as the existing native screens.
3. Revoking an indexing lease cancelled the supervisor rather than only its episode. The worker
   now owns a separate episode task; lease revocation continues the queue while real shutdown
   cancellation still propagates, including a simultaneous revocation/shutdown race.
   Production was exercised with a synthetic forgetting operation while indexing: the same
   worker process survived and the durable queue continued making progress.

4. Replayed exclusion policies passed a canonical `label` through a native adapter expecting
   `conversation_title`, replacing saved names and refreshing their original timestamps.
   Equal-state snapshots now leave native rules untouched; actual remote changes map labels
   explicitly. A native SQLite regression checks replay, remote restore and re-exclusion.
   The six original rule names/timestamps were restored from the pre-upgrade private backup.

Private handset screenshots, redacted acceptance results and temporary fixtures are kept in
`../ui-review-0.5.0` outside the repository. Screenshots may contain personal source content and
must not be uploaded as public examples. Temporary source-access cookies are removed after use.
No commit, push, PR or merge to the primary CW2 checkout is implied by this deployment.

## Continuing background work

Recent and older unexcluded history is progressively synchronized and indexed. A functional
release does not mean the entire archive has completed processing. The map reports synchronized
and indexed counts independently. New data has priority, historical indexing is single-consumer
and limited to 100 old source episodes daily by default, and old-history upload is Wi-Fi only.
Normal collection, offline edits and local reminders continue independently of graph availability.
