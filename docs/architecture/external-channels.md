# External Channel Gateway

## Responsibility

The External Channel Gateway admits messages from messaging transports into the same ChatWaifu
conversation path used by Web and Desktop. It is not another agent, prompt stack, or memory store.

```text
External messaging product
  -> provider adapter: authorization, polling/webhook, provider delivery
  -> External Channel Gateway: policy, binding, idempotency, durable delivery
  -> Conversation / Character Kernel / Memory / LLM
  -> provider adapter: claim, send, ACK
```

Only the outside adapter changes when another provider is added.

## Ownership boundaries

| Concern                                                        | Owner                                  |
| -------------------------------------------------------------- | -------------------------------------- |
| Provider login, token, cursor, reply context, HTTP/SDK objects | Provider adapter                       |
| Authorization-session lifecycle and adapter supervision        | Channel Management Service             |
| Connection and owner policy                                    | External Channel Gateway               |
| Peer-to-session binding and inbound idempotency                | Channel repositories                   |
| Persona, relationship, affect, memory, prompt budget           | Character Kernel and Memory            |
| LLM/tool choice, confirmation, cancellation                    | Agent orchestration and Runtime Skills |
| Text generation lineage and Runtime events                     | Conversation domain                    |
| Provider delivery lease, ACK, retry visibility                 | Channel repositories                   |
| Source-aware prompt projection                                 | Prompt Compiler                        |
| Source on extracted memory                                     | Memory provenance policy               |

Provider secrets and raw transport payloads do not cross into conversation, memory, character, or
frontend state.

## Contracts

The Python protocol package owns the JSON Schemas and generated TypeScript types.

### Provider registration

`ChannelProviderRegistration` declares a stable provider id and observable capabilities:

```text
authorization_methods
chat_types
inbound_message_kinds
outbound_message_kinds
supports_typing
supports_partial_replies
supports_delivery_ack
supports_cancellation
supports_proactive_messages
max_text_chars
```

Capabilities are enforcement facts, not presentation promises. Application services resolve a
registration and reject unsupported operations without adding provider branches to Conversation.

### Authorization session

`ChannelAuthorizationStartRequest` selects a provider, authorization method, character, and Runtime
principal scope. It contains no provider credential or account identity.

`ChannelAuthorizationSnapshot` exposes only:

```text
auth_session_id
provider_id
method
status                      # pending/scanned/verification_required/confirmed/expired/cancelled/failed
qr_code_content?
verification_required
connection?                 # sanitized, only after confirmation
error?
expires_at / timestamps
poll_after_ms?
```

Provider login tokens, cookies, and reply contexts remain in the adapter credential store. The
non-secret sync cursor remains in a durable adapter checkpoint behind the repository port. An
authorization session is short-lived and cancellable. A confirmed session creates its connection
from provider-derived stable identities; the browser does not type account or sender ids.

### Connection

`ChannelConnectionConfiguration` stores non-secret provider, character, principal, account, and owner
peer identities plus enabled state. V1 requires exactly one owner peer. Provider, account, character,
and principal form immutable route identity; changing them means creating a new connection.

`ChannelConnectionSnapshot` adds revision, sanitized health, capabilities, and timestamps. Deleting a
managed connection first stops and awaits its adapter task, removes secure credential state, and then
soft-deletes the configuration.

### Inbound message

`ChannelInboundTextMessage` contains stable, provider-scoped identity and bounded content:

```text
connection_id
account_key?
external_message_id
conversation_key
sender_key
principal_scope
chat_type                    # V1: direct
kind                         # V1: text
text
received_at
reply_to_external_message_id?
conversation_label?          # untrusted display hint
sender_display_name?         # untrusted display hint
```

The gateway checks authoritative connection identity and peer policy. Display labels never authorize,
deduplicate, select memory scope, or become prompt instructions.

### Turn and delivery

`ChannelTurnReceipt` binds a durable channel turn to Runtime session, turn, and generation ids.
`ChannelTurnSnapshot` reports current state and the final reply. Reads may wait at most 30 seconds and
never start a second generation.

Before provider send, the adapter uses `ChannelDeliveryClaimRequest` to acquire a short lease.
`ChannelDeliveryAcknowledgement` records the matching lease's delivered, failed, or cancelled result.
An expired or failed lease can be reclaimed; concurrent leases cannot both own the delivery.

## Runtime API

All routes are provider-neutral:

```text
GET    /v1/channel-providers
POST   /v1/channel-auth-sessions
GET    /v1/channel-auth-sessions/{auth_session_id}?wait_seconds=0..30
POST   /v1/channel-auth-sessions/{auth_session_id}/verification
DELETE /v1/channel-auth-sessions/{auth_session_id}

GET    /v1/channel-connections
GET    /v1/channel-connections/{connection_id}
PUT    /v1/channel-connections/{connection_id}
DELETE /v1/channel-connections/{connection_id}
POST   /v1/channel-connections/{connection_id}/test

POST   /v1/channel-connections/{connection_id}/messages
GET    /v1/channel-connections/{connection_id}/messages/{channel_turn_id}?wait_seconds=0..30
POST   /v1/channel-connections/{connection_id}/messages/{channel_turn_id}/interrupt
POST   /v1/channel-connections/{connection_id}/deliveries/{delivery_id}/claim
POST   /v1/channel-connections/{connection_id}/deliveries/{delivery_id}/ack
```

Provider-specific configuration is validated inside the adapter. Settings navigation and form layout
come from the shared settings registry, not provider conditionals in the page shell.

## Lifecycle and failure boundaries

`ExternalChannelService` owns generic gateway state. `ChannelManagementService` supervises managed
adapters and authorization sessions.

Startup order is:

1. start and reconcile durable gateway turns;
2. initialize the secure credential backend;
3. restore enabled managed connections with valid credential state; and
4. start one adapter task per active connection.

Shutdown reverses the order. Authorization, polling, and delivery tasks are cancelled and awaited.
One provider's network failure marks only its connection degraded and retries with capped jittered
backoff; it does not fail Runtime startup. Missing or invalid secure credentials fail that connection
closed.

No arbitrary sleep is used for correctness. Waits are provider long polls, condition/event waits, or
cancellable bounded backoff.

## Admission and replay

Admission is durable and idempotent:

1. load the enabled connection and enforce capabilities and owner policy;
2. require a stable provider message id;
3. find or create the peer/session binding;
4. insert `(connection_id, external_message_id)` and content hash;
5. allocate Runtime lineage; and
6. return the existing receipt for a same-content replay.

A conflicting replay is rejected. A provider adapter never substitutes a random identity when stable
transport identity is missing.

```text
turn: accepted -> processing -> completed
        |             |            |
        +------> cancelling -> cancelled
                      +------> failed / timed_out

delivery: pending -> sending (leased) -> delivered
                         |
                         +-> failed / cancelled
                         +-> lease expiry -> sending (new lease)
```

Provider sync cursors advance only after all messages in the batch have reached a durable accept or an
explicit policy drop. A crash before cursor commit replays the batch and converges through gateway
idempotency.

## Source and memory awareness

Every admitted turn persists `ConversationSourceContext` with stable provider, connection, account,
principal, chat type, conversation, sender, and receive time. Optional labels remain untrusted.

The Prompt Compiler emits a separate bounded block:

```text
[UNTRUSTED CHANNEL CONTEXT]
{"provider_id":"weixin_ilink","chat_type":"direct", ...}
```

Memory extraction stores `MemoryChannelAttribution` on provenance. A later Desktop turn can therefore
recall that a fact was learned through WeChat without treating the mutable contact label as identity.
Reset, correction, export, and forgetting include this provenance.

The protocol reserves separate group and member keys, but V1 still rejects groups. Adding groups
requires first-class human principals, per-member/group relationship and memory policy, privacy tests,
and a new acceptance record.

## Native WeChat iLink adapter

`weixin_ilink` implements owner-direct-text through the generic boundary:

1. obtain and poll a QR authorization session;
2. store confirmed bot credential state in the operating-system credential service;
3. long-poll provider updates in one supervised task;
4. normalize stable direct text messages and durably admit them;
5. retain the private provider reply context for the matching turn;
6. wait for the terminal Runtime result;
7. claim and send the delivery with a stable client id derived from `delivery_id`; and
8. ACK only the matching lease after proven provider success.

The fixed authorization endpoint and provider-returned API base are HTTPS allowlisted. Tokens and QR
identifiers are redacted from logs. No secure credential backend means the adapter is unavailable; no
plaintext fallback is allowed.

V1 excludes groups, media, voice, proactive sends, and typing indicators.

## Output and Runtime Skill policy

External direct text uses:

```text
text = true
tts = false
audio_stream = false
avatar = false
local_playback_ack = false
allow_tools = false
```

This avoids loading voice workers for a remote text reply. Runtime Skills remain the only possible
tool authority, but V1 has no trusted external confirmation surface. Proactive sends and new
recipients remain separate external side effects.

## Acceptance

An adapter is release-ready only after automated and observable checks cover:

- authorization start, scan, verification, confirmation, expiry, and cancellation;
- credential redaction and no plaintext fallback;
- Runtime shutdown during provider long poll;
- cursor commit after durable batch admission and crash replay;
- duplicate inbound convergence and conflicting replay rejection;
- provider reply-context isolation;
- delivery lease contention, timeout, retry, stable provider client id, and ACK;
- restart recovery of enabled connections;
- source-aware prompt and memory provenance; and
- a real owner direct-text round trip on each claimed installed platform.

Groups, media, voice, proactive delivery, and multiple human principals are pending until their own
contracts, privacy policy, and acceptance gates exist.

## References

- [ADR 0029: External Channel Gateway](../adr/0029-external-channel-gateway.md)
- [ADR 0030: Native WeChat iLink adapter](../adr/0030-native-weixin-ilink-adapter.md)
- [ADR 0032: Durable Multipart Channel Delivery](../adr/0032-durable-multipart-channel-delivery.md)
- [ADR 0033: Instant Messaging Bubble Planning and Durable Cadence](../adr/0033-instant-messaging-bubble-planning-and-durable-cadence.md)
