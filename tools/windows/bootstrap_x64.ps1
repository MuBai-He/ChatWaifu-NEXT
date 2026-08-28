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

$Uv = (Get-Command uv -ErrorAction Stop).Source
$Rustup = (Get-Command rustup -ErrorAction Stop).Source
$Cargo = (Get-Command cargo -ErrorAction Stop).Source

Push-Location $RepoRoot
try {
    Invoke-Checked $Uv @("python", "install", $PythonRequest)
    $PythonExe = (& $Uv python find $PythonRequest | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $PythonExe)) {
        throw "uv did not resolve the requested Windows x64 Python interpreter."
    }

    $PythonPlatform = (& $PythonExe -c "import sysconfig; print(sysconfig.get_platform())").Trim()
    if ($PythonPlatform -ne "win-amd64") {
        throw "Expected win-amd64 Python, received $PythonPlatform."
    }

    if (Test-Path $VenvPython) {
        $VenvPlatform = (& $VenvPython -c "import sysconfig; print(sysconfig.get_platform())").Trim()
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
    Invoke-Checked $VenvPython @("tools/run_pnpm.py", "install", "--frozen-lockfile")
    Invoke-Checked $Rustup @("target", "add", "x86_64-pc-windows-msvc")
    Invoke-Checked $Cargo @("fetch", "--locked")

    Write-Host "Windows x64 toolchain is ready. Python platform: $PythonPlatform"
} finally {
    Pop-Location
}
