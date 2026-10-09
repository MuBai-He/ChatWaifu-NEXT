# QQ member reply labels and defaults — 2026-10-09

Feature commit `282b33b851528674ffa99039ed55f70949b799a5` was published to the
existing server Web at 08:47 CST. The Web release is
`/home/mubai/chatwaifu-server/releases/qq-reply-defaults-20261009-282b33b8`.
See [verification.json](verification.json).

The member checkbox now says “允许 AI 回复该成员（QQ …）”. New-group drafts
default valid linked members to checked. Members linked manually or by confirmed
batch registration also default to checked, while earlier manual opt-outs remain
unchecked. Revoked links and saved existing-group permissions retain their values.
Group creation still requires shared-context confirmation and produces a disabled
route. Enabling it remains a separate explicit action.

Verification passed 480 Web tests, full Web lint, TypeScript checks, formatting,
architecture boundaries, Web/Desktop builds and product artifact isolation. Cases
cover default selections, batch registration preserving an opt-out, revoked links,
existing-group audience refresh and the saved route-creation payload.

All 13 new Web files match SHA-256 on disk and HTTP, and the settings SPA entry
and updated label are served. The release preserves 87 older Web compatibility
files. The Runtime remains healthy with its preceding group-registration source;
all seven service PIDs and all 13 container IDs remain unchanged. No Runtime
restart was needed. The database remains on migration 47 with integrity OK and no
foreign key violations. All 11 control tables and 10 configuration hashes match
the baseline captured immediately before this Web publication. This baseline
includes any user edits made after the earlier 08:32 deployment.

Private records, code and build hashes, and the publication script are under
`/home/mubai/chatwaifu-server/backups/qq-reply-defaults-20261009-282b33b8`.
Rollback switches only the Web pointer to the prior release; no database restore
or service restart is involved. No real group registration, route enablement,
channel send, main merge or native installer publication was performed by this
follow-up.
