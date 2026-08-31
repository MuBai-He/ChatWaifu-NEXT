param(
    [Parameter(Mandatory = $true)][string]$ModelSource,
    [string]$Voice = "ayachi_nene_local",
    [string]$PackVersion = "0.1.0",
    [string]$OutputDirectory = "dist\windows\worker-packs",
    [string]$PythonRequest = "cpython-3.12.10-windows-x86_64-none",
    [string]$QwenCommit = "022e286b98fbec7e1e916cb940cdf532cd9f488e",
    [string]$TorchVersion = "2.7.1",
    [string]$CudaVariant = "cu126",
    [int]$SmokeTimeoutSeconds = 1800,
    [switch]$SkipModelSmoke,
    [switch]$KeepStaging
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Qwen3-TTS Windows worker packs must be built on Windows."
}

. (Join-Path $PSScriptRoot "worker_pack_common.ps1")

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\worker-packs"
$WorkRoot = Join-Path $BuildRoot "qwen3-tts-torch-$CudaVariant"
$StagingRoot = Join-Path $WorkRoot "staging"
$PayloadRoot = Join-Path $StagingRoot "payload"
$ManifestTemplate = Join-Path $WorkRoot "manifest-template.json"
$CandidateArchive = Join-Path $WorkRoot "candidate.cwpack"
$SmokeRoot = Join-Path $WorkRoot "smoke"
$BuilderPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$WorkerPackTool = Join-Path $RepoRoot "tools\worker_packs.py"
$SmokeTool = Join-Path $RepoRoot "tools\windows\smoke_worker_pack.py"
$Uv = (Get-Command uv -ErrorAction Stop).Source
$ResolvedModelSource = (Resolve-Path -LiteralPath $ModelSource).Path
$ResolvedOutputDirectory = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $OutputDirectory))
}
$OutputArchive = Join-Path $ResolvedOutputDirectory (
    "chatwaifu-qwen3-tts-nene-$CudaVariant-$PackVersion.cwpack"
)
$QwenArchive = (
    "https://github.com/QwenLM/Qwen3-TTS/archive/" + $QwenCommit + ".zip"
)
$TorchIndex = "https://download.pytorch.org/whl/$CudaVariant"
$CudaProbePath = Join-Path $WorkRoot "cuda-probe.json"

if ($CudaVariant -ne "cu126") {
    throw "This first Qwen3-TTS pack profile is pinned to cu126."
}
Assert-WorkerPackSemanticVersion -Version $PackVersion
Assert-WorkerPackPathOutsideRoot -Path $OutputArchive -Root $WorkRoot
if (Test-Path -LiteralPath $OutputArchive) {
    throw "Worker Pack version already exists; choose a new -PackVersion: $OutputArchive"
}
if ((Get-WorkerPackPythonPlatform $BuilderPython) -ne "win-amd64") {
    throw "Missing win-amd64 builder environment. Run tools/windows/bootstrap_x64.ps1 first."
}
if (-not (Test-Path -LiteralPath $ResolvedModelSource -PathType Container)) {
    throw "Qwen3-TTS checkpoint must be a directory: $ResolvedModelSource"
}
$ModelConfigPath = Join-Path $ResolvedModelSource "config.json"
if (-not (Test-Path -LiteralPath $ModelConfigPath -PathType Leaf)) {
    throw "Qwen3-TTS checkpoint is missing config.json: $ResolvedModelSource"
}
$ModelConfig = Get-Content -LiteralPath $ModelConfigPath -Raw | ConvertFrom-Json
if ($ModelConfig.model_type -ne "qwen3_tts" -or $ModelConfig.tts_model_type -ne "custom_voice") {
    throw "Expected a Qwen3-TTS custom_voice checkpoint."
}
$SpeakerNames = @($ModelConfig.talker_config.spk_id.PSObject.Properties.Name)
if ($Voice -notin $SpeakerNames) {
    throw "Qwen3-TTS checkpoint does not contain speaker '$Voice': $($SpeakerNames -join ', ')"
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
        "--break-system-packages",
        "--python", $PortablePython,
        "--index-url", $TorchIndex,
        "torch==$TorchVersion+$CudaVariant",
        "torchaudio==$TorchVersion+$CudaVariant"
    )
    Invoke-WorkerPackChecked $Uv @(
        "pip", "install",
        "--break-system-packages",
        "--python", $PortablePython,
        $QwenArchive,
        (Join-Path $RepoRoot "packages\model-worker-sdk-python"),
        (Join-Path $RepoRoot "workers\tts-neural")
    )
    Remove-WorkerPackBuilderMetadata -PortablePythonRoot $PortablePythonRoot

    Invoke-WorkerPackChecked $PortablePython @(
        "-I", "-c",
        ('import sysconfig, torch, torchaudio, qwen_tts; ' +
         'assert sysconfig.get_platform() == "win-amd64"; ' +
         'assert torch.version.cuda == "12.6"; ' +
         'print(torch.__version__, torchaudio.__version__, qwen_tts.__file__)')
    )
    if (-not $SkipModelSmoke) {
        Invoke-WorkerPackChecked $PortablePython @(
            "-I", "-c",
            ('import json, pathlib, sys, torch; ' +
             'assert torch.cuda.is_available(), "CUDA is unavailable"; ' +
             'torch.cuda.set_device(0); ' +
             'probe_tensor = torch.ones(1, device="cuda:0") + 1; ' +
             'assert probe_tensor.item() == 2; ' +
             'free_bytes, total_bytes = torch.cuda.mem_get_info(0); ' +
             'properties = torch.cuda.get_device_properties(0); ' +
             'payload = {' +
             '"schema_version": "1.0", ' +
             '"torch_version": str(torch.__version__), ' +
             '"torch_cuda_version": str(torch.version.cuda), ' +
             '"cudnn_version": torch.backends.cudnn.version(), ' +
             '"cuda_available": True, ' +
             '"device_index": 0, ' +
             '"device": "cuda:0", ' +
             '"tensor_device": str(probe_tensor.device), ' +
             '"gpu_name": properties.name, ' +
             '"compute_capability": list(torch.cuda.get_device_capability(0)), ' +
             '"total_memory_bytes": int(properties.total_memory), ' +
             '"free_memory_bytes": int(free_bytes), ' +
             '"driver_visible_memory_bytes": int(total_bytes)}; ' +
             'pathlib.Path(sys.argv[1]).write_text(' +
             'json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); ' +
             'print(json.dumps(payload, ensure_ascii=False))'),
            $CudaProbePath
        )
    }

    $PackModelRoot = Join-Path $PayloadRoot "models\default"
    Copy-WorkerPackModelTree -Source $ResolvedModelSource -Destination $PackModelRoot
    foreach ($RequiredModelFile in @(
        "config.json", "model.safetensors", "tokenizer_config.json", "speech_tokenizer"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $PackModelRoot $RequiredModelFile))) {
            throw "Qwen3-TTS checkpoint is missing required payload: $RequiredModelFile"
        }
    }

    $MetadataRoot = Join-Path $PayloadRoot "metadata"
    New-Item -ItemType Directory -Path $MetadataRoot -Force | Out-Null
    Invoke-WorkerPackChecked $PortablePython @(
        "-I",
        (Join-Path $RepoRoot "tools\windows\write_python_inventory.py"),
        "--output", (Join-Path $MetadataRoot "python-packages.json")
    )
    if (-not $SkipModelSmoke) {
        if (-not (Test-Path -LiteralPath $CudaProbePath -PathType Leaf)) {
            throw "CUDA probe did not produce structured evidence: $CudaProbePath"
        }
        Copy-Item -LiteralPath $CudaProbePath -Destination (
            Join-Path $MetadataRoot "cuda-probe.json"
        )
    }
    Write-WorkerPackJson -Path (Join-Path $MetadataRoot "build.json") -Value ([ordered]@{
        schema_version = "1.0"
        qwen_upstream_commit = $QwenCommit
        torch_version = $TorchVersion
        cuda_wheel = $CudaVariant
        checkpoint_source = "user-supplied-local-directory"
        voice = $Voice
        redistribution = "owner-only; license review required"
    })

    Remove-WorkerPackBuilderMetadata -PortablePythonRoot $PortablePythonRoot

    $Manifest = [ordered]@{
        schema_version = "1.0"
        pack_id = "chatwaifu.qwen3-tts.nene.$CudaVariant"
        version = $PackVersion
        platform = [ordered]@{
            os = "windows"
            architecture = "x86_64"
            accelerator = "cuda"
            accelerator_version = $CudaVariant.Substring(2, 2) + "." + $CudaVariant.Substring(4)
            python_abi = "cp312"
        }
        worker = [ordered]@{
            kind = "tts"
            backend = "qwen3_tts_torch"
            provider_id = "qwen3_tts_torch"
            display_name = "Qwen3-TTS · 宁宁 · CUDA"
            model = "Qwen3-TTS-12Hz-0.6B-Nene"
            entrypoint = [ordered]@{
                executable = "payload/python/python.exe"
                arguments = @("-I", "-m", "chatwaifu_tts_neural_worker.main")
                working_directory = "."
                environment = [ordered]@{
                    CHATWAIFU_NEURAL_TTS_WORKER_BACKEND = "qwen3_tts_torch"
                    CHATWAIFU_NEURAL_TTS_WORKER_PROVIDER_ID = "qwen3_tts_torch"
                    CHATWAIFU_NEURAL_TTS_WORKER_DISPLAY_NAME = "Qwen3-TTS · 宁宁 · CUDA"
                    CHATWAIFU_NEURAL_TTS_WORKER_WORKER_ID = "tts-qwen3-torch-cuda"
                    CHATWAIFU_NEURAL_TTS_WORKER_MODEL = "Qwen3-TTS-12Hz-0.6B-Nene"
                    CHATWAIFU_NEURAL_TTS_WORKER_MODEL_DIR = '${PACK_ROOT}/payload/models/default'
                    CHATWAIFU_NEURAL_TTS_WORKER_QWEN_VOICE = $Voice
                    CHATWAIFU_NEURAL_TTS_WORKER_QWEN_ATTN_IMPLEMENTATION = "sdpa"
                    CHATWAIFU_NEURAL_TTS_WORKER_QWEN_DTYPE = "auto"
                    CHATWAIFU_NEURAL_TTS_WORKER_DEVICE = "cuda:0"
                    CHATWAIFU_NEURAL_TTS_WORKER_PRELOAD = "false"
                }
                health_path = "/v1/health"
                capabilities_path = "/v1/capabilities"
                startup_timeout_seconds = 300
                shutdown_timeout_seconds = 30
            }
        }
        licenses = @(
            [ordered]@{
                name = "Qwen3-TTS"
                spdx_id = "Apache-2.0"
                url = "https://github.com/QwenLM/Qwen3-TTS/blob/$QwenCommit/LICENSE"
            },
            [ordered]@{
                name = "Ayachi Nene local fine-tuned checkpoint"
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
        Write-Warning "Qwen model synthesis smoke was explicitly skipped; this pack is unverified."
    } else {
        Invoke-WorkerPackChecked $BuilderPython @(
            $SmokeTool,
            "--archive", $CandidateArchive,
            "--kind", "tts",
            "--timeout", $SmokeTimeoutSeconds.ToString(),
            "--output-directory", $SmokeRoot
        )
    }

    New-Item -ItemType Directory -Path $ResolvedOutputDirectory -Force | Out-Null
    Move-Item -LiteralPath $CandidateArchive -Destination $OutputArchive
    $ChecksumPath = Write-WorkerPackChecksum -ArchivePath $OutputArchive
    Write-Host "Qwen3-TTS Windows x64 Worker Pack: $OutputArchive"
    Write-Host "SHA-256: $ChecksumPath"
    if (-not $SkipModelSmoke) {
        $PublishedSmokeRoot = Join-Path $ResolvedOutputDirectory (
            "smoke\chatwaifu-qwen3-tts-nene-$CudaVariant-$PackVersion"
        )
        if (Test-Path -LiteralPath $PublishedSmokeRoot) {
            Remove-Item -LiteralPath $PublishedSmokeRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $PublishedSmokeRoot) -Force | Out-Null
        Move-Item -LiteralPath $SmokeRoot -Destination $PublishedSmokeRoot
        Write-Host "Chinese/Japanese smoke WAVs: $PublishedSmokeRoot"
    }
} finally {
    if (-not $KeepStaging -and (Test-Path -LiteralPath $WorkRoot)) {
        Assert-WorkerPackPathUnderRoot -Path $WorkRoot -Root $BuildRoot
        Remove-Item -LiteralPath $WorkRoot -Recurse -Force
    }
}
