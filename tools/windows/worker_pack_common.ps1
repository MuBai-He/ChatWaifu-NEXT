# Shared, source-only helpers for Windows x64 Worker Pack builders.

$script:WorkerPackCommonRoot = $PSScriptRoot

function Invoke-WorkerPackChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    $PreviousBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE
    try {
        $env:PYTHONDONTWRITEBYTECODE = "1"
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            throw "$FilePath exited with code $LASTEXITCODE"
        }
    } finally {
        if ($null -eq $PreviousBytecodeSetting) {
            Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONDONTWRITEBYTECODE = $PreviousBytecodeSetting
        }
    }
}

function Get-WorkerPackPythonPlatform {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return ""
    }
    $PreviousPreference = $ErrorActionPreference
    $ExitCode = -1
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $Platform = & $PythonPath -I -c "import sysconfig; print(sysconfig.get_platform())" 2>$null
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    if ($ExitCode -ne 0 -or $null -eq $Platform) {
        return ""
    }
    return ([string]$Platform).Trim()
}

function Get-WorkerPackPeMachine {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Stream = [System.IO.File]::OpenRead($Path)
    $Reader = [System.IO.BinaryReader]::new($Stream)
    try {
        if ($Stream.Length -lt 64) {
            throw "Not a PE executable: $Path"
        }
        $Stream.Position = 0x3c
        $PeOffset = $Reader.ReadInt32()
        if ($PeOffset -lt 0 -or $PeOffset + 6 -gt $Stream.Length) {
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

function Assert-WorkerPackX64Pe {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected worker pack executable was not produced: $Path"
    }
    $Machine = Get-WorkerPackPeMachine $Path
    if ($Machine -ne 0x8664) {
        throw ("Expected PE machine 0x8664 (x64), received 0x{0:X4}: {1}" -f $Machine, $Path)
    }
}

function Assert-WorkerPackPayloadX64 {
    param([Parameter(Mandatory = $true)][string]$PayloadRoot)

    $NativeFiles = @(Get-ChildItem -LiteralPath $PayloadRoot -File -Recurse | Where-Object {
        $_.Extension.ToLowerInvariant() -in @(".exe", ".dll", ".pyd")
    })
    if ($NativeFiles.Count -eq 0) {
        throw "Worker Pack payload contains no native x64 files: $PayloadRoot"
    }
    foreach ($NativeFile in $NativeFiles) {
        Assert-WorkerPackX64Pe $NativeFile.FullName
    }
    Write-Host "Verified $($NativeFiles.Count) native payload files as PE machine 0x8664."
}

function Assert-WorkerPackPayloadHasNoBuildPaths {
    param(
        [Parameter(Mandatory = $true)][string]$PayloadRoot,
        [Parameter(Mandatory = $true)][string]$ScannerPython,
        [Parameter(Mandatory = $true)][string[]]$ForbiddenPaths
    )

    $Scanner = Join-Path $script:WorkerPackCommonRoot "scan_worker_pack_payload.py"
    if (-not (Test-Path -LiteralPath $Scanner -PathType Leaf)) {
        throw "Worker Pack payload path scanner is missing: $Scanner"
    }
    $Arguments = @("-I", $Scanner, "--root", $PayloadRoot)
    foreach ($ForbiddenPath in $ForbiddenPaths) {
        $Arguments += @("--forbidden-path", $ForbiddenPath)
    }
    Invoke-WorkerPackChecked $ScannerPython $Arguments
}

function Assert-WorkerPackPathUnderRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $Separators = [char[]]@(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $FullRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd($Separators)
    $Prefix = $FullRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $FullPath.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to mutate a path outside the worker-pack build root: $FullPath"
    }
}

function Assert-WorkerPackPathOutsideRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $Separators = [char[]]@(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $FullRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd($Separators)
    $Prefix = $FullRoot + [System.IO.Path]::DirectorySeparatorChar
    if (
        $FullPath.Equals($FullRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $FullPath.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Worker Pack output must be outside its disposable build root: $FullPath"
    }
}

function Assert-WorkerPackSemanticVersion {
    param([Parameter(Mandatory = $true)][string]$Version)

    $Pattern = (
        '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)' +
        '(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?' +
        '(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$'
    )
    if ($Version -notmatch $Pattern) {
        throw "Worker Pack version must be semantic versioning 2.0.0: $Version"
    }
}

function Reset-WorkerPackDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$AllowedRoot
    )

    Assert-WorkerPackPathUnderRoot -Path $Path -Root $AllowedRoot
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function New-WorkerPackPortablePython {
    param(
        [Parameter(Mandatory = $true)][string]$Uv,
        [Parameter(Mandatory = $true)][string]$PythonRequest,
        [Parameter(Mandatory = $true)][string]$WorkRoot,
        [Parameter(Mandatory = $true)][string]$PayloadRoot
    )

    $InstallRoot = Join-Path $WorkRoot "uv-python"
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    $PreviousInstallRoot = $env:UV_PYTHON_INSTALL_DIR
    try {
        $env:UV_PYTHON_INSTALL_DIR = $InstallRoot
        Invoke-WorkerPackChecked $Uv @(
            "python", "install", $PythonRequest, "--no-bin", "--no-registry"
        )
    } finally {
        if ($null -eq $PreviousInstallRoot) {
            Remove-Item Env:UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue
        } else {
            $env:UV_PYTHON_INSTALL_DIR = $PreviousInstallRoot
        }
    }
    $InstalledRoot = Join-Path $InstallRoot $PythonRequest
    $InstalledPython = Join-Path $InstalledRoot "python.exe"
    if ((Get-WorkerPackPythonPlatform $InstalledPython) -ne "win-amd64") {
        throw "uv did not materialize the requested win-amd64 portable Python."
    }
    $PortableRoot = Join-Path $PayloadRoot "python"
    Move-Item -LiteralPath $InstalledRoot -Destination $PortableRoot
    $PortablePython = Join-Path $PortableRoot "python.exe"
    Assert-WorkerPackX64Pe $PortablePython
    if ((Get-WorkerPackPythonPlatform $PortablePython) -ne "win-amd64") {
        throw "Relocated worker pack Python is not win-amd64."
    }
    return $PortablePython
}

function Copy-WorkerPackModelTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $ResolvedSource = (Resolve-Path -LiteralPath $Source).Path
    if (-not (Test-Path -LiteralPath $ResolvedSource -PathType Container)) {
        throw "Worker model source must be a directory: $Source"
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($Entry in @(Get-ChildItem -LiteralPath $ResolvedSource -Force)) {
        if ($Entry.Name -in @(".cache", ".git", ".idea", ".gitattributes")) {
            continue
        }
        Copy-Item -LiteralPath $Entry.FullName -Destination $Destination -Recurse -Force
    }
    foreach ($PrivateDirectory in @(Get-ChildItem -LiteralPath $Destination -Directory -Force -Recurse | Where-Object {
        $_.Name -in @(".cache", ".git", ".idea")
    })) {
        Remove-Item -LiteralPath $PrivateDirectory.FullName -Recurse -Force
    }
}

function Remove-WorkerPackBuilderMetadata {
    param([Parameter(Mandatory = $true)][string]$PortablePythonRoot)

    foreach ($DirectUrl in @(Get-ChildItem -LiteralPath $PortablePythonRoot -Filter "direct_url.json" -File -Recurse)) {
        Remove-Item -LiteralPath $DirectUrl.FullName -Force
    }
    foreach ($Bytecode in @(Get-ChildItem -LiteralPath $PortablePythonRoot -File -Recurse | Where-Object {
        $_.Extension.ToLowerInvariant() -in @(".pyc", ".pyo")
    })) {
        Remove-Item -LiteralPath $Bytecode.FullName -Force
    }
    foreach ($CacheDirectory in @(Get-ChildItem -LiteralPath $PortablePythonRoot -Directory -Filter "__pycache__" -Recurse | Sort-Object { $_.FullName.Length } -Descending)) {
        Remove-Item -LiteralPath $CacheDirectory.FullName -Recurse -Force
    }
}

function Remove-WorkerPackPackagingTools {
    param([Parameter(Mandatory = $true)][string]$PortablePythonRoot)

    # The embedded interpreter is an application runtime, not a development environment.
    # Besides reducing the attack surface, removing pip also removes distlib's bundled
    # cross-architecture t32.exe/w32.exe launchers before the strict x64 payload audit.
    $SitePackagesRoot = Join-Path $PortablePythonRoot "Lib\site-packages"
    if (Test-Path -LiteralPath $SitePackagesRoot -PathType Container) {
        foreach ($Entry in @(Get-ChildItem -LiteralPath $SitePackagesRoot -Force | Where-Object {
            $_.Name -ieq "pip" -or $_.Name -match '^pip-.*\.dist-info$'
        })) {
            Remove-Item -LiteralPath $Entry.FullName -Recurse -Force
        }
    }

    # Worker manifests launch the relocated interpreter directly with `-I -m`.
    # Every console script is therefore builder-only. uv/distlib launchers embed the
    # staging interpreter path in both the PE launcher and its shebang, while scripts
    # without an extension can carry the same leak, so remove the directory as a unit.
    $ScriptsRoot = Join-Path $PortablePythonRoot "Scripts"
    if (Test-Path -LiteralPath $ScriptsRoot -PathType Container) {
        Remove-Item -LiteralPath $ScriptsRoot -Recurse -Force
    }

    # Some runtime dependencies still import setuptools' Python modules, so retain those.
    # Its cli/gui executables are installer launcher templates (x86/x64/ARM), never runtime
    # entrypoints for a frozen Worker Pack, and would otherwise violate the x64-only payload.
    $SetuptoolsRoot = Join-Path $SitePackagesRoot "setuptools"
    if (Test-Path -LiteralPath $SetuptoolsRoot -PathType Container) {
        foreach ($Launcher in @(Get-ChildItem -LiteralPath $SetuptoolsRoot -File -Filter "*.exe" -Recurse)) {
            Remove-Item -LiteralPath $Launcher.FullName -Force
        }
    }
}

function Write-WorkerPackJson {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $Parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    $Json = $Value | ConvertTo-Json -Depth 16
    [System.IO.File]::WriteAllText($Path, $Json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

function Write-WorkerPackChecksum {
    param([Parameter(Mandatory = $true)][string]$ArchivePath)

    $Digest = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $ChecksumPath = "$ArchivePath.sha256"
    [System.IO.File]::WriteAllText(
        $ChecksumPath,
        "$Digest  $([System.IO.Path]::GetFileName($ArchivePath))`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    return $ChecksumPath
}
