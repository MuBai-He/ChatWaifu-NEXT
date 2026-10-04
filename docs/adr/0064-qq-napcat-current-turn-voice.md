# ADR 0064: Native QQ transport with current-turn voice authorization

- Status: Accepted
- Date: 2026-10-03
- Reply-medium policy: Superseded by [ADR 0067](0067-model-selected-qq-reply-voice.md), which permits model-selected voice for the current admitted owner-private reply.
- Validation state: Automated checks, isolated Linux deployment, QQ login, owner pairing, real text/voice/text playback and Runtime restart are verified. Additional fault and access-boundary acceptance remains scoped in [the validation record](../qq-napcat-validation-2026-10-03.md).

## Context

The owner wants the same character, relationship and memory through an ordinary
QQ account, while another workstream improves search and answer quality. QQ
transport must not introduce a separate agent or bypass the existing channel
delivery lifecycle. Ordinary replies should prefer text. Voice is an optional
model-selected action under ADR 0067, using the current owner's medium preference
and conversation context; it is not attached to every reply.

NapCat provides a Linux/Docker NTQQ bridge and a bidirectional OneBot 11
WebSocket server. Transport `echo` correlates a request and its response; it does
not make a send idempotent or establish that a human played a voice message.

## Decision

Register `qq_napcat` as a native Runtime-managed adapter. Provider login,
WebSocket authentication, bounded transport events, account discovery and send
receipts stay within that adapter. Messages enter the existing external gateway,
Conversation, Character Kernel, Memory and model path. Provider-specific logic
does not move into those domains. Development and tests use an isolated checkout
and state directory to avoid consuming one account from two Runtime instances.

### Pairing and admission

The owner first logs the character's account into NapCat. The authenticated CW2
settings client submits a WebSocket endpoint, a strong access token and the local
character id to a short-lived pairing resource. Runtime obtains the logged-in
account from NapCat and returns a one-time command. The owner sends the exact
`CW2 CODE` command in private chat to that account. Ordinary first-speaker
messages cannot establish an owner. Browser-supplied sender ids are not a pairing
authority.

Credentials go through the Runtime channel credential store; settings do not
persist them in browser storage. Pairing is cancellable and expires. A confirmed
pairing has one configured owner and a durable connection. This slice admits
owner private text only; groups, other senders, self messages and inbound media
are rejected rather than partially converted into conversation text.

Plain `ws://` is allowed only for loopback, including an SSH tunnel established
on the machine running Runtime. A remote endpoint requires trusted TLS via
`wss://` and OneBot bearer authentication. Tokens are not URL query parameters.
NapCat's OneBot and administrative ports bind to host loopback in the deployment
template. QQ state and NapCat configuration use durable mounts. Image selection
requires an explicit reviewed version or digest, with no floating image default.

### Voice as a Runtime Skill

Expose `send_voice(text)` through Runtime Skill `channel.voice`. The model
provides only the actual spoken text. Runtime resolves the current character's
voice, current conversation and fixed configured owner. The tool cannot select
an arbitrary recipient, file path, service endpoint or voice identity.

QQ's external-tool policy makes only `channel.voice` contextually available for
an admitted owner-private reply. ADR 0067 supersedes this ADR's original keyword
gate: the model chooses text or the optional voice tool with `tool_choice=auto`.
The authorization is bound to the active session, turn and generation, admitted
channel turn, enabled connection and configured owner. Re-check it at execution
and before publication. There is no permanent permission grant or persistent
"always use voice" mode. An instruction found in quoted history, remembered
context or a previous turn cannot establish a new reply permission or replace the
current user's medium preference.

TTS writes a bounded Runtime-owned WAV asset, at most 120 seconds and 8 MiB, using
the existing TTS adapter and character voice configuration. The tool creates an
audio-only durable delivery plan and awaits its terminal result. The spoken text
is canonical for the assistant response; a successful tool result must not be
followed by another generated text reply that repeats it. Failed voice delivery
revises the same durable plan to version 2 with one text fallback, preserving the
failed audio part and its diagnostic error as optional. A persisted plan-created
event wakes scheduling; the fallback text is also the canonical assistant text.
The fallback carries truthful status, including a distinct
"send result unconfirmed" explanation where appropriate. Cancellation propagates
to synthesis and unpublished delivery parts; old-generation voice cannot be
published by a newly admitted generation.

The NapCat adapter sends bounded content as `record` with `base64://` file data.
Runtime and NapCat may live on different machines or filesystems. No Runtime path
is handed to a remote container, and no public audio URL or extra file-sharing
service is required for this slice. NapCat performs its QQ audio conversion.
Its published format support does not substitute for real phone playback tests.

### Durable send fence and acknowledgement

Use the existing delivery plan, lease and scheduler acknowledgement contracts.
Before a provider send, persist a nonsecret send fence keyed by the delivery
part's stable provider client id. Persist the provider message id after a
successful OneBot receipt and before acknowledging the scheduler. Recovery can
reuse that confirmed receipt without sending the same part again.

If the request may have reached QQ but no confirmed receipt was durably saved,
retain an unknown result and fail closed without automatically resending the
part. Reusing OneBot `echo` cannot recover an idempotency guarantee. This deliberately
favors avoiding duplicate bubbles over silently retrying uncertain sends. A
known rejection remains distinct from an unknown outcome. A provider receipt
establishes provider acceptance, not human receipt, playback or understanding.

## Consequences and exclusions

QQ shares the existing character path and can follow future answer improvements
after integration. This ADR does not enable every Runtime Skill on an external
channel. Read-only search exposure needs its own compatible policy and acceptance;
successful QQ chat does not establish QQ search integration.

V1 excludes group conversations, additional participants, incoming images/audio,
proactive messages, recipient selection and permanent voice mode. Disable and
disconnect stop the supervised adapter; disconnect also removes its secure
credentials. Shutdown cancels and awaits transport and delivery tasks. Reconnect
restores connection health and pending state without replaying unknown sends.

Real-account acceptance must cover owner binding, denied senders/media/groups,
text replay, reconnect, Runtime restart, explicit voice, ordinary text afterwards,
voice failure, cancellation, phone playback and a send/receipt crash window.
See [QQ setup and verification](../qq-napcat-setup.md).

## References

- [ADR 0029: External Channel Gateway](0029-external-channel-gateway.md)
- [ADR 0032: Durable Multipart Channel Delivery](0032-durable-multipart-channel-delivery.md)
- [ADR 0056: Headless Channel Credential Vault](0056-headless-channel-credential-vault.md)
- [NapCat-Docker repository](https://github.com/NapNeko/NapCat-Docker)
- [NapCat WebSocket and WebUI configuration](https://doc.napneko.icu/config/basic)
- [NapCat message formats](https://doc.napneko.icu/develop/msg)
- [NapCat audio/file handling](https://doc.napneko.icu/develop/file)
