from __future__ import annotations

import json
import tomllib
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

    assert "exec vite --mode desktop --host 127.0.0.1 --port 5173 --strictPort" in command
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
            (ROOT / "apps/desktop/src-tauri/tauri.installer.conf.json").read_text(encoding="utf-8")
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
    assert windows["allowDowngrades"] is False
    assert nsis == {"installMode": "currentUser"}


def test_windows_installer_build_is_x64_private_asset_safe_and_checksummed() -> None:
    script = (ROOT / "tools/windows/build_installer_x64.ps1").read_text(encoding="utf-8")
    bootstrap = (ROOT / "tools/windows/bootstrap_x64.ps1").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert '$Target = "x86_64-pc-windows-msvc"' in script
    assert 'if ($PythonPlatform -ne "win-amd64")' in script
    assert "tools/smoke_runtime_sidecar.py" in script
    assert '"--basetemp", $PytestBaseTemp' in script
    assert "New-Item -ItemType Directory -Path (Split-Path $PytestBaseTemp)" in script
    assert "Assert-X64Pe $HostExecutable" in script
    assert "Assert-X64Pe $RuntimeExecutable" in script
    assert "Assert-X64Pe $HelperExecutable" in script
    assert "$Live2DDestinationTemporarilyOwned" in script
    assert "Move-Item -Path $Live2DDestination -Destination $OriginalLive2DBackup" in script
    assert "Move-Item -Path $OriginalLive2DBackup -Destination $Live2DDestination" in script
    assert "optimize_live2d_texture.ps1" in script
    assert "-TexturePath $Live2DTexture -MaxDimension 4096" in script
    assert '$ChecksumPath = "$FinalInstaller.sha256"' in script
    assert "[System.Diagnostics.FileVersionInfo]::GetVersionInfo" in script
    assert '$VersionInfo.FileDescription -ne "ChatWaifu NEXT Runtime"' in script
    assert "Assert-RuntimeFileIdentity $RuntimeExecutable" in script
    assert '$UvPythonInstallDir = Join-Path $RepoRoot ".local\\toolchains\\uv-python"' in bootstrap
    assert "$env:UV_PYTHON_INSTALL_DIR = $UvPythonInstallDir" in bootstrap
    assert 'Join-Path (Join-Path $UvPythonInstallDir $PythonRequest) "python.exe"' in bootstrap
    assert "Get-PythonPlatform $VenvPython" in bootstrap
    assert "Test-JavaScriptCli $TauriCli" in bootstrap
    assert "Test-JavaScriptCli $ViteCli" in bootstrap
    assert '& $Node $Path "--version"' in bootstrap
    assert '"--frozen-lockfile", "--force"' in bootstrap
    assert 'Join-Path $RepoRoot "node_modules"' in bootstrap
    assert "Remove-Item -LiteralPath $GeneratedRoot -Recurse -Force" in bootstrap
    assert "pnpm did not install runnable Tauri/Vite CLIs" in bootstrap
    assert "$env:UV_PROJECT = $RepoRoot" in bootstrap
    assert "$env:UV_PROJECT_ENVIRONMENT = $ProjectEnvironment" in bootstrap
    assert '"--project", $RepoRoot' in bootstrap
    assert "Remove-Item Env:UV_PROJECT" in bootstrap
    assert "Remove-Item Env:UV_PROJECT_ENVIRONMENT" in bootstrap
    assert '$UvPythonInstallDir = Join-Path $RepoRoot ".local\\toolchains\\uv-python"' in script
    assert 'Join-Path (Join-Path $UvPythonInstallDir $PythonRequest) "python.exe"' in script
    assert "Test-JavaScriptCli $TauriCli" in script
    assert "Test-JavaScriptCli $ViteCli" in script
    assert "Tauri/Vite CLIs are missing or unusable" in script
    assert '"--python", $PythonExe' in script
    assert '"--project", $RepoRoot' in script
    assert "Remove-Item -Path $PackagingEnvironment -Recurse -Force" in script
    assert "Resolve-Path -LiteralPath $Live2DSource" in script
    assert "Test-Path -LiteralPath $Live2DSource -PathType Container" in script
    assert "Copy-DirectoryContents -Source $ResolvedLive2DSource" in script
    assert "Remove-GeneratedInstallerArtifacts -Directory $NsisRoot" in script
    assert "Sort-Object LastWriteTimeUtc" not in script
    assert "$Installers.Count -ne 1" in script
    assert script.count("Remove-Item -LiteralPath $StagedHelper") >= 2
    assert "apps/desktop/src-tauri/binaries/chatwaifu-appcontainer-host-*.exe" in gitignore


def test_frozen_windows_runtime_uses_chatwaifu_file_identity_and_icon() -> None:
    spec_path = ROOT / "packaging/windows/chatwaifu-runtime.spec"
    spec = spec_path.read_text(encoding="utf-8")
    compile(spec, str(spec_path), "exec")
    build_script = (ROOT / "tools/windows/build_installer_x64.ps1").read_text(encoding="utf-8")
    version_path = ROOT / "packaging/windows/runtime-version.txt"
    version_info = version_path.read_text(encoding="utf-8")
    compile(version_info, str(version_path), "eval")
    runtime_package = tomllib.loads(
        (ROOT / "services/runtime/pyproject.toml").read_text(encoding="utf-8")
    )
    runtime_version = str(runtime_package["project"]["version"])
    version_tuple = (*[int(part) for part in runtime_version.split(".")], 0)
    windows_version = f"{runtime_version}.0"
    icon = ROOT / "apps/desktop/src-tauri/icons/icon.ico"

    assert "from PyInstaller.compat import is_win" in spec
    assert 'WINDOWS_RUNTIME_ICON = ROOT / "apps" / "desktop"' in spec
    assert 'WINDOWS_RUNTIME_VERSION = ROOT / "packaging" / "windows"' in spec
    assert "icon=str(WINDOWS_RUNTIME_ICON) if is_win else None" in spec
    assert "version=str(WINDOWS_RUNTIME_VERSION) if is_win else None" in spec
    for distribution in ("httpx", "httpcore", "keyring"):
        assert f'    "{distribution}",' in spec
    assert 'hiddenimports = collect_submodules("keyring.backends")' in spec
    assert icon.read_bytes().startswith(b"\x00\x00\x01\x00")
    assert f"filevers={version_tuple}" in version_info
    assert f"prodvers={version_tuple}" in version_info
    assert f"StringStruct(u'FileVersion', u'{windows_version}')" in version_info
    assert f"StringStruct(u'ProductVersion', u'{windows_version}')" in version_info
    assert f'$VersionInfo.FileVersion -ne "{windows_version}"' in build_script
    assert f'$VersionInfo.ProductVersion -ne "{windows_version}"' in build_script
    assert "StringStruct(u'CompanyName', u'ChatWaifu NEXT')" in version_info
    assert "StringStruct(u'FileDescription', u'ChatWaifu NEXT Runtime')" in version_info
    assert "StringStruct(u'ProductName', u'ChatWaifu NEXT Runtime')" in version_info
    assert "StringStruct(u'OriginalFilename', u'chatwaifu-runtime.exe')" in version_info
