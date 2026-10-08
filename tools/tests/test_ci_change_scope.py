"""Required dependency checks stay present; scope failures must never skip audits."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci.change_scope import changed_paths, scope_for_paths

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("paths", "python", "javascript", "neural"),
    [
        (["apps/web/src/App.tsx", "docs/review.md"], False, False, False),
        (["uv.lock"], True, False, False),
        (["services/runtime/pyproject.toml"], True, False, False),
        (["workers/tts-neural/uv.lock"], True, False, True),
        (["workers/tts-neural/src/worker.py"], False, False, True),
        (["packages/model-worker-sdk-python/src/sdk.py"], False, False, True),
        (["packages/protocol-python/src/events.py"], False, False, True),
        (["apps/web/package.json"], False, True, False),
        (["pnpm-lock.yaml"], False, True, False),
        (["pnpm-workspace.yaml", ".npmrc", "patches/foo.patch"], False, True, False),
        (["requirements/production.txt"], True, False, False),
        (["workers/requirements-test.in"], True, False, False),
        ([".github/workflows/security.yml"], True, True, True),
        (["tools/ci/change_scope.py"], True, True, True),
        ([".github/workflows/ci-python.yml"], False, False, True),
        (["Cargo.lock"], False, False, False),
    ],
)
def test_scopes(paths: list[str], python: bool, javascript: bool, neural: bool) -> None:
    assert scope_for_paths(paths) == {
        "python_dependencies": python,
        "javascript_dependencies": javascript,
        "neural_worker": neural,
    }


def test_rename_delete_unicode_and_stacked_pr_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()

    git("init", "--quiet")
    git("config", "user.email", "ci-test@example.invalid")
    git("config", "user.name", "CI test")
    (tmp_path / "uv.lock").write_text("lock\n", encoding="utf-8")
    git("add", "uv.lock")
    git("commit", "--quiet", "-m", "base")
    base = git("rev-parse", "HEAD")
    git("switch", "--quiet", "-c", "feature")
    git("mv", "uv.lock", "重命名.txt")
    git("commit", "--quiet", "-m", "rename manifest")
    head = git("rev-parse", "HEAD")
    git("switch", "--quiet", "-c", "advanced-base", base)
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    git("add", "package.json")
    git("commit", "--quiet", "-m", "unrelated base advance")
    paths = changed_paths(git("rev-parse", "HEAD"), head)
    assert set(paths) == {"uv.lock", "重命名.txt"}
    assert scope_for_paths(paths)["python_dependencies"]
    assert not scope_for_paths(paths)["javascript_dependencies"]


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
def test_full_events_emit_all_outputs(tmp_path: Path, event: str) -> None:
    output = tmp_path / "output.txt"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools/ci/change_scope.py"), "--event", event],
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"python_dependencies": true' in completed.stdout
    assert set(output.read_text(encoding="utf-8").splitlines()) == {
        "python_dependencies=true",
        "javascript_dependencies=true",
        "neural_worker=true",
    }


@pytest.mark.parametrize("base", ["--output=/tmp/leak", "missing", "0" * 40])
def test_bad_revision_fails_and_emits_no_skip_outputs(tmp_path: Path, base: str) -> None:
    output = tmp_path / "output.txt"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/ci/change_scope.py"),
            "--event",
            "pull_request",
            f"--base={base}",
            "--head=" + "0" * 40,
        ],
        cwd=ROOT,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not output.exists()
