Set-StrictMode -Version Latest

if (-not ("ChatWaifu.WorkerPackInstall.NativePaths" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace ChatWaifu.WorkerPackInstall
{
    public static class NativePaths
    {
        private const uint KfFlagNoPackageRedirection = 0x00010000;

        [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
        private static extern int SHGetKnownFolderPath(
            [MarshalAs(UnmanagedType.LPStruct)] Guid folderId,
            uint flags,
            IntPtr token,
            out IntPtr path
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandle(
            SafeFileHandle handle,
            StringBuilder path,
            uint pathLength,
            uint flags
        );

        public static string GetPhysicalKnownFolder(string folderId)
        {
            IntPtr pointer;
            int result = SHGetKnownFolderPath(
                new Guid(folderId),
                KfFlagNoPackageRedirection,
                IntPtr.Zero,
                out pointer
            );
            if (result < 0)
            {
                Marshal.ThrowExceptionForHR(result);
            }
            if (pointer == IntPtr.Zero)
            {
                throw new InvalidOperationException(
                    "SHGetKnownFolderPath returned a null path."
                );
            }
            try
            {
                string value = Marshal.PtrToStringUni(pointer);
                if (String.IsNullOrWhiteSpace(value))
                {
                    throw new InvalidOperationException(
                        "SHGetKnownFolderPath returned an empty path."
                    );
                }
                return value;
            }
            finally
            {
                Marshal.FreeCoTaskMem(pointer);
            }
        }

        public static string GetFinalPath(SafeFileHandle handle)
        {
            StringBuilder buffer = new StringBuilder(32768);
            uint written = GetFinalPathNameByHandle(
                handle,
                buffer,
                (uint)buffer.Capacity,
                0
            );
            if (written == 0)
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            if (written >= buffer.Capacity)
            {
                throw new InvalidOperationException(
                    "The final Windows path exceeded the supported length."
                );
            }
            return buffer.ToString();
        }
    }
}
'@
}

function ConvertFrom-WorkerPackInstallExtendedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.StartsWith("\\?\UNC\", [StringComparison]::OrdinalIgnoreCase)) {
        return "\\" + $Path.Substring(8)
    }
    if ($Path.StartsWith("\\?\", [StringComparison]::OrdinalIgnoreCase)) {
        return $Path.Substring(4)
    }
    return $Path
}

function Assert-WorkerPackInstallOrdinaryPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $ExistingPath = $FullPath
    while (-not (Test-Path -LiteralPath $ExistingPath)) {
        $Parent = Split-Path -Parent $ExistingPath
        if (-not $Parent -or $Parent -eq $ExistingPath) {
            throw "$Purpose has no existing parent directory: $FullPath"
        }
        $ExistingPath = $Parent
    }

    $Visited = [System.Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    while ($ExistingPath) {
        if (-not $Visited.Add($ExistingPath)) {
            throw "$Purpose contains an unresolvable parent cycle: $FullPath"
        }
        $Item = Get-Item -LiteralPath $ExistingPath -Force
        if (-not $Item.PSIsContainer) {
            throw "$Purpose must resolve through directories: $ExistingPath"
        }
        if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Purpose must not use a junction, symlink, or reparse point: $ExistingPath"
        }
        $Parent = Split-Path -Parent $ExistingPath
        if (-not $Parent -or $Parent -eq $ExistingPath) {
            break
        }
        $ExistingPath = $Parent
    }
}

function Assert-WorkerPackInstallUnredirectedRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label,
        [scriptblock]$FinalPathResolver = {
            param($Stream)
            [ChatWaifu.WorkerPackInstall.NativePaths]::GetFinalPath(
                $Stream.SafeFileHandle
            )
        }
    )

    $FullRoot = [System.IO.Path]::GetFullPath($Root)
    Assert-WorkerPackInstallOrdinaryPath -Path $FullRoot -Purpose $Label
    if (-not (Test-Path -LiteralPath $FullRoot -PathType Container)) {
        throw "$Label is not an existing directory: $FullRoot"
    }
    if ($FullRoot -match '(?i)[\\/]Packages[\\/][^\\/]+[\\/]LocalCache[\\/](Local|Roaming)([\\/]|$)') {
        throw "$Label must not use a Package LocalCache path: $FullRoot"
    }

    $ProbeName = (
        ".chatwaifu-worker-pack-root-probe-$PID-" +
        [Guid]::NewGuid().ToString("N") + ".tmp"
    )
    $ProbePath = Join-Path $FullRoot $ProbeName
    $FinalPath = $null
    $Stream = $null
    $ProbeError = $null
    $CleanupErrors = [System.Collections.Generic.List[string]]::new()
    try {
        $Stream = [System.IO.File]::Open(
            $ProbePath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
        )
        $ProbeBytes = [Text.Encoding]::UTF8.GetBytes($ProbeName)
        $Stream.Write($ProbeBytes, 0, $ProbeBytes.Length)
        $Stream.Flush($true)
        $FinalPath = ConvertFrom-WorkerPackInstallExtendedPath -Path (
            & $FinalPathResolver $Stream
        )
        $ExpectedPath = [System.IO.Path]::GetFullPath($ProbePath)
        $ObservedPath = [System.IO.Path]::GetFullPath($FinalPath)
        if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
            $ExpectedPath,
            $ObservedPath
        )) {
            $ProbeError = (
                "$Label writes are redirected to '$ObservedPath' instead of " +
                "the physical user root '$ExpectedPath'."
            )
        }
    } catch {
        $ProbeError = "$Label could not pass its physical-path probe: $($_.Exception.Message)"
    } finally {
        if ($null -ne $Stream) {
            try {
                $Stream.Dispose()
            } catch {
                $CleanupErrors.Add("closing '$ProbePath': $($_.Exception.Message)")
            }
        }
        foreach ($CleanupPath in @($ProbePath, $FinalPath) | Select-Object -Unique) {
            if (-not $CleanupPath) {
                continue
            }
            try {
                if ([System.IO.Path]::GetFileName($CleanupPath) -cne $ProbeName) {
                    throw "refusing unexpected probe path '$CleanupPath'"
                }
                if ([System.IO.File]::Exists($CleanupPath)) {
                    [System.IO.File]::Delete($CleanupPath)
                }
                if ([System.IO.File]::Exists($CleanupPath)) {
                    throw "probe still exists"
                }
            } catch {
                $CleanupErrors.Add("deleting '$CleanupPath': $($_.Exception.Message)")
            }
        }
    }

    if ($CleanupErrors.Count -gt 0) {
        throw (
            "Worker Pack installation stopped because its $Label probe could not be " +
            "cleaned safely: $($CleanupErrors -join '; ')"
        )
    }
    if ($ProbeError) {
        throw (
            "Worker Pack installation stopped before verification or activation. " +
            "$ProbeError Close this Codex task terminal and run the same command " +
            "from a standalone PowerShell or Windows Terminal window."
        )
    }
}

function Get-WorkerPackInstallUserRoots {
    param(
        [Parameter(Mandatory = $true)][string]$AppIdentifier
    )

    if ($AppIdentifier -notmatch '^[A-Za-z0-9._-]+$') {
        throw "AppIdentifier cannot be mapped safely into the Windows user directories."
    }
    $PhysicalLocalAppData = (
        [ChatWaifu.WorkerPackInstall.NativePaths]::GetPhysicalKnownFolder(
            "f1b32785-6fba-4fcf-9d55-7b8e7f157091"
        )
    )
    $PhysicalRoamingAppData = (
        [ChatWaifu.WorkerPackInstall.NativePaths]::GetPhysicalKnownFolder(
            "3eb685db-65f9-4cf6-a03a-e3ef65729f3d"
        )
    )
    $RuntimeConfigRoot = Join-Path (
        Join-Path $PhysicalRoamingAppData $AppIdentifier
    ) "runtime"
    $RuntimeDataRoot = Join-Path (
        Join-Path $PhysicalLocalAppData $AppIdentifier
    ) "runtime"

    Assert-WorkerPackInstallOrdinaryPath -Path $RuntimeConfigRoot `
        -Purpose "RoamingAppData Runtime config root"
    Assert-WorkerPackInstallOrdinaryPath -Path $RuntimeDataRoot `
        -Purpose "LocalAppData Runtime data root"
    Assert-WorkerPackInstallUnredirectedRoot -Root $PhysicalRoamingAppData `
        -Label "RoamingAppData"
    Assert-WorkerPackInstallUnredirectedRoot -Root $PhysicalLocalAppData `
        -Label "LocalAppData"

    return [pscustomobject]@{
        Config = $RuntimeConfigRoot
        Data = $RuntimeDataRoot
    }
}
