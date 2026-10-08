# Settings center Web publication, 2026-10-07

> 历史发布记录：以下现场验证来自原发布过程；私人路径和关联标识已脱敏。
> 本次 PR 整理的独立验证见 PR 描述，本次未部署、重启或操作线上数据。

The owner requested a clearer organization of existing controls and refreshed UI
styling. The shared settings center groups chat, models/voice, capabilities/tasks,
memory, schedule and connections/devices. Keyword search finds Jev, free
conversation and typing cadence. Model-purpose and cross-category drafts survive
navigation within one Runtime identity; identity changes invalidate old drafts.
Each existing save action and permission/version check remains authoritative.
See [settings-center.md](../settings-center.md) for routes and product boundaries.

## Source and acceptance

Implementation: `4d74e6fc`; independent task-loading fix: `f0db15ef`.
Published frontend snapshot: `694c9d8e35ecbd1a8480dca56be4c8a41fd91e6f` on
`mubai/qq-account-permissions`. This branch remains local and has not been merged
into main. The primary checkout remains unchanged.

Web passed 465 unit tests in 66 files, ESLint and both UI builds/type checks.
Changed-file formatting, diff whitespace, architecture boundaries and strict Web
and desktop artifact isolation passed. Seven isolated browser cases passed:

- Web and desktop preview at 1280px and 390px: navigation/search, model purposes,
  draft retention, all categories, bounded layout and no additional media owner.
- Web and desktop real disposable Runtime APIs: task grants, pause/renew/cancel,
  persisted reload, immutable document download and PDF preview.
- Native settings preview: onboarding, controls, layout and absence of chat media
  ownership.

The extra task reload check exposed a capability-discovery waterfall. Persisted
tasks and files now read concurrently with capability pages; both paths retain
Runtime epoch and request revision checks. A stalled-discovery unit regression
proves canceled tasks are visible without waiting for the capability catalog.
The legacy desktop navigation assertion now selects the exact connection entry,
since MCP's accessible description also mentions service connections.

Browser checks use disposable loopback Runtimes and Chromium previews. They do
not establish a rebuilt native installer or new QQ handset acceptance.

## Publication and preservation

Web: [http://<runtime-host>:18780/settings](http://<runtime-host>:18780/settings).
Port 18780 serves Nginx Web files and proxies `/v1/` to the existing Runtime;
the TLS Runtime endpoint at port 18443 is not the static Web root.

Active Web link: `<server-root>/web` points to
`releases/settings-center-20261007/web`. Only this link was changed atomically.
The source link remains `releases/bubble-typing-20261007/source`; Runtime PID
`2399468` was not restarted. The existing installed desktop binary is not updated
by this static publication, although the shared desktop UI source/build passed.

All 13 new files were hashed against the local build before and after serving.
Five previous immutable asset files were retained for already-open clients using
older lazy chunks. `/settings` and its eight category routes each served the exact
new `index.html`. No credentials are embedded in the assets.

Before/after snapshots matched the source path, private credential file hashes,
five model configurations, global channel settings, connection configurations
and revisions, both group routes/policies, running service PIDs and Docker
containers. QQ remained ready and the two enabled groups remained in `member`
mode. Jev routing, owner private-chat scope and the configured typing cadence were
preserved. No synthetic ingress or manual QQ message was sent.

Private release evidence includes `before.json`, `verification.json`,
`deployment-web-manifest.json`, `compatibility-assets.json` and a backup of the
previous Web provenance. Root `WEB-PROVENANCE.json` records the new frontend and
preservation results; Runtime source provenance is unchanged.

The previous Web remains at `releases/bubble-typing-20261007/web`. A static rollback
can restore that Web link and its provenance without restarting the Runtime or
restoring any database/configuration over live activity.
