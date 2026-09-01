"""Installed Worker Pack discovery, selection, and Runtime supervision."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import errno
import hashlib
import json
import multiprocessing
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
from chatwaifu_runtime.worker_packs import (
    InstalledWorkerPack,
    ManagedWorker,
    WorkerPackSupervisor,
)


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


class _UnstoppableProcess(_FakeProcess):
    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired("unstoppable-worker", 0 if timeout is None else timeout)


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
    executable_path: str = "payload/worker.exe",
    arguments: list[str] | None = None,
) -> Path:
    root = data_root / "worker-packs" / pack_id / version
    executable = root / executable_path
    executable.parent.mkdir(parents=True)
    executable.write_bytes(_x64_pe())
    prefix = "CHATWAIFU_NEURAL_TTS_WORKER_" if kind == "tts" else "CHATWAIFU_STT_WORKER_"
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
                executable=executable_path,
                arguments=arguments or [],
                environment={f"{prefix}MODEL_DIR": "${PACK_ROOT}/payload/model"},
            ),
        ),
        files=[
            WorkerPackFile(
                path=executable_path,
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


def _hold_worker_cache_lease(tmp_path: str, ready: Any) -> None:
    """Own a cache lease until the test deliberately terminates this process."""

    supervisor = _supervisor(Path(tmp_path))
    pack = supervisor.discover()[0]
    cache_root, cache_lease = supervisor._create_worker_cache(pack)
    try:
        assert cache_root.is_dir()
        ready.set()
        threading.Event().wait()
    finally:
        supervisor._release_worker_cache_lease(cache_lease)


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
    assert Path(environment["CHATWAIFU_NEURAL_TTS_WORKER_MODEL_DIR"]) == (
        pack.root / "payload" / "model"
    )
    assert environment["HF_HUB_OFFLINE"] == "1"


@pytest.mark.parametrize("interpreter_name", ["python.exe", "python3.exe", "python3.12.exe"])
def test_python_worker_stays_immutable_and_discoverable_after_first_launch(
    interpreter_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable_path = f"payload/python/{interpreter_name}"
    root = _install_test_pack(
        tmp_path / "data",
        executable_path=executable_path,
        arguments=["-I", "-m", "test_worker.main"],
    )
    supervisor = _supervisor(tmp_path)
    pack = supervisor.discover()[0]
    process = _FakeProcess()
    commands: list[list[str]] = []
    launch_environments: list[dict[str, str]] = []

    def fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        commands.append(command)
        environment = cast(dict[str, str], kwargs["env"])
        launch_environments.append(environment)
        # Reproduce CPython's observed behavior: isolated mode ignores the
        # environment-only bytecode policy and dirties the verified pack unless
        # Runtime supplies the command-line flag.
        if "-B" not in command[1:]:
            cache = root / "payload/python/Lib/site-packages/test_worker/__pycache__"
            cache.mkdir(parents=True)
            (cache / "main.cpython-312.pyc").write_bytes(b"generated bytecode")
        # Reproduce Numba's separate cache locator. librosa uses jit(cache=True),
        # whose default in-tree locator creates these directories even when
        # CPython bytecode writes are disabled.
        numba_cache = environment.get("NUMBA_CACHE_DIR")
        if numba_cache is None:
            (root / "payload/python/Lib/site-packages/librosa/__pycache__").mkdir(parents=True)
        else:
            cache_path = Path(numba_cache)
            cache_path.mkdir(parents=True, exist_ok=True)
            (cache_path / "librosa-test.nbc").write_bytes(b"compiled cache")
        return process

    def fake_probe(url: str, _token: str) -> object:
        return _health_payload() if url.endswith("/v1/health") else _tts_capabilities_payload()

    monkeypatch.setattr(worker_pack_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker_pack_module, "_probe_json", fake_probe)

    managed = supervisor._launch(pack)

    assert commands == [
        [
            str(root / executable_path),
            "-B",
            "-I",
            "-m",
            "test_worker.main",
        ]
    ]
    assert len(launch_environments) == 1
    numba_cache = Path(launch_environments[0]["NUMBA_CACHE_DIR"])
    assert root not in numba_cache.parents
    assert (tmp_path / "data") in numba_cache.parents
    assert (numba_cache / "librosa-test.nbc").is_file()
    assert [item.manifest.pack_id for item in _supervisor(tmp_path).discover()] == [
        "qwen3-tts-torch-win-x64-cu126"
    ]
    supervisor._workers.append(managed)
    supervisor.stop()
    assert not numba_cache.exists()


def test_start_reaps_only_lease_proven_dead_launch_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_test_pack(tmp_path / "data")
    owner = _supervisor(tmp_path)
    pack = owner.discover()[0]
    active, active_lease = owner._create_worker_cache(pack)
    (active / "active.bin").write_bytes(b"active")
    abandoned, abandoned_lease = owner._create_worker_cache(pack)
    (abandoned / "native-cache.bin").write_bytes(b"stale")
    assert owner._release_worker_cache_lease(abandoned_lease) is None

    cache_namespace = active.parent
    missing_marker = cache_namespace / "launch-missing-marker"
    missing_marker.mkdir()
    not_ready = cache_namespace / "launch-not-ready"
    not_ready.mkdir()
    (not_ready / worker_pack_module._WORKER_CACHE_OWNER_FILE).write_text(
        json.dumps(
            {
                "schema_version": worker_pack_module._WORKER_CACHE_OWNER_SCHEMA_VERSION,
                "runtime_pid": 1,
                "instance_id": "0" * 32,
            }
        ),
        encoding="utf-8",
    )
    corrupt_marker = cache_namespace / "launch-corrupt-marker"
    corrupt_marker.mkdir()
    (corrupt_marker / worker_pack_module._WORKER_CACHE_OWNER_READY_FILE).write_bytes(b"ready")
    (corrupt_marker / worker_pack_module._WORKER_CACHE_OWNER_FILE).write_text(
        "not-json", encoding="utf-8"
    )
    unmanaged = cache_namespace / "owner-data"
    unmanaged.mkdir()
    (unmanaged / "keep.txt").write_text("keep", encoding="utf-8")

    reaper = _supervisor(tmp_path)

    def no_packs() -> list[InstalledWorkerPack]:
        return []

    monkeypatch.setattr(reaper, "discover", no_packs)
    reaper.start()

    assert not abandoned.exists()
    assert (active / "active.bin").read_bytes() == b"active"
    assert missing_marker.is_dir()
    assert not_ready.is_dir()
    assert corrupt_marker.is_dir()
    assert (unmanaged / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert owner._release_worker_cache_lease(active_lease) is None
    assert owner._remove_worker_cache(active) is None


def test_start_scans_for_stale_caches_on_both_sides_of_pack_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _supervisor(tmp_path)
    calls: list[str] = []

    def reap() -> None:
        calls.append("reap")

    def discover() -> list[InstalledWorkerPack]:
        calls.append("discover")
        return []

    monkeypatch.setattr(supervisor, "_reap_stale_worker_caches", reap)
    monkeypatch.setattr(supervisor, "discover", discover)

    supervisor.start()

    assert calls == ["reap", "discover", "reap"]


def test_stale_cache_reaper_reports_non_contention_lock_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_test_pack(tmp_path / "data")
    owner = _supervisor(tmp_path)
    cache_root, cache_lease = owner._create_worker_cache(owner.discover()[0])
    assert owner._release_worker_cache_lease(cache_lease) is None

    def fail_lock(_lease: Any) -> None:
        raise OSError(errno.EINVAL, "invalid lock request")

    monkeypatch.setattr(worker_pack_module, "_lock_worker_cache_lease", fail_lock)
    owner._reap_stale_worker_caches()

    assert cache_root.is_dir()
    error = capsys.readouterr().err
    assert "preserved an unverifiable entry" in error
    assert "invalid lock request" in error


def test_stale_cache_lease_is_released_when_owner_process_is_killed(tmp_path: Path) -> None:
    _install_test_pack(tmp_path / "data")
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    owner = context.Process(target=_hold_worker_cache_lease, args=(str(tmp_path), ready))
    owner.start()
    try:
        assert ready.wait(10), "cache owner did not acquire its lease"
        launch_directories = tuple((tmp_path / "data" / "worker-cache").glob("*/*/launch-*"))
        assert len(launch_directories) == 1
        launch_directory = launch_directories[0]

        reaper = _supervisor(tmp_path)
        reaper._reap_stale_worker_caches()
        assert launch_directory.is_dir()

        owner.terminate()
        owner.join(10)
        assert not owner.is_alive()

        for _attempt in range(256):
            reaper._reap_stale_worker_caches()
            if not launch_directory.exists():
                break
        assert not launch_directory.exists()
        assert launch_directory.parent.is_dir()
        assert launch_directory.parent.parent.is_dir()
        assert (tmp_path / "data" / "worker-cache").is_dir()
    finally:
        if owner.is_alive():
            owner.kill()
            owner.join(10)
        owner.close()


def test_stale_cache_reaper_never_follows_linked_namespaces(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    cache_root = tmp_path / "data" / "worker-cache"
    cache_root.mkdir(parents=True)
    linked_pack = cache_root / "linked-pack"
    real_pack = cache_root / "real-pack"
    real_pack.mkdir()
    linked_version = real_pack / "linked-version"
    real_version = real_pack / "0.1.0"
    real_version.mkdir()
    linked_launch = real_version / "launch-linked"
    try:
        linked_pack.symlink_to(external, target_is_directory=True)
        linked_version.symlink_to(external, target_is_directory=True)
        linked_launch.symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink creation is unavailable: {error}")

    _supervisor(tmp_path).start()

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert linked_pack.exists()
    assert linked_version.exists()
    assert linked_launch.exists()


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
    assert not list((tmp_path / "data" / "worker-cache").rglob("launch-*"))


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
        return _health_payload() if url.endswith("/v1/health") else _tts_capabilities_payload()

    monkeypatch.setattr(worker_pack_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(worker_pack_module, "_free_loopback_port", next_port)
    monkeypatch.setattr(worker_pack_module, "_probe_json", fake_probe)

    managed = supervisor._launch(pack)

    assert managed.process is cast(Any, ready)
    assert managed.base_url == "http://127.0.0.1:43128"
    supervisor._workers.append(managed)
    supervisor.stop()
    assert managed.cache_root is not None
    assert not managed.cache_root.exists()


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
            capabilities: SttWorkerCapabilities | TtsWorkerCapabilities = SttWorkerCapabilities(
                provider_id="faster-whisper",
                display_name="Local worker",
                model="test-model",
                languages=["auto"],
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


def test_stop_retains_cache_lease_until_worker_exit_is_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_test_pack(tmp_path / "data")
    supervisor = _supervisor(tmp_path)
    pack = supervisor.discover()[0]
    cache_root, cache_lease = supervisor._create_worker_cache(pack)
    process = _UnstoppableProcess()
    stream = (tmp_path / "worker.log").open("ab")
    supervisor._workers.append(
        ManagedWorker(
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
            cache_root=cache_root,
            cache_lease=cache_lease,
        )
    )

    supervisor.stop()

    assert cache_root.is_dir()
    assert not cache_lease.closed
    assert supervisor.bootstrap_workers == ["tts:qwen3_tts_torch@0.1.0"]
    assert "is still running; cache lease retained" in capsys.readouterr().err

    process.running = False
    supervisor.stop()

    assert not cache_root.exists()
    assert cache_lease.closed
    assert supervisor.bootstrap_workers == []
