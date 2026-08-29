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

    assert "exec vite --mode desktop --host 127.0.0.1 --port 5173" in command
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
    assert scripts["build:windows-installer"] == (
        "tauri build --target x86_64-pc-windows-msvc --bundles nsis "
        "--config src-tauri/tauri.installer.conf.json"
    )


def test_windows_installer_uses_the_versioned_installed_resource_layout() -> None:
    base = cast(
        dict[str, object],
        json.loads((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")),
    )
    installer = cast(
        dict[str, object],
        json.loads(
            (ROOT / "apps/desktop/src-tauri/tauri.installer.conf.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    base_bundle = cast(dict[str, object], base["bundle"])
    bundle = cast(dict[str, object], installer["bundle"])
    resources = cast(dict[str, object], bundle["resources"])
    windows = cast(dict[str, object], bundle["windows"])
    nsis = cast(dict[str, object], windows["nsis"])

    assert base_bundle["active"] is False
    assert bundle["active"] is True
    assert bundle["targets"] == ["nsis"]
    assert resources == {
        "../../../dist/windows/runtime-sidecar/": "runtime-sidecar/",
        "binaries/chatwaifu-appcontainer-host-x86_64-pc-windows-msvc.exe": (
            "bin/chatwaifu-appcontainer-host.exe"
        ),
    }
    assert windows["webviewInstallMode"] == {"type": "downloadBootstrapper"}
    assert nsis == {"installMode": "currentUser", "allowDowngrades": False}


def test_windows_installer_build_is_x64_private_asset_safe_and_checksummed() -> None:
    script = (ROOT / "tools/windows/build_installer_x64.ps1").read_text(encoding="utf-8")

    assert '$Target = "x86_64-pc-windows-msvc"' in script
    assert 'if ($PythonPlatform -ne "win-amd64")' in script
    assert "tools/smoke_runtime_sidecar.py" in script
    assert "Assert-X64Pe $HostExecutable" in script
    assert "Assert-X64Pe $RuntimeExecutable" in script
    assert "Assert-X64Pe $HelperExecutable" in script
    assert "$Live2DDestinationTemporarilyOwned" in script
    assert "Move-Item -Path $Live2DDestination -Destination $OriginalLive2DBackup" in script
    assert "Move-Item -Path $OriginalLive2DBackup -Destination $Live2DDestination" in script
    assert '$ChecksumPath = "$FinalInstaller.sha256"' in script
