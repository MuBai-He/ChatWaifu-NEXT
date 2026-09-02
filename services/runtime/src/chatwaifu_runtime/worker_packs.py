"""Discover, verify, launch, and supervise optional local model Worker Packs."""

from __future__ import annotations

import errno
import hashlib
import importlib
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import MutableMapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal, cast
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
_WORKER_CACHE_OWNER_FILE = ".owner-lock.json"
_WORKER_CACHE_OWNER_READY_FILE = ".owner-ready"
_WORKER_CACHE_OWNER_SCHEMA_VERSION = "1.0"
_MAX_WORKER_CACHE_OWNER_BYTES = 4_096
_WORKER_CACHE_LOCK_CONTENTION_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EDEADLK})
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
    cache_root: Path | None = None
    cache_lease: BinaryIO | None = None

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
        self._cache_root = self._data_root / "worker-cache"
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
        if not self._reap_worker_caches_or_disable():
            return
        installed = self.discover()
        # Windows can briefly retain a terminated process's byte-range lock even
        # after its process object is signaled. Pack verification is a useful
        # lifecycle boundary: scan again after that real work, without sleeping
        # or spinning, and before this Runtime creates any new launch caches.
        if not self._reap_worker_caches_or_disable():
            return
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
        still_running: list[ManagedWorker] = []
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
                try:
                    process_exited = process.poll() is not None
                except OSError as error:
                    process_exited = False
                    errors.append(f"{managed.bootstrap_name} exit check failed: {error}")
                if not process_exited:
                    errors.append(
                        f"{managed.bootstrap_name} is still running; cache lease retained"
                    )
                    still_running.append(managed)
                else:
                    lease_error = self._release_worker_cache_lease(managed.cache_lease)
                    if lease_error is not None:
                        errors.append(
                            f"{managed.bootstrap_name} cache lease release failed: {lease_error}"
                        )
                    cache_error = self._remove_worker_cache(managed.cache_root)
                    if cache_error is not None:
                        errors.append(
                            f"{managed.bootstrap_name} cache cleanup failed: {cache_error}"
                        )
        self._workers = list(reversed(still_running))
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
        # Startup discovery validates the signed installation identity, the
        # declared path shape, and the executable below. Complete payload hashing
        # is intentionally user-triggered from Settings because multi-gigabyte
        # model packs must not turn every desktop launch into a disk-wide scan.
        installed = load_installed_pack(
            pack_root,
            verify_payload=False,
            verify_declared_paths=False,
        )
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
        log_stream, log_path, log_start_offset = self._open_log(manifest.pack_id)
        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process: subprocess.Popen[bytes] | None = None
        cache_root: Path | None = None
        cache_lease: BinaryIO | None = None
        try:
            cache_root, cache_lease = self._create_worker_cache(pack)
            worker_environment = self._worker_environment(
                pack,
                port=port,
                token=token,
                cache_root=cache_root,
            )
            replacements = self._pack_placeholders(pack)
            arguments = [
                _expand_placeholders(argument, replacements) for argument in entrypoint.arguments
            ]
            arguments = _immutable_python_arguments(manifest, executable, arguments)
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
            lease_error = self._release_worker_cache_lease(cache_lease)
            if lease_error is not None:
                print(
                    f"Worker Pack launch cache lease release warning: {lease_error}",
                    file=sys.stderr,
                    flush=True,
                )
            cache_error = self._remove_worker_cache(cache_root)
            if cache_error is not None:
                print(
                    f"Worker Pack launch cache cleanup warning: {cache_error}",
                    file=sys.stderr,
                    flush=True,
                )
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
            cache_root=cache_root,
            cache_lease=cache_lease,
        )

    def _worker_environment(
        self,
        pack: InstalledWorkerPack,
        *,
        port: int,
        token: str,
        cache_root: Path | None = None,
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
        # Numba's on-disk cache is independent of CPython bytecode generation.
        # Libraries such as librosa use ``jit(cache=True)`` and otherwise create
        # ``__pycache__`` inside site-packages even with ``-B`` and
        # PYTHONDONTWRITEBYTECODE. Keep that mutable executable cache outside the
        # verified Worker Pack, isolate it per launch so stale native cache files
        # are never trusted by a later process, and clean it when the worker stops.
        if cache_root is not None:
            result["NUMBA_CACHE_DIR"] = str(cache_root)
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

    def _create_worker_cache(self, pack: InstalledWorkerPack) -> tuple[Path, BinaryIO]:
        cache_root = self._validated_cache_root(create=True)
        pack_namespace = cache_root / pack.manifest.pack_id
        pack_namespace.mkdir(exist_ok=True, mode=0o700)
        resolved_pack_namespace = self._validated_cache_namespace(
            pack_namespace, expected_parent=cache_root
        )
        version_namespace = resolved_pack_namespace / pack.manifest.version
        version_namespace.mkdir(exist_ok=True, mode=0o700)
        resolved_namespace = self._validated_cache_namespace(
            version_namespace, expected_parent=resolved_pack_namespace
        )
        resolved_pack = pack.root.resolve(strict=True)
        if (
            resolved_pack == resolved_namespace
            or resolved_pack in resolved_namespace.parents
            or resolved_namespace in resolved_pack.parents
        ):
            raise RuntimeError("Worker cache directory must remain outside the verified pack")

        launch_directory = Path(
            tempfile.mkdtemp(prefix="launch-", dir=resolved_namespace)
        ).resolve()
        lease: BinaryIO | None = None
        try:
            marker = launch_directory / _WORKER_CACHE_OWNER_FILE
            lease = marker.open("x+b")
            lease.write(
                json.dumps(
                    {
                        "schema_version": _WORKER_CACHE_OWNER_SCHEMA_VERSION,
                        "runtime_pid": os.getpid(),
                        "instance_id": secrets.token_hex(16),
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            lease.flush()
            _lock_worker_cache_lease(lease)
            (launch_directory / _WORKER_CACHE_OWNER_READY_FILE).write_bytes(b"ready")
        except BaseException:
            if lease is not None:
                lease.close()
            shutil.rmtree(launch_directory, ignore_errors=True)
            raise
        return launch_directory, lease

    def _reap_stale_worker_caches(self) -> None:
        """Remove only lease-proven caches abandoned by a crashed Runtime."""
        if not self._cache_root.exists():
            return
        cache_root = self._validated_cache_root(create=False)
        try:
            pack_namespaces = tuple(cache_root.iterdir())
        except OSError as error:
            print(
                f"Worker Pack stale cache scan warning: {error}",
                file=sys.stderr,
                flush=True,
            )
            return
        for pack_namespace in pack_namespaces:
            if pack_namespace.name.startswith("."):
                continue
            try:
                resolved_pack_namespace = self._validated_cache_namespace(
                    pack_namespace, expected_parent=cache_root
                )
                version_namespaces = tuple(resolved_pack_namespace.iterdir())
            except (OSError, RuntimeError) as error:
                print(
                    f"Worker Pack stale cache scan warning at {pack_namespace}: {error}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            for version_namespace in version_namespaces:
                try:
                    resolved_version_namespace = self._validated_cache_namespace(
                        version_namespace, expected_parent=resolved_pack_namespace
                    )
                    launch_directories = tuple(resolved_version_namespace.glob("launch-*"))
                except (OSError, RuntimeError) as error:
                    print(
                        f"Worker Pack stale cache scan warning at {version_namespace}: {error}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                for launch_directory in launch_directories:
                    if _is_link_like(launch_directory) or not launch_directory.is_dir():
                        print(
                            "Worker Pack stale cache scan preserved an unsafe entry at "
                            f"{launch_directory}",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue
                    lease, lease_error = self._acquire_stale_worker_cache_lease(launch_directory)
                    if lease_error is not None:
                        print(
                            "Worker Pack stale cache scan preserved an unverifiable entry at "
                            f"{launch_directory}: {lease_error}",
                            file=sys.stderr,
                            flush=True,
                        )
                    if lease is None:
                        continue
                    release_error = self._release_worker_cache_lease(lease)
                    if release_error is not None:
                        print(
                            "Worker Pack stale cache lease release warning at "
                            f"{launch_directory}: {release_error}",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue
                    cache_error = self._remove_worker_cache(launch_directory)
                    if cache_error is not None:
                        print(
                            "Worker Pack stale launch cache cleanup warning at "
                            f"{launch_directory}: {cache_error}",
                            file=sys.stderr,
                            flush=True,
                        )

    def _reap_worker_caches_or_disable(self) -> bool:
        try:
            self._reap_stale_worker_caches()
        except (OSError, RuntimeError) as error:
            print(
                f"Worker Pack cache root is unsafe; optional workers stay disabled: {error}",
                file=sys.stderr,
                flush=True,
            )
            return False
        return True

    def _validated_cache_root(self, *, create: bool) -> Path:
        if create:
            self._cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _is_link_like(self._cache_root) or not self._cache_root.is_dir():
            raise RuntimeError("Worker cache root is not a real directory")
        resolved = self._cache_root.resolve(strict=True)
        if resolved != self._data_root and self._data_root not in resolved.parents:
            raise RuntimeError("Worker cache root escapes the configured data root")
        resolved_pack_root = self._pack_root.resolve()
        if (
            resolved == resolved_pack_root
            or resolved in resolved_pack_root.parents
            or resolved_pack_root in resolved.parents
        ):
            raise RuntimeError("Worker cache root overlaps the Worker Pack install root")
        return resolved

    def _validated_cache_namespace(self, path: Path, *, expected_parent: Path) -> Path:
        if _is_link_like(path) or not path.is_dir():
            raise RuntimeError("cache namespace is not a real directory")
        resolved = path.resolve(strict=True)
        if resolved.parent != expected_parent:
            raise RuntimeError("cache namespace escapes its expected parent")
        return resolved

    def _acquire_stale_worker_cache_lease(
        self, launch_directory: Path
    ) -> tuple[BinaryIO | None, str | None]:
        ready = launch_directory / _WORKER_CACHE_OWNER_READY_FILE
        if _is_link_like(ready) or not ready.is_file():
            return None, "owner readiness marker is missing or unsafe"
        marker = launch_directory / _WORKER_CACHE_OWNER_FILE
        if _is_link_like(marker) or not marker.is_file():
            return None, "owner marker is missing or unsafe"
        try:
            lease = marker.open("r+b")
        except OSError as error:
            return None, str(error)
        try:
            _lock_worker_cache_lease(lease)
        except OSError as error:
            lease.close()
            if error.errno in _WORKER_CACHE_LOCK_CONTENTION_ERRNOS:
                return None, None
            return None, str(error)
        try:
            lease.seek(0)
            raw = lease.read(_MAX_WORKER_CACHE_OWNER_BYTES + 1)
            if len(raw) > _MAX_WORKER_CACHE_OWNER_BYTES:
                raise ValueError("owner marker exceeds the size limit")
            parsed: object = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("owner marker must be an object")
            payload = cast(dict[str, object], parsed)
            if payload.get("schema_version") != _WORKER_CACHE_OWNER_SCHEMA_VERSION:
                raise ValueError("owner marker schema_version is invalid")
            runtime_pid = payload.get("runtime_pid")
            instance_id = payload.get("instance_id")
            if not isinstance(runtime_pid, int) or runtime_pid <= 0:
                raise ValueError("owner marker runtime_pid is invalid")
            if not isinstance(instance_id, str) or len(instance_id) != 32:
                raise ValueError("owner marker instance_id is invalid")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            release_error = self._release_worker_cache_lease(lease)
            detail = str(error)
            if release_error is not None:
                detail += f"; lease release failed: {release_error}"
            return None, detail
        return lease, None

    def _release_worker_cache_lease(self, lease: BinaryIO | None) -> str | None:
        if lease is None:
            return None
        error_text: str | None = None
        try:
            _unlock_worker_cache_lease(lease)
        except OSError as error:
            error_text = str(error)
        try:
            lease.close()
        except OSError as error:
            error_text = f"{error_text}; {error}" if error_text else str(error)
        return error_text

    def _remove_worker_cache(self, cache_root: Path | None) -> str | None:
        if cache_root is None:
            return None
        if _is_link_like(cache_root):
            return f"refusing to remove a linked cache directory: {cache_root}"
        try:
            managed_root = self._validated_cache_root(create=False)
            relative = cache_root.relative_to(managed_root)
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError, ValueError) as error:
            return str(error)
        if len(relative.parts) != 3 or not cache_root.name.startswith("launch-"):
            return f"refusing to remove a cache outside the managed launch layout: {cache_root}"
        pack_namespace, version_namespace = cache_root.parent.parent, cache_root.parent
        try:
            resolved_pack_namespace = self._validated_cache_namespace(
                pack_namespace, expected_parent=managed_root
            )
            self._validated_cache_namespace(
                version_namespace, expected_parent=resolved_pack_namespace
            )
            resolved = self._validated_cache_namespace(
                cache_root, expected_parent=version_namespace.resolve(strict=True)
            )
        except (OSError, RuntimeError) as error:
            return str(error)
        try:
            shutil.rmtree(resolved)
        except OSError as error:
            return str(error)
        return None

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


def _is_link_like(path: Path) -> bool:
    try:
        attributes = int(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0))
        reparse_flag = int(
            getattr(importlib.import_module("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
        return path.is_symlink() or path.is_junction() or bool(attributes & reparse_flag)
    except OSError:
        return False


def _lock_worker_cache_lease(lease: BinaryIO) -> None:
    lease.seek(0)
    if os.name == "nt":
        msvcrt = cast(Any, importlib.import_module("msvcrt"))
        msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl = cast(Any, importlib.import_module("fcntl"))
    fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_worker_cache_lease(lease: BinaryIO) -> None:
    lease.seek(0)
    if os.name == "nt":
        msvcrt = cast(Any, importlib.import_module("msvcrt"))
        msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl = cast(Any, importlib.import_module("fcntl"))
    fcntl.flock(lease.fileno(), fcntl.LOCK_UN)


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
    if version.startswith("."):
        version = version[1:]
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
