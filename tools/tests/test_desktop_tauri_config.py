from __future__ import annotations

import json
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]


def test_tauri_dev_server_binds_the_same_ipv4_url_it_waits_for() -> None:
    config = cast(
        dict[str, object],
        json.loads((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")),
    )
    build = cast(dict[str, object], config["build"])
    command = str(build["beforeDevCommand"])

    assert "dev:desktop -- --host 127.0.0.1 --port 5173" in command
    assert "vite -- --host" not in command
    assert build["devUrl"] == "http://127.0.0.1:5173"
    assert str(build["beforeBuildCommand"]).endswith("@chatwaifu/web build:desktop")
    assert build["frontendDist"] == "../../web/dist/desktop"


def test_windows_release_script_explicitly_targets_x64() -> None:
    package = cast(
        dict[str, object],
        json.loads((ROOT / "apps/desktop/package.json").read_text(encoding="utf-8")),
    )
    scripts = cast(dict[str, object], package["scripts"])

    assert scripts["build:windows-x64"] == (
        "tauri build --no-bundle --target x86_64-pc-windows-msvc"
    )
