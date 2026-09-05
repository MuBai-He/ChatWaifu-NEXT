# ADR 0036: Opt-in learned sticker library

Status: Accepted design; implementation and acceptance in progress

## Context

Phase 17.3A understands one ephemeral native WeChat image. The owner wants suitable images to
become reusable stickers after explicitly enabling learning. The owner also wants later photo
retention and recall; excluding photographs from sticker screening must not remove that goal.

## Decision

Introduce a sticker-library service with a typed repository port and SQLite adapter. The native
channel forwards a validated provider-neutral image only after normal authenticated generation
admission. A bounded background classifier uses the configured model and a single structured
classification tool and a preview bounded to 384 pixels per side; image text is untrusted content.
No ordinary prose is parsed as control.
Conservative suitability and confidence checks reject uncertain images. Classification is a
heuristic: it can miss a valid sticker or misclassify an image, so the library remains inspectable
and deletable. Classification failures never replace or fail a successful conversational reply.

The library is scoped by server-owned principal and character. Learning is off by default. The
existing channel sticker switch separately authorizes outbound reuse. Turning learning off keeps
previously saved assets; deleting an asset removes it from subsequent selection and lookups.

Only accepted images are re-encoded as metadata-free PNGs bounded to 1024 pixels per side and
5 MiB each. Migration 24 stores BLOBs and metadata transactionally in SQLite, with SHA-256
deduplication and limits of 100 assets / 100 MiB per principal and character. There is no silent
eviction. Keeping bytes and deletion in the same transaction avoids filesystem-orphan recovery.
The public snapshot contains metadata only; previews use authenticated binary HTTP with no-store
responses, never query-string credentials or arbitrary file paths.

Settings use compare-and-swap revisions. Save checks the original revision, enabled learning,
an enabled source connection with matching scope and character, and a completed source channel
turn. Delete increments the revision while removing the BLOB, fencing already-running learners
from resurrecting an asset. A newly received image may be learned again under the new revision.
Shutdown cancels bounded in-memory jobs; restart retains accepted assets, not transient images or
unfinished classifications. No unbounded durable backlog or automatic classification replay is added.

Outbound selection freezes the existing image payload's sticker identifier and content hash.
The `learned_` namespace resolves through the scoped repository; preset identifiers keep their
existing resolver. Missing/deleted/hash-mismatched images fail the optional part rather than
substituting a different image or repeating text. Existing generation cancellation, leases, ACKs
and restart delivery semantics remain authoritative. A part already holding a sending lease may
finish its transfer; deletion cannot unsend it. SQLite logical deletion does not promise forensic
erasure of WAL/backups.

## Scope and later work

This slice handles default-character owner-direct static WeChat images. Photos, wallpaper,
documents, animations, multiple images and visual embeddings are outside sticker collection.
Photo retention and visual memory are a separate planned Phase 17.3C with independent controls,
source-backed recall and deletion of derived references. This ADR does not authorize storing
every inbound photo or changing existing long-term-memory extraction policy.

Acceptance separates automated cancellation, deduplication, persistence, API and UI checks from
real macOS WeChat learning, preview, reuse and deletion. Completing this slice does not complete
all of Phase 17.3 or Phase 17.4's shared-joke and adaptive-recall roadmap.
