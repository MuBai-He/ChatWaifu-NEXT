[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ArchivePath,

    [ValidateNotNullOrEmpty()]
    [string]$ProductName = "ChatWaifu NEXT",

    [string]$RuntimePath = "",

    [switch]$VerifyOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Worker Packs can only be installed into the Windows desktop product on Windows."
}

$ResolvedArchive = (Resolve-Path -LiteralPath $ArchivePath).Path
if ([System.IO.Path]::GetExtension($ResolvedArchive) -ine ".cwpack") {
    throw "Expected a .cwpack archive: $ResolvedArchive"
}

function Get-PeMachine {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Stream = [System.IO.File]::OpenRead($Path)
    $Reader = [System.IO.BinaryReader]::new($Stream)
    try {
        if ($Stream.Length -lt 64 -or $Reader.ReadUInt16() -ne 0x5A4D) {
            throw "Not a PE executable: $Path"
        }
        $Stream.Position = 0x3c
        $PeOffset = $Reader.ReadInt32()
        if ($PeOffset -lt 0 -or ($PeOffset + 6) -gt $Stream.Length) {
            throw "Invalid PE header offset: $Path"
        }
        $Stream.Position = $PeOffset
        if ($Reader.ReadUInt32() -ne 0x00004550) {
            throw "Invalid PE signature: $Path"
        }
        return $Reader.ReadUInt16()
    } finally {
        $Reader.Dispose()
        $Stream.Dispose()
    }
}

function Find-InstalledRuntime {
    param([Parameter(Mandatory = $true)][string]$DisplayName)

    $Candidates = @()
    foreach ($View in @(
        [Microsoft.Win32.RegistryView]::Registry64,
        [Microsoft.Win32.RegistryView]::Registry32
    )) {
        $Base = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
            [Microsoft.Win32.RegistryHive]::CurrentUser,
            $View
        )
        try {
            $Uninstall = $Base.OpenSubKey(
                "Software\Microsoft\Windows\CurrentVersion\Uninstall",
                $false
            )
            if ($null -eq $Uninstall) {
                continue
            }
            try {
                foreach ($Name in $Uninstall.GetSubKeyNames()) {
                    $Entry = $Uninstall.OpenSubKey($Name, $false)
                    if ($null -eq $Entry) {
                        continue
                    }
                    try {
                        if ([string]$Entry.GetValue("DisplayName", "") -cne $DisplayName) {
                            continue
                        }
                        $InstallLocation = ([string]$Entry.GetValue("InstallLocation", "")).Trim().Trim('"')
                        if (-not $InstallLocation) {
                            continue
                        }
                        $Candidate = Join-Path $InstallLocation "runtime-sidecar\chatwaifu-runtime.exe"
                        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
                            $Candidates += [System.IO.Path]::GetFullPath($Candidate)
                        }
                    } finally {
                        $Entry.Dispose()
                    }
                }
            } finally {
                $Uninstall.Dispose()
            }
        } finally {
            $Base.Dispose()
        }
    }
    $Unique = @($Candidates | Sort-Object -Unique)
    if ($Unique.Count -ne 1) {
        throw "Expected one installed '$DisplayName' Runtime, found $($Unique.Count). Use -RuntimePath to select it explicitly."
    }
    return $Unique[0]
}

$ResolvedRuntime = if ($RuntimePath) {
    (Resolve-Path -LiteralPath $RuntimePath).Path
} else {
    Find-InstalledRuntime -DisplayName $ProductName
}
if (-not (Test-Path -LiteralPath $ResolvedRuntime -PathType Leaf)) {
    throw "Installed Runtime is missing: $ResolvedRuntime"
}
$Machine = Get-PeMachine -Path $ResolvedRuntime
if ($Machine -ne 0x8664) {
    throw ("Installed Runtime must be x64 PE machine 0x8664, received 0x{0:X4}: {1}" -f `
        $Machine, $ResolvedRuntime)
}

& $ResolvedRuntime --worker-pack verify $ResolvedArchive
if ($LASTEXITCODE -ne 0) {
    throw "Worker Pack verification failed with exit code $LASTEXITCODE."
}
if ($VerifyOnly) {
    Write-Host "Worker Pack verified; no installation was requested."
    exit 0
}

& $ResolvedRuntime --worker-pack install $ResolvedArchive
if ($LASTEXITCODE -ne 0) {
    throw "Worker Pack installation failed with exit code $LASTEXITCODE."
}
Write-Host "Worker Pack installed and activated. Restart ChatWaifu NEXT to use it."
