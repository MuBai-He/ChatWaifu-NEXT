"""Child-side probes for the Windows AppContainer acceptance suite.

This file intentionally depends only on the Python standard library. It is copied
into a temporary read-only package root before the launcher executes it.
"""

# pyright: basic, reportAttributeAccessIssue=false

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path

TOKEN_QUERY = 0x0008
TOKEN_IS_APP_CONTAINER = 29
TOKEN_APP_CONTAINER_SID = 31
ERROR_INSUFFICIENT_BUFFER = 122


class _TokenAppContainerInformation(ctypes.Structure):
    _fields_ = [("token_app_container", ctypes.c_void_p)]


def _last_error(api: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), api)


def _token_identity() -> tuple[bool, str]:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise _last_error("OpenProcessToken")
    try:
        is_app_container = wintypes.DWORD()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_IS_APP_CONTAINER,
            ctypes.byref(is_app_container),
            ctypes.sizeof(is_app_container),
            ctypes.byref(returned),
        ):
            raise _last_error("GetTokenInformation(TokenIsAppContainer)")

        required = wintypes.DWORD()
        first = advapi32.GetTokenInformation(
            token,
            TOKEN_APP_CONTAINER_SID,
            None,
            0,
            ctypes.byref(required),
        )
        if first or ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or required.value == 0:
            raise _last_error("GetTokenInformation(TokenAppContainerSid)")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_APP_CONTAINER_SID,
            buffer,
            required,
            ctypes.byref(returned),
        ):
            raise _last_error("GetTokenInformation(TokenAppContainerSid data)")
        information = ctypes.cast(
            buffer,
            ctypes.POINTER(_TokenAppContainerInformation),
        ).contents
        if not information.token_app_container:
            return bool(is_app_container.value), ""

        sid_text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(
            information.token_app_container,
            ctypes.byref(sid_text),
        ):
            raise _last_error("ConvertSidToStringSidW")
        try:
            return bool(is_app_container.value), sid_text.value or ""
        finally:
            kernel32.LocalFree(ctypes.cast(sid_text, wintypes.HLOCAL))
    finally:
        kernel32.CloseHandle(token)


def _read(path: Path) -> dict[str, object]:
    try:
        return {"allowed": True, "value": path.read_text(encoding="utf-8")}
    except OSError as error:
        return {"allowed": False, "winerror": error.winerror}


def _write(path: Path, value: str) -> dict[str, object]:
    try:
        path.write_text(value, encoding="utf-8")
        return {"allowed": True, "value": path.read_text(encoding="utf-8")}
    except OSError as error:
        return {"allowed": False, "winerror": error.winerror}


def _connect(host: str, port: int) -> dict[str, object]:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return {"allowed": True}
    except OSError as error:
        return {"allowed": False, "winerror": getattr(error, "winerror", None)}


def _security(args: argparse.Namespace) -> int:
    is_app_container, sid = _token_identity()
    package = Path(args.package)
    data = Path(args.data)
    result = {
        "is_app_container": is_app_container,
        "app_container_sid": sid,
        "package_read": _read(package / "readable.txt"),
        "package_overwrite": _write(package / "readable.txt", "changed"),
        "package_create": _write(package / "created-by-child.txt", "unexpected"),
        "data_write": _write(data / "written-by-child.txt", "expected"),
        "outside_read": _read(Path(args.outside_secret)),
        "state_read": _read(Path(args.state_journal)),
        "network_connect": _connect(args.connect_host, args.connect_port),
    }
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0


def _tree_child(args: argparse.Namespace) -> int:
    marker = Path(args.marker)
    marker.write_text(str(os.getpid()), encoding="ascii")
    print(json.dumps({"descendant_pid": os.getpid()}), flush=True)
    threading.Event().wait()
    return 0


def _tree_parent(args: argparse.Namespace) -> int:
    _child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "tree-child",
            "--marker",
            args.marker,
        ],
        # AppContainer processes cannot rely on opening the DOS NUL device.
        # An owned pipe proves descendant creation without widening device ACLs.
        stdin=subprocess.PIPE,
    )
    threading.Event().wait()
    return 0


def _spawn_once(_args: argparse.Namespace) -> int:
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", "print('child-ok')"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        result: dict[str, object] = {
            "allowed": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
        }
    except OSError as error:
        result = {
            "allowed": False,
            "winerror": getattr(error, "winerror", None),
        }
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0


def _set_inherited_event(args: argparse.Namespace) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    try:
        allowed = bool(kernel32.SetEvent(wintypes.HANDLE(args.handle)))
        winerror = 0 if allowed else ctypes.get_last_error()
    except OSError as error:
        allowed = False
        winerror = error.winerror
    print(
        json.dumps(
            {
                "allowed": allowed,
                "winerror": winerror,
            },
            ensure_ascii=True,
        ),
        flush=True,
    )
    return 0


def _memory_pressure(_args: argparse.Namespace) -> int:
    allocations: list[bytearray] = []
    try:
        for _ in range(128):
            allocations.append(bytearray(8 * 1024 * 1024))
    except MemoryError:
        print(
            json.dumps(
                {"limited": True, "allocated_chunks": len(allocations)},
                ensure_ascii=True,
            ),
            flush=True,
        )
        return 0
    print(
        json.dumps(
            {"limited": False, "allocated_chunks": len(allocations)},
            ensure_ascii=True,
        ),
        flush=True,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    security = commands.add_parser("security")
    security.add_argument("--package", required=True)
    security.add_argument("--data", required=True)
    security.add_argument("--outside-secret", required=True)
    security.add_argument("--state-journal", required=True)
    security.add_argument("--connect-host", required=True)
    security.add_argument("--connect-port", type=int, required=True)
    security.set_defaults(run=_security)

    tree_parent = commands.add_parser("tree-parent")
    tree_parent.add_argument("--marker", required=True)
    tree_parent.set_defaults(run=_tree_parent)

    tree_child = commands.add_parser("tree-child")
    tree_child.add_argument("--marker", required=True)
    tree_child.set_defaults(run=_tree_child)

    spawn_once = commands.add_parser("spawn-once")
    spawn_once.set_defaults(run=_spawn_once)

    inherited_event = commands.add_parser("set-inherited-event")
    inherited_event.add_argument("--handle", type=int, required=True)
    inherited_event.set_defaults(run=_set_inherited_event)

    memory_pressure = commands.add_parser("memory-pressure")
    memory_pressure.set_defaults(run=_memory_pressure)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return int(args.run(args))


if __name__ == "__main__":
    raise SystemExit(main())
