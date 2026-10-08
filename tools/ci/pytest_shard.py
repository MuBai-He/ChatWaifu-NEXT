"""Split the complete pytest collection across isolated runners, without xdist."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest


def partition_nodeids(nodeids: Sequence[str], count: int) -> list[list[str]]:
    """Sorted round-robin keeps parametrized/slow neighboring cases on different runners."""
    if not 1 <= count <= 16:
        raise ValueError("CI shard count must be between 1 and 16")
    if len(set(nodeids)) != len(nodeids):
        raise ValueError("Duplicate pytest node IDs would invalidate shard coverage")
    ordered = sorted(nodeids)
    return [ordered[index::count] for index in range(count)]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("ci-shard")
    group.addoption("--ci-shard-index", type=int, default=None)
    group.addoption("--ci-shard-count", type=int, default=None)
    group.addoption("--ci-shard-manifest", type=Path, default=None)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    index: int | None = config.getoption("ci_shard_index")
    count: int | None = config.getoption("ci_shard_count")
    if index is None and count is None:
        return
    if index is None or count is None or not 0 <= index < count:
        raise pytest.UsageError("Provide both --ci-shard-count and a valid zero-based shard index")
    nodeids = [item.nodeid for item in items]
    try:
        partitions = partition_nodeids(nodeids, count)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    selected_ids = set(partitions[index])
    if not selected_ids:
        raise pytest.UsageError("CI shard selected no tests; refusing a successful empty check")
    selected = [item for item in items if item.nodeid in selected_ids]
    deselected = [item for item in items if item.nodeid not in selected_ids]
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
    manifest = {
        "index": index,
        "count": count,
        "total_collected": len(nodeids),
        "collection_sha256": hashlib.sha256("\n".join(sorted(nodeids)).encode()).hexdigest(),
        "selected": sorted(selected_ids),
    }
    manifest_path: Path | None = config.getoption("ci_shard_manifest")
    if manifest_path is not None:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            f"CI shard {index + 1}/{count}: {len(selected)} of {len(nodeids)} collected tests; "
            f"collection SHA-256 {manifest['collection_sha256']}"
        )
