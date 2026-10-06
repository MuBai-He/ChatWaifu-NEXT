# Messaging sentence pauses, 2026-10-05

## Existing evidence

The user's selected QQ group had nine recent completed replies of 21–29
characters, each delivered as exactly one text part with a provider receipt.
The canonical replies matched their generation output. The connection selected
`instant_message`, preferred 30 characters, soft maximum 60, maximum three parts
and existing durable cadence. This was not single-text selection or a missing
second send: the stored plans contained only one part.

The splitter bypassed all replies below the preferred count before inspecting
sentence boundaries. For larger replies it also merged adjacent short sentences
whenever their combination remained under the soft limit. For example,
`你怎么还在纠结这个呀……！真是拿你没办法。` was one actual delivered part.

## Change and frozen comparison

Preserve complete sentence and line pauses even in short instant-message
replies. Preserve semicolon pauses too. Length-based merging continues only
for weaker clause cuts; the configured part cap still bounds delivery. A
leading/trailing ellipsis or punctuation run remains attached to meaningful
text. Atomic spans and technical/single-text bypass remain in force.

No new prompt, model generation, token budget, private-memory selection, group
grant or message-send tool is introduced. Canonical conversation and Web/desktop
text remain whole. Existing provider-neutral durable parts still own order,
receipts, cadence and cancellation.

The exact nine already-produced replies were frozen before changing production.
With the same saved policy their planned part counts changed from
`[1,1,1,1,1,1,1,1,1]` to `[2,2,1,2,2,2,1,3,2]`. Every candidate reconstructed the
original text exactly. Two complete utterances remain single messages, including
the conditional `就算人家不在意，我也不会跟着你胡闹的啦……`.

| Existing reply | Candidate bubbles |
| --- | --- |
| 你怎么还在纠结这个呀……！真是拿你没办法。 | 你怎么还在纠结这个呀……！ / 真是拿你没办法。 |
| 桌饺……？把饺子直接倒在桌上吃吗，感觉怪怪的…… | 桌饺……？ / 把饺子直接倒在桌上吃吗，感觉怪怪的…… |
| 诶？我没有不尊重大家啦！只是在解释我自己不是而已…… | 诶？ / 我没有不尊重大家啦！ / 只是在解释我自己不是而已…… |

This is a presentation comparison on fixed content, not a new model A/B quality
test or fresh phone delivery. Full Q02 acceptance remains unchanged. The
[comparison artifact](messaging-bubble-rhythm-2026-10-05/comparison.json) retains
canonical text, original delivered parts, candidate parts and cadence.

## Checks and deployment

Before the fix, 11 directed pause/cap cases failed and eight preservation cases
passed. After the fix, 785 related local checks passed, including QQ wire order,
private/group scopes, unchanged local prompts, technical/single-text bypass,
canonical reconstruction, receipt-based scheduling and cancellation of unsent
short-sentence tails. Strict Pyright, Ruff, formatting and the Web build passed;
the existing large-bundle warning remains.

On Linux, 143 directed splitter/style/group-application/QQ-transport checks
passed using the production interpreter plus the existing isolated test
dependencies. No production dependencies were installed.

At 15:53:55 Asia/Shanghai, the splitter and four affected test files were deployed
over the current quote-compatible release. Source inventory confirmed other
files unchanged, including the quote fix and prompt. All model routes, connection
configuration and protected configuration/credential files stayed unchanged;
chat remained Gemini 3.8 Flash High on its current 8318 endpoint. Runtime
restarted once; NapCat and Web assets were not replaced.

The two previously enabled groups were revalidated with fresh matching audiences
and restored with unchanged scenes, links and speaking grants: the requested
five-member route is revision 6 and the original two-member route is revision 29.
SQLite remains at schema 41 with integrity and foreign-key checks passed. The
previous source and a private server-side database backup are preserved; no
database restore or verifier-originated channel message occurred.

The running splitter replayed the frozen nine replies and reproduced the same
candidate part counts and complete canonical text. This still is not a new
phone delivery. See [deployment evidence](messaging-bubble-rhythm-2026-10-05/deployment.json)
and [running-code replay](messaging-bubble-rhythm-2026-10-05/live-replay.json).
Fresh user feedback on phone pacing remains pending.
