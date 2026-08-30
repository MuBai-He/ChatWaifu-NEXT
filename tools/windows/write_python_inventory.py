"""Write a reproducible, path-free package inventory for a portable Python tree."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
import sysconfig
from pathlib import Path


def inventory() -> dict[str, object]:
    packages: list[dict[str, str | None]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        packages.append(
            {
                "name": name,
                "version": distribution.version,
                "license": distribution.metadata.get("License"),
                "homepage": distribution.metadata.get("Home-page"),
            }
        )
    packages.sort(key=lambda item: (str(item["name"]).casefold(), str(item["version"])))
    return {
        "schema_version": "1.0",
        "python_version": platform.python_version(),
        "python_platform": sysconfig.get_platform(),
        "implementation": platform.python_implementation(),
        "byteorder": sys.byteorder,
        "packages": packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(inventory(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(arguments.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
