param(
    [switch]$RecreateEnvironment
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This bootstrap script only supports Windows."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PythonRequest = "cpython-3.12.10-windows-x86_64-none"
$UvPythonInstallDir = Join-Path $RepoRoot ".local\toolchains\uv-python"
$ProjectEnvironment = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $ProjectEnvironment "Scripts\python.exe"
$TauriCli = Join-Path $RepoRoot "apps\desktop\node_modules\@tauri-apps\cli\tauri.js"
$ViteCli = Join-Path $RepoRoot "apps\web\node_modules\vite\bin\vite.js"

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

function Test-PnpmWorkspace {
    return ((Test-JavaScriptCli $TauriCli) -and
        (Test-JavaScriptCli $ViteCli))
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

function Repair-PnpmWorkspace {
    $GeneratedRoots = @(
        (Join-Path $RepoRoot "apps\desktop\node_modules"),
        (Join-Path $RepoRoot "apps\web\node_modules"),
        (Join-Path $RepoRoot "packages\avatar-sdk\node_modules"),
        (Join-Path $RepoRoot "packages\protocol-typescript\node_modules"),
        (Join-Path $RepoRoot "node_modules")
    )
    foreach ($GeneratedRoot in $GeneratedRoots) {
        Remove-Item -LiteralPath $GeneratedRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    Invoke-Checked $VenvPython @(
        "tools/run_pnpm.py", "install", "--frozen-lockfile", "--force"
    )
}

$Uv = (Get-Command uv -ErrorAction Stop).Source
$Rustup = (Get-Command rustup -ErrorAction Stop).Source
$Cargo = (Get-Command cargo -ErrorAction Stop).Source
$Node = (Get-Command node -ErrorAction Stop).Source

Push-Location $RepoRoot
$PreviousUvProject = $env:UV_PROJECT
$PreviousUvEnvironment = $env:UV_PROJECT_ENVIRONMENT
$PreviousUvPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
try {
    # Keep the release interpreter on the repository's native NTFS path. Roaming
    # profiles can be redirected by VM software, and current uv correctly refuses
    # to execute a Python reached through an untrusted mount point.
    $env:UV_PROJECT = $RepoRoot
    $env:UV_PROJECT_ENVIRONMENT = $ProjectEnvironment
    $env:UV_PYTHON_INSTALL_DIR = $UvPythonInstallDir
    New-Item -ItemType Directory -Path $UvPythonInstallDir -Force | Out-Null
    Invoke-Checked $Uv @("python", "install", $PythonRequest)
    $PythonExe = Join-Path (Join-Path $UvPythonInstallDir $PythonRequest) "python.exe"
    if (-not (Test-Path $PythonExe -PathType Leaf)) {
        throw "uv did not install the requested Windows x64 Python interpreter."
    }

    $PythonPlatform = Get-PythonPlatform $PythonExe
    if ($PythonPlatform -ne "win-amd64") {
        throw "Expected win-amd64 Python, received $PythonPlatform."
    }

    if (Test-Path $VenvPython) {
        $VenvPlatform = Get-PythonPlatform $VenvPython
        if ($VenvPlatform -ne "win-amd64") {
            if (-not $RecreateEnvironment) {
                throw "The existing .venv is $VenvPlatform. Re-run with -RecreateEnvironment."
            }
            Invoke-Checked $Uv @(
                "venv", "--clear", "--python", $PythonExe, $ProjectEnvironment
            )
        }
    } else {
        Invoke-Checked $Uv @("venv", "--python", $PythonExe, $ProjectEnvironment)
    }

    Invoke-Checked $Uv @(
        "sync",
        "--project", $RepoRoot,
        "--python", $PythonExe,
        "--all-packages",
        "--all-groups",
        "--locked"
    )
    Invoke-Checked $VenvPython @("tools/setup_nltk_data.py")
    Invoke-Checked $VenvPython @("tools/run_pnpm.py", "install", "--frozen-lockfile")
    if (-not (Test-PnpmWorkspace)) {
        Write-Warning "pnpm metadata was current but workspace links were missing; rebuilding node_modules."
        Repair-PnpmWorkspace
    }
    if (-not (Test-PnpmWorkspace)) {
        throw "pnpm did not install runnable Tauri/Vite CLIs."
    }
    Invoke-Checked $Rustup @("target", "add", "x86_64-pc-windows-msvc")
    Invoke-Checked $Cargo @("fetch", "--locked")

    Write-Host "Windows x64 toolchain is ready. Python platform: $PythonPlatform"
} finally {
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
    Pop-Location
}
