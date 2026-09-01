from __future__ import annotations

import importlib.metadata
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

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
        assert script.count('"--break-system-packages"') == script.count(
            '"pip", "install"'
        )
        assert "Remove-WorkerPackPackagingTools" in script
        last_install = script.rfind('"pip", "install"')
        strip_runtime = script.index("Remove-WorkerPackPackagingTools")
        write_inventory = script.index("write_python_inventory.py")
        assert last_install < strip_runtime < write_inventory
        assert script.count("Remove-WorkerPackPackagingTools") == 1
        assert "Assert-WorkerPackPayloadHasNoBuildPaths" in script
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
    assert 'torch.ones(1, device="cuda:0")' in builder
    assert '"tensor_device": str(probe_tensor.device)' in builder
    assert '"compute_capability": list(torch.cuda.get_device_capability(0))' in builder
    assert 'Join-Path $MetadataRoot "cuda-probe.json"' in builder
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
    assert "model_repository = $ModelRepository" in builder
    assert "model_revision = if ($ModelSource) { $null } else { $ModelRevision }" in builder
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


def test_installed_pack_helper_uses_tauri_user_roots_and_restores_environment() -> None:
    installer = _script("install_worker_pack_x64.ps1")

    assert '[string]$AppIdentifier = "local.chatwaifu.next"' in installer
    assert (
        '$RuntimeConfigRoot = Join-Path (Join-Path $env:APPDATA $AppIdentifier) "runtime"'
        in installer
    )
    assert (
        '$RuntimeDataRoot = Join-Path (Join-Path $env:LOCALAPPDATA $AppIdentifier) "runtime"'
        in installer
    )
    assert '"CHATWAIFU_CONFIG_DIR",\n        $RuntimeConfigRoot' in installer
    assert '"CHATWAIFU_DATA_DIR",\n        $RuntimeDataRoot' in installer
    assert "try {" in installer
    assert "} finally {" in installer
    assert '"CHATWAIFU_CONFIG_DIR",\n        $PreviousConfigDir' in installer
    assert '"CHATWAIFU_DATA_DIR",\n        $PreviousDataDir' in installer


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows x64 install helper")
def test_installed_pack_helper_restores_environment_after_runtime_failure(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "test.cwpack"
    archive.write_bytes(b"the fake runtime does not inspect this archive")
    windows_root = Path(os.environ["WINDIR"])
    powershell = windows_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    runtime = windows_root / "System32/where.exe"
    environment = os.environ.copy()
    environment.update(
        {
            "CHATWAIFU_TEST_INSTALLER": str(WINDOWS_TOOLS / "install_worker_pack_x64.ps1"),
            "CHATWAIFU_TEST_ARCHIVE": str(archive),
            "CHATWAIFU_TEST_RUNTIME": str(runtime),
        }
    )
    command = r"""
$ErrorActionPreference = "Stop"
$env:CHATWAIFU_CONFIG_DIR = "sentinel-config"
$env:CHATWAIFU_DATA_DIR = "sentinel-data"
$FailureMessage = $null
try {
    & $env:CHATWAIFU_TEST_INSTALLER `
        -ArchivePath $env:CHATWAIFU_TEST_ARCHIVE `
        -RuntimePath $env:CHATWAIFU_TEST_RUNTIME `
        -VerifyOnly
} catch {
    $FailureMessage = $_.Exception.Message
}
if ($FailureMessage -notlike "Worker Pack verification failed*") {
    throw "The helper did not reach the expected frozen Runtime failure: $FailureMessage"
}
if ($env:CHATWAIFU_CONFIG_DIR -cne "sentinel-config") {
    throw "CHATWAIFU_CONFIG_DIR was not restored."
}
if ($env:CHATWAIFU_DATA_DIR -cne "sentinel-data") {
    throw "CHATWAIFU_DATA_DIR was not restored."
}
"environment-restored"
"""

    completed = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "environment-restored" in completed.stdout


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_worker_pack_builder_removes_all_pack_only_launchers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    portable = tmp_path / "portable-python"
    pip_package = portable / "Lib/site-packages/pip"
    pip_dist_info = portable / "Lib/site-packages/pip-25.2.dist-info"
    setuptools_package = portable / "Lib/site-packages/setuptools"
    runtime_package = portable / "Lib/site-packages/runtime_dependency"
    runtime_dist_info = portable / "Lib/site-packages/runtime_dependency-1.2.3.dist-info"
    scripts = portable / "Scripts"
    for directory in (
        pip_package / "_vendor/distlib",
        pip_dist_info,
        setuptools_package,
        runtime_package,
        runtime_dist_info,
        scripts,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (pip_package / "_vendor/distlib/t32.exe").write_bytes(b"not-a-runtime-binary")
    (pip_dist_info / "METADATA").write_text("Name: pip\n", encoding="utf-8")
    (runtime_dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: runtime-dependency\n"
        "Version: 1.2.3\n"
        "License: MIT\n",
        encoding="utf-8",
    )
    (runtime_package / "keep.py").write_text("VALUE = 1\n", encoding="utf-8")
    (setuptools_package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (setuptools_package / "cli-32.exe").write_bytes(b"x86-launcher-template")
    (setuptools_package / "cli-64.exe").write_bytes(b"x64-launcher-template")
    (scripts / "pip.exe").write_bytes(b"builder-only")
    (scripts / "uvicorn.exe").write_bytes(b"runtime")
    (scripts / "numba").write_text(
        f"#!{portable / 'python.exe'}\nprint('builder-only')\n", encoding="utf-8"
    )
    (portable / "python.exe").write_bytes(b"portable-interpreter")
    (portable / "python312.dll").write_bytes(b"runtime-dll")
    (runtime_package / "runtime.pyd").write_bytes(b"runtime-extension")

    environment = os.environ.copy()
    environment.update(
        {
            "CHATWAIFU_TEST_COMMON": str(WINDOWS_TOOLS / "worker_pack_common.ps1"),
            "CHATWAIFU_TEST_PORTABLE": str(portable),
        }
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ". $env:CHATWAIFU_TEST_COMMON; "
            "Remove-WorkerPackPackagingTools -PortablePythonRoot $env:CHATWAIFU_TEST_PORTABLE",
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not pip_package.exists()
    assert not pip_dist_info.exists()
    assert not scripts.exists()
    assert (setuptools_package / "__init__.py").is_file()
    assert not list(setuptools_package.glob("*.exe"))
    assert (runtime_package / "keep.py").is_file()
    assert (runtime_package / "runtime.pyd").is_file()
    assert runtime_dist_info.is_dir()
    assert (portable / "python.exe").is_file()
    assert (portable / "python312.dll").is_file()

    site_packages = portable / "Lib/site-packages"
    real_distributions = importlib.metadata.distributions
    expected = sorted(
        (distribution.metadata["Name"], distribution.version)
        for distribution in real_distributions(path=[str(site_packages)])
    )
    monkeypatch.setattr(
        importlib.metadata,
        "distributions",
        lambda: real_distributions(path=[str(site_packages)]),
    )
    inventory_tool = WINDOWS_TOOLS / "write_python_inventory.py"
    inventory_path = tmp_path / "python-packages.json"
    namespace = runpy.run_path(str(inventory_tool))
    monkeypatch.setattr(
        sys, "argv", [str(inventory_tool), "--output", str(inventory_path)]
    )
    assert namespace["main"]() == 0
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    actual = [(package["name"], package["version"]) for package in inventory["packages"]]
    assert actual == expected == [("runtime-dependency", "1.2.3")]
    assert all(package["name"].casefold() != "pip" for package in inventory["packages"])


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell")
def test_worker_pack_builder_rejects_build_paths_in_every_file_type(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "staging/payload"
    scripts = payload / "python/Scripts"
    scripts.mkdir(parents=True)
    (payload / "safe.bin").write_bytes(b"portable payload")
    environment = os.environ.copy()
    environment.update(
        {
            "CHATWAIFU_TEST_COMMON": str(WINDOWS_TOOLS / "worker_pack_common.ps1"),
            "CHATWAIFU_TEST_PAYLOAD": str(payload),
            "CHATWAIFU_TEST_FORBIDDEN": str(tmp_path),
            "CHATWAIFU_TEST_PYTHON": os.fspath(Path(sys.executable)),
        }
    )
    command = (
        ". $env:CHATWAIFU_TEST_COMMON; "
        "Assert-WorkerPackPayloadHasNoBuildPaths "
        "-PayloadRoot $env:CHATWAIFU_TEST_PAYLOAD "
        "-ScannerPython $env:CHATWAIFU_TEST_PYTHON "
        "-ForbiddenPaths @($env:CHATWAIFU_TEST_FORBIDDEN)"
    )

    safe = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    assert safe.returncode == 0, safe.stdout + safe.stderr

    leaked_path = f"{tmp_path}\\python.exe"
    leaked_files = (
        (scripts / "no-extension", f"#!{leaked_path}\n".encode()),
        (payload / "launcher.exe", b"MZ" + leaked_path.encode("utf-16-le")),
    )
    for leaked_file, contents in leaked_files:
        leaked_file.write_bytes(contents)
        rejected = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )
        assert rejected.returncode != 0
        assert "worker payload embeds a forbidden local build path" in (
            rejected.stdout + rejected.stderr
        )
        assert leaked_file.relative_to(payload).as_posix() in (
            rejected.stdout + rejected.stderr
        )
        leaked_file.unlink()
