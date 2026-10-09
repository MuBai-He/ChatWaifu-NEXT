# ADR 0069: QQ group member identity and shared context

- Status: Accepted
- Date: 2026-10-03
- Scope: D3, registered participants, shared memory, member character state,
  fixed external group routes
- Validation state: Automated checks, dedicated Linux deployment, two-member real at/text replies and temporary disable/private continuity are verified. Fresh audience observation restored the same selected group after Runtime restart, with resumed text confirmed on the phone and in the delivery ledger. Further privacy/membership fault acceptance remains pending.

## Decision

D3 remains disabled until the Runtime operator selects a fixed small group and
links every observed human audience member to a registered participant. A group
scene contains 2–2,000 registered participants (expanded on 2026-10-09). The speaking grant is a subset of
that audience. QQ nicknames and model output never resolve or grant identities.
The existing paired-owner private route remains independent.

The first group slice accepts only structured `at.qq == self_id` plus nonempty
text from a granted member of an enabled route. It emits one text reply to the
persisted group target, with no tools, voice, images, quotes or proactive group
messages. Ordinary messages do not invoke generation or memory extraction.
Anonymous, unknown, self-account-mismatched, self-sent and mixed-media messages
fail closed. The connection administrator is separate from the conversational
speaker.

### Reply envelope compatibility, 2026-10-05

The group text receiver now accepts one structured `reply` segment alongside
the required real `at.qq == self_id` and nonempty text. Two actual group inputs
had this shape but were discarded before Runtime admission. A reply reference
must be one canonical, nonzero signed numeric message ID of at most 20 digits;
duplicate or malformed references still reject the whole input.

This is an input-envelope compatibility fix. The reference does not grant a
reply trigger or resolve quoted content. The current text and existing
scope-checked group history are passed to Conversation, as before. No provider
`get_msg` lookup or private quote loader is added. Standalone quotes without a
bot mention, mixed media and output quote segments remain outside this slice.
Operator grants, scene boundaries, route cancellation and delivery fencing
continue to apply. Automated and server checks are recorded separately from
fresh handset acceptance in the [investigation record](../research/qq-agent-plus-evidence/qq-group-quote-2026-10-05.md).

## Session, memory and character state

The persisted session owns conversational identity. Its existing immutable
`user_scope` remains the memory/history boundary. A new immutable `state_scope`
controls character affect and relationship state. New shared sessions derive
`scene_member:<scene_id>:<participant_id>` for state while retaining
`scene:<scene_id>` for memory. Private sessions retain their current scope.
Migration 39 backfills existing `state_scope` from `user_scope`, preserving all
legacy state and facts; it does not copy legacy shared relationship state into
new member state. Newly enabled groups use newly created scenes, never existing
ambiguous shared scenes.

Memory evidence resolves the speaker and scene through source event → persisted
session. First-person facts in shared scenes use the trusted participant subject,
including deterministic extraction and background inference. Model-supplied
subjects and display names cannot select another registered participant.
Third-person claims stay in review rather than being silently attributed.
Legacy shared records with subject `user` are not assigned to a new participant.

Exact-match deduplication, tombstone checks and active uniqueness include the
subject as well as namespace and normalized text. Corrections and conversational
forget requests in shared scenes are limited to the speaker's subject. An
operator's separately authorized management/reset action retains its existing
scope. Retrieval, inference context and character prompts preserve subject labels
through trimming, so identical first-person text from two members stays distinct.
Member private namespaces, owner memories, photos, calendar and tool authority
never enter group context. Resetting scene memory clears its associated member
state and cancels the affected sessions without touching unrelated scopes.

## Fixed routes and cancellation

The channel repository owns immutable provider/account/sender → participant
links and fixed group routes with scene, audience observation, speaker grants
and revision. Each member has a separate session/binding. Trusted session
identity and route lineage are typed values; provider SDK objects remain in the
adapter. Conversation verifies external identity against the persisted session
before retaining QQ provenance and continues to prohibit nonowner tools.

Concurrency is bounded per entire group route: one active generation and one
replaceable latest pending input. A newer accepted mention supersedes the old
work across members. Preparation registers cancellable lineage before any slow
context loading, and generation locks remain short. Guards run before generation,
plan publication, claim and actual send after account preflight. Membership,
route revision, reset, disconnect or disable invalidates unsent old work.

Deduplication uses connection, chat type, conversation and the raw provider
message ID. The raw ID remains separate for provider use. A plan stores its fixed
typed group target and admitted route revision; neither sender nor model can
choose a recipient. Existing unknown-send fences remain fail-closed, and known
provider successes can still reconcile durable receipts after cancellation.

## Audience observation limitation

NapCat v4.18.28's
[official member-list action](https://raw.githubusercontent.com/NapNeko/NapCatQQ/v4.18.28/packages/napcat-onebot/action/group/GetGroupMemberList.ts)
can return the existing cache after starting a `no_cache` refresh. It does not
provide an atomic membership-query/send operation. All group context must be
classified as shareable with the group; it is not a transport for fixed-audience
secrets. A newly joined member may see a message before Runtime observes the join.

Receive bounded membership notices. Observed audience changes and reconnects
pause the old route, cancel unsent work and require operator revalidation with a
new scene when the audience changes. Do not silently union private memories or
resume an old audience. The UI states this observation limitation explicitly;
it cannot claim instantaneous or atomic membership fencing.

## Operator display labels, 2026-10-06

The participant dialog exposes operator-managed display-name changes with an
expected-current-name precondition. Only the label is updated; participant IDs,
QQ links, scene audiences, sessions and memory/state namespaces remain unchanged.
Duplicate names do not merge identities. Existing QQ placeholder labels can be
replaced after reading nicknames by the already-linked account and sender IDs.
Global labels do not automatically follow group-specific cards or override manual
aliases. See [the UI and API verification record](../operations/conversation-scope-ui-2026-10-06.md).

## Integration and acceptance

Implement identity/state/memory isolation first, then fixed route persistence,
adapter admission/delivery, operator management and shared settings. Migration
39 is reserved for session state and subject-aware memory; subsequent route
migration numbers are allocated after its integration. All new group routes
start disabled. No real group lookup or send is authorized by fixture checks.

Verify two speakers' identical preferences, corrections and forgetting stay
independent; private sentinels never reach a group prompt; old state and populated
databases remain intact; slow preparation and late results are cancelled;
cross-group IDs, route CAS, notices/reconnects and receipt reconciliation remain
correct. Real operator enablement and two-member QQ phone acceptance are separate
gates. Roll back by disabling routes and cancelling unsent work while preserving
all admissions, receipts and unknown fences. Never restore an old database over
new business facts.

## Confirmed batch registration and large audiences, 2026-10-09

Membership reads remain previews: they persist a bounded observation and display
provider nickname labels, but never create participants or identity links. The
operator may explicitly confirm batch registration through the separate
`group-participant-registrations` endpoint using that preview's observation ID.
The service checks the preview's account, connection revision and 60-second TTL,
reobserves membership, and rejects a changed audience before creating identities.
An atomic repository transaction creates a separate participant for each missing
QQ identity and its immutable account/sender link. Existing mappings, operator
labels and revoked links are retained. The paired owner's known sender maps to
`local` when no link exists. Nicknames supply labels only; duplicate names never
merge participants. Repeated or concurrent confirmations reuse stored links.

The audience bound is raised from 32 to 2,000 non-self accounts across NapCat,
protocol parsers, group routing, shared scenes and discussion validation. Transport
frames remain bounded at 8 MiB. Runtime generation/queue/discussion budgets are
unchanged; the prompt compiler continues trimming optional audience metadata while
retaining the current speaker and memory subjects. The UI displays 50 members per
page, supports QQ/name search, and offers explicit bulk speaking selection.

Registration never creates or enables a group route, grants speaking rights,
restores revoked links, imports private memories, or enables requested voice.
The existing shared-context confirmation and separate route enablement still apply.
Old manual mapping remains available. This additive protocol has no database
migration; existing rows and historical migration checksums remain unchanged.
