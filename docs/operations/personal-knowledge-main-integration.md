# Personal information and knowledge main integration

Date: 2026-10-15. Base: `bd6d0f5f02097845690d7bd124f6151adc503573` (PR #81).

This slice integrates the previously owner-tested inbox and knowledge backend with the current
password login, Web hosting and knowledge settings entry. The independent Android client lives
under `apps/info-inbox-android`; its installed owner build is 0.5.4 / version code 15.

## Compatibility decisions

- Keep password bearer sessions, Host/Origin checks, settings navigation and product isolation.
  Add scoped native knowledge viewer cookies alongside them; those cookies cannot read Runtime
  password settings/model configuration or mint another viewer credential.
- Use migration 48 after the existing 47. Runtime SQLite remains canonical; Graphiti/Neo4j is
  optional, separately operated and rebuildable. ADR 0084 avoids the now-occupied ADR 0082/0083.
- Keep the latest main relation-list accessibility and return-to-settings navigation. Restore
  bounded relation correction/history and exact-version evidence for confirmed records.
- Android confirmation, Root archive, exclusions and local reminders remain separate lifecycles.
  The category/multiselect UI changes no stored proposal kinds. Fresh development builds use the
  normal local debug key; owner upgrades require the owner's external signing key.

## Local verification

- Strict Pyright: 0 errors; Ruff format/lint and architecture boundaries passed.
- Runtime: 59 sync/graph/information/password/web boundary tests passed; the additional three-test
  knowledge surface run includes the password/native-cookie coexistence regression.
- Web: 496 unit tests and five Chromium knowledge-map cases passed. Web, desktop UI and standalone
  knowledge builds passed; Web/desktop artifact isolation passed.
- Android: 61 JVM/native SQLite tests, fresh Debug build without an owner key and Lint passed.
  Four collector tests cover high/low-ID migration, forced revision rescans and archive retention.
- Owner handset 0.5.4 upgrade was previously verified against the release APK, with original memories,
  work items, review states and six exclusions preserved. The four sections and scoped multi-select
  were inspected on the physical handset. Screenshots and private database snapshots remain outside Git.

CI runs the full Python shards, Web/product gates, dependency/secret checks, and the new standalone
Android gate. Local focused checks and earlier device/model acceptance do not replace those gates.

## Deployment and merge

PR #82 merged on 2026-10-25 as `6543f283796057710d4ea7147efee8e6d67cdb8d` after all 14
checks passed at `2809f0bc5a55405044e807116f39975f5e263734`. The October 25 alignment and
its deployment/checkout evidence are recorded separately in
[the framework alignment record](framework-alignment-2026-10-25.md).

The original October 15 PR preparation did not switch the server release or update the owner's primary checkout. Existing
production source may contain additional owner overlays; deploy using the existing scoped release
procedure after CI/review, with a consistent database/config/secret backup.

The optional adapter installs with `uv sync --all-packages --extra knowledge --locked`. The dedicated
worker runs `tools/knowledge_worker.py` using operator-provided config/env files. Compatibility and
synthetic flow probes take explicit `--workspace` and `--production-state` paths; they do not infer
or mutate a user's production data directory.

The accepted source was integrated using a squash merge after its checks passed.
Database rollback from schema 48 requires the matching pre-migration backup; switching only the
source back to a schema-47 Runtime is insufficient. No credentials, keystores, messages or handset
snapshots are included in the PR.
