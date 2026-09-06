# ADR 0041: Bounded native WeChat image burst collection

Status: Accepted design; native owner retest pending.

## Context

On 2026-09-06, the owner's native WeChat multi-selection arrived as two separate messages approximately one second apart. The second superseded the first generation. The client platform was not established. ADR 0040's support for several images inside one wire message did not cover this observed case.

The owner chose a short collection window followed by one combined reply. Poll response grouping is not a user-message boundary and must not determine image cardinality.

## Decision

- Native, owner-only, direct image intake for the default character collects for 1.5 seconds after the last image, with a 4-second ceiling from the first admission. At four images it seals immediately. Each timer checks batch identity; idle callbacks also check the window revision.
- A binding has at most one active and one pending batch. Across bindings, at most 16 batches are retained. One shared state lock makes admission bounds atomic; no network or generation work runs under it.
- A later image can start a pending batch without cancelling the active image generation or its delivery tail. If another message exceeds a collecting batch's capacity (such as 3+2), or arrives behind an already sealed pending batch, that pending batch produces one durable friendly image-failure notice at its dispatch slot. Excess identities remain in SQLite; excess private media descriptors are not retained. Global admission overflow receives a durable failure notice without allocating another batch.
- Plain text immediately cancels collected/deferred images and the active image generation, then follows existing text intake. This applies to ordinary text as well as “停一下”. An incoming image supersedes an existing text generation before collection.
- Each original message is authenticated, scoped, fingerprinted and durably admitted before the native poll cursor advances. Migration 28 adds `channel_turn_burst_members` with leader/member IDs, ordinal and original `received_at`. Followers remain accepted during collection, mirror processing and terminal states transactionally, and have no independent generation, reply or delivery plan.
- Only the leader retains a private reply context and owns typing/delivery. Follower contexts are released after admission; leader contexts remain until delivery terminates. Terminal reconciliation and restart cleanup use durable membership.
- Dispatch creates one generation and sequentially downloads all images within a shared 20-second deadline. Per-message cardinality must match its source origins. Existing image size/decoding bounds, whole-batch sanitization before scheduling observers, original metadata extraction, sequential photo/sticker observation and batched indexing remain in force.
- Photo persistence validates origin membership, connection, binding and original message ID, then uses the membership record's authoritative receive time. Invalid origins and mismatched image/origin counts fail closed.
- Live collection is protected from `generation_missing` during status queries. Restart without the ephemeral image inputs produces one durable recovery notice, without redownloading. The generation orchestrator reconciles terminal state independently of the completion listener so a missed callback cannot strand the next batch.

## Validation and limits

The burst suite covers separate poll responses, duplicate/conflicting messages, collection queries, interruption during download, late/deferred images, overflow with and without a trailing stop, transactional repeated terminal updates, queued stale timers, lost terminal notification, restart recovery, origin validation and source receive times. Tests synchronize on admission, cursor, loader/provider or delivery transitions with an injected clock and bounded condition polling.

Independent final verification passed 1046 Python tests (46 platform-dependent skips), including 22 burst cases, plus Ruff and Pyright.

An independent probe through the real WeChat parser/downloader, native management service, SQLite and the configured chat model received two synthetic color images one second apart. It produced one two-image model request, a correct ordered red/blue response and two completed source records with cleared contexts. This is not native owner acceptance; that retest remains pending.

Animated images and Phase 17.4 remain outside this slice.
