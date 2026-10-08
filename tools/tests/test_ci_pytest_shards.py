"""Prove sharding covers every test once and propagates test/collection failures."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from tools.ci.pytest_shard import partition_nodeids
from tools.ci.verify_pytest_shards import ShardManifest, verify

ROOT = Path(__file__).resolve().parents[2]


def test_partition_is_order_independent_complete_and_disjoint() -> None:
    ids = [f"test_sample.py::test_case[{index}]" for index in range(37)]
    shards = partition_nodeids(ids, 4)
    assert shards == partition_nodeids(list(reversed(ids)), 4)
    assert sorted(node for shard in shards for node in shard) == sorted(ids)
    assert max(map(len, shards)) - min(map(len, shards)) <= 1


@pytest.mark.parametrize("count", [0, -1, 17])
def test_invalid_counts_are_rejected(count: int) -> None:
    with pytest.raises(ValueError):
        partition_nodeids(["test_case"], count)


def test_duplicate_nodeids_are_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        partition_nodeids(["same", "same"], 2)


def _run_sample(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(
        "import pytest\n@pytest.mark.parametrize('n', range(8))\n"
        "def test_case(n):\n    assert n != 7\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tools.ci.pytest_shard", "-q", *args],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_real_pytest_shards_cover_failed_case_once(tmp_path: Path) -> None:
    manifests: list[ShardManifest] = []
    statuses: list[int] = []
    for index in range(4):
        manifest = tmp_path / f"shard-{index}.json"
        completed = _run_sample(
            tmp_path,
            "--ci-shard-count=4",
            f"--ci-shard-index={index}",
            f"--ci-shard-manifest={manifest}",
        )
        statuses.append(completed.returncode)
        manifests.append(cast(ShardManifest, json.loads(manifest.read_text(encoding="utf-8"))))
    assert sorted(statuses) == [0, 0, 0, 1]
    assert verify(manifests, 4) == 8


@pytest.mark.parametrize(
    "args",
    [
        ("--ci-shard-count=4",),
        ("--ci-shard-count=4", "--ci-shard-index=4"),
        ("--ci-shard-count=16", "--ci-shard-index=15"),
        ("--ci-shard-count=0", "--ci-shard-index=0"),
    ],
)
def test_empty_or_invalid_shard_is_not_success(tmp_path: Path, args: tuple[str, ...]) -> None:
    completed = _run_sample(tmp_path, *args)
    assert completed.returncode == 4, completed.stdout + completed.stderr


def test_collection_error_is_not_success(tmp_path: Path) -> None:
    (tmp_path / "test_broken.py").write_text(
        "import missing_ci_fixture_dependency\n", encoding="utf-8"
    )
    completed = _run_sample(tmp_path, "--ci-shard-count=4", "--ci-shard-index=0")
    assert completed.returncode != 0


def _manifests() -> list[ShardManifest]:
    ids = [f"test_case[{index}]" for index in range(8)]
    digest = hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()
    return [
        {
            "index": index,
            "count": 4,
            "total_collected": len(ids),
            "collection_sha256": digest,
            "selected": selected,
        }
        for index, selected in enumerate(partition_nodeids(ids, 4))
    ]


def test_coverage_check_rejects_missing_duplicate_and_mismatched_manifests() -> None:
    manifests = _manifests()
    assert verify(manifests, 4) == 8
    with pytest.raises(ValueError):
        verify(manifests[:-1], 4)
    manifests[0]["selected"] = manifests[1]["selected"]
    with pytest.raises(ValueError, match="multiple"):
        verify(manifests, 4)
    manifests = _manifests()
    manifests[1]["collection_sha256"] = "different"
    with pytest.raises(ValueError, match="disagree"):
        verify(manifests, 4)
    manifests = _manifests()
    manifests[1]["selected"].pop()
    with pytest.raises(ValueError, match="complete"):
        verify(manifests, 4)
