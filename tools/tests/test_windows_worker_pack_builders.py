from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINDOWS_TOOLS = ROOT / "tools" / "windows"


def _script(name: str) -> str:
    return (WINDOWS_TOOLS / name).read_text(encoding="utf-8")


def test_windows_builders_use_one_canonical_worker_pack_contract() -> None:
    for name in (
        "build_faster_whisper_worker_pack_x64.ps1",
        "build_qwen3_tts_worker_pack_x64.ps1",
    ):
        script = _script(name)
        assert 'Join-Path $RepoRoot "tools\\worker_packs.py"' in script
        assert '$WorkerPackTool, "build"' in script
        assert '$WorkerPackTool, "verify"' in script
        assert "worker_pack_archive.py" not in script
        assert "Assert-WorkerPackPayloadX64" in script
        assert "Assert-WorkerPackSemanticVersion" in script
        assert "CHATWAIFU_STT_WORKER_HOST" not in script
        assert "CHATWAIFU_STT_WORKER_PORT" not in script
        assert "CHATWAIFU_STT_WORKER_TOKEN" not in script
        assert "CHATWAIFU_NEURAL_TTS_WORKER_HOST" not in script
        assert "CHATWAIFU_NEURAL_TTS_WORKER_PORT" not in script
        assert "CHATWAIFU_NEURAL_TTS_WORKER_TOKEN" not in script


def test_qwen_pack_is_pinned_cuda_126_and_smokes_both_languages() -> None:
    builder = _script("build_qwen3_tts_worker_pack_x64.ps1")
    smoke = _script("smoke_worker_pack.py")

    assert 'QwenCommit = "022e286b98fbec7e1e916cb940cdf532cd9f488e"' in builder
    assert 'TorchVersion = "2.7.1"' in builder
    assert 'CudaVariant = "cu126"' in builder
    assert "https://download.pytorch.org/whl/$CudaVariant" in builder
    assert 'torch.version.cuda == "12.6"' in builder
    assert "torch.cuda.is_available()" in builder
    assert 'torch.ones(1, device="cuda")' in builder
    assert "qwen3_tts_torch" in builder
    assert "${PACK_ROOT}/payload/models/default" in builder
    assert 'CHATWAIFU_NEURAL_TTS_WORKER_DEVICE = "cuda:0"' in builder
    assert 'CHATWAIFU_NEURAL_TTS_WORKER_QWEN_ATTN_IMPLEMENTATION = "sdpa"' in builder
    assert '("zh", "你好，我是绫地宁宁。")' in smoke
    assert '("ja", "こんにちは、綾地寧々です。")' in smoke
    assert "_assert_non_silent_wave(audio)" in smoke


def test_faster_whisper_pack_is_materialized_offline_cpu_int8() -> None:
    builder = _script("build_faster_whisper_worker_pack_x64.ps1")

    assert 'ModelRepository = "Systran/faster-whisper-base"' in builder
    assert 'ModelRevision = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"' in builder
    assert 'CHATWAIFU_STT_WORKER_LOCAL_FILES_ONLY = "true"' in builder
    assert 'CHATWAIFU_STT_WORKER_DEVICE = "cpu"' in builder
    assert 'CHATWAIFU_STT_WORKER_COMPUTE_TYPE = "int8"' in builder
    assert 'CHATWAIFU_STT_WORKER_PRELOAD = "true"' in builder
    assert 'throw "A real PCM16 speech WAV is required' in builder
    for required in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        assert f'"{required}"' in builder


def test_portable_runtime_and_smoke_do_not_use_target_python_or_network() -> None:
    common = _script("worker_pack_common.ps1")
    smoke = _script("smoke_worker_pack.py")

    assert '"python", "install", $PythonRequest, "--no-bin", "--no-registry"' in common
    assert 'Join-Path $PortableRoot "python.exe"' in common
    assert "0x8664" in common
    assert 'PYTHONDONTWRITEBYTECODE = "1"' in common
    assert 'Filter "__pycache__"' in common
    assert '"HF_HUB_OFFLINE": "1"' in smoke
    assert '"TRANSFORMERS_OFFLINE": "1"' in smoke
    assert "urllib.request.ProxyHandler({})" in smoke
    assert "install_archive(archive" in smoke
    assert "extractall" not in smoke
    assert "PYTHON_ENVIRONMENT_KEYS" in smoke
    assert "_assert_listener_closed(port)" in smoke


def test_installed_pack_helper_uses_frozen_runtime_and_checks_x64() -> None:
    installer = _script("install_worker_pack_x64.ps1")

    assert '"runtime-sidecar\\chatwaifu-runtime.exe"' in installer
    assert "0x8664" in installer
    assert "--worker-pack verify" in installer
    assert "--worker-pack install" in installer
    assert "InstallLocation" in installer
    assert "CurrentUser" in installer
    assert "python" not in installer.casefold()
