# ADR 0048: Cloud playback confirmation and recoverable spoken memory

- Status: Accepted
- Date: 2026-09-09
- Scope: Phase 13.4C

## Context

Provider `response.done` means audio production has finished. It does not prove client playback has finished. Interrupting buffered audio must remain possible without recording unheard text as spoken memory.

## Decision

Cloud output uses one segment per response because the provider does not supply word-level audio alignment. Item-level audio completion does not close that segment. After all PCM handoffs, the media bridge persists final duration and sends the buffered marker. The client reports playback through the existing ACK contract with stream, generation and segment identity.

PlaybackService joins final duration, final transcript (including an explicitly empty transcript) and an ended ACK in a SQLite transaction. Finalized fields are immutable. Cancelled or failed generations cannot gain new spoken commitments. Partial text is not estimated from character counts or elapsed time.

ConversationService remains authoritative for generation lifecycle. A session-scoped, ownership-token-protected completion listener informs the active cloud coordinator. The coordinator remains interruptible while buffered audio plays; its missing-ACK deadline allows the full audio duration plus grace. Teardown and cancellation fence output handoffs and cancel owned timers. Cascade retains its existing production lifecycle.

A spoken commitment and its pending memory fact are saved atomically. EventHub only wakes a repository-backed worker; bounded reconciliation and startup recovery do not depend on outbox publication. The worker persists extracted candidates before applying them. Stable per-event candidate IDs and atomic proposal/record/source writes allow interrupted work to resume without re-extracting staged content or duplicating records. Extraction runs outside the playback ACK path. Failed attempts use persisted capped backoff and stop after a bounded number of attempts.

Memory reset publishes a durable scope fence and serializes against spoken candidate application. In-flight extraction and staged work must recheck that fence before applying. Error records contain error types rather than provider messages that could contain private text.

## Verification and limits

Automated tests cover the transcript/duration/ACK join, multi-item audio, interruption, duplicate receipts, long-audio deadlines, listener ownership, restart before and after candidate application, partial multi-candidate recovery, retry backoff, and reset while candidates are staged. Web tests cover identity and final duration updates.

Client receipts remain client-reported evidence, not independent physical proof that the user heard sound. Live OpenAI access, microphone capture and native listening acceptance remain pending. Reconnect/context recovery (13.7), cloud tool execution (13.5), context synchronization (13.6), and Phase 17.4C remain separate work.
