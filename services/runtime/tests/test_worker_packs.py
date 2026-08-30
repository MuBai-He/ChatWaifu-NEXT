"""Installed Worker Pack discovery, selection, and Runtime wiring."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.error import URLError

import chatwaifu_runtime.worker_packs as worker_pack_module
import pytest
from chatwaifu_model_worker import (
    SttWorkerCapabilities,
    TtsWorkerCapabilities,
    WorkerPackEntrypoint,
    WorkerPackFile,
    WorkerPackInstallReceipt,
    WorkerPackLicense,
    WorkerPackManifest,
    WorkerPackPlatform,
    WorkerPackWorker,
)
from chatwaifu_runtime.worker_packs import ManagedWorker, WorkerPackSupervisor


class _FakeProcess:
    def __init__(self, *, running: bool = True, return_code: int = 0) -> None:
        self.running = running
        self.return_code = return_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None if self.running else self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.running = False
        self.return_code = -15

    def kill(self) -> None:
        self.killed = True
        self.running = False
        self.return_code = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.running:
            raise subprocess.TimeoutExpired("fake-worker", 0)
        return self.return_code


def _health_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "ready",
        "worker_id": "test-worker",
        "model_loaded": False,
        "model": "test-model",
        "queue_depth": 0,
        "device": "cpu",
        "capabilities": [],
    }


def _tts_capabilities_payload(
    *,
    provider_id: str = "qwen3_tts_torch",
    model: str = "test-model",
    local_only: bool = True,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "provider_id": provider_id,
        "display_name": "Local worker",
        "model": model,
        "languages": ["zh", "ja"],
        "local_only": local_only,
    }


def _x64_pe() -> bytes:
    payload = bytearray(512)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", payload, 0x84, 0x8664)
    return bytes(payload)


def _install_test_pack(
    data_root: Path,
    *,
    pack_id: str = "qwen3-tts-torch-win-x64-cu126",
    version: str = "0.1.0",
    kind: Literal["stt", "tts"] = "tts",
) -> Path:
    root = data_root / "worker-packs" / pack_id / version
    executable = root / "payload" / "worker.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(_x64_pe())
    prefix = (
        "CHATWAIFU_NEURAL_TTS_WORKER_" if kind == "tts" else "CHATWAIFU_STT_WORKER_"
    )
    manifest = WorkerPackManifest(
        pack_id=pack_id,
        version=version,
        platform=WorkerPackPlatform(
            os="windows",
            architecture="x86_64",
            accelerator="cuda" if kind == "tts" else "cpu",
            accelerator_version="12.6" if kind == "tts" else None,
            python_abi="cp312",
        ),
        worker=WorkerPackWorker(
            kind=kind,
            backend="qwen3_tts_torch" if kind == "tts" else "faster_whisper",
            provider_id="qwen3_tts_torch" if kind == "tts" else "faster-whisper",
            display_name="Local worker",
            model="test-model",
            entrypoint=WorkerPackEntrypoint(
                executable="payload/worker.exe",
                environment={f"{prefix}MODEL_DIR": "${PACK_ROOT}/payload/model"},
            ),
        ),
        files=[
            WorkerPackFile(
                path="payload/worker.exe",
                size=executable.stat().st_size,
                sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                role="runtime",
            )
        ],
        licenses=[WorkerPackLicense(name="Test license")],
    )
    manifest_bytes = manifest.model_dump_json().encode()
    (root / "manifest.json").write_bytes(manifest_bytes)
    receipt = WorkerPackInstallReceipt(
        pack_id=manifest.pack_id,
        version=manifest.version,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        archive_sha256="0" * 64,
        installed_at=datetime(2026, 8, 30, tzinfo=UTC),
        verified_file_count=1,
    )
    (root / "install-receipt.json").write_text(receipt.model_dump_json(), encoding="utf-8")
    return root


def _supervisor(tmp_path: Path, **environment: str) -> WorkerPackSupervisor:
    values = {
        "CHATWAIFU_DATA_DIR": str(tmp_path / "data"),
        "CHATWAIFU_CONFIG_DIR": str(tmp_path / "config"),
        "PATH": "/trusted/system/path",
        "CHATWAIFU_LLM__API_KEY": "must-not-reach-worker",
        **environment,
    }
    return WorkerPackSupervisor(values, platform_os="windows", architecture="x86_64")


def test_discovers_receipted_compatible_pack_and_rejects_tampering(tmp_path: Path) -> None:
    root = _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)

    discovered = supervisor.discover()

    assert [(pack.manifest.pack_id, pack.manifest.version) for pack in discovered] == [
        ("qwen3-tts-torch-win-x64-cu126", "0.1.0")
    ]
    (root / "payload" / "worker.exe").write_bytes(b"tampered")
    assert supervisor.discover() == []


def test_auto_selection_uses_semver_and_explicit_config_is_authoritative(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    _install_test_pack(data_root, version="0.2.0")
    _install_test_pack(data_root, version="0.10.0-rc.1")
    _install_test_pack(data_root, version="0.10.0")
    supervisor = _supervisor(tmp_path)
    installed = supervisor.discover()

    selected = supervisor._select(installed)

    assert selected["tts"].manifest.version == "0.10.0"
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True)
    (config_root / "local-ai-selection.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "active": {
                    "tts": {
                        "pack_id": "qwen3-tts-torch-win-x64-cu126",
                        "version": "0.2.0",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert supervisor._select(installed)["tts"].manifest.version == "0.2.0"


def test_worker_environment_is_minimal_and_supervisor_owns_endpoint_secrets(
    tmp_path: Path,
) -> None:
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    pack = supervisor.discover()[0]

    environment = supervisor._worker_environment(pack, port=43127, token="ephemeral")

    assert environment["PATH"] == "/trusted/system/path"
    assert "CHATWAIFU_LLM__API_KEY" not in environment
    assert environment["CHATWAIFU_NEURAL_TTS_WORKER_PORT"] == "43127"
    assert environment["CHATWAIFU_NEURAL_TTS_WORKER_TOKEN"] == "ephemeral"
    assert environment["CHATWAIFU_NEURAL_TTS_WORKER_MODEL_DIR"] == str(
        pack.root / "payload" / "model"
    )
    assert environment["HF_HUB_OFFLINE"] == "1"


def test_tts_capabilities_configure_the_generic_runtime_worker_route(tmp_path: Path) -> None:
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    pack = supervisor.discover()[0]
    capabilities = TtsWorkerCapabilities(
        provider_id="qwen3_tts_torch",
        display_name="宁宁 · Qwen3-TTS CUDA",
        model="nene-0.6b",
        languages=["zh", "ja", "en"],
        supports_voice_cloning=False,
        supports_style=False,
        supports_speed=False,
        native_streaming=False,
    )

    class _RunningProcess:
        def poll(self) -> None:
            return None

    managed = ManagedWorker(
        pack=pack,
        process=cast(Any, _RunningProcess()),
        log_stream=cast(Any, None),
        base_url="http://127.0.0.1:43127",
        token="worker-token",
        capabilities=capabilities,
    )
    supervisor._workers.append(managed)

    supervisor._configure_runtime()

    runtime_environment = supervisor._environment
    assert runtime_environment["CHATWAIFU_TTS__PROVIDER"] == "null"
    assert runtime_environment["CHATWAIFU_TTS__DEFAULT_PROVIDER"] == "qwen3_tts_torch"
    workers = json.loads(runtime_environment["CHATWAIFU_TTS__WORKERS"])
    assert workers["qwen3_tts_torch"]["native_streaming"] is False
    assert workers["qwen3_tts_torch"]["token"] == "worker-token"
    assert managed.bootstrap_name == "tts:qwen3_tts_torch@0.1.0"


def test_corrupt_activation_config_keeps_base_runtime_available(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_test_pack(tmp_path / "data")
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True)
    (config_root / "local-ai-selection.json").write_text("not-json", encoding="utf-8")
    supervisor = _supervisor(tmp_path)

    supervisor.start()

    assert supervisor.bootstrap_workers == []
    assert "optional workers stay disabled" in capsys.readouterr().err


def test_worker_startup_cancellation_terminates_the_spawned_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    pack = supervisor.discover()[0]
    stopped = threading.Event()
    process = _FakeProcess()
    streams: list[Any] = []

    def fake_popen(*_args: object, **kwargs: object) -> _FakeProcess:
        streams.append(kwargs["stdout"])
        return process

    def cancel_during_probe(_url: str, _token: str) -> object:
        stopped.set()
        raise URLError("not ready")

    monkeypatch.setattr(worker_pack_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker_pack_module, "_probe_json", cancel_during_probe)

    with pytest.raises(RuntimeError, match="cancelled"):
        supervisor._launch(
            pack,
            cancel_event=stopped,
            startup_deadline=time.monotonic() + 10,
        )

    assert process.terminated is True
    assert process.poll() == -15
    assert streams[0].closed is True


def test_capability_identity_mismatch_terminates_worker_and_closes_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    pack = supervisor.discover()[0]
    process = _FakeProcess()
    streams: list[Any] = []

    def fake_popen(*_args: object, **kwargs: object) -> _FakeProcess:
        streams.append(kwargs["stdout"])
        return process

    def fake_probe(url: str, _token: str) -> object:
        if url.endswith("/v1/health"):
            return _health_payload()
        return _tts_capabilities_payload(provider_id="unexpected-provider")

    monkeypatch.setattr(worker_pack_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker_pack_module, "_probe_json", fake_probe)

    with pytest.raises(RuntimeError, match="provider_id"):
        supervisor._launch(pack)

    assert process.terminated is True
    assert streams[0].closed is True


def test_non_local_worker_capabilities_are_rejected_and_cleaned_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    pack = supervisor.discover()[0]
    process = _FakeProcess()

    def fake_popen(*_args: object, **_kwargs: object) -> _FakeProcess:
        return process

    def fake_probe(url: str, _token: str) -> object:
        return (
            _health_payload()
            if url.endswith("/v1/health")
            else _tts_capabilities_payload(local_only=False)
        )

    monkeypatch.setattr(worker_pack_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker_pack_module, "_probe_json", fake_probe)

    with pytest.raises(RuntimeError, match="local_only=false"):
        supervisor._launch(pack)

    assert process.terminated is True


def test_explicit_port_collision_retries_with_a_new_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    pack = supervisor.discover()[0]
    failed = _FakeProcess(running=False, return_code=1)
    ready = _FakeProcess()
    processes = [failed, ready]
    assigned_ports = iter([43127, 43128])

    def fake_popen(*_args: object, **kwargs: object) -> _FakeProcess:
        process = processes.pop(0)
        if process is failed:
            cast(Any, kwargs["stdout"]).write(b"address already in use\n")
        return process

    def next_port() -> int:
        return next(assigned_ports)

    def fake_probe(url: str, _token: str) -> object:
        return (
            _health_payload()
            if url.endswith("/v1/health")
            else _tts_capabilities_payload()
        )

    monkeypatch.setattr(worker_pack_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker_pack_module, "_free_loopback_port", next_port)
    monkeypatch.setattr(worker_pack_module, "_probe_json", fake_probe)

    managed = supervisor._launch(pack)

    assert managed.process is cast(Any, ready)
    assert managed.base_url == "http://127.0.0.1:43128"
    managed.process.terminate()
    managed.log_stream.close()


def test_stt_and_tts_start_concurrently_with_one_shared_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_test_pack(
        tmp_path / "data",
        pack_id="faster-whisper-base-win-x64-cpu",
        kind="stt",
    )
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    barrier = threading.Barrier(2)
    deadlines: list[float | None] = []

    def fake_launch(
        pack: Any,
        *,
        cancel_event: threading.Event | None = None,
        startup_deadline: float | None = None,
    ) -> ManagedWorker:
        assert cancel_event is not None
        deadlines.append(startup_deadline)
        barrier.wait(timeout=2)
        if pack.manifest.worker.kind == "stt":
            capabilities: SttWorkerCapabilities | TtsWorkerCapabilities = (
                SttWorkerCapabilities(
                    provider_id="faster-whisper",
                    display_name="Local worker",
                    model="test-model",
                    languages=["auto"],
                )
            )
        else:
            capabilities = TtsWorkerCapabilities(
                provider_id="qwen3_tts_torch",
                display_name="Local worker",
                model="test-model",
                languages=["zh", "ja"],
            )
        return ManagedWorker(
            pack=pack,
            process=cast(Any, _FakeProcess()),
            log_stream=(tmp_path / f"{pack.manifest.worker.kind}.log").open("ab"),
            base_url="http://127.0.0.1:43127",
            token="worker-token",
            capabilities=capabilities,
        )

    monkeypatch.setattr(supervisor, "_launch", fake_launch)

    before = time.monotonic()
    supervisor.start(timeout_seconds=2)

    assert len(supervisor.bootstrap_workers) == 2
    assert len(deadlines) == 2
    assert deadlines[0] == deadlines[1]
    assert deadlines[0] is not None and before < deadlines[0] <= before + 2.1
    supervisor.stop()


def test_supervisor_cancellation_stops_a_worker_that_became_ready_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_test_pack(
        tmp_path / "data",
        pack_id="faster-whisper-base-win-x64-cpu",
        kind="stt",
    )
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    stopped = threading.Event()
    first_appended = threading.Event()
    process = _FakeProcess()
    stream = (tmp_path / "ready-worker.log").open("ab")

    class _ObservedWorkerList(list[ManagedWorker]):
        def append(self, worker: ManagedWorker) -> None:
            super().append(worker)
            first_appended.set()

    supervisor._workers = _ObservedWorkerList()

    def fake_launch(
        pack: Any,
        *,
        cancel_event: threading.Event | None = None,
        startup_deadline: float | None = None,
    ) -> ManagedWorker:
        del startup_deadline
        assert cancel_event is stopped
        if pack.manifest.worker.kind == "tts":
            return ManagedWorker(
                pack=pack,
                process=cast(Any, process),
                log_stream=stream,
                base_url="http://127.0.0.1:43127",
                token="worker-token",
                capabilities=TtsWorkerCapabilities(
                    provider_id="qwen3_tts_torch",
                    display_name="Local worker",
                    model="test-model",
                    languages=["zh"],
                ),
            )
        assert first_appended.wait(timeout=2)
        stopped.set()
        raise worker_pack_module._WorkerStartupCancelled("cancelled")

    monkeypatch.setattr(supervisor, "_launch", fake_launch)

    supervisor.start(stopped, timeout_seconds=2)

    assert supervisor.bootstrap_workers == []
    assert process.terminated is True
    assert stream.closed is True


def test_failed_worker_reports_crash_and_stop_closes_every_worker_log(
    tmp_path: Path,
) -> None:
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    pack = supervisor.discover()[0]
    crashed = _FakeProcess(running=False, return_code=7)
    stream = (tmp_path / "worker.log").open("ab")
    supervisor._workers.append(
        ManagedWorker(
            pack=pack,
            process=cast(Any, crashed),
            log_stream=stream,
            base_url="http://127.0.0.1:43127",
            token="worker-token",
            capabilities=TtsWorkerCapabilities(
                provider_id="qwen3_tts_torch",
                display_name="Local worker",
                model="test-model",
                languages=["zh"],
            ),
        )
    )

    assert supervisor.failed_worker() == "tts:qwen3_tts_torch@0.1.0 exited with code 7"

    supervisor.stop()

    assert supervisor.bootstrap_workers == []
    assert stream.closed is True
