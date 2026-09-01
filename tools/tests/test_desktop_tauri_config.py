from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import cast

import pytest

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


def test_macos_owner_package_embeds_runtime_without_local_model_packs() -> None:
    package = cast(
        dict[str, object],
        json.loads((ROOT / "apps/desktop/package.json").read_text(encoding="utf-8")),
    )
    scripts = cast(dict[str, object], package["scripts"])
    config = cast(
        dict[str, object],
        json.loads(
            (ROOT / "apps/desktop/src-tauri/tauri.macos.conf.json").read_text(encoding="utf-8")
        ),
    )
    bundle = cast(dict[str, object], config["bundle"])
    resources = cast(dict[str, object], bundle["resources"])
    macos = cast(dict[str, object], bundle["macOS"])
    build_script = (ROOT / "tools/macos/build_owner_package_arm64.sh").read_text(encoding="utf-8")
    runtime_spec = (ROOT / "packaging/runtime/chatwaifu-runtime.spec").read_text(encoding="utf-8")

    assert scripts["build:macos-package"] == (
        "tauri build --bundles app,dmg --config src-tauri/tauri.macos.conf.json --no-sign"
    )
    assert bundle["active"] is True
    assert bundle["targets"] == ["app", "dmg"]
    assert resources == {"../../../dist/macos/runtime-sidecar/": "runtime-sidecar/"}
    assert macos["minimumSystemVersion"] == "14.0"
    assert "tools/build_runtime_sidecar.py --platform macos" in build_script
    assert "tools/smoke_runtime_sidecar.py" in build_script
    assert "tools/macos/smoke_packaged_app.py" in build_script
    assert "Contents/Resources/runtime-sidecar/chatwaifu-runtime" in build_script
    assert "*.safetensors" in build_script
    assert "*.sqlite" in build_script
    for package_name in (
        "faster_whisper",
        "mlx",
        "qwen_tts",
        "torch",
        "torchaudio",
        "transformers",
    ):
        assert f'"{package_name}"' in runtime_spec


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
    installer_hooks = (ROOT / "apps/desktop/src-tauri/windows/installer-hooks.nsh").read_text(
        encoding="utf-8"
    )

    assert base_bundle["active"] is False
    assert bundle["active"] is True
    assert bundle["targets"] == ["nsis"]
    assert resources == {
        "../../../dist/windows/runtime-sidecar/": "runtime-sidecar/",
        "../../../build/windows-installer/resources/": "./",
    }
    assert windows["webviewInstallMode"] == {"type": "downloadBootstrapper"}
    assert windows["allowDowngrades"] is False
    assert nsis == {
        "installMode": "currentUser",
        "installerHooks": "./windows/installer-hooks.nsh",
    }
    assert "!macro NSIS_HOOK_POSTUNINSTALL" in installer_hooks
    assert "${If} $UpdateMode <> 1" in installer_hooks
    assert 'DeleteRegKey HKCU "${MANUPRODUCTKEY}"' in installer_hooks
    assert 'DeleteRegKey /ifempty HKCU "${MANUKEY}"' in installer_hooks
    assert "APPDATA" not in installer_hooks
    assert "LOCALAPPDATA" not in installer_hooks
    assert "RmDir" not in installer_hooks


def test_windows_installer_build_is_x64_private_asset_safe_and_checksummed() -> None:
    script = (ROOT / "tools/windows/build_installer_x64.ps1").read_text(encoding="utf-8")
    live2d_transaction = (ROOT / "tools/windows/installer_live2d_transaction.ps1").read_text(
        encoding="utf-8"
    )
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
    assert "Assert-X64PeTree $RuntimeRoot" in script
    assert (
        "$Live2DTransactionRoot = Join-Path $RepoRoot "
        '"build\\windows-installer\\live2d-transaction"'
    ) in script
    assert '. (Join-Path $PSScriptRoot "installer_live2d_transaction.ps1")' in script
    assert "[System.IO.Path]::GetTempPath()" not in script
    assert script.count("Restore-InstallerLive2DTransaction") == 2
    assert "Start-InstallerLive2DTransaction" in script
    assert "Set-InstallerLive2DDestinationOwned" in script
    assert "[System.IO.Directory]::Move($Live2DDestination, $OriginalLive2DBackup)" in script
    assert "Move-Item -LiteralPath $Live2DDestination" not in script
    assert 'OriginalPresent = Join-Path $TransactionRoot "original-was-present"' in (
        live2d_transaction
    )
    assert 'OriginalAbsent = Join-Path $TransactionRoot "original-was-absent"' in (
        live2d_transaction
    )
    assert 'DestinationOwned = Join-Path $TransactionRoot "destination-owned"' in (
        live2d_transaction
    )
    assert 'Restored = Join-Path $TransactionRoot "restored"' in live2d_transaction
    assert "Assert-InstallerLive2DDirectoryIdentity" in live2d_transaction
    assert "authoritative original backup" in live2d_transaction
    assert "before restoration is complete" in live2d_transaction
    assert "Assert-InstallerLive2DTransactionOwnerAvailable" in live2d_transaction
    assert "Refusing concurrent staging" in live2d_transaction
    assert "Get-InstallerLive2DOrdinaryTreeEntries" in live2d_transaction
    assert "[System.IO.FileAttributes]::ReparsePoint" in live2d_transaction
    assert "Refusing Live2D $Purpose containing a reparse point" in live2d_transaction
    assert "Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Live2DSource" in script
    assert "authoritative original backup" in script
    assert "[System.IO.Directory]::Move($PreparationRoot, $TransactionRoot)" in (live2d_transaction)
    assert "Remove-InstallerLive2DTransaction" in live2d_transaction
    assert "build/" in gitignore
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
    assert (
        '$InstallerResourceRoot = Join-Path $RepoRoot "build\\windows-installer\\resources"'
        in script
    )
    assert "$StagedHelper = Join-Path $InstallerResourceRoot" in script
    assert script.count("Remove-Item -LiteralPath $InstallerResourceRoot") >= 2
    assert script.index('"--package", "chatwaifu-appcontainer-host"') < script.index('"clippy",')
    assert "build/" in gitignore


POWERSHELLS = tuple(
    dict.fromkeys(
        executable
        for executable in (shutil.which("pwsh"), shutil.which("powershell"))
        if executable is not None
    )
)
POWERSHELL = POWERSHELLS[0] if POWERSHELLS else None


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize("powershell", POWERSHELLS, ids=lambda value: Path(value).stem)
def test_windows_installer_live2d_transaction_recovers_or_fails_closed(
    tmp_path: Path,
    powershell: str,
) -> None:
    helper = ROOT / "tools/windows/installer_live2d_transaction.ps1"
    test_root = tmp_path / "live2d transaction"
    env = os.environ.copy()
    env.update(
        {
            "CHATWAIFU_LIVE2D_HELPER": str(helper),
            "CHATWAIFU_LIVE2D_TEST_ROOT": str(test_root),
            "CHATWAIFU_LIVE2D_TEST_OWNER_PID": str(os.getpid()),
        }
    )
    command = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. $env:CHATWAIFU_LIVE2D_HELPER
$TestRoot = $env:CHATWAIFU_LIVE2D_TEST_ROOT
New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null

# A fresh checkout has no build/windows-installer parent. Starting the stable
# transaction must create and revalidate that ordinary parent before activation.
$Destination = Join-Path $TestRoot "fresh-parent-destination\vendor\live2d"
$Transaction = Join-Path $TestRoot "fresh-transaction-parent\nested\transaction"
$TransactionParent = Split-Path -Parent $Transaction
if (Test-Path -LiteralPath $TransactionParent) {
    throw "Fresh transaction parent unexpectedly exists before the test."
}
Start-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
if (-not (Test-Path -LiteralPath $Transaction -PathType Container)) {
    throw "Fresh transaction was not activated."
}
Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
if (Test-Path -LiteralPath $Transaction) { throw "Fresh transaction remains." }

# A stop after the atomic original move but before the ownership marker must
# recover from the backup rather than treating an absent destination as normal.
$Destination = Join-Path $TestRoot "before-marker\vendor\live2d"
$Transaction = Join-Path $TestRoot "before-marker\transaction"
New-Item -ItemType Directory -Path (Join-Path $Destination "model") -Force | Out-Null
$Avatar = Join-Path $Destination "model\avatar.model3.json"
[System.IO.File]::WriteAllText($Avatar, "original")
Start-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
Move-Item -LiteralPath $Destination -Destination (Join-Path $Transaction "original")
Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
$Avatar = Join-Path $Destination "model\avatar.model3.json"
if ((Get-Content -LiteralPath $Avatar -Raw) -ne "original") {
    throw "The pre-marker original was not restored."
}
if (Test-Path -LiteralPath $Transaction) { throw "The recovered transaction remains." }

# A stop during owner-overlay copy must discard the partial destination, restore
# the hash-identical original, and remove the completed transaction.
$Destination = Join-Path $TestRoot "owned\vendor\live2d"
$Transaction = Join-Path $TestRoot "owned\transaction"
New-Item -ItemType Directory -Path (Join-Path $Destination "model") -Force | Out-Null
$Avatar = Join-Path $Destination "model\avatar.model3.json"
[System.IO.File]::WriteAllText($Avatar, "original")
Start-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
Move-Item -LiteralPath $Destination -Destination (Join-Path $Transaction "original")
Set-InstallerLive2DDestinationOwned -TransactionRoot $Transaction
New-Item -ItemType Directory -Path (Join-Path $Destination "model") -Force | Out-Null
[System.IO.File]::WriteAllText($Avatar, "partial-overlay")
Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
if ((Get-Content -LiteralPath $Avatar -Raw) -ne "original") {
    throw "The transaction-owned destination was not restored."
}
if (Test-Path -LiteralPath $Transaction) { throw "The recovered transaction remains." }

# A destination that did not exist before staging must become absent again.
$Destination = Join-Path $TestRoot "absent\vendor\live2d"
$Transaction = Join-Path $TestRoot "absent\transaction"
Start-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
Set-InstallerLive2DDestinationOwned -TransactionRoot $Transaction
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $Destination "partial.txt"), "partial")
Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
if (Test-Path -LiteralPath $Destination) { throw "The originally absent destination remains." }
if (Test-Path -LiteralPath $Transaction) { throw "The recovered transaction remains." }

# Losing the authoritative backup after ownership is never reinterpreted as a
# valid local directory. Preserve both the partial destination and transaction.
$Destination = Join-Path $TestRoot "missing-backup\vendor\live2d"
$Transaction = Join-Path $TestRoot "missing-backup\transaction"
New-Item -ItemType Directory -Path (Join-Path $Destination "model") -Force | Out-Null
$Avatar = Join-Path $Destination "model\avatar.model3.json"
[System.IO.File]::WriteAllText($Avatar, "original")
Start-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
Move-Item -LiteralPath $Destination -Destination (Join-Path $Transaction "original")
Set-InstallerLive2DDestinationOwned -TransactionRoot $Transaction
Remove-Item -LiteralPath (Join-Path $Transaction "original") -Recurse -Force
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $Destination "partial.txt"), "partial")
$FailedClosed = $false
try {
    Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
} catch {
    $FailedClosed = $_.Exception.Message.Contains("authoritative original backup")
}
if (-not $FailedClosed) { throw "Missing-backup recovery did not fail closed." }
if (-not (Test-Path -LiteralPath (Join-Path $Destination "partial.txt"))) {
    throw "Fail-closed recovery changed the ambiguous destination."
}
if (-not (Test-Path -LiteralPath $Transaction -PathType Container)) {
    throw "Fail-closed recovery deleted its transaction evidence."
}

# A second live installer process must not reinterpret an active transaction as
# a crash. The pytest parent is a stable, distinct live process for this probe.
$Destination = Join-Path $TestRoot "concurrent\vendor\live2d"
$Transaction = Join-Path $TestRoot "concurrent\transaction"
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $Destination "original.txt"), "original")
Start-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
$OwnerMarker = @(Get-ChildItem -LiteralPath $Transaction -Directory -Filter "owner-*")[0]
$OtherProcessId = [int]$env:CHATWAIFU_LIVE2D_TEST_OWNER_PID
$OtherProcess = [System.Diagnostics.Process]::GetProcessById($OtherProcessId)
try {
    $OtherStartTicks = $OtherProcess.StartTime.ToUniversalTime().Ticks
} finally {
    $OtherProcess.Dispose()
}
Rename-Item -LiteralPath $OwnerMarker.FullName `
    -NewName "owner-$OtherProcessId-$OtherStartTicks"
$ConcurrentFailedClosed = $false
try {
    Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
} catch {
    $ConcurrentFailedClosed = $_.Exception.Message.Contains("Refusing concurrent staging")
}
if (-not $ConcurrentFailedClosed) { throw "A live concurrent owner was not rejected." }
if (-not (Test-Path -LiteralPath (Join-Path $Destination "original.txt"))) {
    throw "Concurrent-owner rejection changed the original destination."
}
$CurrentProcess = [System.Diagnostics.Process]::GetCurrentProcess()
try {
    $CurrentStartTicks = $CurrentProcess.StartTime.ToUniversalTime().Ticks
} finally {
    $CurrentProcess.Dispose()
}
$OwnerMarker = @(Get-ChildItem -LiteralPath $Transaction -Directory -Filter "owner-*")[0]
Rename-Item -LiteralPath $OwnerMarker.FullName -NewName "owner-$PID-$CurrentStartTicks"
Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
if (Test-Path -LiteralPath $Transaction) { throw "The current owner could not finish cleanup." }

# Cleanup removes the owner marker before the immutable original-state marker.
# A crash in that interval must remain finishable under the build script's
# StrictMode setting, even though the owner-marker collection is empty.
$Destination = Join-Path $TestRoot "ownerless-cleanup\vendor\live2d"
$Transaction = Join-Path $TestRoot "ownerless-cleanup\transaction"
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $Destination "original.txt"), "original")
Start-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
New-Item -ItemType Directory -Path (Join-Path $Transaction "restored") | Out-Null
Get-ChildItem -LiteralPath $Transaction -Directory -Filter "owner-*" |
    Remove-Item -Recurse -Force
Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
if (Test-Path -LiteralPath $Transaction) {
    throw "Ownerless interrupted cleanup remained under StrictMode."
}

# Recreating destination after removal must make the atomic restore fail. It
# must not nest restore underneath destination or delete authoritative recovery
# data, and a later retry must remain able to restore the original.
$Destination = Join-Path $TestRoot "restore-race\vendor\live2d"
$Transaction = Join-Path $TestRoot "restore-race\transaction"
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $Destination "original.txt"), "original")
Start-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
[System.IO.Directory]::Move($Destination, (Join-Path $Transaction "original"))
Set-InstallerLive2DDestinationOwned -TransactionRoot $Transaction
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $Destination "partial.txt"), "partial")
$script:RaceDestination = $Destination
$script:OriginalRemoveLive2DTree = ${function:Remove-InstallerLive2DOrdinaryDirectoryTree}
function Remove-InstallerLive2DOrdinaryDirectoryTree {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Purpose
    )
    & $script:OriginalRemoveLive2DTree -Root $Root -Purpose $Purpose
    if ($Purpose -eq "transaction-owned destination tree" -and
        $Root -eq $script:RaceDestination) {
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $Root "racer.txt"), "racer")
    }
}
$RaceFailedClosed = $false
try {
    Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
} catch {
    $RaceFailedClosed = $true
}
Set-Item -Path Function:Remove-InstallerLive2DOrdinaryDirectoryTree `
    -Value $script:OriginalRemoveLive2DTree
if (-not $RaceFailedClosed) { throw "Restore destination race did not fail closed." }
if (-not (Test-Path -LiteralPath (Join-Path $Transaction "original") -PathType Container)) {
    throw "Restore destination race deleted the authoritative original."
}
if (-not (Test-Path -LiteralPath (Join-Path $Transaction "restore") -PathType Container)) {
    throw "Restore destination race lost its verified restore tree."
}
if (Test-Path -LiteralPath (Join-Path $Transaction "restored")) {
    throw "Restore destination race was incorrectly marked complete."
}
if (Test-Path -LiteralPath (Join-Path $Destination "restore")) {
    throw "Restore destination race silently nested the restore tree."
}
if (-not (Test-Path -LiteralPath (Join-Path $Destination "racer.txt") -PathType Leaf)) {
    throw "Restore destination race removed the injected destination."
}
Restore-InstallerLive2DTransaction -TransactionRoot $Transaction -Destination $Destination
if ((Get-Content -LiteralPath (Join-Path $Destination "original.txt") -Raw) -ne "original") {
    throw "Restore destination race could not recover on retry."
}
"LIVE2D_TRANSACTION_TEST_OK"
"""
    completed = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "LIVE2D_TRANSACTION_TEST_OK" in completed.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions require native Windows")
@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize("powershell", POWERSHELLS, ids=lambda value: Path(value).stem)
def test_windows_installer_live2d_transaction_rejects_native_junctions(
    tmp_path: Path,
    powershell: str,
) -> None:
    helper = ROOT / "tools/windows/installer_live2d_transaction.ps1"
    test_root = tmp_path / "live2d junction transaction"
    env = os.environ.copy()
    env.update(
        {
            "CHATWAIFU_LIVE2D_HELPER": str(helper),
            "CHATWAIFU_LIVE2D_TEST_ROOT": str(test_root),
        }
    )
    command = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. $env:CHATWAIFU_LIVE2D_HELPER
$TestRoot = $env:CHATWAIFU_LIVE2D_TEST_ROOT
New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null

function New-TestJunction {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Target
    )
    New-Item -ItemType Junction -Path $Path -Target $Target | Out-Null
    $Item = Get-Item -LiteralPath $Path -Force
    if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) {
        throw "Native junction creation did not produce a reparse point: $Path"
    }
}

function Remove-TestJunction {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0) {
            throw "Refusing test cleanup of a non-junction path: $Path"
        }
        [System.IO.Directory]::Delete($Path)
    }
}

function Assert-ReparseRejected {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [Parameter(Mandatory = $true)][string]$Scenario
    )
    try {
        & $Action
    } catch {
        if ($_.Exception.Message.Contains("reparse point")) {
            return
        }
        throw "${Scenario} failed for the wrong reason: $($_.Exception.Message)"
    }
    throw "${Scenario} accepted a native junction."
}

# The destination root itself cannot be a junction, even when it resolves to a
# seemingly valid Live2D directory.
$ExternalDestination = Join-Path $TestRoot "destination-external"
$DestinationJunction = Join-Path $TestRoot "destination-junction"
$DestinationTransaction = Join-Path $TestRoot "destination-transaction"
New-Item -ItemType Directory -Path $ExternalDestination | Out-Null
[System.IO.File]::WriteAllText((Join-Path $ExternalDestination "sentinel.txt"), "external")
New-TestJunction -Path $DestinationJunction -Target $ExternalDestination
try {
    Assert-ReparseRejected -Scenario "destination root" -Action {
        Start-InstallerLive2DTransaction -TransactionRoot $DestinationTransaction `
            -Destination $DestinationJunction
    }
    if (Test-Path -LiteralPath $DestinationTransaction) {
        throw "Rejected destination created transaction state."
    }
    if ((Get-Content -LiteralPath (Join-Path $ExternalDestination "sentinel.txt") -Raw) -ne
        "external") {
        throw "Rejected destination changed its external target."
    }
} finally {
    Remove-TestJunction -Path $DestinationJunction
}

# A normal leaf below a junction ancestor is equally unsafe: recursive staging
# and cleanup would otherwise escape into the junction target.
$AncestorExternal = Join-Path $TestRoot "ancestor-external"
$AncestorJunction = Join-Path $TestRoot "ancestor-junction"
$AncestorDestination = Join-Path $AncestorJunction "ordinary-live2d"
$AncestorTransaction = Join-Path $TestRoot "ancestor-transaction"
New-Item -ItemType Directory -Path $AncestorExternal | Out-Null
New-Item -ItemType Directory -Path (Join-Path $AncestorExternal "ordinary-live2d") | Out-Null
[System.IO.File]::WriteAllText(
    (Join-Path $AncestorExternal "ordinary-live2d\sentinel.txt"),
    "external"
)
New-TestJunction -Path $AncestorJunction -Target $AncestorExternal
try {
    Assert-ReparseRejected -Scenario "destination ancestor" -Action {
        Start-InstallerLive2DTransaction -TransactionRoot $AncestorTransaction `
            -Destination $AncestorDestination
    }
    if (Test-Path -LiteralPath $AncestorTransaction) {
        throw "Rejected destination ancestor created transaction state."
    }
    if ((Get-Content -LiteralPath (Join-Path $AncestorDestination "sentinel.txt") -Raw) -ne
        "external") {
        throw "Rejected destination ancestor changed its external target."
    }
} finally {
    Remove-TestJunction -Path $AncestorJunction
}

# The stable transaction path itself must not be created through a junction
# ancestor, even when destination is an ordinary local directory.
$TransactionExternal = Join-Path $TestRoot "transaction-ancestor-external"
$TransactionJunction = Join-Path $TestRoot "transaction-ancestor-junction"
$OrdinaryDestination = Join-Path $TestRoot "transaction-ancestor-destination"
New-Item -ItemType Directory -Path $TransactionExternal | Out-Null
New-Item -ItemType Directory -Path $OrdinaryDestination | Out-Null
New-TestJunction -Path $TransactionJunction -Target $TransactionExternal
try {
    $EscapingTransaction = Join-Path $TransactionJunction "nested\transaction"
    Assert-ReparseRejected -Scenario "transaction ancestor" -Action {
        Start-InstallerLive2DTransaction -TransactionRoot $EscapingTransaction `
            -Destination $OrdinaryDestination
    }
    if (Test-Path -LiteralPath (Join-Path $TransactionExternal "nested")) {
        throw "Rejected transaction ancestor created external state."
    }
} finally {
    Remove-TestJunction -Path $TransactionJunction
}

# An input/snapshot tree containing a descendant junction must be rejected
# before Copy-Item can stage any ordinary or external file.
$ExternalSource = Join-Path $TestRoot "source-external"
$Source = Join-Path $TestRoot "source"
$CopyDestination = Join-Path $TestRoot "copy-destination"
New-Item -ItemType Directory -Path $ExternalSource | Out-Null
New-Item -ItemType Directory -Path $Source | Out-Null
New-Item -ItemType Directory -Path $CopyDestination | Out-Null
[System.IO.File]::WriteAllText((Join-Path $ExternalSource "outside.txt"), "outside")
[System.IO.File]::WriteAllText((Join-Path $Source "ordinary.txt"), "ordinary")
$SourceJunction = Join-Path $Source "escape"
New-TestJunction -Path $SourceJunction -Target $ExternalSource
try {
    Assert-ReparseRejected -Scenario "copy source descendant" -Action {
        Copy-InstallerLive2DDirectoryContents -Source $Source -Destination $CopyDestination
    }
    if (@(Get-ChildItem -LiteralPath $CopyDestination -Force).Count -ne 0) {
        throw "Rejected source copied files before failing closed."
    }
    Assert-ReparseRejected -Scenario "inventory descendant" -Action {
        Get-InstallerLive2DFileInventory -Root $Source | Out-Null
    }
} finally {
    Remove-TestJunction -Path $SourceJunction
}

# A junction injected into the authoritative backup leaves both the partial
# destination and transaction evidence untouched. Removing only the test link
# makes the same transaction recoverable again.
$BackupDestination = Join-Path $TestRoot "backup-case\vendor\live2d"
$BackupTransaction = Join-Path $TestRoot "backup-case\transaction"
$BackupExternal = Join-Path $TestRoot "backup-case\external"
New-Item -ItemType Directory -Path $BackupDestination -Force | Out-Null
New-Item -ItemType Directory -Path $BackupExternal -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $BackupDestination "original.txt"), "original")
[System.IO.File]::WriteAllText((Join-Path $BackupExternal "outside.txt"), "outside")
Start-InstallerLive2DTransaction -TransactionRoot $BackupTransaction `
    -Destination $BackupDestination
$Backup = Join-Path $BackupTransaction "original"
Move-Item -LiteralPath $BackupDestination -Destination $Backup
Set-InstallerLive2DDestinationOwned -TransactionRoot $BackupTransaction
New-Item -ItemType Directory -Path $BackupDestination -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $BackupDestination "partial.txt"), "partial")
$BackupJunction = Join-Path $Backup "escape"
New-TestJunction -Path $BackupJunction -Target $BackupExternal
try {
    Assert-ReparseRejected -Scenario "authoritative backup descendant" -Action {
        Restore-InstallerLive2DTransaction -TransactionRoot $BackupTransaction `
            -Destination $BackupDestination
    }
    if (-not (Test-Path -LiteralPath (Join-Path $BackupDestination "partial.txt"))) {
        throw "Rejected backup changed the partial destination."
    }
    if (-not (Test-Path -LiteralPath $BackupTransaction -PathType Container)) {
        throw "Rejected backup deleted transaction evidence."
    }
} finally {
    Remove-TestJunction -Path $BackupJunction
}
Restore-InstallerLive2DTransaction -TransactionRoot $BackupTransaction `
    -Destination $BackupDestination
if ((Get-Content -LiteralPath (Join-Path $BackupDestination "original.txt") -Raw) -ne "original") {
    throw "Backup transaction did not recover after its test junction was removed."
}

# A stale restore tree is also untrusted recovery input and must be inspected
# before the helper removes or replaces it.
$RestoreDestination = Join-Path $TestRoot "restore-case\vendor\live2d"
$RestoreTransaction = Join-Path $TestRoot "restore-case\transaction"
$RestoreExternal = Join-Path $TestRoot "restore-case\external"
New-Item -ItemType Directory -Path $RestoreDestination -Force | Out-Null
New-Item -ItemType Directory -Path $RestoreExternal -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $RestoreDestination "original.txt"), "original")
Start-InstallerLive2DTransaction -TransactionRoot $RestoreTransaction `
    -Destination $RestoreDestination
Move-Item -LiteralPath $RestoreDestination `
    -Destination (Join-Path $RestoreTransaction "original")
Set-InstallerLive2DDestinationOwned -TransactionRoot $RestoreTransaction
New-Item -ItemType Directory -Path $RestoreDestination -Force | Out-Null
[System.IO.File]::WriteAllText((Join-Path $RestoreDestination "partial.txt"), "partial")
$RestoreTree = Join-Path $RestoreTransaction "restore"
New-Item -ItemType Directory -Path $RestoreTree | Out-Null
$RestoreJunction = Join-Path $RestoreTree "escape"
New-TestJunction -Path $RestoreJunction -Target $RestoreExternal
try {
    Assert-ReparseRejected -Scenario "restore descendant" -Action {
        Restore-InstallerLive2DTransaction -TransactionRoot $RestoreTransaction `
            -Destination $RestoreDestination
    }
    if (-not (Test-Path -LiteralPath (Join-Path $RestoreDestination "partial.txt"))) {
        throw "Rejected restore tree changed the partial destination."
    }
} finally {
    Remove-TestJunction -Path $RestoreJunction
}
Remove-InstallerLive2DOrdinaryDirectoryTree -Root $RestoreTree `
    -Purpose "junction-test restore tree"
Restore-InstallerLive2DTransaction -TransactionRoot $RestoreTransaction `
    -Destination $RestoreDestination
if ((Get-Content -LiteralPath (Join-Path $RestoreDestination "original.txt") -Raw) -ne "original") {
    throw "Restore transaction did not recover after its test junction was removed."
}

"LIVE2D_REPARSE_TEST_OK"
"""
    completed = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "LIVE2D_REPARSE_TEST_OK" in completed.stdout


def test_windows_installer_accepts_model_only_or_complete_live2d_vendor_overlay() -> None:
    script = (ROOT / "tools/windows/build_installer_x64.ps1").read_text(encoding="utf-8")

    assert '$DirectModelAvatar = Join-Path $ResolvedLive2DSource "avatar.model3.json"' in script
    assert (
        '$VendorRootAvatar = Join-Path $ResolvedLive2DSource "model\\avatar.model3.json"' in script
    )
    assert '$Live2DSourceLayout = "model"' in script
    assert '$Live2DSourceLayout = "vendor"' in script
    assert "Assert-Live2DVendorInputs -Root $Live2DDestination -BaseOnly" in script
    assert "Assert-Live2DVendorInputs -Root $ResolvedLive2DSource" in script
    assert "Copy-DirectoryContents -Source $OriginalLive2DBackup" in script
    assert '$StagedModel = Join-Path $Live2DDestination "model"' in script
    assert "Copy-DirectoryContents -Source $Live2DSourceSnapshot" in script
    assert "-Destination $StagedModel" in script
    assert "Assert-Live2DVendorInputs -Root $Live2DDestination" in script
    assert 'Join-Path $Root "live2dcubismcore.min.js"' in script
    assert 'Join-Path $Root "chatwaifu-live2d-bridge.js"' in script
    assert 'Join-Path $Root "model\\avatar.model3.json"' in script


def test_windows_installer_validates_every_frozen_runtime_native_file_as_pe_x64() -> None:
    script = (ROOT / "tools/windows/build_installer_x64.ps1").read_text(encoding="utf-8")

    assert "$DosSignature -ne 0x5A4D" in script
    assert "$PeSignature -ne 0x00004550" in script
    assert "([long]$PeOffset + 6) -gt $Stream.Length" in script
    assert "Get-ChildItem -LiteralPath $Root -Recurse -File" in script
    for extension in (".exe", ".dll", ".pyd"):
        assert f'"{extension}"' in script
    assert "foreach ($NativeFile in $NativeFiles)" in script
    assert "Assert-X64Pe $NativeFile.FullName" in script
    assert "Assert-X64PeTree $RuntimeRoot" in script


def test_frozen_windows_runtime_uses_chatwaifu_file_identity_and_icon() -> None:
    spec_path = ROOT / "packaging/runtime/chatwaifu-runtime.spec"
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
