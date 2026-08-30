"""Materialize a revision-pinned Hugging Face model into an offline pack tree."""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import cast

SnapshotDownload = Callable[..., str]


def materialize(
    *, repo_id: str, revision: str, output: Path, required_files: tuple[str, ...]
) -> Path:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError(f"Model destination must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    snapshot_download = cast(
        SnapshotDownload,
        importlib.import_module("huggingface_hub").snapshot_download,
    )
    snapshot_download(
        repo_id=repo_id,
        revision=revision,
        local_dir=output,
        repo_type="model",
    )
    shutil.rmtree(output / ".cache", ignore_errors=True)
    missing = [relative for relative in required_files if not (output / relative).is_file()]
    if missing:
        raise RuntimeError(f"Materialized model is missing required files: {missing}")
    (output / "CHATWAIFU_MODEL_SOURCE.json").write_text(
        json.dumps(
            {"schema_version": "1.0", "repo_id": repo_id, "revision": revision},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--required-file", action="append", default=[])
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    output = materialize(
        repo_id=arguments.repo_id,
        revision=arguments.revision,
        output=arguments.output,
        required_files=tuple(arguments.required_file),
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
