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
$TauriCli = Join-Path $RepoRoot "apps\desktop\node_modules\@tauri-apps\cli\tauri.js"
$ViteCli = Join-Path $RepoRoot "apps\web\node_modules\vite\bin\vite.js"
$Target = "x86_64-pc-windows-msvc"
$RuntimeExecutable = Join-Path $RepoRoot "dist\windows\runtime-sidecar\chatwaifu-runtime.exe"
$HelperExecutable = Join-Path $RepoRoot "target\$Target\release\chatwaifu-appcontainer-host.exe"
$StagedHelper = Join-Path $RepoRoot "apps\desktop\src-tauri\binaries\chatwaifu-appcontainer-host-$Target.exe"
$PytestBaseTemp = Join-Path $RepoRoot "build\pytest\windows-installer"
$Live2DDestination = Join-Path $RepoRoot "apps\web\public\vendor\live2d"
$NsisRoot = Join-Path $RepoRoot "target\$Target\release\bundle\nsis"
$InstallerOutput = Join-Path $RepoRoot "dist\windows\installer"
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
    $PreviousPreference = $ErrorActionPreference
    $ExitCode = -1
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $Platform = & $Path -c "import sysconfig; print(sysconfig.get_platform())" 2>$null
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    if ($ExitCode -ne 0 -or $null -eq $Platform) {
        return ""
    }
    return ([string]$Platform).Trim()
}

function Test-JavaScriptCli {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $PreviousPreference = $ErrorActionPreference
    $ExitCode = -1
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $Node $Path "--version" *> $null
        $ExitCode = $LASTEXITCODE
    } catch {
        $ExitCode = -1
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    return ($ExitCode -eq 0)
}

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    foreach ($Entry in @(Get-ChildItem -LiteralPath $Source -Force)) {
        Copy-Item -LiteralPath $Entry.FullName -Destination $Destination -Recurse -Force
    }
}

function Remove-GeneratedInstallerArtifacts {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string[]]$Filters
    )

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return
    }
    foreach ($Filter in $Filters) {
        foreach ($Artifact in @(Get-ChildItem -LiteralPath $Directory -Filter $Filter -File)) {
            Remove-Item -LiteralPath $Artifact.FullName -Force
        }
    }
}

$Node = (Get-Command node -ErrorAction Stop).Source

if (-not (Test-Path $VenvPython -PathType Leaf)) {
    throw "Missing x64 .venv. Run tools/windows/bootstrap_x64.ps1 first."
}

$PythonPlatform = Get-PythonPlatform $VenvPython
if ($PythonPlatform -ne "win-amd64") {
    throw "Expected a working win-amd64 .venv. Run tools/windows/bootstrap_x64.ps1 -RecreateEnvironment."
}
if (-not (Test-JavaScriptCli $TauriCli) -or
    -not (Test-JavaScriptCli $ViteCli)) {
    throw "Tauri/Vite CLIs are missing or unusable. Run tools/windows/bootstrap_x64.ps1 before the installer build."
}

$Uv = (Get-Command uv -ErrorAction Stop).Source
$Cargo = (Get-Command cargo -ErrorAction Stop).Source
$Rustup = (Get-Command rustup -ErrorAction Stop).Source

Push-Location $RepoRoot
$PreviousUvProject = $env:UV_PROJECT
$PreviousUvEnvironment = $env:UV_PROJECT_ENVIRONMENT
$PreviousUvPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
try {
    $env:UV_PROJECT = $RepoRoot
    $env:UV_PROJECT_ENVIRONMENT = $PackagingEnvironment
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
        if (-not (Test-Path -LiteralPath $Live2DSource -PathType Container)) {
            throw "Private Live2D overlay source must be an existing directory: $Live2DSource"
        }
        $ResolvedLive2DSource = (Resolve-Path -LiteralPath $Live2DSource).Path
        $Live2DSourceSnapshot = Join-Path $Live2DStagingRoot "source"
        New-Item -ItemType Directory -Path $Live2DSourceSnapshot -Force | Out-Null
        Copy-DirectoryContents -Source $ResolvedLive2DSource `
            -Destination $Live2DSourceSnapshot
    }
    if (Test-Path $Live2DDestination -PathType Container) {
        $OriginalLive2DBackup = Join-Path $Live2DStagingRoot "original"
        Move-Item -Path $Live2DDestination -Destination $OriginalLive2DBackup
        $Live2DDestinationTemporarilyOwned = $true
    }
    if ($Live2DSource) {
        $Live2DDestinationTemporarilyOwned = $true
        New-Item -ItemType Directory -Path $Live2DDestination -Force | Out-Null
        Copy-DirectoryContents -Source $Live2DSourceSnapshot `
            -Destination $Live2DDestination
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
    Invoke-Checked $Uv @(
        "sync",
        "--project", $RepoRoot,
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
    Remove-Item -LiteralPath $StagedHelper -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path (Split-Path $StagedHelper) -Force | Out-Null
    Copy-Item -Path $HelperExecutable -Destination $StagedHelper -Force

    Remove-GeneratedInstallerArtifacts -Directory $NsisRoot `
        -Filters @("*-setup.exe")
    Remove-GeneratedInstallerArtifacts -Directory $InstallerOutput `
        -Filters @("*-setup.exe", "*-setup.exe.sha256")
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

    $Installers = @(Get-ChildItem -LiteralPath $NsisRoot -Filter "*-setup.exe" -File)
    if ($Installers.Count -ne 1) {
        throw "Tauri must produce exactly one fresh NSIS installer under $NsisRoot; received $($Installers.Count)."
    }
    $Installer = $Installers[0]
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
    Remove-Item -LiteralPath $StagedHelper -Force -ErrorAction SilentlyContinue
    if ($null -eq $PreviousUvProject) {
        Remove-Item Env:UV_PROJECT -ErrorAction SilentlyContinue
    } else {
        $env:UV_PROJECT = $PreviousUvProject
    }
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
