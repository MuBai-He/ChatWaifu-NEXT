# ADR 0044: Bounded delivery-backed sticker usage history

- Status: Accepted; native macOS desktop verification completed
- Date: 2026-09-07

## Context

Adaptive sticker selection needs reliable usage evidence. Model selection does not prove a sticker
arrived: a required text part can succeed while the optional image fails, retries can produce several
attempts, and a stop request can race with an image already sent. Transport failures do not express
user dislike. Phase 17.4A supplies grounded shared jokes; this slice does not yet bind them to stickers.

## Decision

Read the existing durable `channel_delivery_parts` through a `StickerUsageRepository` port. A row is
one logical image part, identified by `part_id`, with its latest status and attempt count. The count
tracks delivery lease acquisitions, not the number of messages created at the provider. Only
`delivered` plus `delivered_at` and a positive attempt count is successful use. Pending, sending,
failed, cancelled and skipped remain separate outcomes. Acknowledged delivery remains evidence even
when cancellation was requested. Duplicate acknowledgements, restart and missed event notifications
cannot increment a separate counter because there is none. No schema migration or backfill is needed.

The SQLite adapter checks immutable owner scope, character, direct-chat route, matching binding and
retained user-turn/generation lineage in one transaction. It considers the newest 200 scoped image
parts by creation time with a stable part-id tie-break, validates typed payloads, and returns at most
50 visible records. This bounds decoded rows and response size, not the database's physical scan.
`has_more` reports a truncated view, not an all-time total or a pagination cursor.

A learned sticker must still exist in the same owner/character library under the same ID and content
hash; a preset must match the current local manifest ID and hash. Missing, replaced, malformed and
unrecognized assets are excluded. Reads do not load image blobs. Deletion/relearning cannot revive
an old asset's usage. Experience reset removes the source conversation turns; those entries then
leave this view even if delivery facts remain for channel reconciliation. This is visibility
invalidation, not a promise to erase all transport audit rows.

The authenticated, non-cacheable `GET /v1/sticker-library/usage` is limited to the supported local owner
and default character. It returns friendly labels, timestamps and delivery outcomes; no message text,
provider identifiers or raw errors. The settings library offers an initially collapsed recent-history
section with explicit refresh, retries and local dates. Library/character changes, closure and offline
transitions invalidate old results and cancel outstanding reads.

## Boundaries and follow-up

This slice does not change selection, sending, image learning or long-term memory. It does not add
preference scores, shared-joke bindings, cross-user sharing, automatic sending or animated-media
acceptance. Phase 17.4B-2 can add bounded adaptive selection using this port after its policy is agreed.
An all-time statistics product would need its own retention and completeness contract.

## Verification

Regression coverage uses real SQLite delivery transitions for optional image failure despite parent
success, retry/cancellation races, duplicate acknowledgement, restart, source reset, asset deletion,
scoped lineage, hash checks and bounded windows. Protocol validation rejects impossible successful
outcomes and oversized histories. UI tests cover lazy loading, refresh, offline state and stale results
arriving after deletion. Isolated Runtime/browser acceptance uses a read-only snapshot of retained
owner delivery facts; it neither operates WeChat nor replaces the running desktop.

On 2026-09-07, the actual macOS development desktop was verified against its live Runtime and
existing owner database. Native settings displayed the same five image parts (three delivered,
two cancelled) and local dates; explicit refresh preserved the result. The channel reported
connected and the Live2D character remained visible with unchanged desktop preferences.
No WeChat client operation or new test message was used.
