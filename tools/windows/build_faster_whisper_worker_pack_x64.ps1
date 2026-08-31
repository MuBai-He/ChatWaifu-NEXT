param(
    [string]$ModelSource = "",
    [string]$SmokeWav = "",
    [string]$PackVersion = "0.1.0",
    [string]$OutputDirectory = "dist\windows\worker-packs",
    [string]$PythonRequest = "cpython-3.12.10-windows-x86_64-none",
    [string]$ModelRepository = "Systran/faster-whisper-base",
    [string]$ModelRevision = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    [int]$SmokeTimeoutSeconds = 600,
    [switch]$SkipModelSmoke,
    [switch]$KeepStaging
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "faster-whisper Windows worker packs must be built on Windows."
}

. (Join-Path $PSScriptRoot "worker_pack_common.ps1")

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\worker-packs"
$WorkRoot = Join-Path $BuildRoot "faster-whisper-base-cpu-int8"
$StagingRoot = Join-Path $WorkRoot "staging"
$PayloadRoot = Join-Path $StagingRoot "payload"
$ManifestTemplate = Join-Path $WorkRoot "manifest-template.json"
$CandidateArchive = Join-Path $WorkRoot "candidate.cwpack"
$SmokeRoot = Join-Path $WorkRoot "smoke"
$BuilderPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$WorkerPackTool = Join-Path $RepoRoot "tools\worker_packs.py"
$SmokeTool = Join-Path $RepoRoot "tools\windows\smoke_worker_pack.py"
$Uv = (Get-Command uv -ErrorAction Stop).Source
$ResolvedOutputDirectory = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $OutputDirectory))
}
$OutputArchive = Join-Path $ResolvedOutputDirectory (
    "chatwaifu-faster-whisper-base-cpu-int8-$PackVersion.cwpack"
)

Assert-WorkerPackSemanticVersion -Version $PackVersion
Assert-WorkerPackPathOutsideRoot -Path $OutputArchive -Root $WorkRoot
if (Test-Path -LiteralPath $OutputArchive) {
    throw "Worker Pack version already exists; choose a new -PackVersion: $OutputArchive"
}
if ((Get-WorkerPackPythonPlatform $BuilderPython) -ne "win-amd64") {
    throw "Missing win-amd64 builder environment. Run tools/windows/bootstrap_x64.ps1 first."
}
if (-not $SkipModelSmoke) {
    if (-not $SmokeWav) {
        throw "A real PCM16 speech WAV is required unless -SkipModelSmoke is explicit."
    }
    $ResolvedSmokeWav = (Resolve-Path -LiteralPath $SmokeWav).Path
    if (-not (Test-Path -LiteralPath $ResolvedSmokeWav -PathType Leaf)) {
        throw "Whisper smoke WAV does not exist: $SmokeWav"
    }
} else {
    $ResolvedSmokeWav = ""
}

Reset-WorkerPackDirectory -Path $WorkRoot -AllowedRoot $BuildRoot
New-Item -ItemType Directory -Path $PayloadRoot -Force | Out-Null

try {
    $PortablePython = New-WorkerPackPortablePython `
        -Uv $Uv `
        -PythonRequest $PythonRequest `
        -WorkRoot $WorkRoot `
        -PayloadRoot $PayloadRoot
    $PortablePythonRoot = Split-Path -Parent $PortablePython

    Invoke-WorkerPackChecked $Uv @(
        "pip", "install",
        "--python", $PortablePython,
        (Join-Path $RepoRoot "packages\model-worker-sdk-python"),
        (Join-Path $RepoRoot "workers\asr-faster-whisper")
    )
    Remove-WorkerPackBuilderMetadata -PortablePythonRoot $PortablePythonRoot
    Invoke-WorkerPackChecked $PortablePython @(
        "-I", "-c",
        ('import sysconfig, av, ctranslate2, faster_whisper; ' +
         'assert sysconfig.get_platform() == "win-amd64"; ' +
         'print(faster_whisper.__version__, ctranslate2.__version__, av.__version__)')
    )

    $PackModelRoot = Join-Path $PayloadRoot "models\default"
    if ($ModelSource) {
        $ResolvedModelSource = (Resolve-Path -LiteralPath $ModelSource).Path
        Copy-WorkerPackModelTree -Source $ResolvedModelSource -Destination $PackModelRoot
        $Materialization = "user-supplied-local-directory"
    } else {
        New-Item -ItemType Directory -Path $PackModelRoot -Force | Out-Null
        Invoke-WorkerPackChecked $PortablePython @(
            "-I",
            (Join-Path $RepoRoot "tools\windows\materialize_huggingface_model.py"),
            "--repo-id", $ModelRepository,
            "--revision", $ModelRevision,
            "--output", $PackModelRoot,
            "--required-file", "config.json",
            "--required-file", "model.bin",
            "--required-file", "tokenizer.json",
            "--required-file", "vocabulary.txt"
        )
        $Materialization = "$ModelRepository@$ModelRevision"
    }
    foreach ($RequiredModelFile in @(
        "config.json", "model.bin", "tokenizer.json", "vocabulary.txt"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $PackModelRoot $RequiredModelFile) -PathType Leaf)) {
            throw "faster-whisper model is missing required payload: $RequiredModelFile"
        }
    }

    $MetadataRoot = Join-Path $PayloadRoot "metadata"
    New-Item -ItemType Directory -Path $MetadataRoot -Force | Out-Null
    Invoke-WorkerPackChecked $PortablePython @(
        "-I",
        (Join-Path $RepoRoot "tools\windows\write_python_inventory.py"),
        "--output", (Join-Path $MetadataRoot "python-packages.json")
    )
    Write-WorkerPackJson -Path (Join-Path $MetadataRoot "build.json") -Value ([ordered]@{
        schema_version = "1.0"
        backend = "faster-whisper"
        faster_whisper_version = "1.2.1"
        compute_type = "int8"
        model_source = $Materialization
        model_repository = $ModelRepository
        model_revision = if ($ModelSource) { $null } else { $ModelRevision }
        redistribution = "license review required"
    })

    Remove-WorkerPackBuilderMetadata -PortablePythonRoot $PortablePythonRoot

    $Manifest = [ordered]@{
        schema_version = "1.0"
        pack_id = "chatwaifu.faster-whisper.base.cpu-int8"
        version = $PackVersion
        platform = [ordered]@{
            os = "windows"
            architecture = "x86_64"
            accelerator = "cpu"
            python_abi = "cp312"
        }
        worker = [ordered]@{
            kind = "stt"
            backend = "faster-whisper"
            provider_id = "faster-whisper"
            display_name = "faster-whisper Base · CPU int8"
            model = "faster-whisper-base"
            entrypoint = [ordered]@{
                executable = "payload/python/python.exe"
                arguments = @("-I", "-m", "chatwaifu_asr_worker.main")
                working_directory = "."
                environment = [ordered]@{
                    CHATWAIFU_STT_WORKER_WORKER_ID = "asr-faster-whisper-base-cpu-int8"
                    CHATWAIFU_STT_WORKER_PROVIDER_ID = "faster-whisper"
                    CHATWAIFU_STT_WORKER_DISPLAY_NAME = "faster-whisper Base · CPU int8"
                    CHATWAIFU_STT_WORKER_MODEL = "base"
                    CHATWAIFU_STT_WORKER_MODEL_DIR = '${PACK_ROOT}/payload/models/default'
                    CHATWAIFU_STT_WORKER_LOCAL_FILES_ONLY = "true"
                    CHATWAIFU_STT_WORKER_DEVICE = "cpu"
                    CHATWAIFU_STT_WORKER_COMPUTE_TYPE = "int8"
                    CHATWAIFU_STT_WORKER_PRELOAD = "true"
                }
                health_path = "/v1/health"
                capabilities_path = "/v1/capabilities"
                startup_timeout_seconds = 300
                shutdown_timeout_seconds = 30
            }
        }
        licenses = @(
            [ordered]@{
                name = "faster-whisper"
                spdx_id = "MIT"
                url = "https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE"
            },
            [ordered]@{
                name = "Systran faster-whisper-base model"
                spdx_id = "CC-BY-4.0"
                url = "https://huggingface.co/Systran/faster-whisper-base"
            }
        )
    }
    Write-WorkerPackJson -Path $ManifestTemplate -Value $Manifest

    Assert-WorkerPackPayloadX64 -PayloadRoot $PayloadRoot

    Invoke-WorkerPackChecked $BuilderPython @(
        $WorkerPackTool, "build",
        "--staging", $StagingRoot,
        "--manifest-template", $ManifestTemplate,
        "--output", $CandidateArchive
    )
    Invoke-WorkerPackChecked $BuilderPython @(
        $WorkerPackTool, "verify", $CandidateArchive
    )

    if ($SkipModelSmoke) {
        Write-Warning "faster-whisper model smoke was explicitly skipped; this pack is unverified."
    } else {
        Invoke-WorkerPackChecked $BuilderPython @(
            $SmokeTool,
            "--archive", $CandidateArchive,
            "--kind", "stt",
            "--timeout", $SmokeTimeoutSeconds.ToString(),
            "--smoke-wav", $ResolvedSmokeWav,
            "--output-directory", $SmokeRoot
        )
    }

    New-Item -ItemType Directory -Path $ResolvedOutputDirectory -Force | Out-Null
    Move-Item -LiteralPath $CandidateArchive -Destination $OutputArchive
    $ChecksumPath = Write-WorkerPackChecksum -ArchivePath $OutputArchive
    Write-Host "faster-whisper Windows x64 Worker Pack: $OutputArchive"
    Write-Host "SHA-256: $ChecksumPath"
    if (-not $SkipModelSmoke) {
        $PublishedSmokeRoot = Join-Path $ResolvedOutputDirectory (
            "smoke\chatwaifu-faster-whisper-base-cpu-int8-$PackVersion"
        )
        if (Test-Path -LiteralPath $PublishedSmokeRoot) {
            Remove-Item -LiteralPath $PublishedSmokeRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $PublishedSmokeRoot) -Force | Out-Null
        Move-Item -LiteralPath $SmokeRoot -Destination $PublishedSmokeRoot
        Write-Host "Whisper smoke result: $PublishedSmokeRoot"
    }
} finally {
    if (-not $KeepStaging -and (Test-Path -LiteralPath $WorkRoot)) {
        Assert-WorkerPackPathUnderRoot -Path $WorkRoot -Root $BuildRoot
        Remove-Item -LiteralPath $WorkRoot -Recurse -Force
    }
}
