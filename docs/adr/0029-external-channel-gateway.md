# ADR 0029: External messaging uses one Runtime-owned Channel Gateway

- Status: Accepted
- Date: 2026-08-31
- Validation state: Gateway contracts and local conformance pass; real provider acceptance is tracked per adapter

## Context

ChatWaifu needs to receive messages from external products without moving character identity,
relationship state, memory, model routing, or Runtime Skills into each provider integration. WeChat,
QQ, Telegram, and future transports have different login, polling, delivery, and identity objects,
but a message must enter the same character Runtime used by Web and Desktop.

Provider-specific callbacks must not become a second agent loop or write directly to conversation,
memory, or Character Kernel storage. Display names are mutable presentation data and cannot be used
as authorization, deduplication, or memory identity.

## Decision

ChatWaifu owns a provider-neutral `ExternalChannelGateway`. Provider adapters translate transport
objects into versioned channel contracts and retain provider credentials, cursors, reply contexts,
and SDK types behind their adapter boundary.

The gateway owns:

1. provider capability discovery and sanitized connection state;
2. connection and peer authorization before conversation admission;
3. stable peer-to-session binding and idempotency by provider message identity;
4. normal Conversation, Character Kernel, Memory, and model orchestration;
5. immutable source context and memory provenance;
6. durable terminal turn state; and
7. leased delivery followed by explicit provider acknowledgement.

`ChannelBindingRepository` and `ChannelDeliveryRepository` are application ports. SQL remains in
the persistence adapter. Provider adapters do not read conversation tables or call model providers
directly.

### Identity and provenance

Every external turn carries stable provider-scoped account, conversation, and sender keys plus a
Runtime-owned `principal_scope`. Optional conversation and sender labels are untrusted, bounded
presentation hints. The Prompt Compiler includes them only in a separately delimited untrusted
source block. Memory provenance retains the typed source attribution after recent chat history rolls
over.

The protocol reserves direct and group identities separately, but a provider may admit only the
capabilities it advertises. The first product slice remains one explicitly authorized owner in a
direct text conversation. Groups, multiple humans sharing one relationship, media, and proactive
delivery require separate policy and acceptance.

### Durable admission and delivery

`(connection_id, external_message_id)` is unique. A same-content replay returns the existing turn; a
different payload under the same identity is rejected. An adapter that cannot provide a stable
identity fails closed instead of fabricating a random one.

Outbound delivery is independent from text generation. An adapter claims a short server-timed lease,
invokes the provider once with a stable provider idempotency key when available, and acknowledges the
matching lease. Exactly-once visible delivery is not claimed because a process can stop between a
successful provider send and the Runtime ACK.

### Provider-neutral authorization

Interactive login is exposed through a short-lived `ChannelAuthorizationSnapshot`. The browser sees
only authorization method, status, QR content when applicable, expiry, normalized failure, and the
sanitized connection after confirmation. Provider tokens, cookies, cursors, and reply contexts never
cross into browser storage or SQLite.

The generic API uses `/v1/channel-auth-sessions`, `/v1/channel-connections`, and the existing message,
turn, delivery-claim, and delivery-ACK resources. No route is named after a provider. Provider
registration advertises supported authorization methods and messaging capabilities.

### Output and tool policy

An external direct-text turn generates text but no local TTS, PCM stream, avatar cue, or playback ACK.
Runtime Skills remain the only tool authority. V1 external turns disable tools because the channel
does not yet provide ChatWaifu's trusted confirmation surface. Proactive messages and new recipients
remain separate external side effects.

## Consequences

Web, Desktop, and external channels share one persona, relationship, memory, and model path. Adding a
provider means implementing an adapter and registration rather than adding transport conditionals to
ConversationService. Durable binding, replay, cancellation, and delivery state add storage and
lifecycle work, but make failures observable and recoverable.

Provider adapters remain responsible for compatibility with their transport. Each adapter requires
its own credential, cancellation, restart, replay, and real-account acceptance record.

## Alternatives

- A separate bot persona per provider was rejected because it fragments character and memory truth.
- Provider conditionals in ConversationService were rejected because they couple transport churn to
  character orchestration.
- MCP as inbound messaging was rejected because capability invocation is not authenticated message
  admission, peer binding, or delivery lifecycle.

## References

- [External channel architecture](../architecture/external-channels.md)
- [ADR 0003: Domain event envelope](0003-domain-event-envelope.md)
- [ADR 0004: Generation identity controls cancellation](0004-generation-id-cancellation.md)
- [ADR 0016: Runtime-persisted model routing and secrets](0016-role-scoped-model-routing-and-secrets.md)
- [ADR 0022: Conversation composition and persistence ports](0022-conversation-composition-and-persistence-ports.md)
- [ADR 0024: Durable event cursor and session recovery](0024-durable-event-cursor-and-session-recovery.md)
