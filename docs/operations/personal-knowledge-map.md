# Personal knowledge map operations

The canonical store is Runtime SQLite migration 48. Graphiti 0.30.2 / Neo4j Community is a
rebuildable index, never the authority for user edits, exclusions or forgotten records.
The Android 0.5.0 client adds schema 5 alongside the existing local inbox and reminders.

## Entry points

- Web: `/knowledge`, also linked from Settings → Memory. Existing Runtime authentication applies.
- Android: Settings → Personal knowledge map → enable synchronization / open map.
- The native viewer reuses the saved backend connection, then obtains a 15-minute HttpOnly cookie
  scoped to `/v1/knowledge`. It has no Root or general Runtime bridge and cannot mint another cookie.
- `/knowledge-assets/` serves the compiled map shell from `knowledge.web_dir`. Build using
  `pnpm --filter @chatwaifu/web build:knowledge`; the normal Web build remains `build`.
- The Runtime Skill `knowledge.search` is read-only, interruptible, permission-broker controlled and
  bound to the trusted private owner session. It does not accept a caller-supplied identity.

## Worker and limits

Install the pinned optional adapter with
`uv sync --all-packages --extra knowledge --locked`. The default Runtime can keep
the feature disabled without loading Graphiti; the graph worker needs this extra
and an operator-configured Neo4j service.

Run the independent worker from the same release and state configuration as Runtime:

```sh
.venv/bin/python tools/knowledge_worker.py --config /path/to/runtime.toml --env /path/to/server.env
```

Use a separate user service with `UMask=0077`, `Nice=10`, one CPU and a memory limit. The worker
holds an exclusive lock beside the configuration file and claims durable leased jobs. It never
starts chat, voice, channel delivery or a reminder scheduler. New/recent records precede old
history. Historical indexing defaults to 100 bounded source episodes per day, Asia/Shanghai;
`knowledge.history_daily_limit` adjusts the limit and `knowledge.history_enabled=false` pauses
historical indexing while new records and deletion cleanup continue. Restart the worker after
configuration changes. Android's history switch separately pauses uploads of older raw history.
Historical bulk upload uses Wi-Fi; recent data, confirmed objects and pending edits use the saved
connection. Android's 30-second intake window and 15-minute compensation job are OS-scheduled.

`GET /v1/knowledge/status` separates synchronized records, indexed records, pending/failed jobs and
received cursors. Record responses include per-object phone application receipts. A received cursor
alone does not assert that a changed reminder was applied. The phone acknowledges after committing
local changes and reconciling OS alarms. Offline conflicts retain both versions.

Failed jobs are retried with bounded backoff. The map exposes an explicit retry action. Exclusion
and forgetting immediately fence queries once committed to Runtime; mobile changes await network
synchronization. Local exclusion fences uploads immediately and requests prompt policy sync.
Confirmed records and raw local WeChat text have separate lifecycles. Knowledge forgetting does
not delete the independent Root archive.

## Rebuild and recovery

Stop the normal worker, keep its lock exclusive, and run:

```sh
.venv/bin/python tools/knowledge_worker.py --config /path/to/runtime.toml --env /path/to/server.env --rebuild
```

Rebuild copies canonical relations, labels, review states and exact evidence versions into Neo4j;
it does not ask a model to rewrite previously reviewed facts. Excluded/forgotten evidence is omitted.
Edits during rebuild invalidate it; retry after synchronizing/pausing incoming edits. While rebuilding,
semantic graph lookup degrades to canonical lexical evidence. An embedding-model change also blocks
mixed vector-space reads until rebuild succeeds. Interrupted rebuilds keep this guard active.

Before a deployment, stop Runtime/worker briefly and make a SQLite backup plus protected copies of
`runtime.toml` and `server.env`. Preserve model-secret files, channel routes and prior release links.
An older Runtime cannot open migration 48: rollback requires the matching pre-migration database
backup, not only switching the source link. Never log bearer tokens, model keys, source bodies or
Neo4j credentials. Pin the Neo4j image digest and opt out of automatic image replacement.

## Acceptance evidence

- Real Chinese structured extraction and Voyage multilingual semantic ordering passed on isolated
  server fixtures. Production message data was not used for those probes.
- Source → durable queue → Graphiti → cited answer → canonical rebuild → cancellation update →
  exclusion was exercised with actual configured models and Neo4j. Fixtures were cleaned up.
- Runtime regression suite covers auth, lifecycle, sync CAS, replay, source versions, source-policy
  fences, forgetting, viewer scoping and per-object receipts.
- Android JVM/native SQLite tests cover process reopen, source revision retries, offline conflict
  resolution, remote reschedule/cancel, exclusion and schema 4 → 5 preservation.
- Browser tests use clearly synthetic fixtures at desktop and phone widths, including a build with
  no Runtime credential, so the native viewer cannot accidentally fall into the connection chooser.
- Physical installation, preserved user data, actual HTTPS browsing, Root intake and OS reminder
  reconciliation are separate gates recorded in the delivery ledger. Do not infer them from builds.

Formal CW2 memory enters through `TemporalMemoryGraph` with its own provenance and namespace.
Imported inbox sources do not impersonate user chat turns. Existing formal memories remain managed
by the original memory controls; inbox-confirmed memories/work/events are editable through sync.
