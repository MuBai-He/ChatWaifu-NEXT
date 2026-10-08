# Behavior decision model and Jev deployment, 2026-10-07

> 历史发布记录：以下现场验证来自原发布过程；私人路径和关联标识已脱敏。
> 本次 PR 整理的独立验证见 PR 描述，本次未部署、重启或操作线上数据。

The owner requested independent decision model selection and then supplied a TypeSafe
credential and its official documentation. Runtime now offers `behavior_decision`
with explicit chat inheritance, OpenAI-compatible native tool decisions, or TypeSafe
Jev's native Choice judgments. Only the decision role is set to Jev; formal reply
generation and task execution continue using the prior chat model. Credentials stay
in Runtime's role-specific 0600 private secret store and never enter this document.

## Source and checks

Reviewed changes are committed on `mubai/qq-account-permissions`: `d0c3dc3c` introduces
the role, migration and shared model settings; `f81cdcca` adds the native typed adapter.
The main checkout is unchanged and the branch has not been pushed or merged.

Final targeted Python: 188 passed, one Windows-only recovery check skipped on macOS.
Web: 461 passed. Changed Python Ruff, formatting and strict Pyright, Web lint/types,
architecture boundaries, both Web/desktop UI builds and product isolation passed.
Tests exercise native response/source/distribution validation, independent credentials,
live routing, old model preservation, populated schema-46 upgrade/restart, HTTP failures,
bounded retries, timeouts and cancellation of the real pending native request.

Five isolated calls with the supplied key resolved `jev-latest` to `jev-1.13.0` and
completed successfully: private/group invitations selected respond, explicit private/
group silence requests selected wait, and a poke observation voluntarily selected wait.
Those small fixtures took 338–543 ms; they do not predict latency for larger contexts.
An isolated Linux Runtime with the actual character persona separately verified native
Jev invitation/respond and silence/wait, while preserving its demo chat route. Neither
probe performed QQ ingress, task execution, external sends or production database writes.

## Deployment and state

Release: `<server-root>/releases/decision-model-20261007`.
Previous release: `qq-free-chat-20261007`.
Stopped-service backup: `<server-root>/backups/decision-model-20261007`.

A final compatibility release, `decision-model-compat-20261007`, is now active over
that release. Commit `5fbfdb45` makes new clients explicitly request the decision role
with `include_behavior_decision=true`; default listing keeps the four original roles
for older installed clients. API regression passed 36 checks and the full Web 461
checks, lint/types, both builds and isolation passed again. Four overlay source files
and ten served Web files are verified; credentials, every model row and migration
ledger remain byte-identical to the already activated Jev release. A separate stopped-
service backup exists at `backups/decision-model-compat-20261007`. Both member group
routes are again revalidated and restored with their original authority and budgets.
The deployed Web selector is available; existing desktop binaries retain their old
model settings and need a new build to show the new selector.

The server is an explicitly recorded overlay, not a complete Git checkout matching
`f81cdcca`: 15 reviewed source files were applied, 1420 other source files preserved,
and all 10 served Web files verified. The historical server `test_api.py` retains its
pre-existing completion-barrier fixture; local API regression uses the reviewed branch.
This deployment does not update an installed desktop binary.

Migration 47 extends only the model-role table's role constraint. Every old model row,
budget, earlier migration/checksum, existing credential and configuration is preserved.
SQLite integrity and foreign keys passed. Earlier Runtime builds reject schema 47;
rollback requires the stopped-service database backup plus previous source/Web links,
and any subsequent data must be reconciled before restoring that backup.

The existing QQ connection recovered ready. Both existing group audiences/scenes,
speaker links and requested-voice grants were revalidated and restored unchanged.
Member policies were retargeted to current route revisions without changing budgets,
quiet hours or disabled memory grants. Channel policy revision 2 retains free conversation
and QQ account operations. The original owner private-chat grant remains unchanged.
All 13 containers, seven unrelated service PIDs and hostname-verified HTTPS were preserved.

Deployment verification and the actual activation probe are recorded in release
`verification.json`, `jev-activation.json`, `jev-real-preflight.json` and
`model-preflight.json`; these contain no credential values. Source/Web manifests record
the overlay hashes and provenance. Fresh phone acceptance for Jev-selected group/private
replies and voice/poke remains separate from native model and deployment checks. The
owner's earlier free-conversation and real-poke acceptance used the prior decision route.
