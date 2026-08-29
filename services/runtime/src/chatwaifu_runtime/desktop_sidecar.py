"""Frozen desktop Runtime entrypoint with an explicit bootstrap handshake."""

from __future__ import annotations

import ctypes
import json
import os
import runpy
import signal
import socket
import sys
import threading
import time
from collections.abc import MutableMapping
from ctypes import wintypes as ctypes_wintypes
from pathlib import Path
from types import FrameType
from typing import Any, cast

BOOTSTRAP_PREFIX = "CHATWAIFU_BOOTSTRAP "
STACK_VERSION = "1.0"
STARTUP_TIMEOUT_SECONDS = 120.0
WINDOWS_SYNCHRONIZE = 0x00100000
WINDOWS_WAIT_TIMEOUT = 0x00000102
WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_CLOSE = 0x00002000
WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_JOB_HANDLE: int | None = None


class _WindowsJobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes_wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes_wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes_wintypes.DWORD),
        ("SchedulingClass", ctypes_wintypes.DWORD),
    ]


class _WindowsIoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _WindowsJobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _WindowsJobBasicLimitInformation),
        ("IoInfo", _WindowsIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def main(arguments: list[str] | None = None) -> int:
    resolved_arguments = list(sys.argv[1:] if arguments is None else arguments)
    if resolved_arguments[:1] == ["--plugin-python"]:
        return _run_plugin_python(resolved_arguments[1:])
    if resolved_arguments:
        raise RuntimeError(f"Unknown packaged Runtime arguments: {resolved_arguments!r}")

    _install_windows_process_job()
    environment = prepare_environment()
    listener = _loopback_listener()
    runtime_port = cast(tuple[str, int], listener.getsockname())[1]
    environment["CHATWAIFU_RUNTIME__PORT"] = str(runtime_port)

    # These imports intentionally happen after the packaged resource and writable
    # data roots are exported. Pipecat imports NLTK while the Runtime graph loads.
    import uvicorn

    from chatwaifu_runtime.config.settings import load_settings
    from chatwaifu_runtime.main import create_app
    from chatwaifu_runtime.observability.logging import configure_logging

    settings = load_settings()
    configure_logging(settings.log_level)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host=settings.runtime.host,
            port=runtime_port,
            log_config=None,
        )
    )
    stopped = threading.Event()
    _install_signal_handlers(stopped)
    _start_stdin_watchdog(stopped)
    _start_parent_watchdog(environment, stopped)
    server_thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="chatwaifu-runtime",
        daemon=True,
    )
    server_thread.start()

    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while not server.started and server_thread.is_alive() and not stopped.is_set():
            if time.monotonic() >= deadline:
                raise RuntimeError("Packaged Runtime startup timed out")
            stopped.wait(0.05)
        if not server.started:
            raise RuntimeError("Packaged Runtime stopped before startup completed")

        runtime_url = f"http://127.0.0.1:{runtime_port}"
        _write_bootstrap(runtime_url)
        while server_thread.is_alive() and not stopped.is_set():
            server_thread.join(timeout=0.25)
        return 0 if server.started else 1
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        listener.close()


def _run_plugin_python(arguments: list[str]) -> int:
    """Run a declared Python MCP entrypoint without recursing into Runtime startup."""

    if not arguments:
        raise RuntimeError("--plugin-python requires a script path")
    script = Path(arguments[0]).expanduser().resolve()
    if not script.is_file() or script.suffix.casefold() != ".py":
        raise RuntimeError("--plugin-python only accepts an existing .py script")
    sys.dont_write_bytecode = True
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict", write_through=True)
    previous_arguments = sys.argv
    sys.argv = [str(script), *arguments[1:]]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else int(error.code is not None)
    finally:
        sys.argv = previous_arguments
    return 0


def prepare_environment(
    environment: MutableMapping[str, str] | None = None,
    *,
    frozen: bool | None = None,
    resource_root: Path | None = None,
) -> MutableMapping[str, str]:
    """Set immutable resources and writable per-user paths before Runtime import."""

    target = os.environ if environment is None else environment
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    resolved_resources = resource_root or _runtime_resource_root(is_frozen)
    user_root = _default_user_root(target)
    config_root = Path(target.get("CHATWAIFU_CONFIG_DIR", user_root / "config"))
    data_root = Path(target.get("CHATWAIFU_DATA_DIR", user_root / "data"))
    config_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    target.setdefault("PYTHONUNBUFFERED", "1")
    target.setdefault("CHATWAIFU_ENVIRONMENT", "desktop")
    target.setdefault("CHATWAIFU_RESOURCE_ROOT", str(resolved_resources))
    target.setdefault("CHATWAIFU_CONFIG_DIR", str(config_root))
    target.setdefault("CHATWAIFU_DATA_DIR", str(data_root))
    target.setdefault("CHATWAIFU_CHARACTERS_DIR", str(resolved_resources / "characters"))
    target.setdefault("CHATWAIFU_SKILLS_DIR", str(resolved_resources / "skills"))
    target.setdefault("CHATWAIFU_RUNTIME__HOST", "127.0.0.1")
    target.setdefault("CHATWAIFU_RUNTIME__WEB_ORIGIN", "http://tauri.localhost")
    target.setdefault("CHATWAIFU_STT__PROVIDER", "disabled")
    target.setdefault("CHATWAIFU_TTS__PROVIDER", "fake")
    target.setdefault("CHATWAIFU_TTS__DEFAULT_PROVIDER", "fake")
    target.setdefault("CHATWAIFU_TTS__WORKERS", "{}")
    target.setdefault("NLTK_DATA", str(resolved_resources / "nltk_data"))
    return target


def _runtime_resource_root(frozen: bool) -> Path:
    if frozen:
        bundle_root = getattr(sys, "_MEIPASS", None)
        if not isinstance(bundle_root, str) or not bundle_root:
            raise RuntimeError("Frozen Runtime is missing its PyInstaller resource root")
        return Path(bundle_root).resolve()
    return Path(__file__).resolve().parents[4]


def _default_user_root(environment: MutableMapping[str, str]) -> Path:
    if os.name == "nt":
        base = environment.get("LOCALAPPDATA")
        if base:
            return Path(base) / "ChatWaifu NEXT" / "Runtime"
    return Path.home() / ".local" / "share" / "chatwaifu-next" / "runtime"


def _loopback_listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    return listener


def _write_bootstrap(runtime_url: str) -> None:
    payload: dict[str, object] = {
        "schema_version": STACK_VERSION,
        "type": "runtime.ready",
        "runtime_url": runtime_url,
        "pid": os.getpid(),
        "workers": [],
    }
    print(f"{BOOTSTRAP_PREFIX}{json.dumps(payload, separators=(',', ':'))}", flush=True)


def _install_signal_handlers(stopped: threading.Event) -> None:
    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stopped.set()

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        candidate = getattr(signal, name, None)
        if isinstance(candidate, signal.Signals):
            signal.signal(candidate, request_stop)


def _start_stdin_watchdog(stopped: threading.Event) -> None:
    def watch() -> None:
        try:
            stream = getattr(sys.stdin, "buffer", sys.stdin)
            while not stopped.is_set():
                if not stream.read(1):
                    stopped.set()
                    return
        except (OSError, ValueError):
            stopped.set()

    threading.Thread(target=watch, name="desktop-stdin-watchdog", daemon=True).start()


def _install_windows_process_job() -> None:
    """Contain Runtime and plugin descendants in a kill-on-close Windows Job."""

    global _PROCESS_JOB_HANDLE
    if os.name != "nt" or _PROCESS_JOB_HANDLE is not None:
        return
    windll = cast(Any, getattr(ctypes, "windll", None))
    if windll is None:
        raise RuntimeError("Windows process APIs are unavailable")
    kernel32 = windll.kernel32
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes_wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = ctypes_wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        ctypes_wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes_wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = ctypes_wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes_wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [
        ctypes_wintypes.HANDLE,
        ctypes_wintypes.HANDLE,
    ]
    kernel32.AssignProcessToJobObject.restype = ctypes_wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes_wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes_wintypes.BOOL

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError()
    limits = _WindowsJobExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_CLOSE
    if not kernel32.SetInformationJobObject(
        handle,
        WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        error = ctypes.WinError()
        kernel32.CloseHandle(handle)
        raise error
    if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
        error = ctypes.WinError()
        kernel32.CloseHandle(handle)
        raise error
    _PROCESS_JOB_HANDLE = int(handle)


def _start_parent_watchdog(environment: MutableMapping[str, str], stopped: threading.Event) -> None:
    raw_parent_pid = environment.get("CHATWAIFU_DESKTOP_PARENT_PID", "").strip()
    if not raw_parent_pid:
        return
    try:
        parent_pid = int(raw_parent_pid)
    except ValueError as error:
        raise RuntimeError("CHATWAIFU_DESKTOP_PARENT_PID must be an integer") from error
    if parent_pid <= 1 or parent_pid == os.getpid():
        raise RuntimeError("CHATWAIFU_DESKTOP_PARENT_PID is not a valid supervisor")

    def watch() -> None:
        while not stopped.wait(0.5):
            if not _process_exists(parent_pid):
                stopped.set()
                return

    threading.Thread(target=watch, name="desktop-parent-watchdog", daemon=True).start()


def _process_exists(process_id: int, *, platform_name: str | None = None) -> bool:
    if (platform_name or os.name) == "nt":
        return _windows_process_exists(process_id)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_process_exists(process_id: int) -> bool:
    windll = cast(Any, getattr(ctypes, "windll", None))
    if windll is None:
        raise RuntimeError("Windows process APIs are unavailable")
    kernel32 = windll.kernel32
    kernel32.OpenProcess.argtypes = [
        ctypes_wintypes.DWORD,
        ctypes_wintypes.BOOL,
        ctypes_wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = ctypes_wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [ctypes_wintypes.HANDLE, ctypes_wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = ctypes_wintypes.DWORD
    kernel32.CloseHandle.argtypes = [ctypes_wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes_wintypes.BOOL
    process_handle = kernel32.OpenProcess(WINDOWS_SYNCHRONIZE, False, process_id)
    if not process_handle:
        return False
    try:
        return kernel32.WaitForSingleObject(process_handle, 0) == WINDOWS_WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(process_handle)


if __name__ == "__main__":
    raise SystemExit(main())
