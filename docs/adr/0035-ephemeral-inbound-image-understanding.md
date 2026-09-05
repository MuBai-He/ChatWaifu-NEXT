# ADR 0035: Ephemeral inbound image understanding

- Status: Accepted
- Date: 2026-09-05
- Validation: 682 Python tests, 181 Web tests, native/model integration and live configured-model image probe passed; real macOS WeChat image understanding confirmed by the owner on 2026-09-05

## Context

Phase 17.3 includes image understanding and later sticker learning. Receiving a picture must not
block the native update loop or prevent a newer message from cancelling an older response. Provider
media URLs and decryption keys must stay out of conversation events and durable public records.

## Decision

The native iLink adapter accepts one static PNG/JPEG, optionally with a caption. It owns signed CDN
references, AES decoding, bounded download and image validation. Requests use the explicitly allowed
WeChat CDN without redirects, bot authorization headers, or cookies. Reject unsupported, malformed,
animated and oversized images through the existing conversational failure path.

Use the existing text ingress envelope for source identity and a caption or `[图片]` marker. An internal
typed attachment supplies a fingerprint of the private source reference and an asynchronous loader.
The fingerprint participates in idempotency but is not a claim about decoded content equality. Keep
the existing text-only digest unchanged. The public HTTP text endpoint does not gain a raw image or
URL upload API in this slice.

Authenticate and admit the turn before invoking its loader. Conversation commits the normal generation
identity and starts its owned task before fetching bytes. Loading is part of that cancellable task,
outside the admission lock and native polling loop. Check current generation after loading and before
provider streaming. Reuse durable terminal state and multipart delivery for failure replies; never
redownload on duplicate ingress or replay an ephemeral attachment after restart.

Pass one provider-neutral image to the configured chat provider. Only its adapter creates a data URI;
images never appear in public events, conversation history or memory inputs. History retains the
caption/marker and ordinary assistant text. The current picture is untrusted user content, including
any text inside it. External-channel turns retain their existing tool restrictions. A provider that
rejects images must fail visibly rather than silently retrying the prompt without the image.

## Scope and consequences

Image bytes exist only for the current generation and are not saved as media assets. Restart reconciles
an interrupted channel turn to a durable terminal failure; a new message can start normally. This
slice does not promise resumption of an interrupted download.

This is Phase 17.3A, not completion of Phase 17.3. Dynamic sticker learning, persistent media libraries,
visual embeddings, image deduplication, deletion controls, OCR services, animations, multiple images,
video, groups and additional providers remain separate work. The configured chat model must actually
support image input; endpoint compatibility alone does not establish visual understanding.

## Sources

- [External-channel architecture](../architecture/external-channels.md)
- [ADR 0034](0034-preset-sticker-outbound.md)
- [Tencent adapter source, pinned commit 70ab695](https://github.com/Tencent/openclaw-weixin/tree/70ab695f6a1ca87da4102f857a452e2acb6b37cf)
