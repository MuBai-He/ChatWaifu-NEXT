# ADR 0042: Bounded animated media understanding and sticker reuse

Status: Accepted design; implementation verified; native acceptance pending.

## Context

PR #28 established bounded collection of native WeChat image messages. The
previous image validator accepts static PNG/JPEG and rejects animations. Phase
17.3G extends that image path to GIF/APNG while preserving source cardinality,
interruption, optional retention, and durable image delivery.

Tencent's reference implementation at commit
`2f4dcbf57bedf0e17e266fdedcf0cd2dd141b7d3` maps `.gif` to `image/gif` and routes
image MIME types through image upload and `image_item` messages:
[mime.ts](https://github.com/Tencent/openclaw-weixin/blob/2f4dcbf57bedf0e17e266fdedcf0cd2dd141b7d3/src/media/mime.ts),
[send-media.ts](https://github.com/Tencent/openclaw-weixin/blob/2f4dcbf57bedf0e17e266fdedcf0cd2dd141b7d3/src/messaging/send-media.ts).
This supports using the existing transport. It does not establish how every
native sticker is received or whether the recipient renders GIF/APNG animation.
Synthetic wire fixtures and successful uploads are not native playback evidence.

## Decision

### Media and provider boundary

`InboundMediaItem` retains original bytes for local processing and carries a
clean provider-ready `LlmInputImage`. Providers receive static PNG/JPEG only.
One animation produces one storyboard, so four original images still consume
four image slots. Temporal instructions identify the source image, frame order,
sample timestamps, total frame count and duration. Up to four temporally sampled
frames fit within a 1024x1024 storyboard. Samples cannot establish every event
between frames; prompts must not imply exhaustive video understanding.

Original static photo metadata remains available to photo memory independently
of the sanitized model input. Animated media is excluded from photo retention in
this slice; saving complete animated photographs remains future work. A storyboard
must never masquerade as the original saved photograph.

### Resource and cancellation boundary

Each encoded source is at most 5 MiB. Static limits remain 8192 pixels per dimension
and 16,777,216 canvas pixels. Animations are limited to 60 animation frames, 4096
pixels per dimension, 32,000,000 cumulative decoded canvas pixels, and 60 seconds.
An APNG default poster is not an animation frame but its decoding consumes budget.

Inspection must stop at the frame limit rather than traversing an unbounded
`n_frames` property. Media work runs outside the event loop with at most two
running jobs and at most eight admission waiters. Cancellation/deadline checks occur before inspection and between
frames; a canceled awaiter must not release capacity while its worker still runs.
A single Pillow operation cannot be forcibly interrupted, so finite pixel limits
remain necessary. The existing 20-second whole-burst loading budget still applies.

### Animation and storage boundary

The opt-in sticker classifier receives a clean storyboard. Ordinary photos and
unsuitable content remain excluded from the sticker library. Approved animated
stickers retain their animation rather than becoming a static first frame.
Normalization strips metadata and preserves rendered frames, transparency,
positive frame delays within format precision, and absent/finite/infinite loop
semantics. GIF and APNG disposal/blend behavior and APNG default posters require
pixel-level regression checks.

Migration 29 widens the existing learned-sticker MIME constraint to PNG/GIF and
adds `is_animated`, preserving previous rows and settings. APNG uses `image/png`.
Revision fences, scoped lookup, hash verification, deletion and capacity limits
continue to apply. Provider SDK objects and private source metadata do not enter
public contracts.

### Preview and delivery boundary

`GET /v1/sticker-library/{sticker_id}/image?poster=true` returns a static PNG poster
for an animated asset. The authenticated endpoint keeps no-store/nosniff headers.
The library shows posters and a Chinese animation badge by default; an explicit
play button fetches animation. Requests are bounded, aborted on removal, and late
object URLs are revoked. Failed previews retain their poster and allow retry.

Outbound GIF/APNG uses the existing durable image-part upload, hash and lease
path. There is no invented emoji endpoint or automatic static resend. A failed
optional image follows the existing delivery failure behavior. No transport result
is described as confirmed native animation until observed on the recipient.

## Verification and remaining acceptance

Automated checks cover temporal content, resource bounds, alpha/disposal, static
metadata, mixed source ordinals, opt-in/deletion behavior, and late-work rejection.
Native acceptance separately checks reception, motion understanding, learned
reuse, playback and explicit interruption. Video, WebP, undocumented emoji wire
formats, group identity handling, animated photo retention, and Phase 17.4 are
outside this slice.

Local verification on 2026-09-06 passed 1082 Python tests (46 platform skips),
238 Web tests, 34 TypeScript protocol tests, Ruff, Pyright, architecture checks,
frontend lint and both Web/desktop builds. An isolated copy of the owner database
preserved two stickers, three photos and settings through migration 29 with clean
integrity/foreign-key checks. Four independent GIF/APNG fixtures retained rendered
pixels, alpha, frame counts and loops. The configured chat model correctly
identified left-to-right motion and red-to-blue changes for both GIF and APNG
storyboards. These checks do not substitute for native WeChat playback acceptance.
