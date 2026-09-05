# ADR 0034: Preset sticker outbound presentation

- Status: Accepted
- Date: 2026-09-05
- Validation: Local protocol, lifecycle, catalog, adapter, and end-to-end tests passed; real WeChat acceptance pending

## Context

Phase 17.2 adds preset reaction images to the owner-direct WeChat experience. The existing multipart
plan is the durable authority for delivery; canonical assistant text remains the conversation truth.
A sticker must not require a second model agent, a new conversation turn, or parsing model prose.

## Decision

Use an opt-in channel presentation policy and a local versioned preset catalog. Select at most one
image from the exact generation's durable structured Character ResponsePlan. Unmatched plans,
technical bypass, unsupported provider capabilities, and invalid or missing catalog assets fall back
to text. Generic `answer` intent also falls back to text even when persisted affect yields a happy
expression; background mood alone is not an intentional sticker reaction. The initial generated cat artwork is a small test preset pack for the default character.

Store a typed image part containing a stable asset identifier, content hash, and MIME type. Resolve
only catalog-owned bounded local files; never accept arbitrary paths, remote asset URLs, or model
output as a file authority. Revalidate the bytes against the frozen hash before sending. Changed or
missing assets fail that image without silently substituting another image.

Allow one optional final image after required text parts. Parent completion waits for all children to
be terminal, including that image. Exhausted image failure preserves successful text and can complete
the parent without repeating text. New ingress cancels an unsent image tail through the existing
transactional cancellation path. An already-sending part retains the existing lease semantics.
Restart and replay retain asset identity and the stable provider client ID.

The native adapter owns Tencent-specific upload/encryption/send fields. Bound the complete operation
below the delivery lease duration, propagate cancellation, disable redirects, and keep bot credentials
off CDN requests. CDN hosts are explicitly allowlisted. Do not persist or log provider media keys,
signed upload URLs, or reply credentials in public image payloads. A crash after upstream send and
before ACK still relies on upstream idempotency; no exactly-once display claim is added.

## Scope

This decision covers preset outbound PNG/JPEG reactions. Inbound image downloading/learning, OCR/VLM,
user media libraries, shared jokes, group identities, proactive sends, and new recipients remain
separate roadmap work. Real macOS acceptance does not imply installed Windows/Linux acceptance.

## Sources

- [ADR 0032](0032-durable-multipart-channel-delivery.md)
- [ADR 0033](0033-instant-messaging-bubble-planning-and-durable-cadence.md)
- [Tencent adapter source, pinned commit 70ab695](https://github.com/Tencent/openclaw-weixin/tree/70ab695f6a1ca87da4102f857a452e2acb6b37cf)
