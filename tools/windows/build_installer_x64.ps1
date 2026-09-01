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
$RuntimeRoot = Join-Path $RepoRoot "dist\windows\runtime-sidecar"
$RuntimeExecutable = Join-Path $RuntimeRoot "chatwaifu-runtime.exe"
$HelperExecutable = Join-Path $RepoRoot "target\$Target\release\chatwaifu-appcontainer-host.exe"
$StagedHelper = Join-Path $RepoRoot "apps\desktop\src-tauri\binaries\chatwaifu-appcontainer-host-$Target.exe"
$PytestBaseTemp = Join-Path $RepoRoot "build\pytest\windows-installer"
$Live2DDestination = Join-Path $RepoRoot "apps\web\public\vendor\live2d"
$NsisRoot = Join-Path $RepoRoot "target\$Target\release\bundle\nsis"
$InstallerOutput = Join-Path $RepoRoot "dist\windows\installer"
$Live2DTransactionRoot = Join-Path $RepoRoot "build\windows-installer\live2d-transaction"
$OriginalLive2DBackup = Join-Path $Live2DTransactionRoot "original"
$Live2DSourceSnapshot = Join-Path $Live2DTransactionRoot "source"
$Live2DSourceLayout = ""

. (Join-Path $PSScriptRoot "installer_live2d_transaction.ps1")

# Recover before toolchain preflight as well as before inspecting local assets.
# A broken or incomplete development environment must not leave an interrupted
# private-overlay transaction in place until the next successful build attempt.
Restore-InstallerLive2DTransaction -TransactionRoot $Live2DTransactionRoot `
    -Destination $Live2DDestination

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

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected PE file was not produced: $Path"
    }
    $Stream = [System.IO.File]::OpenRead($Path)
    $Reader = [System.IO.BinaryReader]::new($Stream)
    try {
        if ($Stream.Length -lt 0x40) {
            throw "File is too small to contain a PE header: $Path"
        }
        $DosSignature = $Reader.ReadUInt16()
        if ($DosSignature -ne 0x5A4D) {
            throw "Invalid MZ signature in PE file: $Path"
        }
        $Stream.Position = 0x3c
        $PeOffset = $Reader.ReadUInt32()
        if (([long]$PeOffset + 6) -gt $Stream.Length) {
            throw "Invalid PE header offset in file: $Path"
        }
        $Stream.Position = $PeOffset
        $PeSignature = $Reader.ReadUInt32()
        if ($PeSignature -ne 0x00004550) {
            throw "Invalid PE signature in file: $Path"
        }
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

function Assert-X64PeTree {
    param([Parameter(Mandatory = $true)][string]$Root)

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "Expected frozen Runtime directory was not produced: $Root"
    }
    $NativeFiles = @(
        Get-ChildItem -LiteralPath $Root -Recurse -File |
            Where-Object { $_.Extension.ToLowerInvariant() -in @(".exe", ".dll", ".pyd") }
    )
    if ($NativeFiles.Count -eq 0) {
        throw "Frozen Runtime contains no EXE, DLL, or PYD files: $Root"
    }
    foreach ($NativeFile in $NativeFiles) {
        Assert-X64Pe $NativeFile.FullName
    }
    Write-Host "Verified $($NativeFiles.Count) frozen Runtime native files as x64 PE."
}

function Assert-Live2DVendorInputs {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$BaseOnly
    )

    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Root `
        -Purpose "vendor input tree"
    $RequiredInputs = [ordered]@{
        "Cubism Core" = Join-Path $Root "live2dcubismcore.min.js"
        "ChatWaifu Cubism bridge" = Join-Path $Root "chatwaifu-live2d-bridge.js"
    }
    if (-not $BaseOnly) {
        $RequiredInputs["avatar model"] = Join-Path $Root "model\avatar.model3.json"
    }
    $MissingInputs = @()
    foreach ($Input in $RequiredInputs.GetEnumerator()) {
        if (-not (Test-Path -LiteralPath $Input.Value -PathType Leaf)) {
            $MissingInputs += "- $($Input.Key): $($Input.Value)"
        }
    }
    if ($MissingInputs.Count -gt 0) {
        throw (
            "Private Live2D owner overlay is incomplete:`n" +
            ($MissingInputs -join "`n")
        )
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

    Copy-InstallerLive2DDirectoryContents -Source $Source -Destination $Destination
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
    # out for the duration of every build, then restore it in finally. The stable,
    # ignored, same-volume transaction keeps an authoritative backup and atomic
    # phase markers across process termination. An explicit owner-only overlay is
    # first copied to a snapshot so source and destination may safely be identical.
    if ($Live2DSource -or (Test-Path $Live2DDestination -PathType Container)) {
        Start-InstallerLive2DTransaction -TransactionRoot $Live2DTransactionRoot `
            -Destination $Live2DDestination
    }
    if ($Live2DSource) {
        if (-not (Test-Path -LiteralPath $Live2DSource -PathType Container)) {
            throw "Private Live2D overlay source must be an existing directory: $Live2DSource"
        }
        Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Live2DSource `
            -Purpose "private overlay input tree"
        $ResolvedLive2DSource = (Resolve-Path -LiteralPath $Live2DSource).Path
        $DirectModelAvatar = Join-Path $ResolvedLive2DSource "avatar.model3.json"
        $VendorRootAvatar = Join-Path $ResolvedLive2DSource "model\avatar.model3.json"
        if (Test-Path -LiteralPath $DirectModelAvatar -PathType Leaf) {
            $Live2DSourceLayout = "model"
            # A model-only overlay deliberately inherits the ignored local SDK
            # inputs. Validate those inputs before moving anything or starting
            # the expensive Runtime and installer build.
            Assert-Live2DVendorInputs -Root $Live2DDestination -BaseOnly
        } elseif (Test-Path -LiteralPath $VendorRootAvatar -PathType Leaf) {
            $Live2DSourceLayout = "vendor"
            Assert-Live2DVendorInputs -Root $ResolvedLive2DSource
        } else {
            throw (
                "Private Live2D overlay must be either a model directory containing " +
                "avatar.model3.json or a vendor root containing model/avatar.model3.json: " +
                $ResolvedLive2DSource
            )
        }
        New-Item -ItemType Directory -Path $Live2DSourceSnapshot -Force | Out-Null
        Copy-DirectoryContents -Source $ResolvedLive2DSource `
            -Destination $Live2DSourceSnapshot
    }
    if (Test-Path $Live2DDestination -PathType Container) {
        Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Live2DDestination `
            -Purpose "destination before backup"
        if (Test-Path -LiteralPath $OriginalLive2DBackup) {
            throw "Live2D original backup path already exists: $OriginalLive2DBackup"
        }
        # Directory.Move is same-volume and fails if the backup path appears;
        # Move-Item would silently nest the original and corrupt recovery state.
        [System.IO.Directory]::Move($Live2DDestination, $OriginalLive2DBackup)
        Assert-InstallerLive2DOrdinaryDirectoryTree -Root $OriginalLive2DBackup `
            -Purpose "authoritative original backup"
    }
    if ($Live2DSource -or (Test-Path -LiteralPath $OriginalLive2DBackup -PathType Container)) {
        Set-InstallerLive2DDestinationOwned -TransactionRoot $Live2DTransactionRoot
    }
    if ($Live2DSource) {
        New-Item -ItemType Directory -Path $Live2DDestination -Force | Out-Null
        if ($Live2DSourceLayout -eq "model") {
            if (-not (Test-Path -LiteralPath $OriginalLive2DBackup -PathType Container)) {
                throw "A model-only Live2D overlay requires an existing local vendor base."
            }
            Copy-DirectoryContents -Source $OriginalLive2DBackup `
                -Destination $Live2DDestination
            $StagedModel = Join-Path $Live2DDestination "model"
            if (Test-Path -LiteralPath $StagedModel) {
                Remove-InstallerLive2DOrdinaryDirectoryTree -Root $StagedModel `
                    -Purpose "staged model tree"
            }
            New-Item -ItemType Directory -Path $StagedModel -Force | Out-Null
            Copy-DirectoryContents -Source $Live2DSourceSnapshot `
                -Destination $StagedModel
        } else {
            Copy-DirectoryContents -Source $Live2DSourceSnapshot `
                -Destination $Live2DDestination
        }
        Assert-Live2DVendorInputs -Root $Live2DDestination
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
    Assert-X64PeTree $RuntimeRoot
    Assert-RuntimeFileIdentity $RuntimeExecutable
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
    $Live2DRestoreFailure = $null
    try {
        Restore-InstallerLive2DTransaction -TransactionRoot $Live2DTransactionRoot `
            -Destination $Live2DDestination
    } catch {
        $Live2DRestoreFailure = $_
    } finally {
        Pop-Location
    }
    if ($null -ne $Live2DRestoreFailure) {
        throw $Live2DRestoreFailure
    }
}
