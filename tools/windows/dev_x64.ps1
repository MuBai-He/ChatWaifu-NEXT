Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This development script only supports Windows."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Live2DModel = Join-Path $RepoRoot "apps\web\public\vendor\live2d\model\avatar.model3.json"
$Live2DTexture = Join-Path $RepoRoot "apps\web\public\vendor\live2d\model\texture\texture_00.png"
$Live2DTextureOptimizer = Join-Path $RepoRoot "tools\windows\optimize_live2d_texture.ps1"
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
    $SessionId = (Get-Process -Id $PID).SessionId
    if ($SessionId -eq 0) {
        Write-Warning "This shell is not attached to the visible Windows desktop. Run this script inside the Parallels Windows PowerShell window."
    }
    if (-not (Test-Path $Live2DModel)) {
        Write-Warning "Local Live2D assets are missing. The Windows app will use the deterministic fallback avatar."
    } elseif (Test-Path $Live2DTexture) {
        try {
            & $Live2DTextureOptimizer -TexturePath $Live2DTexture
        } catch {
            Write-Warning "Could not optimize the local Live2D texture: $($_.Exception.Message)"
        }
    }
    Write-Host "Starting ChatWaifu NEXT as Windows x64 ($Target)."
    Write-Host "Keep this PowerShell window open. Ctrl+C is the normal way to stop the development stack."
    Write-Host "Parallels Coherence can display this Windows window directly on the macOS desktop."
    & $VenvPython tools/run_pnpm.py --filter '@chatwaifu/desktop' exec tauri dev --target $Target
    if ($LASTEXITCODE -ne 0) {
        throw "Windows x64 desktop development host exited with code $LASTEXITCODE"
    }
} finally {
    Remove-Item Env:CHATWAIFU_DESKTOP_OPTIONAL_LOCAL_WORKERS -ErrorAction SilentlyContinue
    Pop-Location
}
