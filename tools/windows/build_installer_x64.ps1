param(
    [string]$Live2DSource = "",
    [switch]$SkipChecks
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This installer build only supports Windows."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$PackagingEnvironment = Join-Path $RepoRoot ".packaging\windows-x64"
$PackagingPython = Join-Path $PackagingEnvironment "Scripts\python.exe"
$PythonRequest = "cpython-3.12.10-windows-x86_64-none"
$UvPythonInstallDir = Join-Path $RepoRoot ".local\toolchains\uv-python"
$Target = "x86_64-pc-windows-msvc"
$RuntimeExecutable = Join-Path $RepoRoot "dist\windows\runtime-sidecar\chatwaifu-runtime.exe"
$HelperExecutable = Join-Path $RepoRoot "target\$Target\release\chatwaifu-appcontainer-host.exe"
$StagedHelper = Join-Path $RepoRoot "apps\desktop\src-tauri\binaries\chatwaifu-appcontainer-host-$Target.exe"
$PytestBaseTemp = Join-Path $RepoRoot "build\pytest\windows-installer"
$Live2DDestination = Join-Path $RepoRoot "apps\web\public\vendor\live2d"
$Live2DStagingRoot = $null
$OriginalLive2DBackup = $null
$Live2DDestinationTemporarilyOwned = $false

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE"
    }
}

function Get-PeMachine {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Stream = [System.IO.File]::OpenRead($Path)
    $Reader = [System.IO.BinaryReader]::new($Stream)
    try {
        $Stream.Position = 0x3c
        $PeOffset = $Reader.ReadInt32()
        $Stream.Position = $PeOffset + 4
        return $Reader.ReadUInt16()
    } finally {
        $Reader.Dispose()
        $Stream.Dispose()
    }
}

function Assert-X64Pe {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path $Path -PathType Leaf)) {
        throw "Expected executable was not produced: $Path"
    }
    $Machine = Get-PeMachine $Path
    if ($Machine -ne 0x8664) {
        throw ("Expected PE machine 0x8664 (x64), received 0x{0:X4}: {1}" -f $Machine, $Path)
    }
}

function Assert-RuntimeFileIdentity {
    param([Parameter(Mandatory = $true)][string]$Path)

    $VersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    if ($VersionInfo.FileDescription -ne "ChatWaifu NEXT Runtime") {
        throw "Unexpected Runtime file description: $($VersionInfo.FileDescription)"
    }
    if ($VersionInfo.ProductName -ne "ChatWaifu NEXT Runtime") {
        throw "Unexpected Runtime product name: $($VersionInfo.ProductName)"
    }
    if ($VersionInfo.CompanyName -ne "ChatWaifu NEXT") {
        throw "Unexpected Runtime company name: $($VersionInfo.CompanyName)"
    }
    if ($VersionInfo.OriginalFilename -ne "chatwaifu-runtime.exe") {
        throw "Unexpected Runtime original filename: $($VersionInfo.OriginalFilename)"
    }
    if ($VersionInfo.FileVersion -ne "0.1.0.0") {
        throw "Unexpected Runtime file version: $($VersionInfo.FileVersion)"
    }
    if ($VersionInfo.ProductVersion -ne "0.1.0.0") {
        throw "Unexpected Runtime product version: $($VersionInfo.ProductVersion)"
    }
}

function Get-PythonPlatform {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path $Path -PathType Leaf)) {
        return ""
    }
    $Platform = & $Path -c "import sysconfig; print(sysconfig.get_platform())" 2>$null
    if ($LASTEXITCODE -ne 0 -or $null -eq $Platform) {
        return ""
    }
    return ([string]$Platform).Trim()
}

if (-not (Test-Path $VenvPython -PathType Leaf)) {
    throw "Missing x64 .venv. Run tools/windows/bootstrap_x64.ps1 first."
}

$PythonPlatform = Get-PythonPlatform $VenvPython
if ($PythonPlatform -ne "win-amd64") {
    throw "Expected a working win-amd64 .venv. Run tools/windows/bootstrap_x64.ps1 -RecreateEnvironment."
}

$Uv = (Get-Command uv -ErrorAction Stop).Source
$Cargo = (Get-Command cargo -ErrorAction Stop).Source
$Rustup = (Get-Command rustup -ErrorAction Stop).Source

Push-Location $RepoRoot
$PreviousUvEnvironment = $env:UV_PROJECT_ENVIRONMENT
$PreviousUvPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
try {
    # The ignored development model must never leak into a base installer. Move it
    # out for the duration of every build, then restore it in finally. An explicit
    # owner-only overlay is first copied to a temporary snapshot so source and
    # destination may safely refer to the same directory.
    if ($Live2DSource -or (Test-Path $Live2DDestination -PathType Container)) {
        $Live2DStagingRoot = Join-Path (
            [System.IO.Path]::GetTempPath()
        ) ("chatwaifu-live2d-" + [System.Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $Live2DStagingRoot -Force | Out-Null
    }
    if ($Live2DSource) {
        $ResolvedLive2DSource = (Resolve-Path $Live2DSource).Path
        $Live2DSourceSnapshot = Join-Path $Live2DStagingRoot "source"
        New-Item -ItemType Directory -Path $Live2DSourceSnapshot -Force | Out-Null
        Copy-Item -Path (Join-Path $ResolvedLive2DSource "*") `
            -Destination $Live2DSourceSnapshot -Recurse -Force
    }
    if (Test-Path $Live2DDestination -PathType Container) {
        $OriginalLive2DBackup = Join-Path $Live2DStagingRoot "original"
        Move-Item -Path $Live2DDestination -Destination $OriginalLive2DBackup
        $Live2DDestinationTemporarilyOwned = $true
    }
    if ($Live2DSource) {
        $Live2DDestinationTemporarilyOwned = $true
        New-Item -ItemType Directory -Path $Live2DDestination -Force | Out-Null
        Copy-Item -Path (Join-Path $Live2DSourceSnapshot "*") `
            -Destination $Live2DDestination -Recurse -Force
        $RequiredAvatar = Join-Path $Live2DDestination "model\avatar.model3.json"
        if (-not (Test-Path $RequiredAvatar -PathType Leaf)) {
            throw "Private Live2D overlay is missing model/avatar.model3.json."
        }
        $Live2DTexture = Join-Path $Live2DDestination "model\texture\texture_00.png"
        if (Test-Path $Live2DTexture -PathType Leaf) {
            & (Join-Path $RepoRoot "tools\windows\optimize_live2d_texture.ps1") `
                -TexturePath $Live2DTexture -MaxDimension 4096
            $PrivateSourceTexture = Join-Path (
                Split-Path $Live2DTexture
            ) "texture_00.source.png"
            Remove-Item -Path $PrivateSourceTexture -Force -ErrorAction SilentlyContinue
        }
        Write-Host "Private Live2D overlay staged locally; it will not be committed or uploaded."
    }

    $env:UV_PYTHON_INSTALL_DIR = $UvPythonInstallDir
    $PythonExe = Join-Path (Join-Path $UvPythonInstallDir $PythonRequest) "python.exe"
    if (-not (Test-Path $PythonExe -PathType Leaf)) {
        throw "The repository-local Windows x64 Python is missing. Run tools/windows/bootstrap_x64.ps1."
    }
    if ((Get-PythonPlatform $PackagingPython) -ne "win-amd64" -and
        (Test-Path $PackagingEnvironment -PathType Container)) {
        Remove-Item -Path $PackagingEnvironment -Recurse -Force
    }
    $env:UV_PROJECT_ENVIRONMENT = $PackagingEnvironment
    Invoke-Checked $Uv @(
        "sync",
        "--python", $PythonExe,
        "--package", "chatwaifu-runtime",
        "--group", "packaging",
        "--no-dev",
        "--locked"
    )
    Invoke-Checked $PackagingPython @("tools/setup_nltk_data.py")
    Invoke-Checked $PackagingPython @("tools/build_runtime_sidecar.py", "--platform", "windows")
    Invoke-Checked $PackagingPython @(
        "tools/smoke_runtime_sidecar.py",
        "--executable", $RuntimeExecutable,
        "--timeout", "180"
    )

    Invoke-Checked $Rustup @("target", "add", $Target)
    if (-not $SkipChecks) {
        if (Test-Path $PytestBaseTemp) {
            Remove-Item -Path $PytestBaseTemp -Recurse -Force
        }
        New-Item -ItemType Directory -Path (Split-Path $PytestBaseTemp) -Force | Out-Null
        Invoke-Checked $VenvPython @(
            "-m", "pytest",
            "services/runtime/tests/test_desktop_sidecar.py",
            "--basetemp", $PytestBaseTemp
        )
        Invoke-Checked $Cargo @(
            "clippy",
            "--package", "chatwaifu-desktop-host",
            "--all-targets",
            "--target", $Target,
            "--", "-D", "warnings"
        )
        Invoke-Checked $Cargo @("test", "--package", "chatwaifu-desktop-host", "--target", $Target)
    }
    Invoke-Checked $Cargo @(
        "build",
        "--package", "chatwaifu-appcontainer-host",
        "--release",
        "--locked",
        "--target", $Target
    )
    New-Item -ItemType Directory -Path (Split-Path $StagedHelper) -Force | Out-Null
    Copy-Item -Path $HelperExecutable -Destination $StagedHelper -Force

    Invoke-Checked $VenvPython @(
        "tools/run_pnpm.py",
        "--filter", "@chatwaifu/desktop",
        "build:windows-installer"
    )

    $HostExecutable = Join-Path $RepoRoot "target\$Target\release\chatwaifu-desktop-host.exe"
    Assert-X64Pe $HostExecutable
    Assert-X64Pe $RuntimeExecutable
    Assert-X64Pe $HelperExecutable
    Assert-RuntimeFileIdentity $RuntimeExecutable

    $NsisRoot = Join-Path $RepoRoot "target\$Target\release\bundle\nsis"
    $Installer = Get-ChildItem -Path $NsisRoot -Filter "*-setup.exe" -File |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($null -eq $Installer) {
        throw "Tauri did not produce an NSIS installer under $NsisRoot"
    }
    $InstallerOutput = Join-Path $RepoRoot "dist\windows\installer"
    New-Item -ItemType Directory -Path $InstallerOutput -Force | Out-Null
    $FinalInstaller = Join-Path $InstallerOutput $Installer.Name
    Copy-Item -Path $Installer.FullName -Destination $FinalInstaller -Force
    $Digest = Get-FileHash -Path $FinalInstaller -Algorithm SHA256
    $ChecksumPath = "$FinalInstaller.sha256"
    $ChecksumLine = "$($Digest.Hash.ToLowerInvariant())  $($Installer.Name)`n"
    [System.IO.File]::WriteAllText(
        $ChecksumPath,
        $ChecksumLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host "Windows x64 installer: $FinalInstaller"
    Write-Host "SHA256: $($Digest.Hash.ToLowerInvariant())"
} finally {
    if ($null -eq $PreviousUvEnvironment) {
        Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
    } else {
        $env:UV_PROJECT_ENVIRONMENT = $PreviousUvEnvironment
    }
    if ($null -eq $PreviousUvPythonInstallDir) {
        Remove-Item Env:UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue
    } else {
        $env:UV_PYTHON_INSTALL_DIR = $PreviousUvPythonInstallDir
    }
    if ($Live2DDestinationTemporarilyOwned -and (Test-Path $Live2DDestination)) {
        Remove-Item -Path $Live2DDestination -Recurse -Force
    }
    if ($null -ne $OriginalLive2DBackup -and (Test-Path $OriginalLive2DBackup)) {
        New-Item -ItemType Directory -Path (Split-Path $Live2DDestination) -Force | Out-Null
        Move-Item -Path $OriginalLive2DBackup -Destination $Live2DDestination
    }
    if ($null -ne $Live2DStagingRoot -and (Test-Path $Live2DStagingRoot)) {
        Remove-Item -Path $Live2DStagingRoot -Recurse -Force
    }
    Pop-Location
}
