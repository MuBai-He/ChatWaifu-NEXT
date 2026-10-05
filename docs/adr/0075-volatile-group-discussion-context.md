# ADR 0075: Volatile group discussion and on-demand compression

- Status: Accepted
- Date: 2026-10-05
- Scope: Existing authorized QQ fixed groups, untrusted text context, mention-only text replies
- Validation: Source, deployment and phone results recorded separately in the evidence document

## Responsibility and trust

NapCat normalizes ordinary structured group text without treating it as a trigger.
ChannelGroupService authenticates the current account/connection, enabled route,
linked member and scene before collecting anything. All linked current audience
members may contribute context; the existing can_speak grant alone permits a
real bot mention to trigger a reply. No nicknames resolve identity or grant access.
Unknown, self, anonymous, wrong-account, disabled and unsupported media inputs
remain outside this collection path. Receiving an image still uses ADR 0074's
separate bounded reference and opt-in learning path.

The application-owned GroupDiscussionCache contains only volatile raw text.
Conversation receives a version-1 immutable scope/snapshot and verifies it against
the persisted shared identity before cancelling or admitting another generation.
The snapshot is separate user-role untrusted model evidence, never system facts,
a formal user turn, private history, a memory source or a tool permission. The
current question alone passes through the existing formal commit and memory
admission: its scene, trusted speaker, subject attribution, explicit-command and
extraction/privacy policies remain in force. Discussion text/selection is not
submitted to memory extraction. The assistant's normal reply remains formal
history; this is not a promise to erase facts a user subsequently explicitly
submits in an authorized turn.

## Limits and lifecycle

Operator TOML/environment settings use frozen `group_discussion` configuration:

| Setting | Default |
| --- | ---: |
| enabled | true, only within already enabled/authorized group routes |
| max_groups | 32 |
| message_characters | 800 |
| cache_messages / cache_characters | 96 / 24000 |
| retention_seconds | 900 |
| member_messages / member_characters | 24 / 6000 |
| member_messages_per_window / frequency_window_seconds | 6 / 30 |
| duplicate_window_seconds | 60 |
| input_tokens | 1536 reference tokens, additionally at most 1/4 of chat allowance |
| summary_input_tokens / summary_output_tokens | 3072 / 256 |
| summary_timeout_seconds | 8 |

Per-member capacity is also at most group capacity divided by audience size;
only that member's oldest entries may be evicted for its quota. It cannot evict
another member. These are maxima, not promised fully usable capacity. Dedup tracks
at most twice cache_messages IDs for the retention period; equal bounded text
from the same speaker within 60 seconds is also rejected without refreshing TTL.
Only accepted items consume the six-item rate allowance. Text truncation is
explicit in evidence. Retention uses host admission time; no provider timestamp
can extend it. Only already authorized scopes allocate caches. At capacity, new
scopes fail collection without evicting another group's live discussion.

Ingress remains bounded at 32 pending tasks, with at most 24 unmentioned tasks
and three per sender/group, reserving slots for mentions and other members.
There is no provider media download, model call, transcript or memory write for
unmentioned text.

Scope includes account, connection revision, group, route revision, scene and
audience fingerprint. Successful route/link changes, reset, disable, account
changes, membership notices, disconnect, reconnect and stop clear the relevant
cache through existing fences. No restart replay/load from transcript exists.
Fresh audience reauthorization remains required. The live audience observation
limitations in ADR 0069 still apply; this cache adds no atomic membership guarantee.

## Compression and budgets

No work is done per message. At a valid mention, recent original messages take
priority. If all available evidence fits, no summary call occurs. Otherwise at
most one bounded call uses the admitted memory_summary route/config and a timeout.
Recent originals receive at least 75% of the evidence allowance; older summary
at most 25%. The model selects up to 16 source message IDs. Runtime quotes complete
selected originals, with trusted participant, receipt time and original message ID,
in original order. Unknown/repeated IDs, additional prose, overlong output,
oversized selection and dropping a represented older speaker reject the summary.
This is extractive compression: it cannot fabricate facts, but selection can still
miss an important condition or topic. Those quality limits require actual review.
Older summary-input evidence is also bounded; omitted messages are counted.

Failure, invalid output, disabled summary provider or timeout falls back to the
full allowance of recent raw text. CancelledError is propagated. Authorization and
active-generation checks run before and after compression. Evidence TTL is checked
again after a slow call. Summaries live only in that generation; no persistent
summary store, incremental rolling abstraction or additional model thread exists.

Complete chat-wire JSON is measured against the frozen model input limit, existing
output reservation and estimation margin before adding evidence. New evidence
cannot push a previously fitting complete request over its allowance. With no
headroom it is omitted, without causing a reply failure. An oversized recent
message can retain an explicitly marked prefix; current question/system rules are
never truncated by this feature. The existing final Agent complete-request guard
still runs. Both input and summary use bundled cl100k reference estimates, not
native Gemini token counts or universal upper bounds; provider usage is distinct.
No operational model window, output reservation, persona or legacy section cap is
expanded by this change.

## Output and verification

This request's text-only group rule overrides ADR 0074's optional output image:
group plan creation and send authorization now reject image parts. Scoped image
understanding/learning can continue, and private stickers/voice remain unchanged.
Existing migration 42 remains compatible; no persistence migration is added.

Verify silence/no model/no memory for unmentioned text, scoped attribution and
references at @, fair quotas/dedup/TTL, full-wire limits, compression provenance and
failure, stale/uncancelled late output, lifecycle reauthorization, private/voice
regression and operator overrides. Frozen real-model samples measure attribution,
conditions, disagreement, unresolved questions, payloads, omissions, usage and
latency independently of fixtures. Server source/old facts/model/config/units and
original grants must be verified after deployment. Phone receipt and reference
quality require user evidence and cannot be inferred from fixture sends or HTTP 200.
