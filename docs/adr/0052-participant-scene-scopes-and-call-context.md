# ADR 0052: Participant and scene scopes with in-call context synchronization

- Status: Accepted
- Date: 2026-09-21
- Scope: conversation identity, durable memory/relationship boundaries, P13.6

## Decision

The Runtime operator remains the authenticated administrator. A conversational
participant is a registered, stable identity, not a login account, a display name,
or a speaker-recognition result. The existing owner is `local`. Management APIs
create participants and immutable shared scenes; a scene contains 2–32 registered
participants. Clients select the speaker and scene when creating a session.

Runtime derives the immutable session audience and `user_scope`:

| Conversation  | Scope              | Allowed durable context                |
| ------------- | ------------------ | -------------------------------------- |
| Owner private | `local`            | Existing owner memory and relationship |
| Other private | `participant:<id>` | That participant's private context     |
| Shared scene  | `scene:<id>`       | That scene's shared context only       |

Memory namespaces remain `character/<character>/user/<scope>` and
`user/<scope>/global`. “Global” means across characters **within the same scope**.
Sharing an audience does not authorize unioning its members' private memories.
Changing an audience creates a new scene, memory boundary, and provider session.
Changing the speaker within a scene creates another session using the same scene
memory, with distinct sender attribution. Names are untrusted display metadata.

Session, memory retrieval/write/proposal decisions/sources, background projections,
spoken memory, relationship/affect state, photo management/recall, history recovery and reset
resolve authority from the persisted session. Clients cannot supply `user_scope`.
Unscoped legacy memory management reads show only owner namespaces; scoped reads
filter before pagination. Mutations reject records/proposals from another scope.
Owner skill permissions and cloud tool definitions are unavailable in nonowner
scopes; this ADR does not grant filesystem/MCP authority to conversational guests.
External channel adapters retain their existing owner-only admission policy.

Migration 32 preserves existing sessions, memories, relationships and reset
watermarks in `local`; reset watermarks become `(character_id, user_scope)` keyed.
Reset cancels active generations in the same character/scope and invalidates
background projections. The reset transaction writes the durable watermark;
a failed deletion does not hide recoverable history. Sibling history before the
watermark is excluded from display/recovery/prompt context, while unrelated scopes
remain intact. Existing session-owned audio deletion semantics remain unchanged.

## P13.6 boundary

The cloud factory constructs the initial context and registers a callback executed
by the bounded media sender **before each input commit**. It rebuilds scoped persona,
relationship/affect, pinned and relevant memory, enabled/exposed skill capabilities,
and confirmed recent history. The most recent finalized user transcript drives
recall for subsequent turns. It does not delay or reinterpret the current audio's
transcription, nor claim to replace context already used by an active response.

A deterministic content hash suppresses unchanged updates. Every changed update
goes through `CloudEgressGateway`: current allow/ask/deny policy, consent/budget and
sensitivity filtering, durable `cloud.egress_receipt`, then provider update. A failed
sync prevents input commit and follows existing fatal media cleanup. Cancellation
is rechecked after sync, so a late update cannot revive a cancelled generation.
Unsupported provider updates require a fresh connection, not silent stale context.

Deletion, supersession and reset synchronously revoke matching retained provider
contexts. Closing the old provider session is necessary because replacing its
instructions cannot erase previously generated dialogue. A scope revision also
fences deletion during connection creation. Reconnection uses the existing P13.7A
fresh-session path; listener cleanup belongs to coordinator termination.

## Client and deployment consequences

Web, desktop pet and desktop settings share the same scope selector. Apply closes
the previous Runtime session/media before remounting client state. Selection is
persisted per Runtime endpoint and synchronized across same-origin windows;
session recovery checks the selected participant/scene. The API remains transport
independent and works with the remote server/thin-client work in the separate
server deployment PR. No Python server or provider credentials are added to the UI.

This is conversation isolation within an operator-controlled Runtime, not a
multi-tenant authentication system. VRChat/QQ group adapters, automatic speaker
identification, per-participant tool grants and redistribution/install packaging
remain separate work. Public provider, microphone, listening and multi-machine
acceptance require the actual provider/server environment.
