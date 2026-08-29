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
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

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
    $Platform = & $Path -c "import sysconfig; print(sysconfig.get_platform())" 2>$null
    if ($LASTEXITCODE -ne 0 -or $null -eq $Platform) {
        return ""
    }
    return ([string]$Platform).Trim()
}

$Uv = (Get-Command uv -ErrorAction Stop).Source
$Rustup = (Get-Command rustup -ErrorAction Stop).Source
$Cargo = (Get-Command cargo -ErrorAction Stop).Source

Push-Location $RepoRoot
$PreviousUvPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
try {
    # Keep the release interpreter on the repository's native NTFS path. Roaming
    # profiles can be redirected by VM software, and current uv correctly refuses
    # to execute a Python reached through an untrusted mount point.
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
            Invoke-Checked $Uv @("venv", "--clear", "--python", $PythonExe, ".venv")
        }
    } else {
        Invoke-Checked $Uv @("venv", "--python", $PythonExe, ".venv")
    }

    Invoke-Checked $Uv @(
        "sync",
        "--python", $PythonExe,
        "--all-packages",
        "--all-groups",
        "--locked"
    )
    Invoke-Checked $VenvPython @("tools/setup_nltk_data.py")
    Invoke-Checked $VenvPython @("tools/run_pnpm.py", "install", "--frozen-lockfile")
    Invoke-Checked $Rustup @("target", "add", "x86_64-pc-windows-msvc")
    Invoke-Checked $Cargo @("fetch", "--locked")

    Write-Host "Windows x64 toolchain is ready. Python platform: $PythonPlatform"
} finally {
    if ($null -eq $PreviousUvPythonInstallDir) {
        Remove-Item Env:UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue
    } else {
        $env:UV_PYTHON_INSTALL_DIR = $PreviousUvPythonInstallDir
    }
    Pop-Location
}
