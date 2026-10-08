"""Reject missing, overlapping or different pytest shard collections."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict, cast


class ShardManifest(TypedDict):
    index: int
    count: int
    total_collected: int
    collection_sha256: str
    selected: list[str]


def verify(manifests: Sequence[ShardManifest], count: int) -> int:
    if not manifests or len(manifests) != count:
        raise ValueError("Missing shard manifests")
    if {manifest["index"] for manifest in manifests} != set(range(count)):
        raise ValueError("Missing or duplicate shard indices")
    first = manifests[0]
    selected: list[str] = []
    for manifest in manifests:
        if (
            manifest["count"] != count
            or manifest["total_collected"] != first["total_collected"]
            or manifest["collection_sha256"] != first["collection_sha256"]
            or not manifest["selected"]
        ):
            raise ValueError("Shard collections disagree or contain an empty shard")
        selected.extend(manifest["selected"])
    if len(set(selected)) != len(selected):
        raise ValueError("A test appeared in multiple shards")
    digest = hashlib.sha256("\n".join(sorted(selected)).encode()).hexdigest()
    if len(selected) != first["total_collected"] or digest != first["collection_sha256"]:
        raise ValueError("Shard union does not cover the complete pytest collection")
    return len(selected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    manifests = [
        cast(ShardManifest, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(args.directory.rglob("pytest-shard.json"))
    ]
    total = verify(manifests, args.count)
    print(f"Verified {args.count} disjoint shards cover all {total} collected tests")


if __name__ == "__main__":
    main()
