# Local evaluation artifacts

Keep mutable evaluation state separate from reviewable evidence. Temporary Runtime
and model evaluations may generate `eval_isolated.db` under a research run’s
`isolated_db/` directory. The database and its WAL/SHM sidecars are local state;
they are ignored by Git. Scenario JSON, summaries, raw trial JSONL and console
logs remain eligible for review and archival.

Root-level `.qq-*-probe.py` and `.qq-*-probe-result.json` files are temporary
investigation artifacts. Preserve useful probes in a private snapshot, then move
reproducible regression coverage into the appropriate test suite. The ignore
rules do not hide ordinary Python tests or research results. New disposable
outputs should preferably use `.local/validation/`.

Before splitting worktrees into functional PRs:

1. Capture status with all untracked paths, the worktree HEAD, binary diffs,
   untracked files and file hashes. Store snapshots outside the repository.
2. Compare each change with the current base branch. Record changes already
   integrated or superseded by a later implementation rather than reverting it.
3. Build each complete feature in a separate integration checkout, including its
   contracts, implementation, regression coverage and documentation.
4. Publish only reviewed evidence. Keep original logs and trials intact, identify
   their historical source and distinguish them from current acceptance.
5. Verify original worktree HEADs, status and file hashes after integration.

Adding ignore rules does not delete files, alter tracked fixtures or remove a
worktree’s original changes. Backups and evaluation databases remain local.
