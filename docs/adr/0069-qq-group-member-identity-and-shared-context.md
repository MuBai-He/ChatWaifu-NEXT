# ADR 0069: QQ group member identity and shared context

- Status: Accepted
- Date: 2026-10-03
- Scope: D3, registered participants, shared memory, member character state,
  fixed external group routes

## Decision

D3 remains disabled until the Runtime operator selects a fixed small group and
links every observed human audience member to a registered participant. A group
scene contains 2–32 registered participants. The speaking grant is a subset of
that audience. QQ nicknames and model output never resolve or grant identities.
The existing paired-owner private route remains independent.

The first group slice accepts only structured `at.qq == self_id` plus nonempty
text from a granted member of an enabled route. It emits one text reply to the
persisted group target, with no tools, voice, images, quotes or proactive group
messages. Ordinary messages do not invoke generation or memory extraction.
Anonymous, unknown, self-account-mismatched, self-sent and mixed-media messages
fail closed. The connection administrator is separate from the conversational
speaker.

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
