# ADR 0073: Channel-scoped short replies and QQ group bubbles

- Status: Accepted
- Date: 2026-10-05
- Scope: External messaging presentation; extends ADRs 0032, 0033 and 0069
- Validation state: Deterministic and targeted model checks complete; main Runtime deployed; both connections ready and original group restored; handset UX pending

## Decision

External messages use a short conversational output contract. The whole casual
reply is usually 5–30 Chinese characters or a similarly brief utterance in another
language. Default to one natural short response; express character in that response
instead of adding an explanation after a short reaction. A second message requires
requested content or a necessary clarification. Acknowledgements and goodbyes do not add advice or a new
question. Detailed tasks retain the requested content, source conditions, code,
and uncertainty. Length guidance is not a truncation limit or a bubble quota.

Conversation's existing `external_channel` origin selects this contract for all
messaging providers, including a connection that requests single-text delivery.
Local text, Web, desktop-pet and voice generations retain the existing standard
output contract. External source metadata in their history does not change their
current origin. The v7 persona and global model/context budgets remain unchanged.

Use the existing canonical assistant turn and lossless delivery plan factory;
do not introduce a second sending tool or reinterpret arbitrary model text as
commands. Missing channel presentation configuration receives instant-message
defaults: three maximum text parts, preferred 30 characters, soft maximum 60,
and existing bounded durable cadence. Explicit operator policy remains effective.
Separate complete message ideas may use paragraph boundaries; a sole useful
sentence remains one message. Structured technical replies retain their bypass.

QQ fixed-group replies may contain ordered required text parts under the same
immutable admitted group target. No group tools, media, quotes, new participation
grants or proactive messages are added. Each actual send rechecks group authority,
completed generation, full plan text, part lease and earlier receipts. A newer
mention, membership change, disconnect, reset or disable cancels the unsent tail.
Known send receipts and unknown-send fences remain per part.

Migration 41 replaces only the singleton group-part and scheduling guards. Text
content, part identities and targets remain immutable. Part insertion is bounded
and sequential; `not_before_at` may be set only for a pending tail after its
predecessor's delivered receipt, at the predecessor's persisted cadence delay.
Existing facts and migration 40 checksums are preserved.

## Verification and deployment

Verify production Conversation origin isolation, private/group plan generation,
canonical reconstruction, technical bypass, per-part send authorization, cadence,
new-input/revocation cancellation, receipt recovery, malformed plans and populated
40→41 migration. Run targeted real Gemini 3.8 Flash High comparisons with fixed
questions and context, retaining actual replies, usage and latency. These samples
assess this change; they do not close the broader Q02 quality gate.

Deploy from a frozen local commit after source and regression review. Preserve
the current model endpoint, credentials, state, Web assets and search services;
public `web.read` continues to require `dns_resolver=cloudflare`. Back up the live
database and policy before stopping the main Runtime. Revalidate the previously
enabled fixed group against a fresh matching audience after reconnect.

Rollback preserves the current database and new facts. A rollback source must
understand migration 41; do not reopen it with a migration-40-only executable or
restore an old database over new messages. Cancel any unsent multipart group tail
before restoring the previous group implementation and previous channel policy.
