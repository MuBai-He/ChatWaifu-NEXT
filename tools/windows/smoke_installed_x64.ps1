[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$InstallerPath,

    [ValidateRange(30, 600)]
    [int]$StartupTimeoutSeconds = 180,

    [ValidateRange(5, 120)]
    [int]$CleanupTimeoutSeconds = 45,

    [ValidateNotNullOrEmpty()]
    [string]$ProductName = "ChatWaifu NEXT",

    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$ProductVersion = "0.2.0",

    [ValidateNotNullOrEmpty()]
    [string]$AppIdentifier = "local.chatwaifu.next",

    [ValidateNotNullOrEmpty()]
    [string]$HostExecutableName = "chatwaifu-desktop-host.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "This installed-product smoke only supports Windows."
}
if (-not $env:LOCALAPPDATA -or -not $env:APPDATA) {
    throw "LOCALAPPDATA and APPDATA are required for the current-user acceptance test."
}

$ResolvedInstaller = (Resolve-Path $InstallerPath).Path
if ([System.IO.Path]::GetExtension($ResolvedInstaller) -ine ".exe") {
    throw "Expected an NSIS .exe installer: $ResolvedInstaller"
}

$ExpectedMachine = 0x8664
$RuntimeRelativePath = "runtime-sidecar\chatwaifu-runtime.exe"
$HelperRelativePath = "bin\chatwaifu-appcontainer-host.exe"
$RequiredRuntimeResources = @(
    "runtime-sidecar\_internal\config\default.toml",
    "runtime-sidecar\_internal\characters\default\character.yaml",
    "runtime-sidecar\_internal\skills\builtin\runtime-status\chatwaifu.yaml",
    "runtime-sidecar\_internal\nltk_data\tokenizers\punkt_tab\.chatwaifu-source.sha256",
    "runtime-sidecar\_internal\pipecat\audio\vad\data\silero_vad.onnx"
)
$ConfigRoot = Join-Path (Join-Path $env:APPDATA $AppIdentifier) "runtime"
$DataRoot = Join-Path (Join-Path $env:LOCALAPPDATA $AppIdentifier) "runtime"
$LogRoot = Join-Path (Join-Path $env:LOCALAPPDATA $AppIdentifier) "logs"
$MarkerName = ".chatwaifu-installed-smoke-preserve-$([System.Guid]::NewGuid().ToString('N')).json"
$ConfigMarker = Join-Path $ConfigRoot $MarkerName
$DataMarker = Join-Path $DataRoot $MarkerName
$StartMenuShortcut = Join-Path $env:APPDATA (
    "Microsoft\Windows\Start Menu\Programs\$ProductName.lnk"
)
$HostProcess = $null
$RuntimeProcessId = $null
$RuntimePort = $null
$InstallRoot = $null
$InstallCompleted = $false
$UninstallCompleted = $false

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}

function Test-PathEqual {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right
    )

    return (Get-NormalizedPath $Left) -ieq (Get-NormalizedPath $Right)
}

function Test-IsChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Child,
        [Parameter(Mandatory = $true)][string]$Parent
    )

    $NormalizedChild = Get-NormalizedPath $Child
    $NormalizedParent = Get-NormalizedPath $Parent
    $Prefix = $NormalizedParent + [System.IO.Path]::DirectorySeparatorChar
    return $NormalizedChild.StartsWith(
        $Prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
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

function Assert-X64Pe {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path $Path -PathType Leaf)) {
        throw "$Label is missing: $Path"
    }
    $Machine = Get-PeMachine $Path
    if ($Machine -ne $ExpectedMachine) {
        throw ("{0} must be PE machine 0x8664, received 0x{1:X4}: {2}" -f `
            $Label, $Machine, $Path)
    }
}

function Assert-RuntimeFileIdentity {
    param([Parameter(Mandatory = $true)][string]$Path)

    $VersionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    if ($VersionInfo.FileDescription -ne "ChatWaifu NEXT Runtime" -or
        $VersionInfo.ProductName -ne "ChatWaifu NEXT Runtime" -or
        $VersionInfo.CompanyName -ne "ChatWaifu NEXT" -or
        $VersionInfo.OriginalFilename -ne "chatwaifu-runtime.exe") {
        throw "Installed Runtime VERSIONINFO does not identify ChatWaifu NEXT: $Path"
    }
}

function Get-CurrentUserUninstallEntries {
    param([Parameter(Mandatory = $true)][string]$DisplayName)

    $Entries = @()
    $Views = @(
        [Microsoft.Win32.RegistryView]::Registry64,
        [Microsoft.Win32.RegistryView]::Registry32
    )
    foreach ($View in $Views) {
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
                        $ActualDisplayName = [string]$Entry.GetValue("DisplayName", "")
                        if ($ActualDisplayName -cne $DisplayName) {
                            continue
                        }
                        $Entries += [PSCustomObject]@{
                            RegistryView = [string]$View
                            RegistryKey = $Name
                            DisplayName = $ActualDisplayName
                            DisplayVersion = [string]$Entry.GetValue("DisplayVersion", "")
                            InstallLocation = [string]$Entry.GetValue("InstallLocation", "")
                            UninstallString = [string]$Entry.GetValue("UninstallString", "")
                            QuietUninstallString = [string]$Entry.GetValue(
                                "QuietUninstallString",
                                ""
                            )
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

    # A 64-bit process can observe the same logical entry through both views on
    # some Windows configurations. De-duplicate only byte-identical records.
    return @($Entries | Sort-Object RegistryKey, UninstallString -Unique)
}

function Split-ExecutableCommand {
    param([Parameter(Mandatory = $true)][string]$CommandLine)

    $Value = $CommandLine.Trim()
    if (-not $Value) {
        throw "The uninstall registry command is empty."
    }
    if ($Value.StartsWith('"')) {
        $ClosingQuote = $Value.IndexOf('"', 1)
        if ($ClosingQuote -lt 2) {
            throw "Malformed quoted uninstall command: $CommandLine"
        }
        return [PSCustomObject]@{
            Executable = $Value.Substring(1, $ClosingQuote - 1)
            Arguments = $Value.Substring($ClosingQuote + 1).Trim()
        }
    }

    $ExecutableEnd = $Value.IndexOf(".exe", [System.StringComparison]::OrdinalIgnoreCase)
    if ($ExecutableEnd -lt 0) {
        throw "Uninstall command does not contain an executable: $CommandLine"
    }
    $ExecutableEnd += 4
    return [PSCustomObject]@{
        Executable = $Value.Substring(0, $ExecutableEnd).Trim()
        Arguments = $Value.Substring($ExecutableEnd).Trim()
    }
}

function Get-InstalledProcesses {
    param([Parameter(Mandatory = $true)][string]$ExecutablePath)

    $ExecutableName = [System.IO.Path]::GetFileName($ExecutablePath)
    $EscapedName = $ExecutableName.Replace("'", "''")
    $Candidates = @(Get-CimInstance Win32_Process `
        -Filter "Name='$EscapedName'" -ErrorAction SilentlyContinue)
    return @($Candidates | Where-Object {
        $_.ExecutablePath -and (Test-PathEqual $_.ExecutablePath $ExecutablePath)
    })
}

function Invoke-LoopbackJson {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutMilliseconds = 2000
    )

    $Request = [System.Net.HttpWebRequest]::CreateHttp($Url)
    $Request.Method = "GET"
    $Request.Proxy = $null
    $Request.Timeout = $TimeoutMilliseconds
    $Request.ReadWriteTimeout = $TimeoutMilliseconds
    $Response = $null
    $Reader = $null
    try {
        $Response = $Request.GetResponse()
        $Reader = [System.IO.StreamReader]::new(
            $Response.GetResponseStream(),
            [System.Text.Encoding]::UTF8,
            $true
        )
        return ($Reader.ReadToEnd() | ConvertFrom-Json)
    } finally {
        if ($null -ne $Reader) {
            $Reader.Dispose()
        }
        if ($null -ne $Response) {
            $Response.Dispose()
        }
    }
}

function Get-RuntimeLogTail {
    $LogPath = Join-Path $LogRoot "runtime-sidecar.log"
    if (-not (Test-Path $LogPath -PathType Leaf)) {
        return "Runtime log does not exist at $LogPath"
    }
    return ((Get-Content $LogPath -Tail 40 -ErrorAction SilentlyContinue) -join "`n")
}

function Wait-ForInstalledRuntime {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Owner,
        [Parameter(Mandatory = $true)][string]$RuntimeExecutable,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        $Owner.Refresh()
        if ($Owner.HasExited) {
            throw "Installed desktop host exited before Runtime became healthy."
        }
        foreach ($Runtime in @(Get-InstalledProcesses $RuntimeExecutable)) {
            if ([int]$Runtime.ParentProcessId -ne $Owner.Id) {
                continue
            }
            $Listeners = @(Get-NetTCPConnection -OwningProcess $Runtime.ProcessId `
                -State Listen -ErrorAction SilentlyContinue | Where-Object {
                    $_.LocalAddress -in @("127.0.0.1", "::1")
                })
            foreach ($Listener in $Listeners) {
                $Address = if ($Listener.LocalAddress -eq "::1") {
                    "[::1]"
                } else {
                    "127.0.0.1"
                }
                $Url = "http://$Address`:$($Listener.LocalPort)"
                try {
                    $Health = Invoke-LoopbackJson "$Url/v1/runtime/health"
                    if ($Health.status -eq "ok" -and $Health.database -eq "ready") {
                        return [PSCustomObject]@{
                            ProcessId = [int]$Runtime.ProcessId
                            Port = [int]$Listener.LocalPort
                            Url = $Url
                            Health = $Health
                        }
                    }
                } catch {
                    # The listener can exist briefly before FastAPI accepts health.
                }
            }
        }
        Start-Sleep -Milliseconds 250
    }

    throw "Installed Runtime did not become healthy.`n$(Get-RuntimeLogTail)"
}

function Wait-ForProcessExit {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            return
        }
        Start-Sleep -Milliseconds 200
    }
    throw "$Label process $ProcessId remained after $TimeoutSeconds seconds."
}

function Assert-RuntimeStopped {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeExecutable,
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    Wait-ForProcessExit -ProcessId $ProcessId -TimeoutSeconds $TimeoutSeconds `
        -Label "Installed Runtime"
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        $RuntimeProcesses = @(Get-InstalledProcesses $RuntimeExecutable)
        $Listener = @(Get-NetTCPConnection -LocalPort $Port -State Listen `
            -ErrorAction SilentlyContinue)
        if ($RuntimeProcesses.Count -eq 0 -and $Listener.Count -eq 0) {
            try {
                $null = Invoke-LoopbackJson "http://127.0.0.1:$Port/v1/runtime/health" 500
            } catch {
                return
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Installed Runtime or its loopback listener remained after forced host exit."
}

function Wait-ForPathRemoval {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if (-not (Test-Path $Path)) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Silent uninstall left the installation path behind: $Path"
}

function Write-PreservationMarkers {
    $RunId = [System.Guid]::NewGuid().ToString("D")
    $Payload = [PSCustomObject]@{
        schema_version = "1.0"
        smoke_run_id = $RunId
        created_at_utc = [DateTime]::UtcNow.ToString("o")
        purpose = "Verify that ordinary NSIS uninstall retains per-user ChatWaifu data."
    } | ConvertTo-Json -Compress
    foreach ($Root in @($ConfigRoot, $DataRoot)) {
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
    }
    [System.IO.File]::WriteAllText(
        $ConfigMarker,
        $Payload,
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $DataMarker,
        $Payload,
        [System.Text.UTF8Encoding]::new($false)
    )
    return $Payload
}

$ExistingEntries = @(Get-CurrentUserUninstallEntries $ProductName)
if ($ExistingEntries.Count -ne 0) {
    throw "A current-user '$ProductName' installation already exists. Use a clean test account or uninstall it explicitly before this destructive installer smoke."
}
$ExistingHostProcesses = @(Get-Process `
    -Name ([System.IO.Path]::GetFileNameWithoutExtension($HostExecutableName)) `
    -ErrorAction SilentlyContinue)
$ExistingRuntimeProcesses = @(Get-Process -Name "chatwaifu-runtime" -ErrorAction SilentlyContinue)
if ($ExistingHostProcesses.Count -ne 0 -or $ExistingRuntimeProcesses.Count -ne 0) {
    throw "ChatWaifu host or frozen Runtime is already running; installed smoke requires an isolated process baseline."
}

try {
    Write-Host "Installing NSIS candidate silently: $ResolvedInstaller"
    $Installer = Start-Process -FilePath $ResolvedInstaller -ArgumentList "/S" `
        -Wait -PassThru
    if ($Installer.ExitCode -ne 0) {
        throw "NSIS installer exited with code $($Installer.ExitCode)."
    }
    $InstallCompleted = $true

    $Entries = @(Get-CurrentUserUninstallEntries $ProductName)
    if ($Entries.Count -ne 1) {
        throw "Expected one current-user uninstall entry for '$ProductName', found $($Entries.Count)."
    }
    $Entry = $Entries[0]
    if ($Entry.DisplayVersion -ne $ProductVersion) {
        throw "Expected installed version $ProductVersion, received $($Entry.DisplayVersion)."
    }
    $RawUninstallCommand = if ($Entry.QuietUninstallString) {
        $Entry.QuietUninstallString
    } else {
        $Entry.UninstallString
    }
    $UninstallCommand = Split-ExecutableCommand $RawUninstallCommand
    $UninstallerPath = Get-NormalizedPath $UninstallCommand.Executable
    if ($Entry.InstallLocation) {
        $InstallRoot = Get-NormalizedPath $Entry.InstallLocation
    } else {
        $InstallRoot = Get-NormalizedPath (Split-Path $UninstallerPath)
    }
    if (-not (Test-IsChildPath $InstallRoot $env:LOCALAPPDATA)) {
        throw "NSIS current-user install root is not below LOCALAPPDATA: $InstallRoot"
    }
    if (-not (Test-IsChildPath $UninstallerPath $InstallRoot)) {
        throw "Uninstaller is outside the resolved installation root: $UninstallerPath"
    }

    $HostExecutable = Join-Path $InstallRoot $HostExecutableName
    $RuntimeExecutable = Join-Path $InstallRoot $RuntimeRelativePath
    $HelperExecutable = Join-Path $InstallRoot $HelperRelativePath
    Assert-X64Pe $HostExecutable "Installed desktop host"
    Assert-X64Pe $RuntimeExecutable "Installed frozen Runtime"
    Assert-X64Pe $HelperExecutable "Installed AppContainer helper"
    Assert-RuntimeFileIdentity $RuntimeExecutable
    if (-not (Test-Path $StartMenuShortcut -PathType Leaf)) {
        throw "NSIS did not create the current-user Start Menu shortcut: $StartMenuShortcut"
    }
    $Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($StartMenuShortcut)
    if (-not (Test-PathEqual $Shortcut.TargetPath $HostExecutable)) {
        throw "Start Menu shortcut targets an unexpected executable: $($Shortcut.TargetPath)"
    }
    foreach ($RelativePath in $RequiredRuntimeResources) {
        $ResourcePath = Join-Path $InstallRoot $RelativePath
        if (-not (Test-Path $ResourcePath -PathType Leaf)) {
            throw "Installed Runtime resource is missing: $ResourcePath"
        }
    }
    Write-Host "Installed layout and x64 PE checks passed: $InstallRoot"

    $HostProcess = Start-Process -FilePath $HostExecutable -PassThru
    $Ready = Wait-ForInstalledRuntime -Owner $HostProcess `
        -RuntimeExecutable $RuntimeExecutable -TimeoutSeconds $StartupTimeoutSeconds
    $RuntimeProcessId = $Ready.ProcessId
    $RuntimePort = $Ready.Port
    Write-Host "Installed Runtime health passed: $($Ready.Url) (PID $RuntimeProcessId)"

    foreach ($Root in @($ConfigRoot, $DataRoot, $LogRoot)) {
        if (-not (Test-Path $Root -PathType Container)) {
            throw "Installed host did not create the expected Tauri user root: $Root"
        }
    }
    $DatabasePath = Join-Path $DataRoot "chatwaifu.db"
    if (-not (Test-Path $DatabasePath -PathType Leaf)) {
        throw "Installed Runtime did not create SQLite under app_local_data_dir: $DatabasePath"
    }
    $MarkerPayload = Write-PreservationMarkers

    Write-Host "Forcing desktop host PID $($HostProcess.Id) to validate parent cleanup."
    Stop-Process -Id $HostProcess.Id -Force
    Wait-ForProcessExit -ProcessId $HostProcess.Id `
        -TimeoutSeconds $CleanupTimeoutSeconds -Label "Desktop host"
    Assert-RuntimeStopped -RuntimeExecutable $RuntimeExecutable `
        -ProcessId $RuntimeProcessId -Port $RuntimePort `
        -TimeoutSeconds $CleanupTimeoutSeconds
    $HostProcess = $null
    Write-Host "Forced-host Runtime cleanup passed."

    if (-not (Test-Path $UninstallerPath -PathType Leaf)) {
        throw "Registered uninstaller does not exist: $UninstallerPath"
    }
    $UninstallArguments = $UninstallCommand.Arguments
    if ($UninstallArguments -notmatch '(?i)(^|\s)/S(\s|$)') {
        $UninstallArguments = ("$UninstallArguments /S").Trim()
    }
    Write-Host "Uninstalling current-user candidate silently."
    $Uninstaller = Start-Process -FilePath $UninstallerPath `
        -ArgumentList $UninstallArguments -Wait -PassThru
    if ($Uninstaller.ExitCode -ne 0) {
        throw "NSIS uninstaller exited with code $($Uninstaller.ExitCode)."
    }
    Wait-ForPathRemoval -Path $InstallRoot -TimeoutSeconds $CleanupTimeoutSeconds
    $RemainingEntries = @(Get-CurrentUserUninstallEntries $ProductName)
    if ($RemainingEntries.Count -ne 0) {
        throw "Silent uninstall left a current-user uninstall registry entry behind."
    }
    if (Test-Path $StartMenuShortcut) {
        throw "Silent uninstall left the Start Menu shortcut behind: $StartMenuShortcut"
    }

    foreach ($Root in @($ConfigRoot, $DataRoot, $LogRoot)) {
        if (-not (Test-Path $Root -PathType Container)) {
            throw "Silent uninstall removed a per-user ChatWaifu directory: $Root"
        }
    }
    foreach ($Marker in @($ConfigMarker, $DataMarker)) {
        if (-not (Test-Path $Marker -PathType Leaf)) {
            throw "Silent uninstall removed the preservation marker: $Marker"
        }
        if ((Get-Content $Marker -Raw) -cne $MarkerPayload) {
            throw "Silent uninstall changed the preservation marker: $Marker"
        }
    }
    $UninstallCompleted = $true

    Remove-Item -Path $ConfigMarker, $DataMarker -Force

    Write-Host "Installed Windows x64 smoke passed."
    Write-Host "User data was retained; the two test-owned markers were removed after verification."
} finally {
    if ($null -ne $HostProcess) {
        $HostProcess.Refresh()
        if (-not $HostProcess.HasExited) {
            Stop-Process -Id $HostProcess.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if ($null -ne $InstallRoot) {
        $RuntimeExecutable = Join-Path $InstallRoot $RuntimeRelativePath
        foreach ($Runtime in @(Get-InstalledProcesses $RuntimeExecutable)) {
            Stop-Process -Id $Runtime.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    if ($InstallCompleted -and -not $UninstallCompleted) {
        Write-Warning "Installed smoke stopped before verified uninstall. The script did not delete the installation or any AppData; inspect and uninstall '$ProductName' explicitly."
    }
}
