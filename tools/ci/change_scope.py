"""Choose expensive optional checks without suppressing required status jobs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

SCOPE_KEYS: tuple[str, ...] = (
    "python_dependencies",
    "javascript_dependencies",
    "neural_worker",
)
CONTROL_PATHS = {
    "tools/ci/change_scope.py",
    "tools/tests/test_ci_change_scope.py",
    ".github/workflows/security.yml",
}


def scope_for_paths(paths: Sequence[str]) -> dict[str, bool]:
    """Renames must supply both paths, so removing a manifest also triggers its audit."""
    result = dict.fromkeys(SCOPE_KEYS, False)
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        name = path.name
        if raw_path in CONTROL_PATHS:
            return dict.fromkeys(SCOPE_KEYS, True)
        if (
            name
            in {"pyproject.toml", "uv.lock", "setup.py", "setup.cfg", "Pipfile", "Pipfile.lock"}
            or (name.startswith("requirements") and path.suffix in {".txt", ".in"})
            or raw_path.startswith("requirements/")
        ):
            result["python_dependencies"] = True
        if name in {
            "package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "package-lock.json",
            "npm-shrinkwrap.json",
            "yarn.lock",
            ".npmrc",
        } or raw_path.startswith("patches/"):
            result["javascript_dependencies"] = True
        if raw_path == ".github/workflows/ci-python.yml" or raw_path.startswith(
            (
                "workers/tts-neural/",
                "packages/model-worker-sdk-python/",
                "packages/protocol-python/",
            )
        ):
            result["neural_worker"] = True
    return result


def changed_paths(base: str, head: str) -> list[str]:
    """Use exact event SHAs, with no rename folding or API pagination/path limits."""
    if not all(re.fullmatch(r"[0-9a-fA-F]{40}", revision) for revision in (base, head)):
        raise ValueError("PR base and head must be full Git commit SHAs")
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", "--"],
        check=True,
        capture_output=True,
    )
    return [path.decode("utf-8") for path in completed.stdout.split(b"\0") if path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch"))
    parser.add_argument("--base", default=os.environ.get("PR_BASE_SHA", ""))
    parser.add_argument("--head", default=os.environ.get("PR_HEAD_SHA", ""))
    args = parser.parse_args()
    # Diff/encoding failures deliberately fail the job. They never mean "no changes".
    scope = (
        scope_for_paths(changed_paths(args.base, args.head))
        if args.event == "pull_request"
        else dict.fromkeys(SCOPE_KEYS, True)
    )
    print(json.dumps(scope, sort_keys=True))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.writelines(f"{key}={str(value).lower()}\n" for key, value in scope.items())
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write("### Optional check scope\n\n")
            stream.writelines(
                f"- {key}: {'run' if value else 'unchanged; covered by periodic full checks'}\n"
                for key, value in scope.items()
            )


if __name__ == "__main__":
    main()
