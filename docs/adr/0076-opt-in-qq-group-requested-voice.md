# ADR 0076: Opt-in requested voice for a fixed QQ group

- Status: Accepted
- Date: 2026-10-05
- Scope: A separately opted-in group route; private reply policy is unchanged.
- Supersedes: The text-only restriction of ADRs 0069/0075 only for explicitly opted-in routes and a current voice request.

## Decision

Add `allow_requested_voice`, default false, to an operator-managed route and its
immutable revision. Old clients that omit the update field retain the stored
setting. Route updates still require CAS/audience observation and revoke older
work. Enable only the user-designated existing route with its original audience
and speaker grants. Do not hardcode a QQ group ID in source or change other routes.

Groups normally remain text. A conservative, bounded current-message request
recognizer admits direct requests to send/use voice or read aloud, excluding
quoted/code material, negations and reported requests. It is a group-only
admission boundary, not a replacement for private ADR 0067's semantic model
selection. Ambiguous wording stays text; there is no additional classifier model
call and no voice forced by the recognizer. The model receives only the trusted
existing `channel.voice` tool, with auto choice, and can still choose/refuse text.
History, listening data, pure mentions and old medium requests do not authorize
voice. Repeat recognition against the committed current input at execution.

The same Voice Skill owns bounded WAV synthesis, the current character voice,
one reply and terminal receipt semantics. A group host port fixes the admitted
target and rechecks current audience/route/speaker, generation, transport epoch
and cancellation before synthesis and publication. No private owner allowlist,
memory or recipient selection enters group voice authorization. Web, desktop,
WeChat, proactive, other tools and group inbound audio remain outside this change.

Group voice needs its own atomic plan preparation while the generation is running:
the private voice tool waits for delivery, whereas ordinary group text plans wait
for a completed generation. Prepare one required audio part with fixed group
lineage while retaining the processing turn. Send guards accept that exact audio
reply during processing; normal text still requires a completed source. After the
voice tool returns, finalize canonical spoken text without a second text send.
Definite/unknown audio failure can revise the same aggregate to a truthful text
fallback, retaining the failed audio as optional audit data. Revocation or a new
mention cancels the old work rather than producing a stale fallback.

Migration 43 adds default-off route/version fields and bounded group audio/fallback
guards, preserving old facts and migration checksums. Group content/target remains
immutable except the narrow failed-audio required-to-optional transition needed
for recovery. Use native OneBot `record`, validated Runtime WAV bytes, existing
send fences and receipt reconciliation. Unknown audio sends are never replayed.

## Acceptance and rollback

Test actual Runtime/SQLite/loopback OneBot across two speakers: opt-in/default-off,
current request vs quotation/old listening, one voice reply and ordinary text next,
fallback, TTS cancellation, membership/route/epoch revocation, duplicate/unknown
delivery, manual/stale denial and unchanged private voice. Verify populated 42→43
preservation and malformed/unauthorized SQL part rejection. Real Gemini selection,
server deployment and phone receipt/playback are separate evidence layers.

Rollback disables the route's voice flag through the existing operator update,
cancelling unsent work. Keep the compatible additive schema and new receipts;
never replace current business data with an older database.
