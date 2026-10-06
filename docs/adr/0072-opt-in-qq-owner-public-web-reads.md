# ADR 0072: Host opt-in for current QQ owner public-web reads

- Status: Accepted for the temporary QQ/v7 test slice
- Date: 2026-10-04

## Scope and decision

The QQ branch and Q02 v7 branch share `e609345`. Merging their implementations
does not itself authorize QQ tools: the existing private channel allows only
current-reply voice, while public-web capabilities require local confirmation.
The owner requested one temporary merged version for server testing.

Add `public_web.qq_owner_reads_enabled`, defaulting to false. A trusted host may
enable only the built-in `web.search` and `web.read` capabilities for a current
QQ owner private request. This host choice supplies the bounded channel authority
instead of an unavailable desktop confirmation. It grants no persistent generic
permission and cannot be enabled by model output, a skill manifest or a message.

The External Channel domain verifies the live connection, account, owner, direct
binding, session, turn and active generation through its ports. Runtime Skills
owns schemas, timeouts, execution and audit. It may accept this policy only for
built-in read capabilities, and rechecks that authority before invoking the
adapter. Completed or revoked generations cannot reuse it. Local/manual calls,
plugins, other channels, group scenes and proactive work keep their existing
permission rules. No database migration or credential change is required.

Only voice is contextual in capability discovery; public reads remain relevant
to the current request. When a source capability is selected, the QQ response
uses text through the existing retrieval and source-review loop. Ordinary chat
continues to let the model select text or current-reply voice. Combining retrieval
and voice in one reply is outside this temporary slice.

## Validation and rollback

Verify successful read and retained-source follow-up, manual-call rejection,
pre-execution revocation, cancellation without stale delivery, ordinary voice,
and group admission/privacy with the owner-read switch both disabled and enabled.
Provider retrieval, answer fidelity and actual phone delivery are distinct gates.
The v7 persona and Q02 quality verdict remain experimental.

Disable the flag or return to the saved QQ source/Web release. Keep new channel
turns, memories, receipts and the database; do not restore an older database over
new business facts. Proposed source-answer ADRs are numbered 0070 and 0071 in
this integration to distinguish them from QQ ADRs 0064 and 0065. Historical
evidence snapshots retain their original numbering.
