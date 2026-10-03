# ADR 0065: Bounded QQ images and permissioned reply references

- Status: Accepted
- Date: 2026-10-03
- Extends: ADR 0064; preserves its current-turn voice authorization and durable send fence.
- Validation state: Automated integration acceptance is in progress; real QQ image and quote acceptance remains pending.

## Context

The original QQ plan's phase B requires incoming image understanding, outgoing
images or stickers, and reply references. Existing ephemeral image contracts
(ADRs 0035 and 0040) already connect authenticated channel admission to the shared
Conversation vision path. QQ must reuse that path rather than create a second
character or bypass its generation cancellation.

The pinned NapCat v4.18.28 image API returns provider-local paths by default.
Its ordinary WebSocket response does not fit large Base64 images in the current
bounded reader. Public CDN fetching would introduce a separate network trust
boundary. `get_msg` lacks the peer identity for an outgoing private message and
its short-ID lookup cache is process-local, so it cannot authorize historical
quote reads by itself.

## Decision

### Ephemeral incoming images

Normalize only authenticated account events from the configured owner in private
chat. Pairing remains exact text-only. Keep the existing text envelope and an
internal immutable attachment descriptor; no signed URL, provider path, image
bytes, or raw OneBot payload enters public contracts or durable transcript rows.

Admit and deduplicate before a generation-owned asynchronous loader fetches the
image. Use the authenticated `download_file_image_stream` RPC with 64 KiB chunks,
a separate bounded echo demultiplexer, one active image stream per connection,
and the shared 32-request capacity. Validate frame shape, ordered indices,
canonical Base64, per-chunk sizes, declared totals and terminal completion.
Cancellation releases correlation state; late frames cannot reach another call.
Account preflight and postflight must match the paired identity.

File references come only from admitted image events and must be bounded safe
basenames. Never accept model-selected references, URLs, paths, arbitrary file
downloads, or a provider-local path as Runtime input. NapCat's filename lookup
is global to its authenticated account; the pinned API provides no atomic
peer-qualified read or cryptographic content hash. Owner event admission and
account checks are required, and do not add such a provider guarantee.

Reuse the static PNG/JPEG limits: 1–4 images, 5 MiB each, 20 MiB total,
8192 pixels per dimension, 16,777,216 pixels per image, and a 20-second whole
batch deadline. Validate actual image decoding and remove EXIF before model
projection. Unsupported animation, expired references, malformed data, partial
batch failure and timeout produce the existing durable image-unavailable text
notice, without a partial image request. A cancelled generation produces no
late model request or notice.

QQ images are ephemeral in this slice even if another channel has opted into
photo retention or sticker learning. The gateway's explicit retention flag
suppresses both observers; burst collection cannot bypass that policy. A later
retention feature requires correct provider attribution and its own acceptance.

### Outgoing images

Reuse the existing optional sticker delivery plan. The default character and
its owner-private presentation policy must opt in. The model cannot choose a
recipient, path or remote URL. Resolve immutable preset or learned sticker IDs
through existing catalog/library services and verify hash, MIME, decoded format,
single-frame status, dimensions and size before creating an `image` segment
with bounded `base64://` bytes. The existing cancellation checks, unknown-result
fence, durable receipt and restart recovery apply unchanged. Required text
remains delivered if the optional image fails.

Settings exposes a separate sticker toggle; it does not grant permanent voice
permission or enable image retention. General photo generation, arbitrary photo
uploads and animated market stickers are outside this bounded image capability.

### Permissioned reply references

Persist the reference ID in the existing durable conversation source metadata.
Resolve only admitted user text or confirmed outgoing parts from the same
connection and binding. Respect scope resets and photo-context redactions.
Ambiguous IDs and unknown or inaccessible references are unavailable; never
fall back to unrestricted provider-history reads. Quoted image bytes are not
re-fetched. The user may resend a picture for a new visual question.

Load bounded historical text inside the active generation. Project it as
explicitly untrusted context, separate from fresh `user_text`, previous-user
routing, memory writes and the current skill whitelist. It cannot authorize
`channel.voice`. A reply to an incoming quoted message includes a OneBot reply
segment targeting that current admitted message, once on the first delivery
part. The model cannot choose that target.

## Acceptance and remaining stages

Transport and full Runtime tests must cover image integrity, limits, account
changes, cancellation, duplicate admission, no retention, text/voice continuity,
optional-image failure, quote scope/reset/redaction, and unknown-send recovery.
Real phone image understanding, received sticker display and quote display
remain separate acceptance gates.

Phase D (incoming voice transcription, proactive messages and group permissions
with participant isolation) is not enabled by this ADR. Search and answer-quality
work remains an independent integration dependency.
