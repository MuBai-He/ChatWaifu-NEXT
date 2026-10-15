# Information inbox 0.5.1, 2026-10-14

User-approved changes: a single primary AI suggestion surface; full unexcluded local text history
feeds both suggestion generation and knowledge indexing; extracted task/event times are visible
before confirmation. No automatic promotion to confirmed work, memory or OS alarms is introduced.

## Behavior

Android reuses the existing source queue, immutable knowledge outbox, revisions, source-policy
fences and owner-authenticated backend. Full import does not send a whole archive as one prompt.
Every request retains its existing record/character budget; recent data still wins selection.

A user-started `dataSync` foreground service drains the two pipelines on Wi-Fi, displays progress
and supports pause/resume. Intent is persisted outside the process. Native job compensation takes
over when the platform defers a long run; opening the App resumes the faster foreground path.
A manual pause preserves progress and leaves ordinary collection/synchronization intact.
Completed phone processing means sources are synchronized and suggestion batches processed,
not that the independent Graphiti worker has finished every source. Failed/conflicting queues
are not falsely declared complete. Temporary test suggestions never enter confirmed business tables.

Foreground-service type/permissions and timeout handling follow the Android contracts:
https://developer.android.com/develop/background-work/services/fgs/service-types#data-sync
https://developer.android.com/develop/background-work/services/fgs/timeout
The service does not launch directly from BOOT_COMPLETED. Persisted existing jobs recover first.

`knowledge.history_daily_limit=0` now explicitly disables the old-history daily count cap. Positive
values keep the existing cap and default 100; disabling history still blocks old jobs. The deployed
server setting is 0 by the owner's explicit full-import choice. New records and deletion cleanup
retain priority. Status exposes the effective history limit and enabled flag for authenticated owners.
SQLite migration 48 and Android schema 5 remain unchanged.

Date extraction uses the original `message_time_ms` and timezone, including historical records.
Due dates and event starts/ends stay separate. A date without a clock or a vague expression is
retained in `due_text` for confirmation; no default 18:00 or 23:59 is fabricated. Native cards show
absolute dates immediately, rather than hiding them in the confirmation editor.

## Verification

- Runtime: 47 relevant tests passed, including explicit unlimited history versus the ordinary cap.
- Android: 47 unit/native SQLite scenarios passed; debug/release builds and both lints passed.
- Strict changed-code Pyright: 0 errors on the Linux deployment host. Ruff and architecture checks pass.
- Actual configured memory-extraction model: three synthetic cases passed (year crossing, month
  crossing and vague time). Real user text was not used for these model quality probes.
- Existing Web source is byte-identical to the previously verified 0.5.0 surface. New status fields
  are additive; no frontend publication is needed for these Android controls.
- Physical UI and full-run evidence is kept privately in the sibling `ui-review-0.5.1` directory.

Deployment overlays four reviewed Runtime source files at the current release; old files, env and
consistent SQLite backup are protected under
`/home/mubai/chatwaifu-server/backups/info-inbox-0.5.1-20261014`.
The model credential digest and schema 48 were checked after restart. The original main checkout
is untouched. This overlay does not imply a commit, PR, push or merge to main.

Physical acceptance: direct AI entry and extracted-date cards were inspected. Full import
start/pause/resume passed and finished submitting the current 852 eligible text messages.
The Root archive contains 4258 messages of all types; excluded conversations and non-text
messages are outside this text-only run. A temporary task saved separate planned start/end
and deadline values correctly; its reminder was empty and the fixture was removed.
Existing memories, work and six exclusion rules matched the pre-upgrade snapshot exactly.
The handset runs the matching signed release APK without instrumentation.
