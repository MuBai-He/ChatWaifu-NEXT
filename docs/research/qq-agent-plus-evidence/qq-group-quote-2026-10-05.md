# QQ group quoted-message admission, 2026-10-05

## Observed failure

Authenticated, read-only NapCat history inspection was bounded to the latest
20 messages in the user-selected group. Two human messages at 14:59:42 and
15:00:23 (Asia/Shanghai) had segments `[reply, at, text, text]`, exactly one bot
mention, and a reply target that NapCat identified as the same group's bot
message. Neither input had a durable Runtime turn. Normal `[at, text, text]`
messages immediately before and between them reached completed generation and
recorded delivery receipts.

The deployed `normalize_group_inbound` only accepted `at` and `text`; its final
segment branch returned `None` for every `reply`. A read-only normalizer replay
of the history shapes reproduced both rejections. These inputs never invoked
the model, so this failure is located in adapter admission. It does not establish
a Gemini, model-provider, DNS or outbound delivery defect.

## Bounded fix

Accept one canonical nonzero signed numeric `reply.id`, up to 20 digits, in an
otherwise valid text-plus-bot-mention group event. Keep every existing account,
sender, audience, speaking-grant, enabled-route, size and mixed-media check.

The reply segment is treated as an envelope. It does not fetch provider history
or inject the segment's untrusted `text` metadata. The new user text reaches
Conversation with its existing scope-checked group history. Selecting a specific
quoted body outside that retained history and quote-only triggering are not
implemented by this fix.

No model or prompt change, group-audience expansion, unsolicited live messages,
or replay of previously dropped inputs is part of the repair. Existing ordinary
messages without a mention remain quiet.

## Verification

- Before the production change, all four new valid-reference cases reproduced
  the defect; 71 other parser cases passed.
- After the change, the parser and real Runtime/SQLite/loopback-OneBot integration
  suite passed 103 checks. Quoted mentions reach one model invocation and one
  fixed-group delivery; repeated delivery does not invoke the model again.
- Quoted in-flight inputs also pass route-disable, scene-reset and reconnect
  cancellation checks, including a deliberately uncancelled late model result.
- Broader local QQ adapter, private/group Runtime, group application, repository
  and host-contract checks passed 722 tests. Strict Pyright reported zero errors;
  Ruff, formatting, `git diff --check` and the Web build passed. The Web build's
  existing large-bundle warning remains.
- On Linux, 103 parser and real Runtime integration checks passed using the
  production interpreter plus the existing isolated test dependencies. The
  production environment itself does not include pytest; its dependencies were
  not installed or modified for the checks.
- After deployment, a second read-only history-shape replay accepted both exact
  previously rejected quoted inputs. Their Runtime status remained absent,
  consistent with not replaying old messages. This checks parser compatibility,
  not a fresh live model reply or phone receipt. See [admission evidence](qq-group-quote-2026-10-05/admission.json).

## Deployment

At 15:21:25 Asia/Shanghai, the parser and its two affected test files were
deployed over the current release. A source inventory verified all other files
unchanged. Runtime restarted once; NapCat was not restarted. All model routes,
connection configuration and protected configuration/credential files stayed
unchanged; chat remained Gemini 3.8 Flash High on its existing 8318 endpoint.

Only the two previously enabled groups were restored, using fresh matching
audiences and unchanged scenes, participant links and speaking grants. The
requested five-member group resumed at route revision 4; the original
two-member group resumed at revision 27. SQLite remained at schema 41 with
integrity and foreign-key checks passed. A private server-side database backup
and the previous source release are preserved; no database was restored and no
previously dropped input or verifier-originated channel message was sent.

See [deployment evidence](qq-group-quote-2026-10-05/deployment.json). A new
user-initiated quoted message reaching the model, delivery ledger and user's
phone is still pending. Complete Q02 answer-quality acceptance is unchanged.
