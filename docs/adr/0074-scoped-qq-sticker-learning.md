# ADR 0074: Scoped QQ sticker learning and native expressions

Status: Accepted; deployed implementation, handset learning/reuse acceptance pending

## Decision

2026-10-05 follow-up: ADR 0075 restores the explicitly requested text-only group output. Group input understanding/learning and private sticker output retain this ADR; optional group image output is superseded.

Extend ADRs 0036 and 0065 to the paired QQ owner and individually opted-in fixed QQ
groups. Reuse the existing conservative static-image classifier, normalized PNG
library, cancellation, immutable delivery payloads and receipt scheduler. Keep
ordinary photo retention disabled for QQ, independently of sticker learning.

Private learning uses the existing owner/character scope. A group's library uses
its server-owned `scene:<scene_id>` scope. The operator API derives that scope from
a route ID; callers cannot supply an arbitrary scope. Group image admission still
requires the existing account, enabled route, current member/speaking grant and
explicit bot mention. Only groups whose scoped learning switch is enabled admit
images. Other groups remain text only. No passive collection or historical replay.

The asynchronous learner saves only after the source turn completes. Group saves
also verify the exact current route revision, scene, audience, connection and
granted speaker in the same SQLite transaction. Transport/membership/scene fences
cancel in-flight learners. Revalidation with the same scene preserves its assets;
a changed scene cannot read its predecessor's library. Photos, screenshots,
uncertain classifications and animated/market media are not retained.

QQ can label GIF bytes as `.png`. Inspect the bytes and offer a bounded first-frame
PNG preview for the current conversation. Only a single-frame GIF is eligible for
static learning; animated previews are excluded before classification or saving.
This does not preserve animation or prove understanding of later frames.

In a QQ owner-private conversation, a standalone image may keep its descriptor
for 60 seconds in the same authenticated durable binding. The next ordinary text
turn can use it under its own generation and content hash, even if that text
superseded image processing. At most 32 bindings keep one reference each. Explicit
quotes and audio do not inherit it; quoted image posts do not establish it. A new
image replaces it, failure discards it, and duplicate events never refresh its
lifetime or repeat downloads. Current account/owner/configuration checks and
synchronous manual-cancel, transport, connection-update/delete and stop fences
revoke it. Restart loses the descriptor; it is never recovered from QQ history.
Only QQ's trusted adapter opts into this behavior. No bytes, animation or photos
are retained, and ordinary Web, desktop and other-channel ingress is unchanged.

Mobile QQ often sends an image and a mention as separate envelopes. With scoped
learning enabled, an authenticated, granted member's image-only envelope may keep
only its bounded descriptors for 60 seconds. There are at most 32 references, one
per connection/group/sender. No bytes, model request, transcript, memory or sticker
save is produced until that same member mentions the bot in the same current
route/scene/settings revision. Only the first such turn consumes the reference;
its duplicate uses the same descriptor while available. Later turns do not inherit
it. Account, membership, route and lifecycle fences clear references; expired or
restart-lost descriptors are never recovered from provider history. Group ingress
admission is ordered within each connection/group, independently of generation,
so a quick mention cannot overtake its preceding image observation. This adds
recent image context, not collection of unmentioned group text or an archive.

A group response may append at most one optional learned image after the required
canonical text bubbles. Persistence verifies the asset belongs to that scene;
delivery resolves it from the same scene and rechecks current group authority and
the lease before the send. Private presets/assets cannot become group fallbacks.
Text reconstruction, ordered receipts, cancellation and unknown-send no-replay
remain mandatory. Disabling learning keeps existing assets; the channel sticker
switch separately controls outbound reuse.

SQLite migration 42 replaces only the group part-insert guard. Existing migration
checksums and facts stay intact. The guard retains bounded required text and
admits only a final optional image whose hash and type match the current scene's
learned asset. It rejects private/preset images, audio, required images and content
mutations; the receipt-only scheduling guard is unchanged. Older binaries cannot
reopen this newer schema, so deployment preserves a verified pre-migration backup.

Support bounded structured QQ `face` inputs as labeled system-expression context,
never as pairing codes or voice permission. Render an explicit small Unicode emoji
allowlist through native `face` segments, preserving canonical transcript text.
Send saved PNGs as QQ expression images (`sub_type=1`). No prose/CQ-code parsing,
arbitrary URLs, paths, recipients or model-selected RPCs are introduced.

For an accepted QQ sticker, optionally mirror its normalized bytes into the bot
account's native favorites using authenticated bounded `upload_file_stream` then
`add_custom_face`. The pinned NapCat v4.18.28 requires a provider-local file, so
only the path returned for our unique upload is accepted. Verify account, source,
settings revision and asset hash before mutation. A bounded namespaced journal
shares the adapter's existing cursor/lock, is ignored by message reconciliation,
and fences uncertain favorites against automatic retries. A successful RPC alone
is not favorite acceptance: read back the normalized image MD5. Native favorite
failure is auxiliary and does not fail the conversation or CW2 library save.
CW2 deletion does not silently delete an account-wide QQ favorite.

## Evidence and acceptance

The live server reports NapCat 4.18.28, matching the inspected source tag, and
`fetch_custom_face_detail` succeeds. Pinned primary sources:
[native favorites](https://github.com/NapNeko/NapCatQQ/blob/v4.18.28/packages/napcat-onebot/action/extends/CustomFace.ts),
[bounded upload](https://github.com/NapNeko/NapCatQQ/blob/v4.18.28/packages/napcat-onebot/action/stream/UploadFileStream.ts),
[native expression mapping](https://github.com/NapNeko/NapCatQQ/blob/v4.18.28/packages/napcat-core/external/face_config.json).

Automated gates cover opt-in, source completion, private/group separation, speaker
and audience revocation, duplicate images, failed reads, optional sends, delete
and settings fences, native upload validation/account change, favorite unknown
results and send-journal continuity. Real Gemini classification, native favorite
readback and handset learning/reuse/native expression display are separate gates.
This slice does not close Q02's full character/presentation A/B acceptance.
Group images with opaque account-global references, mismatched checksums, failed
reads or animated content are not retained. Read failures remain failed
turns under the existing group policy, without a generated-answer success claim.
