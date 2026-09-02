# ADR 0030: WeChat uses a native iLink adapter with QR authorization

- Status: Accepted
- Date: 2026-08-31
- Validation state: Protocol and implementation tests required; real-account and installed-platform acceptance pending

## Context

The desktop product needs a familiar WeChat setup: open settings, scan a QR code, and continue the
same character relationship from WeChat. Asking the user to install another agent host, copy account
identifiers, or paste a bridge token duplicates orchestration and creates a confusing second setup
surface.

Tencent publishes the iLink request and QR-login behavior in its MIT-licensed WeChat channel project.
The required owner-direct-text slice is HTTPS JSON plus bounded long polling. ChatWaifu already owns
an asynchronous Python Runtime and a provider-neutral Channel Gateway, so this transport can remain a
small adapter instead of a second agent process.

## Decision

Implement `weixin_ilink` as a native Runtime-managed adapter under the external-channel domain.
Provider HTTP models and transport state stay inside that adapter. The adapter depends on the generic
gateway for connection authorization, durable admission, conversation execution, delivery leasing,
and ACK.

### QR authorization

The settings client starts a generic channel authorization session and renders the returned opaque QR
content. Runtime polls the provider state and maps it to `pending`, `scanned`,
`verification_required`, `confirmed`, `expired`, `cancelled`, or `failed`. A pairing-code input is
shown only when requested. Confirmation derives the provider bot and owner identities and creates the
owner-only connection automatically; the browser never supplies or receives them as credentials.

Authorization sessions are short-lived, cancellable, and bounded in memory. QR polling accepts only
the fixed Tencent endpoint or an HTTPS redirect host validated against the WeChat allowlist. URLs with
userinfo, unexpected ports, or non-WeChat hosts fail closed.

### Credentials and recovery

Bot token, provider base URL, gateway credential, and message reply context are stored through a
`ChannelCredentialStore` port. Production uses the operating-system credential service: macOS
Keychain, Windows Credential Manager, or Linux Secret Service. Tests use an in-memory implementation.
If no secure backend is available, the adapter reports unavailable; it never falls back to a
plaintext file, browser storage, environment echo, or SQLite secret column.

The non-secret update cursor is a durable adapter checkpoint behind the external-channel repository
port. It advances only after a complete batch reaches durable admission or an explicit policy drop.

Enrollment is ordered so a confirmed provider token is stored securely before the sanitized
connection is committed. Startup reconciles incomplete enrollment and activates only enabled
connections that also have valid credential state.

### Polling, replay, and delivery

Each active connection has one supervised polling task. Stop, disconnect, and Runtime shutdown cancel
and await that task. Network failures degrade only the connection and retry with capped jittered
backoff; they do not fail Runtime startup.

Inbound batches advance their cursor only after every accepted or explicitly policy-dropped message
has reached a durable decision. Stable provider message identity is required. Crash replay converges
through the gateway's existing idempotency instead of creating another generation.

The provider `context_token` remains private transport state associated with the admitted turn.
Outbound text claims the existing durable delivery, uses a client id derived from `delivery_id`, sends
with the matching context, and ACKs only the matching lease. Late responses after cancellation or
deactivation are ignored.

### V1 boundary

V1 supports one scanned owner, direct inbound text, and one final text reply. It excludes groups,
media, voice messages, proactive sends, multiple human principals, and provider typing indicators.
Those require explicit capabilities and policy rather than silent partial support.

## Consequences

The user gets one in-product QR flow and one character Runtime. The project takes responsibility for
tracking the small iLink compatibility surface and secure installed-platform credential behavior.
Real-account smoke tests are required before release claims, and provider changes may require adapter
updates without affecting Conversation, Memory, or Character Kernel code.

## Alternatives

- A separate messaging agent host was rejected because it duplicates setup and agent ownership.
- Browser-side provider calls were rejected because they expose credentials and make cancellation and
  restart recovery unreliable.
- Storing provider tokens in Runtime SQLite or a JSON file was rejected because the existing database
  and backup boundary is not a credential vault.

## References

- [ADR 0029: External Channel Gateway](0029-external-channel-gateway.md)
- [Tencent WeChat channel reference, v2.4.6](https://github.com/Tencent/openclaw-weixin/tree/cef0bfc390393f716903e16d50408118047f87e0)
- [Python keyring](https://pypi.org/project/keyring/)
