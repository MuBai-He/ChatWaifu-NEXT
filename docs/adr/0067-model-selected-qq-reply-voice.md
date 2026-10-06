# ADR 0067: Model-selected voice for the current QQ reply

- Status: Accepted
- Date: 2026-10-03
- Supersedes: The reply-medium keyword gate in ADRs 0064 and 0066.
- Scope: Paired owner private QQ replies; no proactive sends or group permissions.

## Context

The owner explicitly requested that the character decide whether to use voice
instead of a keyword classifier. A recording requesting speech had also been
misrecognized by STT; guessing words in the authorization layer would conflate
transcription, model reasoning and transport permissions.

The old implementation had three text-dependent gates: QQ's tool allowlist,
query relevance and voice execution authorization. It also required a tool call
whenever a reply-completing voice tool was projected. Removing just one gate
would either leave indirect requests unavailable or force ordinary chat into
audio. Skill descriptions that require particular request wording would retain
the same product restriction even after the executor changed.

## Decision

For an admitted owner-private QQ turn, the host makes only its existing trusted
`channel.voice` capability contextually available. This context is typed,
generation-local and independent of message wording. Router projection still
checks installation, enabled status, schema safety, count and byte budgets.
Contextual priority applies only to trusted builtins and is not an execution
permission. Desktop, proactive and other channel flows receive no new context
or grant. Existing retrieval routing remains unchanged outside this surface.

When voice is the only projected reply capability, use model `tool_choice=auto`.
The model may produce normal text directly or call `send_voice` with the full
reply. The channel instruction prefers text for ordinary chat, honors the
current user's medium preference semantically, and allows the character to
choose voice when suitable without particular keywords. Receiving audio does
not force an audio response. Historical quotes, images and retrieved data are
not new medium instructions. These are model behavior instructions, not a
deterministic guarantee that a stochastic model will follow every preference.

Execution still requires agent origin, a live QQ owner connection, an active
matching session/turn/generation and a committed current input. Manual, other
provider, stale, revoked and unrelated calls fail. The Runtime fixes the
recipient and character voice; the model cannot choose a recipient, path or
voice profile. No permanent permission record is created. This private current
reply satisfies the existing generation-scoped permission boundary without a
separate desktop confirmation; it grants no other external operation.

Preserve at most one voice reply, 2000 text characters, bounded synthesis,
durable delivery and truthful receipts. Recheck live authorization after TTS
and while waiting for delivery. Cancellation prevents stale publication.
Successful voice delivery completes the reply without repeating its full text;
failure retains the existing text fallback. A receipt does not prove handset
playback. Incoming STT retains its original final transcript and no raw audio;
there is no homophone correction or keyword classifier in the voice path.

The isolated QQ ASR worker can opt into bounded beam configuration and a generic
Chinese initial prompt; defaults for other workers remain unchanged. This is
independent of tool permission and does not prove transcription accuracy.

## Acceptance and rollback

Use scripted model decisions to verify the same keyword-free input can yield
either one TEXT or one AUDIO reply, and that a model can choose text without a
forced retry while the tool is visible. Test unavailable function calling,
manual and other-provider denial, owner cancellation, duplicate delivery,
history provenance, TTS failure and receipt failure. Test contextual projection
without relaxing schema, disabled, plugin or budget boundaries.

Then verify the actual deployed model and a fresh owner-initiated QQ message,
including handset receipt/playback. Synthetic model fixtures prove control
flow, not semantic accuracy or real playback. Rollback restores this bounded
source and private worker configuration only; never overwrite business state
accepted after deployment. No database migration is required for this change.
