# ADR 0068: Owner opt-in QQ proactive text

- Status: Accepted
- Date: 2026-10-03
- Scope: Fixed paired-owner private QQ idle check-ins, disabled by default.
- Extends: ADR 0064 delivery fences and ADR 0067 current-reply voice boundaries.

## Context

Phase D2 requires character-initiated text to the existing paired owner. An
inbound reply permission, the desktop companion toggle, quoted text or model
output cannot authorize a new external send. Existing channel delivery plans
require an inbound turn, so inventing a provider message to attach an unsolicited
reply would give the wrong source, recipient and recovery semantics.

Conversation already owns proactive character generation. The channel domain
must supply independently authorized intent and delivery, rather than add a
second prompt, model loop or memory implementation. Desktop Ambient currently
scans all ready sessions and must exclude channel-owned sessions.

## Decision

An operator manages a versioned policy on the existing QQ connection. Defaults
are disabled, idle 45 minutes, cooldown 60 minutes, three reservations per local
day, quiet hours 23:00–08:00, explicit IANA `Asia/Shanghai`, and a 15-minute TTL
bounded to 60 minutes. Only `idle_check_in` is supported. The operator may change
these values or disable the route; no remote message or Runtime Skill can do so.
Generation and delivery are text-only with no tools, TTS, local playback or
stickers. Current-reply voice authorization does not apply.

The last real admitted owner input anchors an idle episode, using the Runtime's
accepted timestamp rather than an untrusted provider timestamp. Its due time is
owner activity plus idle duration and its expiry is due time plus TTL. This
prevents startup or enabling a policy from sending old idle backlogs. One episode
may reserve at most one intent regardless of policy updates or a local midnight.
Quiet hours, cooldown, busy generation and daily budget can defer only within
this episode window. Only a new owner input creates a new episode.
Every saved policy revision fences the current owner anchor and cancels the old
episode. A new owner input after saving is required before another idle episode
can open, including first opt-in and re-enabling. Due/expiry are durable episode
facts; changing idle duration or TTL cannot give an unreserved old anchor a fresh
window. GET and preview never create an episode. Scoped experience reset also
invalidates old owner activity and unsent intents without deleting send facts.

A channel-owned persistence port atomically reserves policy/route revisions,
fixed connection/account/binding/character/scope, source key, local budget day,
expiry and preallocated session/turn/generation lineage. Budget is not refunded
on failure, cancellation or unknown send. Admission uses a unique episode key,
at most one nonterminal intent per binding and at most 32 globally. Indexed
bounded scans and paginated sanitized history prevent unbounded tick work.

Intent work states are `pending`, `generating`, `planned`, and `settled`.
Settled work reason and cancellation metadata remain distinct from actual
delivery status and confirmed provider receipts. A late successful provider
result is retained even if cancellation or expiry settled the intent meanwhile.

Channel delivery records select exactly one source: a real inbound channel turn
or an outbound intent. Preserve existing part/lease/ACK/journal state machines,
identities and provider success facts. Outbound plans have no inbound reply
reference. Existing inbound delivery snapshots keep schema 1.0; outbound snapshots
use schema 1.1 with null `channel_turn_id` and an `outbound_intent_id`. Validate
the version/source pairing and leave legacy parent claim/ACK inbound-only.
Migration 38 rebuilds parent/part tables safely with foreign keys on,
copies existing facts, verifies them and recreates indexes; no synthetic inbound
message or destructive down migration is permitted.

The normal Conversation proactive path accepts preallocated lineage and explicit
external source/output options. It retains a hidden SYSTEM turn and existing
Character/Memory reasoning. Preparation must be registered and cancellable
before slow context awaits; it cannot hold the start lock across those awaits.
Old Desktop callers retain their output behavior. Restart reconciles the saved
generation; a possibly started generation is never submitted again. An orphan
fails once without replaying model work or creating a recovery greeting.

Recheck route/policy, owner/scope, episode, TTL, quiet hours and active lineage
before generation, plan publication, claim and after account preflight just
before provider send. New admitted owner input supersedes proactive generation
and untransmitted parts. Stop, reset, disable, unpair, delete and policy revision
changes durably cancel pending work and await bounded tasks.

Known provider success is reconciled into receipts before policy rejection.
Reconciliation makes no send and does not require a now-revoked permission.
Unknown send is fenced and not automatically retried. Cancellation may stop
unsent work, but cannot make an already completed external side effect untrue.
Provider receipt proves API acceptance; phone receipt requires human observation.

## Management surface

Authenticated operator endpoints expose policy GET/PUT with revision CAS,
policy-only preview, paginated intent history and idempotent cancel. Preview
returns eligibility, reason, episode times and reservation counts; it does not
call a model, reserve budget or send to QQ. The existing QQ panel displays fixed
owner destination, the independent opt-in, schedule/frequency controls and
recent actual delivery results. Cancellation and delivery facts are both shown.
No API accepts a model-selected recipient, raw OneBot payload or credential.
Absent policy has revision zero and remains disabled; its first PUT uses CAS
zero. Cancel includes the observed intent revision and returns the latest
snapshot. A conflict refreshes the view without silently retrying an old action.
Policy updates and intent cancellation commit the observed-revision CAS before
fencing and joining generation work. A rejected CAS has no cancellation side
effects; successful policy changes atomically revoke their old episodes/intents
with the new revision, then await only the revoked work.

## Acceptance and rollback

Use injected clocks and controlled events for disabled/default and desktop-toggle
isolation, timezone/DST and midnight, quiet hours, episode/cooldown/budget races,
busy deferral, TTL, revocation at every await, current-owner supersession and
bounded teardown. SQLite reopen verifies unique preallocation, no repeated
generation, preserved old plans, unknown-send fencing and successful-send ACK
reconciliation after cancellation. Exercise the real local OneBot boundary and
shared Web/API surface; keep owner text/voice/image regressions passing.

Implement and deploy with the policy disabled. Real enabling requires an
operator-selected schedule and opt-in; then independently confirm phone text,
quiet hours/disable, new-input cancellation and restart without replay. Automated
tests and healthy endpoints cannot substitute for this acceptance.

Disable the feature and retain business facts as the first rollback. Back up
the migrated database before any code downgrade. Never restore an older database
over newly admitted turns or confirmed provider sends. Groups, calendar events,
Skill completions, proactive voice and search-workstream integration remain
separate phases and permissions.
