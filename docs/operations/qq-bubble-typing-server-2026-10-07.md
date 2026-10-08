# QQ bubble typing cadence, 2026-10-07

> 历史发布记录：以下现场验证来自原发布过程；私人路径和关联标识已脱敏。
> 本次 PR 整理的独立验证见 PR 描述，本次未部署、重启或操作线上数据。

The owner requested natural time between the already accepted sentence bubbles.
Commit `83815dbc` adds a random pause plus the **next** bubble's typing duration to
the shared external-channel planner. Splitting and canonical text are unchanged.
The reviewed isolated branch is `mubai/qq-account-permissions`; main remains
unchanged and this branch has not been pushed or merged.

## Policy and behavior

The existing QQ connection now has revision 4 and presentation policy version 3:

- Estimated typing speed: 8 visible grapheme clusters per second.
- Base pause: 800 ms; additional random pause: 0–600 ms.
- Per-gap ceiling: 8000 ms; cumulative plan ceiling: 16000 ms.
- Existing three-text-bubble cap, 30/60 character bounds, stickers, technical
  bypass and text-default/explicit-voice behavior remain unchanged.

The planner calculates `800 + uniform_integer(0, 600) + round(1000 * next_visible_graphemes / 8)`
before applying the ceilings. Whitespace is excluded and emoji clusters count once.
Optional stickers receive a pause without synthetic typing time. The first bubble
has no added wait. Sampled delays are persisted once, and each successor becomes
due relative to the predecessor's delivered receipt. Restart/retry does not
resample; incoming messages and authority revocation still cancel the unsent tail.

Shared Web settings expose typing speed and random pause alongside the existing
delay bounds. Old policy JSON defaults to 8 characters/second and 600 ms jitter;
explicit old bounds stay effective. The WeChat connection configurations and
bounds were preserved; the common planner uses the new formula there too. No
database migration or installed desktop binary update is included.

## Verification

The primary Python selection produced 308 passes and two failures from a stale
QQ schema-version assertion (46 versus the already deployed 47). Updating the
assertion to 47 then passed the entire supplementary QQ/image/sticker selection:
42 passes. This includes private/group cadence, interruption, receipts, scheduler
recovery and a real SQLite close/reopen with sampled delays preserved. No remaining
failure in the selected tests. Protocol TypeScript: 127 passes; Web: 461 passes.
Changed Python Ruff/format/strict typing, protocol/Web lint/types, architecture
boundaries, Web and desktop UI builds passed. The pre-existing intentional filename
NUL rejection now has a scoped ESLint annotation; its validation is unchanged.

The staged Linux planner retained canonical text and first-bubble immediacy.
Twenty samples per length gave 1922–2378 ms for an eight-character next bubble and
5821–6319 ms for forty characters. This probe performed no QQ sends or production
database writes. Phone acceptance of the new timing remains separate and pending.

## Deployment

Active source/Web: `<server-root>/releases/bubble-typing-20261007`.
Previous release: `decision-model-compat-20261007`.
Stopped-service backup: `<server-root>/backups/bubble-typing-20261007`.

Sixteen reviewed source files overlay the previous release; 1419 unrelated source
files and ten served Web hashes were verified. This is an explicit overlay rather
than a complete Git tree. Historical server architecture/status/ADR documents are
preserved; reviewed updated documents remain in the isolated branch.

Changing QQ presentation increments its connection revision and reconnects the
adapter. The initial immediate audience request received a transient 403 during
that reconnect. Once revision 4 was ready, fresh audience observations revalidated
the two existing groups and restored their original members, scenes, speaker and
requested-voice grants. Both remain `member`, with their original budgets/quiet
hours and disabled memory. Owner private-chat scope and free-chat/account settings
revision 2 remain unchanged. No synthetic ingress or manual external message was sent.

The post-check normalized the serialized backup's migration rows before comparing
them with SQLite tuples. Every model row, schema-47 migration/checksum, private
configuration/credential hash and original row count was preserved; integrity and
foreign-key checks passed. All thirteen containers, seven unrelated service PIDs,
hostname-verified HTTPS and existing WeChat states were preserved. The native Jev
decision completion and four-role legacy/five-role current model listing passed.

Release `cadence-preflight.json`, `verification.json`, `restored-routes.json`,
source/Web manifests and provenance record these checks. A cadence-only rollback
can retain the schema-47 database and current facts: restore the previous source/Web
links and QQ presentation bounds, then reconnect and revalidate the same two groups.
Do not restore the old database over subsequent live messages.
