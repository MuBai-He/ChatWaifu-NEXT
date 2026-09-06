# ADR 0039: Bounded Photo Source Metadata and Capture Date Extraction

Status: Accepted design; implementation and isolated Runtime/Web acceptance complete; owner real photo WeChat acceptance succeeded on 2026-09-06

## Context

Phase 17.3C and 17.3D established opt-in photo retention, conservative structured classification,
metadata-free local storage, and semantic recall under [ADR 0037](0037-opt-in-photo-memory.md) and
[ADR 0038](0038-bounded-photo-semantic-recall.md).

However, photos saved under ADR 0037 recorded only channel receipt time (`received_at`) and
database insertion time (`saved_at`). When a user shares a past photo (e.g. a vacation photo from
several months or years ago), referring to `received_at` as the date the photo was taken is inaccurate
and harms user trust. At the same time, arbitrary EXIF dumps pose major privacy and security risks:

- GPS coordinates (`GPSInfo`) disclose precise home, work, or private user locations.
- Hardware serial numbers (`BodySerialNumber`, `LensSerialNumber`) enable device fingerprinting.
- Proprietary maker notes, thumbnail streams, and face regions expose unexpected side channels.
- Submitting raw EXIF data to vision language models leaks location and device data to third parties.

Furthermore, EXIF timestamps are self-reported and untrusted: clocks may be desynchronized, camera
timezones are frequently unconfigured (naive wall-clock time), and timestamps can be forged.
Pre-migration stored photos possess no EXIF metadata; fabricating capture dates from channel
receipt times would create false historical records.

## Decision

1. **Strict Allowlist Extraction from Inbound Original Bytes**:
   - Extraction occurs once from raw inbound bytes before normalization.
   - The allowlist consists strictly of:
     - `captured_at`: EXIF `DateTimeOriginal` (Exif IFD tag 0x9003 or primary IFD tag 0x9003) parsed
       and normalized into ISO 8601 (`YYYY-MM-DDTHH:MM:SS` or `YYYY-MM-DDTHH:MM:SS±HH:MM`).
     - `captured_at_offset`: EXIF `OffsetTimeOriginal` (tag 0x9011) normalized to `±HH:MM`.
     - `original_width` & `original_height`: Inbound pixel dimensions adjusted for EXIF orientation
       (orientations 5, 6, 7, 8 transpose dimensions to reflect visual orientation).
     - `original_mime_type`: Inbound media type (`image/jpeg` or `image/png`).
   - **Strict Privacy Exclusions**:
     - GPS tags (tag 0x8825), device serials (tag 0xA431), camera models, maker notes, and private IFDs
       are strictly excluded and dropped from memory immediately upon extraction.
   - **Safe Unknown Fallback**:
     - Missing tags, corrupted bytes, invalid calendar dates
       gracefully yield `None` without raising exceptions or aborting photo retention.
     - Naive timestamps (lacking timezone offsets) remain naive without assuming host or browser timezones.

2. **Metadata-Free Stored Raster Retention (ADR 0037 Invariant)**:
   - Stored normalized image bytes in `photo_assets.data` remain completely stripped of all EXIF metadata.
   - Extracted allowlist values are persisted exclusively as dedicated columns in `photo_assets`.

3. **Vision Provider EXIF Isolation**:
   - Inbound images dispatched to vision models (both main conversation turns and preview classifiers)
     have their EXIF metadata stripped at the conversation loader and existing classifier preview boundary.
   - External vision providers never receive raw EXIF headers, GPS coordinates, or device identifiers.

4. **Persistence and Migration 27**:
   - Migration 27 introduces nullable columns to `photo_assets`:
     - `captured_at TEXT`
     - `captured_at_offset TEXT`
     - `original_width INTEGER`
     - `original_height INTEGER`
     - `original_mime_type TEXT`
   - Pre-migration photos remain `NULL` for all new columns. The migration never synthesizes capture dates
     from `received_at`.

5. **Deduplication Invariant**:
   - Inbound duplicate photos matching an existing `(principal_scope, character_id, sha256)` create a
     new `photo_references` entry but never overwrite or mutate the authoritative metadata of `photo_assets`.

6. **Conversational Recall and Guidance**:
   - Recall evidence packets include `captured_at` alongside `received_at`.
   - The prompt preamble instructs the assistant:
     - `received_at` is authoritative channel receipt evidence and is NOT the date taken.
     - `captured_at` is EXIF-attributed metadata and is not guaranteed true.
     - Descriptions are observations; captions are attributed user statements.

7. **Desktop Gallery Experience**:
   - Photo tiles display `captured_at` (formatted with `拍摄:`) when available, falling back to `received_at`.
   - Preview dialog displays an explicit metadata section showing capture time (with explicit `(时区未知)`
     for naive timestamps to avoid browser timezone corruption), receive time, save time, and original vs stored specs.

## Consequences

- **Positive**:
  - High user trust: users see accurate capture dates for older photos without temporal confusion.
  - Strong privacy: zero GPS, device serial, or private EXIF retention or transmission to LLM providers.
  - Complete backwards compatibility: pre-migration photos and photos without EXIF remain intact and safely handled.
  - Clear architectural separation between immutable channel evidence (`received_at`), untrusted device metadata (`captured_at`), and system insertion time (`saved_at`).

- **Negative / Trade-offs**:
  - EXIF dates can be spoofed by clients; conversational guidance must maintain appropriate skepticism.
  - Naive EXIF dates without timezone offsets cannot be converted to exact UTC points in time without user clarification.

## User-provided photo information

The owner additionally requested automatic association of user statements with photos. A bounded
background service uses the configured memory_extraction role to propose a photo ID, exact source
quote, category and optional correction target. It reuses model configuration and conservative
candidate validation; it does not bypass the personal MemoryRecord pipeline or manufacture a
personal fact from visual evidence. Opt-in photo retention is required.

Only photos already referenced by the completed current generation or its immediately preceding
user turn in the same session (within 30 minutes) are candidates. Up to three candidates are
provided; unclear references, questions and unrelated remarks should produce no candidate.
An unsaved intervening photo has no reference and cannot fall back to an older picture. This
first slice may skip a follow-up while photo collection is pending; it does not claim arbitrary
long-range pronoun resolution or pending-message replay.

Persist up to 32 attributed quotes per photo, with source generation, authoritative utterance time,
and supersession history in user_annotations_json. The quote must occur literally in the user
message, confidence must be at least 0.9, and corrections can supersede only an active annotation
of the same category. Replay and active identical quotes do not duplicate entries. EXIF stays
separate; relative dates remain verbatim and are interpreted against utterance time with original
precision, never normalized using the current day.

Writes recheck completed source, settings revision and asset existence inside the transaction.
They update lexical search, invalidate that photo's vector, and register deletion/history dependencies.
Incremental vector commits check annotation count so a pre-correction embedding cannot overwrite
new content. Model changes still never start an automatic bulk rebuild. Tasks are bounded to two,
with ten-second deadlines and shutdown cancellation. Existing annotations survive disabled collection.
Recall presents bounded active quotes, and the photo detail panel displays the user's additions.

Independent live memory-model probes in an isolated database associated a birthday statement,
accepted its correction, and rejected an aesthetic question and unrelated food statement. These
four examples do not establish general reference-resolution accuracy; owner real photo WeChat acceptance succeeded on 2026-09-06 (single photo annotation/recall confirmed; animations and Phase 17.4 remain pending).
