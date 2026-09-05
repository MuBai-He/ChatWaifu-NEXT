# ADR 0037: Opt-in photo retention and source-backed visual recall

Status: Accepted design; implementation and acceptance in progress

## Decision

Photo retention has an independent, default-off owner/character setting. A bounded background
observer describes ordinary static photos with a structured provider-neutral tool response.
Only confident observations qualify; documents, screenshots and reaction stickers do not enter
this gallery. Human identity, relationships, location and personal facts must not be inferred
from appearance. User captions remain attributed user statements. This is an image evidence
store, separate from MemoryRecord extraction and policy; no visual description is automatically
promoted into a durable personal fact.

Retain a metadata-free local copy bounded to 2048 pixels per side and 5 MiB, its visible-content
description and keywords, and original conversation/time provenance. The copy is not a promise
of original-file fidelity. Limit each owner/character to 200 photos and 500 MiB. Capacity exhaustion
skips new retention without silent eviction. Disabling retention stops new collection; existing
photos remain available until deleted. No photo becomes an outbound sticker automatically.

Save is conditional on the captured settings revision, enabled retention, an enabled source
connection of matching scope and character, and a completed source generation/channel turn.
Hash deduplication preserves all source generations needed for later deletion. Deleting a photo
increments the settings revision to fence in-flight saves; a later explicit new upload can be
retained under a new revision. Shutdown cancels bounded unfinished observations, without replay.

Retrieval uses scoped bounded lexical search and explicit recent-photo references, with a small
context budget. It does not claim general embedding-based semantic search. A recalled photo may
be supplied as image evidence to the model when the user refers to that photo; the response must
distinguish observed content, user caption and uncertainty. Ambiguous or absent matches must not
be invented. Source and recall generations are recorded atomically against still-present photos.

Deletion removes bytes, descriptions, search entries and recall associations in one transaction.
Opaque generation redactions survive deletion so future model context excludes assistant replies
derived from that photo, including cross-surface history. The visible chat transcript remains a
transcript; deleting a photo does not rewrite user-authored messages or unrelated user facts.
Active generations using deleted photo evidence are cancelled by exact generation identity.
Deletion cannot withdraw already delivered messages or bytes already transmitted to a provider.

Opaque history dependency edges are recorded before prompt compilation. Deletion traverses
those edges as well as direct image recall references, so indirect assistant paraphrases and
replies made before the background observation finishes are covered. Context read before a
deletion is rechecked at the prompt boundary. Experience reset clears photo assets in the same
transaction as other character/owner state, retaining revision fences and context redactions.

## Architecture and acceptance

The photo service owns observation and relevance policy and depends on a typed repository port.
SQLite owns atomic media/provenance/index storage. Conversation owns generation cancellation and
prompt assembly. The frontend uses authenticated versioned metadata APIs and no-store binary
previews. The existing memory extraction/commit pipeline remains authoritative for personal facts.

The first complete slice covers one owner, default character and static native WeChat photos,
settings, gallery, preview/delete, restart-safe retrieval and deletion-aware context. Embeddings,
groups, animations, automatic shared jokes and adaptive sticker selection remain later work.
Acceptance requires a real photo, intervening conversation, Runtime restart, accurate later
reference, and deletion followed by a negative recall check, separately from deterministic tests.
