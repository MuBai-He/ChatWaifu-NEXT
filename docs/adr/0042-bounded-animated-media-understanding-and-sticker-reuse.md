# ADR 0042: Bounded animated media understanding and sticker reuse

Status: Accepted design; native owner retest pending.

## Context

Phase 17.3 added inbound multi-image understanding and multi-message burst collection. However, animated media—specifically animated GIF and animated PNG (APNG)—were rejected at the boundary (`validate_image_bounds`). Users sending animated stickers and reaction GIFs experienced silent rejections or unexpected static image fallback.

Existing naive approaches commonly downgrade animated media to their static first frame. For animated stickers (where the punchline or emotional reaction develops over time, or where the first frame is an empty initialization canvas), first-frame extraction obliterates the communicative intent.

Furthermore, WeChat iLink transmits animated stickers and user GIFs strictly over the standard `item_type: 2` (`image_item`) wire path via AES-128-ECB CDN uploads, identical to static photographs. The client does not use custom XML or invented emoji 47 types for user-uploaded custom animated media.

Finally, raw animated GIF/APNG files cannot be safely or reliably passed directly to multi-modal vision LLMs: provider support is inconsistent, token and bandwidth costs are unbounded, and frame pacing is opaque. A robust, bounded, and resource-governed architecture is required.

## Decision

### 1. Wire Format and Transport Reality
- Inbound and outbound animated media strictly utilize the existing `item_type: 2` (`image_item`) wire contract. No synthetic or undocumented emoji (type 47) or XML payload formats are introduced.
- Outbound reuse of learned animated stickers leverages the proven AES-128-ECB CDN upload path (`send_image`), accommodating both normalized animated GIF and APNG assets.

### 2. Resource Guardrails and Bounded Inspection
- Strict bounds are enforced during header sniffing and initial validation before full frame decompression:
  - Encoded file size: maximum 5 MiB (5,242,880 bytes).
  - Encoded dimensions: maximum 4096 pixels per dimension.
  - Frame count: maximum 60 frames.
  - Total duration: maximum 60.0 seconds.
  - Decoded canvas memory: maximum 32,000,000 cumulative decoded pixels across all frames.
- Sequential decoding incorporates a cooperative cancellation token and monotonic time budget check between consecutive frames, preventing decompression bombs or decoder hangs from stalling the Runtime event loop.
- Header MIME sniffing reliably distinguishes GIF87a/GIF89a, static PNG, and APNG (`acTL` chunk preceding any `IDAT` chunk).

### 3. APNG and GIF Decoding Semantics
- APNG `default_image` handling: When an APNG file features a fallback default image that does not participate in the animation sequence (lacking an `fcTL` chunk before `IDAT`), frame 0 is discarded so that only genuine animation frames enter the storyboard and metadata pipelines.
- GIF disposal methods and APNG blend/dispose operations are fully respected during frame synthesis using composite canvases, preventing visual ghosting or corrupted accumulation.

### 4. Temporal Vision Understanding via Storyboard Synthesis
- Large Language Model (LLM) vision providers never receive raw animated GIF or APNG byte streams.
- Up to 4 representative frames are sampled uniformly across the animation duration (e.g. initial, intermediate, and final key states).
- The sampled frames are composited into a clean, metadata-free storyboard raster grid (maximum 1024x1024, JPEG quality 85) packaged inside provider-ready `LlmInputImage`.
- System prompts are augmented with structured temporal context:
  - Header: `[Temporal Animation Analysis]`
  - Layout description (e.g. `2x2 grid (reading order: top-left, top-right, bottom-left, bottom-right)` or `1x2 horizontal sequence`).
  - Frame index, presentation timestamps (PTS in seconds), and individual frame durations.
- The vision model interprets motion, expression progression, and timing without provider-specific animated format support.

### 5. Photo Memory Explicit Exclusion
- Photo memory specifically targets long-term autobiographical photographs.
- Inbound animated items (`InboundMediaItem.is_animated is True`) are explicitly skipped by `PhotoMemoryObserver`. Storyboard composites and animated stickers are never silently retained in photo memory albums.

### 6. Opt-In Sticker Learning and Asset Normalization
- Reject static-first-frame downgrade: when animated media is identified and approved as a reusable chat sticker by the opt-in sticker classifier, the complete multi-frame animation sequence is preserved.
- Deterministic asset normalization strips auxiliary metadata, EXIF, unneeded comments, and ancillary chunks while strictly preserving:
  - Exact frame timing and durations.
  - Loop counts (`loop=0` for infinite playback).
  - Transparency channels (alpha channels for APNG, transparent index for GIF).
- Persistence Migration 29 adds `mime_type` (`CHECK(mime_type IN ('image/png', 'image/gif'))`) and `is_animated` (`CHECK(is_animated IN (0, 1))`) columns to `learned_stickers`.
- Outbound sticker selection provides either static PNG or animated GIF assets directly to channel adapters based on recipient capabilities.

### 7. Web UI Safe Preview and Deferred Playback
- To prevent battery drain, excessive CPU utilization, and visual clutter from dozens of concurrently playing GIFs across sticker grids:
  - The runtime sticker routes support `GET /api/stickers/{id}/asset?poster=true`, extracting a lightweight single-frame PNG poster.
  - Web UI sticker library displays static posters by default with an `[ANIM]` badge.
  - Hovering or clicking toggles an inline preview player that loads the full animated asset on-demand.

## Validation and Limits

- Comprehensive unit and integration test coverage (`test_animated_media.py`):
  - Sniffing, bounded validation, and rejection of malformed or out-of-bounds files (oversized bytes, excessive dimensions, frame count > 60).
  - Verification of real validation fixtures (`moving-ball.gif`, `moving-ball.apng`).
  - Storyboard layout generation for 1, 2, 3, and 4 panels.
  - Poster frame extraction.
  - APNG `default_image` skip logic.
  - Animation preservation and metadata stripping during normalization.
  - End-to-end conversation pipeline temporal instruction injection.
  - Photo memory exclusion verification.
  - SQLite Migration 29 and repository round-trip asset retrieval.
  - Cooperative decoding timeout and cancellation.
- Complete regression verification:
  - WeChat iLink adapter wire tests (`test_image.py`, `test_wire_image.py`).
  - Burst integration tests (`test_inbound_image_lifecycle.py`, `test_learned_sticker_delivery.py`).
  - Protocol schema generation and Web UI TypeScript typecheck and Vitest suite.
- Limits: Video formats (MP4/WebM) and animated WebP remain outside the scope of Phase 17.3G.
