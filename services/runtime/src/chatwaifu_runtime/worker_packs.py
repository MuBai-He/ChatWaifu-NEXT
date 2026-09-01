"""Discover, verify, launch, and supervise optional local model Worker Packs."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections.abc import MutableMapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from chatwaifu_model_worker import (
    SttWorkerCapabilities,
    TtsWorkerCapabilities,
    WorkerHealth,
    WorkerPackActivationConfig,
    WorkerPackManifest,
    semver_sort_key,
)
from chatwaifu_model_worker.pack_installer import load_installed_pack

WorkerKind = Literal["stt", "tts"]
_MAX_PROBE_BYTES = 1_048_576
_LOG_ROTATE_BYTES = 5 * 1024 * 1024
_MAX_PORT_BIND_ATTEMPTS = 3
WORKER_PACK_STARTUP_BUDGET_SECONDS = 300.0


class _WorkerStartupCancelled(RuntimeError):
    """Internal control flow used when the owning sidecar begins shutdown."""


class _WorkerPortCollisionError(RuntimeError):
    """A freshly assigned loopback port was lost before the Worker bound it."""


@dataclass(frozen=True, slots=True)
class InstalledWorkerPack:
    root: Path
    manifest: WorkerPackManifest


@dataclass(slots=True)
class ManagedWorker:
    pack: InstalledWorkerPack
    process: subprocess.Popen[bytes]
    log_stream: BinaryIO
    base_url: str
    token: str
    capabilities: SttWorkerCapabilities | TtsWorkerCapabilities

    @property
    def kind(self) -> WorkerKind:
        return self.pack.manifest.worker.kind

    @property
    def bootstrap_name(self) -> str:
        worker = self.pack.manifest.worker
        return f"{worker.kind}:{worker.provider_id}@{self.pack.manifest.version}"


class WorkerPackSupervisor:
    """Own optional Worker Pack processes for the lifetime of the frozen Runtime.

    A pack is owner-installed data, not an arbitrary launch description. Discovery
    requires an install receipt, a matching manifest digest, a compatible platform,
    and an entrypoint hash before any process is created.
    """

    def __init__(
        self,
        environment: MutableMapping[str, str],
        *,
        platform_os: str | None = None,
        architecture: str | None = None,
    ) -> None:
        self._environment = environment
        self._data_root = Path(environment["CHATWAIFU_DATA_DIR"]).resolve()
        self._config_root = Path(environment["CHATWAIFU_CONFIG_DIR"]).resolve()
        self._pack_root = self._data_root / "worker-packs"
        self._log_root = self._data_root / "worker-logs"
        self._platform_os = platform_os or _current_platform_os()
        self._architecture = architecture or _current_architecture()
        self._workers: list[ManagedWorker] = []

    @property
    def bootstrap_workers(self) -> list[str]:
        return [worker.bootstrap_name for worker in self._workers]

    def start(
        self,
        cancel_event: threading.Event | None = None,
        *,
        timeout_seconds: float = WORKER_PACK_STARTUP_BUDGET_SECONDS,
    ) -> None:
        """Start selected packs concurrently within one bounded startup budget.

        The desktop host gives the complete Runtime stack a finite readiness
        window. Starting STT and TTS serially would add their manifest timeouts
        and make an otherwise healthy cold start look like a crash loop. Both
        independent workers therefore share one deadline and observe the same
        shutdown event as the packaged Runtime.
        """

        stopped = cancel_event or threading.Event()
        if stopped.is_set():
            return
        installed = self.discover()
        try:
            selected = self._select(installed)
        except Exception as error:
            print(
                f"Local AI activation config is invalid; optional workers stay disabled: {error}",
                file=sys.stderr,
                flush=True,
            )
            return
        packs = [selected[kind] for kind in ("stt", "tts") if kind in selected]
        if not packs:
            return
        deadline = time.monotonic() + timeout_seconds
        futures: dict[Future[ManagedWorker], InstalledWorkerPack] = {}
        with ThreadPoolExecutor(
            max_workers=len(packs),
            thread_name_prefix="worker-pack-startup",
        ) as executor:
            futures = {
                executor.submit(
                    self._launch,
                    pack,
                    cancel_event=stopped,
                    startup_deadline=deadline,
                ): pack
                for pack in packs
            }
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    pack = futures[future]
                    try:
                        managed = future.result()
                    except _WorkerStartupCancelled:
                        continue
                    except Exception as error:
                        print(
                            f"Optional {pack.manifest.worker.kind.upper()} Worker Pack "
                            f"{pack.manifest.pack_id}@{pack.manifest.version} did not start; "
                            f"Runtime fallback remains active: {error}",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue
                    self._workers.append(managed)
        if stopped.is_set():
            self.stop()
            return
        self._configure_runtime()

    def stop(self) -> None:
        errors: list[str] = []
        for managed in reversed(self._workers):
            try:
                if managed.process.poll() is None:
                    managed.process.terminate()
            except OSError as error:
                errors.append(f"{managed.bootstrap_name} terminate failed: {error}")
        for managed in reversed(self._workers):
            process = managed.process
            try:
                if process.poll() is None:
                    try:
                        process.wait(
                            timeout=managed.pack.manifest.worker.entrypoint.shutdown_timeout_seconds
                        )
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(f"{managed.bootstrap_name} shutdown failed: {error}")
            finally:
                try:
                    managed.log_stream.close()
                except OSError as error:
                    errors.append(f"{managed.bootstrap_name} log close failed: {error}")
        self._workers.clear()
        for error in errors:
            print(
                f"Worker Pack cleanup warning: {error}",
                file=sys.stderr,
                flush=True,
            )

    def failed_worker(self) -> str | None:
        for managed in self._workers:
            return_code = managed.process.poll()
            if return_code is not None:
                return f"{managed.bootstrap_name} exited with code {return_code}"
        return None

    def discover(self) -> list[InstalledWorkerPack]:
        if not self._pack_root.is_dir():
            return []
        installed: list[InstalledWorkerPack] = []
        for manifest_path in sorted(self._pack_root.glob("*/*/manifest.json")):
            try:
                pack = self._load_installed(manifest_path)
            except Exception as error:
                print(
                    f"Ignoring invalid Worker Pack at {manifest_path.parent}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            if (
                pack.manifest.platform.os != self._platform_os
                or pack.manifest.platform.architecture != self._architecture
            ):
                continue
            installed.append(pack)
        return installed

    def _load_installed(self, manifest_path: Path) -> InstalledWorkerPack:
        pack_root = manifest_path.parent.resolve()
        if self._pack_root not in pack_root.parents:
            raise ValueError("pack root escapes the configured Worker Pack directory")
        # Installed model packs remain executable input. Re-hash the complete
        # declared tree and reject injected files on every Runtime boot rather
        # than trusting only the installation-time receipt.
        installed = load_installed_pack(pack_root, verify_payload=True)
        manifest = installed.manifest
        expected_root = (self._pack_root / manifest.pack_id / manifest.version).resolve()
        if pack_root != expected_root:
            raise ValueError("installed path does not match manifest identity")
        entrypoint = _resolve_pack_path(pack_root, manifest.worker.entrypoint.executable)
        entry_file = next(
            (
                item
                for item in manifest.files
                if item.path.casefold() == manifest.worker.entrypoint.executable.casefold()
            ),
            None,
        )
        if entry_file is None:
            raise ValueError("entrypoint is absent from the verified file list")
        if not entrypoint.is_file() or entrypoint.is_symlink():
            raise ValueError("entrypoint is missing or is a symbolic link")
        if entrypoint.stat().st_size != entry_file.size:
            raise ValueError("entrypoint size does not match manifest")
        if _sha256_file(entrypoint) != entry_file.sha256:
            raise ValueError("entrypoint digest does not match manifest")
        return InstalledWorkerPack(root=pack_root, manifest=manifest)

    def _select(
        self,
        installed: list[InstalledWorkerPack],
    ) -> dict[WorkerKind, InstalledWorkerPack]:
        by_identity = {(pack.manifest.pack_id, pack.manifest.version): pack for pack in installed}
        config_path = self._config_root / "local-ai-selection.json"
        if config_path.is_file():
            config = WorkerPackActivationConfig.model_validate_json(config_path.read_bytes())
            selected: dict[WorkerKind, InstalledWorkerPack] = {}
            for kind in ("stt", "tts"):
                choice = getattr(config.active, kind)
                if choice is None:
                    continue
                pack = by_identity.get((choice.pack_id, choice.version))
                if pack is None or pack.manifest.worker.kind != kind:
                    print(
                        f"Configured {kind.upper()} Worker Pack {choice.pack_id}@"
                        f"{choice.version} is not installed or compatible",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                selected[kind] = pack
            return selected

        selected = {}
        for pack in installed:
            kind = pack.manifest.worker.kind
            current = selected.get(kind)
            if current is None or semver_sort_key(pack.manifest.version) > semver_sort_key(
                current.manifest.version
            ):
                selected[kind] = pack
        return selected

    def _launch(
        self,
        pack: InstalledWorkerPack,
        *,
        cancel_event: threading.Event | None = None,
        startup_deadline: float | None = None,
    ) -> ManagedWorker:
        stopped = cancel_event or threading.Event()
        last_collision: _WorkerPortCollisionError | None = None
        for _attempt in range(_MAX_PORT_BIND_ATTEMPTS):
            if stopped.is_set():
                raise _WorkerStartupCancelled("Worker Pack startup was cancelled")
            if startup_deadline is not None and time.monotonic() >= startup_deadline:
                raise TimeoutError("Worker Pack shared startup budget was exhausted")
            try:
                return self._launch_once(
                    pack,
                    cancel_event=stopped,
                    startup_deadline=startup_deadline,
                )
            except _WorkerPortCollisionError as error:
                last_collision = error
        raise RuntimeError(
            f"Worker could not bind an assigned loopback port after "
            f"{_MAX_PORT_BIND_ATTEMPTS} attempts"
        ) from last_collision

    def _launch_once(
        self,
        pack: InstalledWorkerPack,
        *,
        cancel_event: threading.Event,
        startup_deadline: float | None,
    ) -> ManagedWorker:
        manifest = pack.manifest
        entrypoint = manifest.worker.entrypoint
        executable = _resolve_pack_path(pack.root, entrypoint.executable)
        working_directory = _resolve_pack_path(pack.root, entrypoint.working_directory)
        port = _free_loopback_port()
        token = secrets.token_urlsafe(32)
        worker_environment = self._worker_environment(pack, port=port, token=token)
        replacements = self._pack_placeholders(pack)
        arguments = [
            _expand_placeholders(argument, replacements) for argument in entrypoint.arguments
        ]
        arguments = _immutable_python_arguments(manifest, executable, arguments)
        log_stream, log_path, log_start_offset = self._open_log(manifest.pack_id)
        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                [str(executable), *arguments],
                cwd=working_directory,
                env=worker_environment,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
            base_url = f"http://127.0.0.1:{port}"
            remaining_budget = (
                max(0.0, startup_deadline - time.monotonic())
                if startup_deadline is not None
                else entrypoint.startup_timeout_seconds
            )
            capabilities = self._wait_ready(
                process,
                base_url,
                token,
                kind=manifest.worker.kind,
                health_path=entrypoint.health_path,
                capabilities_path=entrypoint.capabilities_path,
                timeout_seconds=min(entrypoint.startup_timeout_seconds, remaining_budget),
                cancel_event=cancel_event,
            )
            if capabilities.provider_id != manifest.worker.provider_id:
                raise RuntimeError(
                    "Worker capabilities provider_id does not match the installed manifest"
                )
            if capabilities.model != manifest.worker.model:
                raise RuntimeError(
                    "Worker capabilities model does not match the installed manifest"
                )
            if not capabilities.local_only:
                raise RuntimeError(
                    "Worker capabilities local_only=false violates the local Worker Pack contract"
                )
        except BaseException as error:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            try:
                log_stream.flush()
            except OSError:
                pass
            log_stream.close()
            if (
                isinstance(error, RuntimeError)
                and not isinstance(error, _WorkerStartupCancelled)
                and _log_indicates_port_collision(log_path, start_offset=log_start_offset)
            ):
                raise _WorkerPortCollisionError(
                    f"Worker lost assigned loopback port {port} before bind"
                ) from error
            raise
        return ManagedWorker(
            pack=pack,
            process=process,
            log_stream=log_stream,
            base_url=base_url,
            token=token,
            capabilities=capabilities,
        )

    def _worker_environment(
        self,
        pack: InstalledWorkerPack,
        *,
        port: int,
        token: str,
    ) -> dict[str, str]:
        allowed_host_keys = {
            "APPDATA",
            "COMSPEC",
            "HOME",
            "LOCALAPPDATA",
            "NUMBER_OF_PROCESSORS",
            "PATH",
            "PATHEXT",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "WINDIR",
        }
        result = {
            key: value for key, value in self._environment.items() if key in allowed_host_keys
        }
        result.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1",
                "PYTHONUTF8": "1",
            }
        )
        replacements = self._pack_placeholders(pack)
        for key, value in pack.manifest.worker.entrypoint.environment.items():
            result[key] = _expand_placeholders(value, replacements)
        worker = pack.manifest.worker
        if worker.kind == "stt":
            prefix = "CHATWAIFU_STT_WORKER_"
        else:
            prefix = "CHATWAIFU_NEURAL_TTS_WORKER_"
            result[f"{prefix}BACKEND"] = worker.backend
        result.update(
            {
                f"{prefix}HOST": "127.0.0.1",
                f"{prefix}PORT": str(port),
                f"{prefix}TOKEN": token,
                f"{prefix}PROVIDER_ID": worker.provider_id,
                f"{prefix}DISPLAY_NAME": worker.display_name,
                f"{prefix}WORKER_ID": f"{pack.manifest.pack_id}-{pack.manifest.version}",
                f"{prefix}MODEL": worker.model,
            }
        )
        return result

    def _pack_placeholders(self, pack: InstalledWorkerPack) -> dict[str, str]:
        return {
            "${PACK_ROOT}": str(pack.root),
            "${DATA_ROOT}": str(self._data_root),
            "${CONFIG_ROOT}": str(self._config_root),
        }

    def _wait_ready(
        self,
        process: subprocess.Popen[bytes],
        base_url: str,
        token: str,
        *,
        kind: WorkerKind,
        health_path: str,
        capabilities_path: str,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> SttWorkerCapabilities | TtsWorkerCapabilities:
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if cancel_event.is_set():
                raise _WorkerStartupCancelled("Worker Pack startup was cancelled")
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(f"Worker process exited with code {return_code}")
            try:
                health = WorkerHealth.model_validate(_probe_json(base_url + health_path, token))
                if health.status in {"ready", "busy"}:
                    payload = _probe_json(base_url + capabilities_path, token)
                    if kind == "stt":
                        return SttWorkerCapabilities.model_validate(payload)
                    return TtsWorkerCapabilities.model_validate(payload)
            except (HTTPError, URLError, TimeoutError, ValueError) as error:
                last_error = error
            cancel_event.wait(0.1)
        if cancel_event.is_set():
            raise _WorkerStartupCancelled("Worker Pack startup was cancelled")
        detail = f": {last_error}" if last_error is not None else ""
        raise TimeoutError(f"Worker health check timed out{detail}")

    def _configure_runtime(self) -> None:
        tts_workers: dict[str, dict[str, object]] = {}
        for managed in self._workers:
            capabilities = managed.capabilities
            if managed.kind == "stt":
                self._environment["CHATWAIFU_STT__PROVIDER"] = "faster_whisper_worker"
                self._environment["CHATWAIFU_STT__WORKER_URL"] = managed.base_url
                self._environment["CHATWAIFU_STT__WORKER_TOKEN"] = managed.token
                self._environment["CHATWAIFU_STT__LANGUAGE"] = "auto"
                continue
            if not isinstance(capabilities, TtsWorkerCapabilities):
                raise RuntimeError("TTS Worker returned an STT capabilities document")
            provider_id = capabilities.provider_id
            tts_workers[provider_id] = {
                "url": managed.base_url,
                "token": managed.token,
                "display_name": capabilities.display_name,
                "model": capabilities.model,
                "languages": capabilities.languages,
                "supports_voice_cloning": capabilities.supports_voice_cloning,
                "supports_style": capabilities.supports_style,
                "supports_speed": capabilities.supports_speed,
                "supports_pitch": capabilities.supports_pitch,
                "native_streaming": capabilities.native_streaming,
            }
        if tts_workers:
            default_provider = next(iter(tts_workers))
            # JSON null is Pydantic Settings' explicit value for the optional
            # legacy single-provider override.
            self._environment["CHATWAIFU_TTS__PROVIDER"] = "null"
            self._environment["CHATWAIFU_TTS__DEFAULT_PROVIDER"] = default_provider
            self._environment["CHATWAIFU_TTS__WORKERS"] = json.dumps(
                tts_workers,
                ensure_ascii=True,
                separators=(",", ":"),
            )

    def _open_log(self, pack_id: str) -> tuple[BinaryIO, Path, int]:
        self._log_root.mkdir(parents=True, exist_ok=True)
        path = self._log_root / f"{pack_id}.log"
        if path.is_file() and path.stat().st_size > _LOG_ROTATE_BYTES:
            previous = path.with_suffix(".log.previous")
            previous.unlink(missing_ok=True)
            path.replace(previous)
        start_offset = path.stat().st_size if path.is_file() else 0
        return path.open("ab", buffering=0), path, start_offset


def _resolve_pack_path(pack_root: Path, raw_path: str) -> Path:
    candidate = (pack_root / raw_path).resolve()
    if candidate != pack_root and pack_root not in candidate.parents:
        raise ValueError(f"Worker Pack path escapes its root: {raw_path}")
    return candidate


def _expand_placeholders(value: str, replacements: dict[str, str]) -> str:
    expanded = value
    for placeholder, replacement in replacements.items():
        expanded = expanded.replace(placeholder, replacement)
    return expanded


def _immutable_python_arguments(
    manifest: WorkerPackManifest,
    executable: Path,
    arguments: list[str],
) -> list[str]:
    """Prevent Python Worker Packs from mutating their verified install tree.

    Python's isolated mode (``-I``) implies ``-E``, so it ignores the
    ``PYTHONDONTWRITEBYTECODE`` environment policy projected by the supervisor.
    The command-line ``-B`` flag remains authoritative in isolated mode. Keep
    this compatibility guard in Runtime as well as current pack builders so
    already-issued, otherwise valid packs stay immutable across restarts.
    """

    if manifest.platform.python_abi is None or not _is_python_interpreter(executable):
        return arguments
    if "-B" in arguments:
        return arguments
    return ["-B", *arguments]


def _is_python_interpreter(executable: Path) -> bool:
    name = executable.name.casefold()
    if name.endswith(".exe"):
        name = name[:-4]
    if name in {"python", "pythonw", "python3"}:
        return True
    if not name.startswith("python3"):
        return False
    version = name[len("python3") :]
    return bool(version) and all(part.isdigit() for part in version.split("."))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _current_platform_os() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _current_architecture() -> str:
    machine = platform.machine().casefold()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return cast(tuple[str, int], listener.getsockname())[1]


def _probe_json(url: str, token: str) -> object:
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    # Local Worker control traffic must never be routed through the user's proxy.
    with build_opener(ProxyHandler({})).open(request, timeout=1.0) as response:
        payload = response.read(_MAX_PROBE_BYTES + 1)
    if len(payload) > _MAX_PROBE_BYTES:
        raise ValueError("Worker probe response exceeds the configured limit")
    return json.loads(payload)


def _log_indicates_port_collision(path: Path, *, start_offset: int = 0) -> bool:
    """Recognize only explicit bind failures before attempting another port."""

    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            source.seek(max(start_offset, size - 16_384))
            tail = source.read().decode("utf-8", errors="replace").casefold()
    except OSError:
        return False
    signatures = (
        "address already in use",
        "eaddrinuse",
        "winerror 10048",
        "errno 48",
        "errno 98",
        "error 10048",
    )
    return any(signature in tail for signature in signatures)
