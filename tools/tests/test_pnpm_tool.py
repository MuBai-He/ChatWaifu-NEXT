from __future__ import annotations

import json
import os
from pathlib import Path

from tools.pnpm_tool import PNPM_VERSION, ROOT, environment_with_pnpm


def test_pinned_version_matches_package_manager() -> None:
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package_json["packageManager"] == f"pnpm@{PNPM_VERSION}"


def test_nested_package_scripts_resolve_project_pnpm() -> None:
    pnpm = Path("/workspace/.local/tooling/node_modules/.bin/pnpm")

    environment = environment_with_pnpm(pnpm, {"PATH": "/usr/bin", "KEEP_ME": "yes"})

    assert environment["PATH"] == os.pathsep.join([str(pnpm.parent), "/usr/bin"])
    assert environment["KEEP_ME"] == "yes"
