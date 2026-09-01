function Assert-InstallerLive2DOrdinaryAncestorPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $ExistingPath = $FullPath
    while (-not (Test-Path -LiteralPath $ExistingPath)) {
        $Parent = Split-Path -Parent $ExistingPath
        if (-not $Parent -or $Parent -eq $ExistingPath) {
            throw "Live2D $Purpose has no existing parent directory: $FullPath"
        }
        $ExistingPath = $Parent
    }

    $Visited = [System.Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    while ($ExistingPath) {
        if (-not $Visited.Add($ExistingPath)) {
            throw "Live2D $Purpose contains an unresolvable parent cycle: $FullPath"
        }
        $Item = Get-Item -LiteralPath $ExistingPath -Force
        if (-not $Item.PSIsContainer) {
            throw "Live2D $Purpose must resolve through directories: $ExistingPath"
        }
        if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing Live2D $Purpose through an ancestor reparse point: $ExistingPath"
        }
        $Parent = Split-Path -Parent $ExistingPath
        if (-not $Parent -or $Parent -eq $ExistingPath) {
            break
        }
        $ExistingPath = $Parent
    }
}

function Get-InstallerLive2DOrdinaryTreeEntries {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    Assert-InstallerLive2DOrdinaryAncestorPath -Path $Root -Purpose $Purpose
    if (-not (Test-Path -LiteralPath $Root)) {
        throw "Live2D $Purpose is missing: $Root"
    }
    $RootItem = Get-Item -LiteralPath $Root -Force
    if (-not $RootItem.PSIsContainer) {
        throw "Live2D $Purpose must be a directory: $Root"
    }
    if (($RootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing Live2D $Purpose at a reparse point: $($RootItem.FullName)"
    }

    # Enumerate one ordinary directory at a time. Get-ChildItem -Recurse can
    # traverse junctions on some PowerShell/.NET combinations before a caller
    # gets an opportunity to inspect the returned item.
    $Pending = [System.Collections.Generic.Queue[string]]::new()
    $Entries = [System.Collections.Generic.List[System.IO.FileSystemInfo]]::new()
    $Pending.Enqueue($RootItem.FullName)
    while ($Pending.Count -gt 0) {
        $CurrentPath = $Pending.Dequeue()
        $CurrentItem = Get-Item -LiteralPath $CurrentPath -Force
        if (-not $CurrentItem.PSIsContainer -or
            ($CurrentItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing Live2D $Purpose through a reparse point: $CurrentPath"
        }
        foreach ($Child in @(Get-ChildItem -LiteralPath $CurrentItem.FullName -Force)) {
            # Re-read attributes rather than trusting an earlier directory
            # enumeration so a changed entry fails closed before recursion.
            $ObservedChild = Get-Item -LiteralPath $Child.FullName -Force
            if (($ObservedChild.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw (
                    "Refusing Live2D $Purpose containing a reparse point: " +
                    $ObservedChild.FullName
                )
            }
            $Entries.Add($ObservedChild) | Out-Null
            if ($ObservedChild.PSIsContainer) {
                $Pending.Enqueue($ObservedChild.FullName)
            }
        }
    }
    return $Entries
}

function Assert-InstallerLive2DOrdinaryDirectoryTree {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    $null = @(Get-InstallerLive2DOrdinaryTreeEntries -Root $Root -Purpose $Purpose)
}

function Remove-InstallerLive2DOrdinaryDirectoryTree {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    if (-not (Test-Path -LiteralPath $Root)) {
        return
    }
    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Root -Purpose $Purpose
    Remove-Item -LiteralPath $Root -Recurse -Force
}

function Copy-InstallerLive2DDirectoryContents {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Source `
        -Purpose "copy source"
    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Destination `
        -Purpose "copy destination"
    foreach ($Entry in @(Get-ChildItem -LiteralPath $Source -Force)) {
        Copy-Item -LiteralPath $Entry.FullName -Destination $Destination -Recurse -Force
    }
    # Recheck both trees after the copy. This both verifies that the staged
    # snapshot stayed ordinary and rejects a copied/replaced reparse entry
    # before another build or restoration step can consume it.
    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Source `
        -Purpose "copy source"
    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Destination `
        -Purpose "copy destination"
}

function Get-InstallerLive2DFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $HashBytes = $Hasher.ComputeHash($Stream)
        return [System.BitConverter]::ToString($HashBytes).Replace("-", "")
    } finally {
        $Hasher.Dispose()
        $Stream.Dispose()
    }
}

function Get-InstallerLive2DFileInventory {
    param([Parameter(Mandatory = $true)][string]$Root)

    $Entries = @(
        Get-InstallerLive2DOrdinaryTreeEntries -Root $Root `
            -Purpose "inventory tree"
    )
    $ResolvedRoot = (Get-Item -LiteralPath $Root -Force).FullName.TrimEnd("\", "/")
    $PrefixLength = $ResolvedRoot.Length + 1
    $Inventory = @{}
    foreach ($Entry in $Entries) {
        $ObservedEntry = Get-Item -LiteralPath $Entry.FullName -Force
        if (($ObservedEntry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing Live2D inventory tree containing a reparse point: $($Entry.FullName)"
        }
        $RelativePath = $ObservedEntry.FullName.Substring($PrefixLength).Replace("/", "\")
        if ($ObservedEntry.PSIsContainer) {
            $Inventory[$RelativePath] = [pscustomobject]@{
                Kind = "directory"
                Length = [long]0
                Sha256 = ""
            }
        } else {
            $Inventory[$RelativePath] = [pscustomobject]@{
                Kind = "file"
                Length = $ObservedEntry.Length
                Sha256 = Get-InstallerLive2DFileSha256 -Path $ObservedEntry.FullName
            }
        }
    }
    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Root `
        -Purpose "inventory tree"
    return $Inventory
}

function Assert-InstallerLive2DDirectoryIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Actual
    )

    $ExpectedInventory = Get-InstallerLive2DFileInventory -Root $Expected
    $ActualInventory = Get-InstallerLive2DFileInventory -Root $Actual
    if ($ExpectedInventory.Count -ne $ActualInventory.Count) {
        throw (
            "Live2D restoration verification failed: expected " +
            "$($ExpectedInventory.Count) files but copied $($ActualInventory.Count)."
        )
    }
    foreach ($RelativePath in $ExpectedInventory.Keys) {
        if (-not $ActualInventory.ContainsKey($RelativePath)) {
            throw "Live2D restoration verification failed: missing $RelativePath"
        }
        $ExpectedFile = $ExpectedInventory[$RelativePath]
        $ActualFile = $ActualInventory[$RelativePath]
        if ($ExpectedFile.Kind -ne $ActualFile.Kind -or
            $ExpectedFile.Length -ne $ActualFile.Length -or
            $ExpectedFile.Sha256 -ne $ActualFile.Sha256) {
            throw "Live2D restoration verification failed: topology or content changed for $RelativePath"
        }
    }
}

function Get-InstallerLive2DTransactionPaths {
    param([Parameter(Mandatory = $true)][string]$TransactionRoot)

    return [pscustomobject]@{
        OriginalPresent = Join-Path $TransactionRoot "original-was-present"
        OriginalAbsent = Join-Path $TransactionRoot "original-was-absent"
        DestinationOwned = Join-Path $TransactionRoot "destination-owned"
        Restored = Join-Path $TransactionRoot "restored"
        Original = Join-Path $TransactionRoot "original"
        Source = Join-Path $TransactionRoot "source"
        Restore = Join-Path $TransactionRoot "restore"
    }
}

function Assert-InstallerLive2DTransactionOwnerAvailable {
    param([Parameter(Mandatory = $true)][string]$TransactionRoot)

    $OwnerMarkers = @(
        Get-ChildItem -LiteralPath $TransactionRoot -Force -Directory |
            Where-Object { $_.Name.StartsWith("owner-") }
    )
    if ($OwnerMarkers.Count -eq 0) {
        # Cleanup removes the owner marker only after restoration. A transaction
        # interrupted in that final cleanup phase remains safe to finish.
        return
    }
    if ($OwnerMarkers.Count -ne 1 -or
        $OwnerMarkers[0].Name -notmatch '^owner-([1-9][0-9]*)-([1-9][0-9]*)$') {
        throw "Live2D transaction has invalid or conflicting process-owner markers: $TransactionRoot"
    }

    $OwnerProcessId = [int]$Matches[1]
    $OwnerStartTicks = [long]$Matches[2]
    $CurrentProcess = [System.Diagnostics.Process]::GetCurrentProcess()
    try {
        $CurrentStartTicks = $CurrentProcess.StartTime.ToUniversalTime().Ticks
    } finally {
        $CurrentProcess.Dispose()
    }
    if ($OwnerProcessId -eq $PID -and $OwnerStartTicks -eq $CurrentStartTicks) {
        return
    }

    try {
        $OwnerProcess = [System.Diagnostics.Process]::GetProcessById($OwnerProcessId)
    } catch [System.ArgumentException] {
        return
    } catch {
        throw (
            "Could not prove that Live2D transaction owner PID $OwnerProcessId exited: " +
            $_.Exception.Message
        )
    }
    try {
        try {
            $ObservedStartTicks = $OwnerProcess.StartTime.ToUniversalTime().Ticks
        } catch [System.InvalidOperationException] {
            return
        } catch {
            throw (
                "Could not inspect Live2D transaction owner PID ${OwnerProcessId}: " +
                $_.Exception.Message
            )
        }
    } finally {
        $OwnerProcess.Dispose()
    }
    if ($ObservedStartTicks -eq $OwnerStartTicks) {
        throw (
            "Live2D transaction is still owned by active installer PID $OwnerProcessId. " +
            "Refusing concurrent staging."
        )
    }
}

function Remove-InstallerLive2DTransaction {
    param(
        [Parameter(Mandatory = $true)][string]$TransactionRoot,
        [Parameter(Mandatory = $true)][object]$Paths,
        [Parameter(Mandatory = $true)][string]$OriginalStateMarker
    )

    if (-not (Test-Path -LiteralPath $Paths.Restored -PathType Container)) {
        throw "Refusing to delete Live2D recovery data before restoration is complete."
    }
    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $TransactionRoot `
        -Purpose "transaction cleanup tree"

    # Recovery data is deliberately removed only after the caller has durably
    # marked restoration complete. Keep the immutable original-state marker
    # until every backup, snapshot, and phase marker has been removed so an
    # interruption during cleanup remains recoverable on the next invocation.
    foreach ($RecoveryDirectory in @($Paths.Source, $Paths.Restore, $Paths.Original)) {
        if (Test-Path -LiteralPath $RecoveryDirectory) {
            Remove-InstallerLive2DOrdinaryDirectoryTree -Root $RecoveryDirectory `
                -Purpose "transaction recovery data"
        }
    }
    $OwnerMarkers = @(
        Get-ChildItem -LiteralPath $TransactionRoot -Force -Directory |
            Where-Object { $_.Name.StartsWith("owner-") }
    )
    $OwnerMarkerPaths = @($OwnerMarkers | ForEach-Object { $_.FullName })
    foreach ($PhaseMarker in @($Paths.DestinationOwned, $Paths.Restored) + $OwnerMarkerPaths) {
        if (Test-Path -LiteralPath $PhaseMarker) {
            Remove-InstallerLive2DOrdinaryDirectoryTree -Root $PhaseMarker `
                -Purpose "transaction phase marker"
        }
    }

    $UnexpectedEntries = @(
        Get-ChildItem -LiteralPath $TransactionRoot -Force |
            Where-Object { $_.FullName -ne $OriginalStateMarker }
    )
    if ($UnexpectedEntries.Count -gt 0) {
        throw (
            "Refusing to delete unexpected Live2D transaction data under " +
            "${TransactionRoot}: $($UnexpectedEntries.FullName -join ', ')"
        )
    }
    Remove-Item -LiteralPath $OriginalStateMarker -Recurse -Force
    if (@(Get-ChildItem -LiteralPath $TransactionRoot -Force).Count -ne 0) {
        throw "Refusing to remove a non-empty Live2D transaction root: $TransactionRoot"
    }
    Remove-Item -LiteralPath $TransactionRoot -Force
}

function Start-InstallerLive2DTransaction {
    param(
        [Parameter(Mandatory = $true)][string]$TransactionRoot,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $TransactionParent = Split-Path -Parent (
        [System.IO.Path]::GetFullPath($TransactionRoot)
    )
    if (-not $TransactionParent) {
        throw "Live2D transaction root has no parent directory: $TransactionRoot"
    }
    Assert-InstallerLive2DOrdinaryAncestorPath -Path $TransactionParent `
        -Purpose "transaction parent path"
    if (-not (Test-Path -LiteralPath $TransactionParent -PathType Container)) {
        New-Item -ItemType Directory -Path $TransactionParent -Force | Out-Null
    }
    Assert-InstallerLive2DOrdinaryAncestorPath -Path $TransactionParent `
        -Purpose "transaction parent path"
    Assert-InstallerLive2DOrdinaryAncestorPath -Path $Destination `
        -Purpose "destination path"
    if (Test-Path -LiteralPath $TransactionRoot) {
        throw "Live2D transaction recovery must complete before a new transaction starts: $TransactionRoot"
    }
    if ((Test-Path -LiteralPath $Destination) -and
        -not (Test-Path -LiteralPath $Destination -PathType Container)) {
        throw "Live2D destination must be a directory when it exists: $Destination"
    }
    if (Test-Path -LiteralPath $Destination -PathType Container) {
        Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Destination `
            -Purpose "destination tree"
    }

    $CurrentProcess = [System.Diagnostics.Process]::GetCurrentProcess()
    try {
        $CurrentStartTicks = $CurrentProcess.StartTime.ToUniversalTime().Ticks
    } finally {
        $CurrentProcess.Dispose()
    }
    # Keep the sibling name compact enough for Windows PowerShell 5.1's legacy
    # MAX_PATH behavior. Owner start ticks remain in the marker itself.
    $PreparationRoot = Join-Path $TransactionParent (
        ".cwli-$PID-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 12)
    )
    try {
        New-Item -ItemType Directory -Path $PreparationRoot | Out-Null
        New-Item -ItemType Directory `
            -Path (Join-Path $PreparationRoot "owner-$PID-$CurrentStartTicks") | Out-Null
        if (Test-Path -LiteralPath $Destination -PathType Container) {
            New-Item -ItemType Directory `
                -Path (Join-Path $PreparationRoot "original-was-present") | Out-Null
        } else {
            New-Item -ItemType Directory `
                -Path (Join-Path $PreparationRoot "original-was-absent") | Out-Null
        }
        # Directory.Move is a same-volume atomic activation and, unlike
        # Move-Item, cannot silently nest this prepared transaction inside a
        # concurrently created destination directory.
        [System.IO.Directory]::Move($PreparationRoot, $TransactionRoot)
    } finally {
        if (Test-Path -LiteralPath $PreparationRoot -PathType Container) {
            Remove-InstallerLive2DOrdinaryDirectoryTree -Root $PreparationRoot `
                -Purpose "transaction preparation tree"
        }
    }
}

function Set-InstallerLive2DDestinationOwned {
    param([Parameter(Mandatory = $true)][string]$TransactionRoot)

    $Paths = Get-InstallerLive2DTransactionPaths -TransactionRoot $TransactionRoot
    if (-not (Test-Path -LiteralPath $TransactionRoot -PathType Container)) {
        throw "Live2D transaction root is missing: $TransactionRoot"
    }
    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $TransactionRoot `
        -Purpose "transaction tree"
    New-Item -ItemType Directory -Path $Paths.DestinationOwned -Force | Out-Null
}

function Restore-InstallerLive2DTransaction {
    param(
        [Parameter(Mandatory = $true)][string]$TransactionRoot,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $TransactionRoot)) {
        return
    }
    if (-not (Test-Path -LiteralPath $TransactionRoot -PathType Container)) {
        throw "Live2D transaction root is not a directory: $TransactionRoot"
    }
    Assert-InstallerLive2DOrdinaryDirectoryTree -Root $TransactionRoot `
        -Purpose "transaction recovery tree"
    Assert-InstallerLive2DOrdinaryAncestorPath -Path $Destination `
        -Purpose "destination recovery path"
    if (Test-Path -LiteralPath $Destination) {
        Assert-InstallerLive2DOrdinaryDirectoryTree -Root $Destination `
            -Purpose "destination recovery tree"
    }
    Assert-InstallerLive2DTransactionOwnerAvailable -TransactionRoot $TransactionRoot

    $Paths = Get-InstallerLive2DTransactionPaths -TransactionRoot $TransactionRoot
    $OriginalWasPresent = Test-Path -LiteralPath $Paths.OriginalPresent -PathType Container
    $OriginalWasAbsent = Test-Path -LiteralPath $Paths.OriginalAbsent -PathType Container
    if ($OriginalWasPresent -and $OriginalWasAbsent) {
        throw "Live2D transaction has conflicting original-state markers: $TransactionRoot"
    }
    if (-not $OriginalWasPresent -and -not $OriginalWasAbsent) {
        if (@(Get-ChildItem -LiteralPath $TransactionRoot -Force).Count -eq 0) {
            # The process may have stopped between creating the reserved root and
            # its first atomic directory marker. No destination mutation is
            # possible before that marker, so this empty shell is safe to remove.
            Remove-Item -LiteralPath $TransactionRoot -Force
            return
        }
        throw (
            "Interrupted Live2D transaction is missing its original-state marker. " +
            "Recovery data was preserved at $TransactionRoot."
        )
    }

    $OriginalStateMarker = if ($OriginalWasPresent) {
        $Paths.OriginalPresent
    } else {
        $Paths.OriginalAbsent
    }
    $DestinationOwned = Test-Path -LiteralPath $Paths.DestinationOwned -PathType Container
    $Restored = Test-Path -LiteralPath $Paths.Restored -PathType Container

    if ($Restored) {
        if ($OriginalWasPresent -and
            -not (Test-Path -LiteralPath $Destination -PathType Container)) {
            throw "Live2D transaction says restoration completed, but the original is missing: $Destination"
        }
        if ($OriginalWasAbsent -and (Test-Path -LiteralPath $Destination)) {
            throw "Live2D transaction says an absent destination was restored, but a path exists: $Destination"
        }
        Remove-InstallerLive2DTransaction -TransactionRoot $TransactionRoot `
            -Paths $Paths -OriginalStateMarker $OriginalStateMarker
        return
    }

    if ($OriginalWasPresent) {
        if (Test-Path -LiteralPath $Paths.Original -PathType Container) {
            if ((Test-Path -LiteralPath $Destination) -and -not $DestinationOwned) {
                throw (
                    "Both an authoritative Live2D backup and an unowned destination exist. " +
                    "Recovery was preserved at $TransactionRoot."
                )
            }
            if (Test-Path -LiteralPath $Paths.Restore) {
                Remove-InstallerLive2DOrdinaryDirectoryTree -Root $Paths.Restore `
                    -Purpose "restore staging tree"
            }
            New-Item -ItemType Directory -Path $Paths.Restore | Out-Null
            Copy-InstallerLive2DDirectoryContents -Source $Paths.Original `
                -Destination $Paths.Restore
            Assert-InstallerLive2DDirectoryIdentity -Expected $Paths.Original `
                -Actual $Paths.Restore
            if (Test-Path -LiteralPath $Destination) {
                Remove-InstallerLive2DOrdinaryDirectoryTree -Root $Destination `
                    -Purpose "transaction-owned destination tree"
            }
            New-Item -ItemType Directory -Path (Split-Path $Destination) -Force | Out-Null
            # Directory.Move is same-volume and fails if a destination appears
            # after the owned tree was removed. Move-Item would silently nest
            # the restore tree and could then discard the authoritative backup.
            [System.IO.Directory]::Move($Paths.Restore, $Destination)
            Assert-InstallerLive2DDirectoryIdentity -Expected $Paths.Original `
                -Actual $Destination
            New-Item -ItemType Directory -Path $Paths.Restored | Out-Null
        } elseif ($DestinationOwned) {
            throw (
                "Live2D destination is transaction-owned, but its authoritative original backup " +
                "is missing: $($Paths.Original)"
            )
        } elseif (Test-Path -LiteralPath $Destination -PathType Container) {
            # The build stopped before the atomic original-to-backup move.
            New-Item -ItemType Directory -Path $Paths.Restored | Out-Null
        } else {
            throw (
                "Live2D original and its authoritative backup are both missing. " +
                "Recovery was preserved at $TransactionRoot."
            )
        }
    } else {
        if (Test-Path -LiteralPath $Paths.Original) {
            throw "Unexpected Live2D original backup for an initially absent destination: $($Paths.Original)"
        }
        if ($DestinationOwned) {
            if (Test-Path -LiteralPath $Destination) {
                Remove-InstallerLive2DOrdinaryDirectoryTree -Root $Destination `
                    -Purpose "transaction-owned destination tree"
            }
            if (Test-Path -LiteralPath $Destination) {
                throw "An initially absent Live2D destination reappeared during restoration: $Destination"
            }
            New-Item -ItemType Directory -Path $Paths.Restored | Out-Null
        } elseif (Test-Path -LiteralPath $Destination) {
            throw (
                "A Live2D destination appeared before this transaction owned it. " +
                "Recovery was preserved at $TransactionRoot."
            )
        } else {
            New-Item -ItemType Directory -Path $Paths.Restored | Out-Null
        }
    }

    Remove-InstallerLive2DTransaction -TransactionRoot $TransactionRoot `
        -Paths $Paths -OriginalStateMarker $OriginalStateMarker
}
