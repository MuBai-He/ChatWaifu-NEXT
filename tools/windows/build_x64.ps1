Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This build script only supports Windows."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Target = "x86_64-pc-windows-msvc"

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

if (-not (Test-Path $VenvPython)) {
    throw "Missing .venv. Run tools/windows/bootstrap_x64.ps1 first."
}

$PythonPlatform = (& $VenvPython -c "import sysconfig; print(sysconfig.get_platform())").Trim()
if ($PythonPlatform -ne "win-amd64") {
    throw "Expected win-amd64 .venv, received $PythonPlatform."
}

$Rustup = (Get-Command rustup -ErrorAction Stop).Source
$Cargo = (Get-Command cargo -ErrorAction Stop).Source

Push-Location $RepoRoot
try {
    Invoke-Checked $Rustup @("target", "add", $Target)
    Invoke-Checked $Cargo @("fmt", "--all", "--check")
    Invoke-Checked $Cargo @("clippy", "--workspace", "--all-targets", "--target", $Target, "--", "-D", "warnings")
    Invoke-Checked $Cargo @("test", "--workspace", "--target", $Target)
    Invoke-Checked $Cargo @(
        "build",
        "--package", "chatwaifu-appcontainer-host",
        "--release",
        "--target", $Target
    )
    Invoke-Checked $VenvPython @("tools/run_pnpm.py", "build:windows-x64")

    $Executable = Join-Path $RepoRoot "target\$Target\release\chatwaifu-desktop-host.exe"
    if (-not (Test-Path $Executable)) {
        throw "The expected Windows x64 executable was not produced: $Executable"
    }
    $Machine = Get-PeMachine $Executable
    if ($Machine -ne 0x8664) {
        throw ("Expected PE machine 0x8664 (x64), received 0x{0:X4}." -f $Machine)
    }

    $SandboxExecutable = Join-Path $RepoRoot "target\$Target\release\chatwaifu-appcontainer-host.exe"
    if (-not (Test-Path $SandboxExecutable)) {
        throw "The expected Windows x64 AppContainer launcher was not produced: $SandboxExecutable"
    }
    $SandboxMachine = Get-PeMachine $SandboxExecutable
    if ($SandboxMachine -ne 0x8664) {
        throw ("Expected AppContainer launcher PE machine 0x8664 (x64), received 0x{0:X4}." -f $SandboxMachine)
    }

    Write-Host "Windows x64 desktop build verified: $Executable"
    Write-Host "Windows x64 AppContainer launcher verified: $SandboxExecutable"
} finally {
    Pop-Location
}
