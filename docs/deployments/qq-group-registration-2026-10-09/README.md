# QQ group registration deployment — 2026-10-09

Deployed feature commit `2222ad39529956784309e2b09e111a76c9a198ff` to
`/home/mubai/chatwaifu-server/releases/qq-group-registration-20261009-2222ad39`.
Runtime became healthy at 08:32:21 China Standard Time. Final verification is
recorded in [verification.json](verification.json).

Reading a group's members previews identities and nickname labels. The operator
then explicitly confirms batch registration of missing participants and links.
Existing identities and revoked links are preserved. Registration supports 2000
members; the UI searches and pages 50 members. Creation of a disabled group route,
speaking selection, context-sharing confirmation and route enablement remain
separate explicit actions.

The release starts from the previously deployed source and Web trees. It preserves
the live Home Assistant patch, reviewed documentation deltas, production dependency
environment and old immutable Web assets for cached clients. Forty reviewed files
were overlaid; 1390 other source files were preserved. A stale Runtime source CSS
rule was aligned with the existing active Web theme. The server's old three-second
test join guard failed only while waiting for the 2000-member Conversation case;
the already accepted main fifteen-second test guard passed the full application
test file. No production behavior was changed for that test repair.

Validation:

- Linux regression: 439 unique cases passed; the application file rerun passed 53.
- Live registration endpoint: no operator token 401, invalid body 422, nonexistent
  observation 404. The live OpenAPI schema includes the registration endpoint and
  2000-member limit. These probes never observe or register a real group.
- All 13 new Web files match disk and HTTP SHA-256; the settings SPA entry and
  confirmation UI code are served. A fresh browser rendered the connection screen.
- Runtime healthy; QQ and WeChat connections ready; HTTPS verified with hostname
  and system trust roots. Database integrity OK, no foreign key violations,
  migration 47. All 11 control tables and 10 configuration hashes unchanged.
- Both existing group routes remain disabled at their prior revisions. Nginx,
  model workers and stage proxy retain their PIDs; all 13 container IDs are unchanged.
- Local checks before deployment: Python 439, Web 477 and TypeScript protocol 127
  tests, strict Pyright, scoped Ruff and ESLint, formatting, architecture,
  Web/Desktop builds and product artifact isolation.

Private backups and deployment scripts are under
`/home/mubai/chatwaifu-server/backups/qq-group-registration-20261009-2222ad39`.
They include consistent online and stopped database snapshots, configuration,
control-row baselines and source provenance. Rollback switches the source and Web
symlinks and restarts Runtime with the current database; it never overwrites live
state from an older snapshot. Runtime restart also restarts its required HTTPS
socket proxy.

The actual user-confirmed group registration and handset message acceptance remain
pending. No existing route was enabled, no real channel message sent, and no main
merge, Git push or native installer publication performed.
