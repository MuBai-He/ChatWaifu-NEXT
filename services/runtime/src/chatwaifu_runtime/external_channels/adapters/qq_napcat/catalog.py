"""Read immutable catalog version evidence; unknown versions remain fenced."""

import json
import re
from pathlib import Path


def catalog_versions(skills_root: Path) -> frozenset[str]:
    path = skills_root / "builtin" / "qq-scene" / "api-catalog.json"
    if not path.is_file() or path.stat().st_size > 2_000_000:
        return frozenset()
    catalog = json.loads(path.read_text(encoding="utf-8"))
    version = catalog["version"]
    reviewed = sorted(action["action"] for action in catalog["actions"] if action["reviewed"])
    versions = {version}
    for compatible in catalog.get("compatible_versions", []):
        if compatible["reviewed_actions"] == reviewed and re.fullmatch(
            r"[a-f0-9]{64}", compatible["sha256"]
        ):
            versions.add(compatible["version"])
    if any(not re.fullmatch(r"\d+\.\d+\.\d+", value) for value in versions):
        raise ValueError("invalid immutable QQ catalog version")
    return frozenset(versions)
