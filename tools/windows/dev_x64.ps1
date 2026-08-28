Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This development script only supports Windows."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Target = "x86_64-pc-windows-msvc"

if (-not (Test-Path $VenvPython)) {
    throw "Missing .venv. Run tools/windows/bootstrap_x64.ps1 first."
}

$PythonPlatform = (& $VenvPython -c "import sysconfig; print(sysconfig.get_platform())").Trim()
if ($PythonPlatform -ne "win-amd64") {
    throw "Expected win-amd64 .venv, received $PythonPlatform."
}

$Rustup = (Get-Command rustup -ErrorAction Stop).Source

Push-Location $RepoRoot
try {
    & $Rustup target add $Target
    if ($LASTEXITCODE -ne 0) {
        throw "rustup target add $Target exited with code $LASTEXITCODE"
    }

    # Windows development must remain usable before optional local model workers are installed.
    # The Runtime starts with deterministic fallback providers; cloud providers can still be
    # configured through the normal settings UI.
    $env:CHATWAIFU_DESKTOP_OPTIONAL_LOCAL_WORKERS = "true"
    & $VenvPython tools/run_pnpm.py --filter '@chatwaifu/desktop' exec tauri dev --target $Target
    if ($LASTEXITCODE -ne 0) {
        throw "Windows x64 desktop development host exited with code $LASTEXITCODE"
    }
} finally {
    Remove-Item Env:CHATWAIFU_DESKTOP_OPTIONAL_LOCAL_WORKERS -ErrorAction SilentlyContinue
    Pop-Location
}
