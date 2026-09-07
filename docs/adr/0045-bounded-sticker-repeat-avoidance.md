# ADR 0045: Bounded repeat avoidance for equally eligible learned stickers

- Status: Accepted; owner WeChat repeat-avoidance acceptance completed
- Date: 2026-09-07

## Context

The learned library selected the first eligible sticker on every turn. Phase 17.4B-1 supplies
validated delivery evidence, so equivalent choices can vary without mistaking a failed send for
user dislike. This is a repeat-avoidance policy, not preference learning.

## Decision

Keep the existing blocked-input, interaction, expression, neutral/answer and sending gates.
Collect exactly the learned stickers those gates already admit. With no candidates, send none;
with one, keep it and do no history I/O. Preset selection, learned-before-preset priority, and
sticker send frequency stay under the existing policy.

Inject `StickerUsageRepository` into `StickerLibraryService` at bootstrap. For multiple eligible
candidates, read the ADR 0044 projection with a 250 ms cooperative timeout. The empty preset map
limits visible results to current learned assets: at most 50 records from the newest 200 scoped
image-part candidates. Existing owner, character, direct-route, source-lineage and asset hash
checks remain authoritative. This is a recent window, not all-time or per-connection usage.

Choose an eligible sticker absent from successful delivery records in that window first. Otherwise
choose the one with the oldest **most recent delivery timestamp**. Aggregate the maximum timestamp
per sticker rather than relying on record order (which is by creation time). Original library order
breaks ties. Only delivered records with a delivery timestamp and positive attempt count count;
duplicate facts and retry counts do not multiply use. Pending, sending, failed, cancelled and
skipped parts have no preference meaning. Confirmed delivery remains evidence after an interrupt.

On history timeout or non-cancellation error, use the original candidate order and log a bounded
reason. Propagate cancellation. Log candidate count and whether ranking changed the first choice,
without chat text, image bytes, provider payloads or raw exceptions. No new table, counter,
migration, network provider call or preference score is added. Deletion and source reset invalidate
history through the existing projection; no ranking cache can preserve stale evidence on restart.

### Cancellation during reply preparation

History lookup exposed an existing interruption window: `interrupt` used to synchronize the old
turn first, waiting for optional reply preparation to create its delivery plan. Now interruption
records `cancelling` before waiting, cancels the turn orchestration task, and then cancels the
generation. A completed model generation can still lose to cancellation of an unpublished channel
reply. The same SQLite transaction that creates either normal or recovery delivery parts rejects
`cancelling` turns. Synchronization resolves them to cancelled without publishing a plan event.
It waits until the old active generation has stopped before releasing queued work; cancellation
targets that exact generation rather than any newer generation in the session.
If the plan committed before cancellation, existing delivery cancellation continues to apply.
The durable fence remains effective after a restart and for sync callers outside orchestration.

## Alternatives and limits

Random choice is harder to reproduce. All-time frequency needs a different retention contract.
General semantic ranking could change suitability and is out of scope. Shared-joke binding
(17.4C) needs its own grounded association design. A window full of recent non-deliveries can
exclude older successes; absence is only absence in this window. Concurrent generations can still
choose the same sticker before either is delivered; pending selection is not delivery evidence.

## Verification

Deterministic tests cover unseen/oldest delivery, stable ties, out-of-order timestamps, retries,
duplicate records, excluded outcomes, scope forwarding, eligibility gates, optional-history failure,
bounded timeout and cancellation. Runtime/SQLite tests create actual channel delivery plans,
acknowledge their text and image parts, and reconstruct the Runtime between rounds to prove that
equivalent choices alternate using durable delivery facts. A stop during history lookup cancels
the old turn without publishing its image; a late completion after a durable cancel cannot create
a delivery even after reopening the database. Existing usage deletion/reset and channel delivery
regressions remain gates. Cancellation/restart scenarios use controlled adapters.

On 2026-09-07, the owner confirmed that two consecutive singing requests received different
eligible happy stickers. Read-only Runtime evidence confirmed two distinct current learned assets,
each acknowledged as delivered with attempt 1, at 08:51:06 and 08:51:23 Asia/Shanghai. This accepts
real-channel repeat avoidance; it does not claim a new manual interruption test. Codex did not
operate the WeChat client. Settings reconnection after a Runtime port change is tracked separately
in issue #35.
