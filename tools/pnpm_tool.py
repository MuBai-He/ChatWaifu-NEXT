"""Resolve the repository-pinned pnpm without requiring a global install."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNPM_VERSION = "11.19.0"
TOOLING_ROOT = ROOT / ".local" / "tooling"


class PnpmToolError(RuntimeError):
    """Raised when the pinned pnpm executable cannot be prepared."""


def environment_with_pnpm(
    pnpm: Path, environment: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Prepend pnpm's directory so nested package scripts resolve the same binary."""
    resolved = dict(os.environ if environment is None else environment)
    resolved["PATH"] = os.pathsep.join([str(pnpm.parent), resolved.get("PATH", os.defpath)])
    return resolved


def resolve_pnpm(*, install: bool = True) -> Path:
    """Return a validated pnpm executable, installing it project-locally if needed."""
    override = os.environ.get("CHATWAIFU_PNPM")
    if override:
        candidate = Path(override).expanduser()
        _require_version(candidate, source="CHATWAIFU_PNPM")
        return candidate

    local_candidate = _local_pnpm_path()
    if _pnpm_version(local_candidate) == PNPM_VERSION:
        return local_candidate

    if install:
        npm = shutil.which("npm")
        if npm is not None:
            _install_local_pnpm(Path(npm))
            _require_version(local_candidate, source="project-local install")
            return local_candidate

    system_pnpm = shutil.which("pnpm")
    if system_pnpm is not None:
        candidate = Path(system_pnpm)
        _require_version(candidate, source="PATH")
        return candidate

    action = "Install Node.js 22+ (including npm), then retry `make demo`."
    if not install:
        action = "Run with install enabled or install pnpm 11.19.0."
    raise PnpmToolError(f"pnpm {PNPM_VERSION} is unavailable. {action}")


def _local_pnpm_path() -> Path:
    executable = "pnpm.cmd" if os.name == "nt" else "pnpm"
    return TOOLING_ROOT / "node_modules" / ".bin" / executable


def _pnpm_version(candidate: Path) -> str | None:
    if not candidate.is_file():
        return None
    try:
        result = subprocess.run(
            [str(candidate), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _require_version(candidate: Path, *, source: str) -> None:
    actual = _pnpm_version(candidate)
    if actual != PNPM_VERSION:
        rendered = actual if actual is not None else "not executable"
        raise PnpmToolError(f"{source} pnpm must be {PNPM_VERSION}, got {rendered}: {candidate}")


def _install_local_pnpm(npm: Path) -> None:
    TOOLING_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"Preparing project-local pnpm {PNPM_VERSION}...", flush=True)
    try:
        subprocess.run(
            [
                str(npm),
                "install",
                "--prefix",
                str(TOOLING_ROOT),
                "--no-save",
                "--no-package-lock",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                f"pnpm@{PNPM_VERSION}",
            ],
            cwd=ROOT,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PnpmToolError(
            f"Could not install project-local pnpm {PNPM_VERSION} with {npm}"
        ) from error
