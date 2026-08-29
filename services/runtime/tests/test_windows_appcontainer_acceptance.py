"""Real Windows acceptance probes for the AppContainer stdio launcher.

Run from an unelevated x64 shell after building the helper::

    uv run pytest services/runtime/tests/test_windows_appcontainer_acceptance.py -q

The module skips on non-Windows hosts and when no locally built helper exists.
Setting ``CHATWAIFU_APPCONTAINER_HOST`` to a missing or invalid binary is an
explicit configuration error and fails instead of skipping.
"""

# pyright: basic, reportAttributeAccessIssue=false

from __future__ import annotations

import ctypes
import json
import os
import queue
import shutil
import socket
import struct
import subprocess
import sys
import threading
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, cast
from uuid import uuid4

import pytest
from mcp.types import LATEST_PROTOCOL_VERSION

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows AppContainer acceptance requires a real Windows host",
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PROBE_FIXTURE = Path(__file__).parent / "fixtures" / "windows_appcontainer_probe.py"
MCP_FIXTURE = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
PE_MACHINE_AMD64 = 0x8664
BASE_PYTHON = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
PROCESS_SYNCHRONIZE = 0x0010_0000
PROCESS_TERMINATE = 0x0001
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x0000_0102
ERROR_INVALID_PARAMETER = 87
DACL_SECURITY_INFORMATION = 0x0000_0004
SE_FILE_OBJECT = 1


@dataclass(frozen=True)
class _Layout:
    helper: Path
    profile: str
    package: Path
    data: Path
    state: Path
    outside_secret: Path
    runtime_roots: tuple[Path, ...]

    @property
    def journal(self) -> Path:
        return self.state / f"{self.profile}.json"


class _JsonLineReader:
    def __init__(self, stream: IO[str]) -> None:
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream: IO[str]) -> None:
        for line in stream:
            self._lines.put(line)
        self._lines.put(None)

    def receive(self, *, timeout: float = 15.0) -> dict[str, Any]:
        line = self._lines.get(timeout=timeout)
        if line is None:
            raise AssertionError("sandboxed process closed stdout before the expected message")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise AssertionError(f"non-JSON bytes crossed MCP stdout: {line!r}") from error
        assert isinstance(value, dict)
        return value

    def response(self, request_id: int, *, timeout: float = 15.0) -> dict[str, Any]:
        while True:
            message = self.receive(timeout=timeout)
            if message.get("id") == request_id:
                return message


def _pe_machine(path: Path) -> int:
    with path.open("rb") as executable:
        assert executable.read(2) == b"MZ", f"not a PE executable: {path}"
        executable.seek(0x3C)
        pe_offset = struct.unpack("<I", executable.read(4))[0]
        executable.seek(pe_offset)
        assert executable.read(4) == b"PE\0\0", f"invalid PE header: {path}"
        return struct.unpack("<H", executable.read(2))[0]


def _discover_helper() -> Path:
    configured = os.environ.get("CHATWAIFU_APPCONTAINER_HOST") or os.environ.get(
        "CHATWAIFU_SECURITY__WINDOWS_APPCONTAINER_LAUNCHER"
    )
    if configured:
        path = Path(configured).resolve()
        assert path.is_file(), f"configured AppContainer helper does not exist: {path}"
        return path

    candidates = (
        REPO_ROOT
        / "target"
        / "x86_64-pc-windows-msvc"
        / "debug"
        / "chatwaifu-appcontainer-host.exe",
        REPO_ROOT
        / "target"
        / "x86_64-pc-windows-msvc"
        / "release"
        / "chatwaifu-appcontainer-host.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    pytest.skip("build chatwaifu-appcontainer-host for x86_64-pc-windows-msvc first")


@pytest.fixture(scope="module")
def appcontainer_helper() -> Path:
    helper = _discover_helper()
    assert ctypes.windll.shell32.IsUserAnAdmin() == 0, (
        "run this acceptance suite from an unelevated shell to prove the helper "
        "does not require administrator rights"
    )
    assert _pe_machine(helper) == PE_MACHINE_AMD64, f"helper is not x64: {helper}"
    return helper


@pytest.fixture
def layout(tmp_path: Path, appcontainer_helper: Path) -> Iterator[_Layout]:
    package = tmp_path / "package"
    data = tmp_path / "data"
    state = tmp_path / "trusted-state"
    outside = tmp_path / "outside"
    for path in (package, data, state, outside):
        path.mkdir()
    (package / "readable.txt").write_text("package-readable", encoding="utf-8")
    outside_secret = outside / "secret.txt"
    outside_secret.write_text("must-not-cross-boundary", encoding="utf-8")

    runtime_roots = tuple(
        dict.fromkeys(Path(value).resolve() for value in (sys.prefix, sys.base_prefix))
    )
    value = _Layout(
        helper=appcontainer_helper,
        profile=f"ChatWaifu.Acceptance.{uuid4().hex}",
        package=package.resolve(),
        data=data.resolve(),
        state=state.resolve(),
        outside_secret=outside_secret.resolve(),
        runtime_roots=runtime_roots,
    )
    try:
        yield value
    finally:
        _revoke(value, check=False)


def _run_command(
    layout: _Layout,
    child: list[str],
    *,
    network: str = "deny",
    max_processes: int = 8,
    memory_bytes: int = 512 * 1024 * 1024,
) -> list[str]:
    command = [
        str(layout.helper),
        "run",
        "--profile-name",
        layout.profile,
        "--state-dir",
        str(layout.state),
        "--cwd",
        str(layout.data),
    ]
    for root in (*layout.runtime_roots, layout.package):
        command.extend(("--read-only", str(root)))
    command.extend(
        (
            "--writable",
            str(layout.data),
            "--network",
            network,
            "--memory-bytes",
            str(memory_bytes),
            "--max-processes",
            str(max_processes),
            "--",
            *child,
        )
    )
    return command


def _revoke(layout: _Layout, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            str(layout.helper),
            "revoke",
            "--profile-name",
            layout.profile,
            "--state-dir",
            str(layout.state),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if check:
        assert result.returncode == 0, result.stderr
        assert not layout.journal.exists()
    return result


def _run_child_with_open_stdin(
    command: list[str],
    *,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Run a finite child without turning parent stdin into cancellation.

    Runtime keeps the helper's stdin pipe open for the lifetime of an MCP
    session. ``subprocess.run`` inherits an already-closed stdin under some CI
    and Parallels command channels, which correctly asks the helper to cancel.
    This harness holds an explicit pipe open until the child exits.
    """

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        returncode = process.wait(timeout=timeout)
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = process.stdout.read()
        stderr = process.stderr.read()
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def _profile_sid(profile: str) -> str:
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    advapi32.FreeSid.restype = ctypes.c_void_p
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    sid = ctypes.c_void_p()
    result = userenv.DeriveAppContainerSidFromAppContainerName(profile, ctypes.byref(sid))
    if result != 0:
        raise ctypes.WinError(result, "DeriveAppContainerSidFromAppContainerName")
    try:
        sid_text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error(), "ConvertSidToStringSidW")
        try:
            return sid_text.value or ""
        finally:
            kernel32.LocalFree(ctypes.cast(sid_text, wintypes.HLOCAL))
    finally:
        advapi32.FreeSid(sid)


def _dacl_sddl(path: Path) -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    status = advapi32.GetNamedSecurityInfoW(
        str(path),
        SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != 0:
        raise ctypes.WinError(status, f"GetNamedSecurityInfoW({path})")
    try:
        value = wintypes.LPWSTR()
        length = wintypes.DWORD()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            DACL_SECURITY_INFORMATION,
            ctypes.byref(value),
            ctypes.byref(length),
        ):
            raise ctypes.WinError(
                ctypes.get_last_error(),
                f"ConvertSecurityDescriptorToStringSecurityDescriptorW({path})",
            )
        try:
            return value.value or ""
        finally:
            kernel32.LocalFree(ctypes.cast(value, wintypes.HLOCAL))
    finally:
        kernel32.LocalFree(ctypes.cast(descriptor, wintypes.HLOCAL))


def _root_dacls(layout: _Layout) -> dict[Path, str]:
    return {
        root: _dacl_sddl(root)
        for root in (*layout.runtime_roots, layout.package, layout.data)
    }


def _grant_unrelated_read_ace(path: Path) -> None:
    result = subprocess.run(
        ["icacls.exe", str(path), "/grant", "*S-1-1-0:(RX)"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _remove_sid_ace(path: Path, sid: str) -> None:
    result = subprocess.run(
        ["icacls.exe", str(path), "/remove:g", f"*{sid}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _delete_profile(profile: str) -> None:
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    result = int(userenv.DeleteAppContainerProfile(profile))
    assert result in {0, -2147024894, -2147023728}, f"DeleteAppContainerProfile: {result:#x}"


def _security_cycle(
    layout: _Layout,
    connect_host: str,
    connect_port: int,
    *,
    network: str = "deny",
) -> dict[str, Any]:
    shutil.copy2(PROBE_FIXTURE, layout.package / "probe.py")
    child = [
        str(BASE_PYTHON),
        "-I",
        "-B",
        str(layout.package / "probe.py"),
        "security",
        "--package",
        str(layout.package),
        "--data",
        str(layout.data),
        "--outside-secret",
        str(layout.outside_secret),
        "--state-journal",
        str(layout.journal),
        "--connect-host",
        connect_host,
        "--connect-port",
        str(connect_port),
    ]
    result = _run_child_with_open_stdin(_run_command(layout, child, network=network))
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"unexpected stdout framing: {result.stdout!r}"
    payload = json.loads(lines[0])
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _assert_security_result(
    payload: dict[str, Any],
    expected_sid: str,
    *,
    network_allowed: bool,
) -> None:
    assert payload["is_app_container"] is True
    assert payload["app_container_sid"] == expected_sid
    assert payload["package_read"] == {"allowed": True, "value": "package-readable"}
    assert payload["package_overwrite"]["allowed"] is False
    assert payload["package_create"]["allowed"] is False
    assert payload["data_write"] == {"allowed": True, "value": "expected"}
    assert payload["outside_read"]["allowed"] is False
    assert payload["state_read"]["allowed"] is False
    assert payload["network_connect"]["allowed"] is network_allowed


def test_token_filesystem_loopback_and_exact_dacl_revoke(layout: _Layout) -> None:
    expected_sid = _profile_sid(layout.profile)
    initial_dacls = _root_dacls(layout)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        first = _security_cycle(layout, "127.0.0.1", listener.getsockname()[1])
        _assert_security_result(first, expected_sid, network_allowed=False)
        journal = json.loads(layout.journal.read_text(encoding="utf-8"))
        assert journal["profile_name"] == layout.profile
        assert journal["sid"] == expected_sid
        assert any(expected_sid in value for value in _root_dacls(layout).values())

        _revoke(layout)
        assert _root_dacls(layout) == initial_dacls
        assert expected_sid not in _dacl_sddl(layout.package / "readable.txt")
        assert expected_sid not in _dacl_sddl(layout.data / "written-by-child.txt")

        # A third party may legitimately change a DACL while a stable profile
        # exists. Revoke must remove only ChatWaifu's SID ACE and preserve it.
        _grant_unrelated_read_ace(layout.package)
        unrelated_baseline = _root_dacls(layout)
        second = _security_cycle(layout, "127.0.0.1", listener.getsockname()[1])
        _assert_security_result(second, expected_sid, network_allowed=False)
        _revoke(layout)
        assert _root_dacls(layout) == unrelated_baseline
    finally:
        listener.close()


def test_explicit_network_allow_reaches_private_lan_but_not_loopback(layout: _Layout) -> None:
    configured_probe = os.environ.get("CHATWAIFU_APPCONTAINER_LAN_PROBE")
    if not configured_probe:
        pytest.skip(
            "set CHATWAIFU_APPCONTAINER_LAN_PROBE=host:port to prove private LAN access"
        )
    try:
        host_ip, port_text = configured_probe.rsplit(":", 1)
        probe_port = int(port_text)
    except ValueError as error:
        raise AssertionError(
            "CHATWAIFU_APPCONTAINER_LAN_PROBE must be an IPv4 host:port"
        ) from error
    expected_sid = _profile_sid(layout.profile)
    loopback = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    loopback.bind(("127.0.0.1", 0))
    loopback.listen(1)
    try:
        denied = _security_cycle(layout, host_ip, probe_port)
        _assert_security_result(denied, expected_sid, network_allowed=False)
        allowed = _security_cycle(
            layout,
            host_ip,
            probe_port,
            network="allow",
        )
        _assert_security_result(allowed, expected_sid, network_allowed=True)
        still_no_loopback = _security_cycle(
            layout,
            "127.0.0.1",
            loopback.getsockname()[1],
            network="allow",
        )
        _assert_security_result(still_no_loopback, expected_sid, network_allowed=False)
    finally:
        loopback.close()


def test_reconcile_repairs_active_policy_and_revokes_inactive_policy(layout: _Layout) -> None:
    expected_sid = _profile_sid(layout.profile)
    initial_dacls = _root_dacls(layout)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        first = _security_cycle(layout, "127.0.0.1", listener.getsockname()[1])
        _assert_security_result(first, expected_sid, network_allowed=False)
        _remove_sid_ace(layout.package, expected_sid)
        _delete_profile(layout.profile)
        assert expected_sid not in _dacl_sddl(layout.package)

        repaired = subprocess.run(
            [
                str(layout.helper),
                "reconcile",
                "--state-dir",
                str(layout.state),
                "--active-profile-name",
                layout.profile,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        assert repaired.returncode == 0, repaired.stderr
        assert expected_sid in _dacl_sddl(layout.package)
        second = _security_cycle(layout, "127.0.0.1", listener.getsockname()[1])
        _assert_security_result(second, expected_sid, network_allowed=False)

        revoked = subprocess.run(
            [str(layout.helper), "reconcile", "--state-dir", str(layout.state)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        assert revoked.returncode == 0, revoked.stderr
        assert not layout.journal.exists()
        assert _root_dacls(layout) == initial_dacls
    finally:
        listener.close()


def _send_json(process: subprocess.Popen[str], value: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _receive_or_report_exit(
    process: subprocess.Popen[str],
    reader: _JsonLineReader,
    *,
    timeout: float,
) -> dict[str, Any]:
    try:
        return reader.receive(timeout=timeout)
    except (AssertionError, queue.Empty) as error:
        if process.poll() is None:
            raise
        assert process.stderr is not None
        detail = process.stderr.read()
        raise AssertionError(
            f"sandboxed process exited with {process.returncode}: {detail}"
        ) from error


def test_official_mcp_stdio_round_trip_and_eof(layout: _Layout) -> None:
    shutil.copy2(MCP_FIXTURE, layout.package / "mcp_server.py")
    child = [sys.executable, "-I", "-B", str(layout.package / "mcp_server.py")]
    process = subprocess.Popen(
        _run_command(layout, child),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    reader = _JsonLineReader(process.stdout)
    stderr: list[str] = []
    assert process.stderr is not None
    stderr_stream = process.stderr
    stderr_thread = threading.Thread(
        target=lambda: stderr.extend(stderr_stream.readlines()),
        daemon=True,
    )
    stderr_thread.start()
    try:
        _send_json(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "chatwaifu-appcontainer-acceptance", "version": "1"},
                },
            },
        )
        try:
            initialized = reader.response(1)
        except AssertionError as error:
            process.wait(timeout=5)
            stderr_thread.join(timeout=2)
            detail = "".join(stderr)
            raise AssertionError(f"MCP child failed before initialize: {detail}") from error
        assert "error" not in initialized, initialized
        _send_json(
            process,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        _send_json(
            process,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {"text": "appcontainer-echo"}},
            },
        )
        called = reader.response(2)
        assert "error" not in called, called
        assert "appcontainer-echo" in json.dumps(called, ensure_ascii=False)

        assert process.stdin is not None
        process.stdin.close()
        assert process.wait(timeout=10) in {0, 1}, "".join(stderr)
        stderr_thread.join(timeout=2)
        assert layout.journal.read_text(encoding="utf-8").startswith("{")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        _revoke(layout)


def _wait_for_process_exit(pid: int, timeout_ms: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_SYNCHRONIZE, False, pid)
    if not handle:
        if ctypes.get_last_error() == ERROR_INVALID_PARAMETER:
            return True
        raise ctypes.WinError(ctypes.get_last_error(), f"OpenProcess({pid})")
    try:
        result = kernel32.WaitForSingleObject(handle, timeout_ms)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        raise ctypes.WinError(ctypes.get_last_error(), f"WaitForSingleObject({pid})")
    finally:
        kernel32.CloseHandle(handle)


def _terminate_process(pid: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


def test_killing_host_terminates_the_entire_child_tree(layout: _Layout) -> None:
    shutil.copy2(PROBE_FIXTURE, layout.package / "probe.py")
    marker = layout.data / "descendant.pid"
    child = [
        str(BASE_PYTHON),
        "-I",
        "-B",
        str(layout.package / "probe.py"),
        "tree-parent",
        "--marker",
        str(marker),
    ]
    process = subprocess.Popen(
        _run_command(layout, child, max_processes=4),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    descendant_pid: int | None = None
    try:
        assert process.stdout is not None
        reader = _JsonLineReader(process.stdout)
        ready = _receive_or_report_exit(process, reader, timeout=20)
        descendant_pid = int(ready["descendant_pid"])
        assert marker.read_text(encoding="ascii") == str(descendant_pid)
        assert not _wait_for_process_exit(descendant_pid, 0)

        process.kill()
        process.wait(timeout=5)
        assert _wait_for_process_exit(descendant_pid, 5_000), (
            "KILL_ON_JOB_CLOSE did not terminate the sandbox descendant"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if descendant_pid is not None and not _wait_for_process_exit(descendant_pid, 0):
            _terminate_process(descendant_pid)
        _revoke(layout)


def test_parent_stdin_eof_terminates_the_entire_child_tree(layout: _Layout) -> None:
    shutil.copy2(PROBE_FIXTURE, layout.package / "probe.py")
    marker = layout.data / "stdin-eof-descendant.pid"
    child = [
        str(BASE_PYTHON),
        "-I",
        "-B",
        str(layout.package / "probe.py"),
        "tree-parent",
        "--marker",
        str(marker),
    ]
    process = subprocess.Popen(
        _run_command(layout, child, max_processes=4),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    descendant_pid: int | None = None
    try:
        assert process.stdout is not None
        reader = _JsonLineReader(process.stdout)
        ready = _receive_or_report_exit(process, reader, timeout=20)
        descendant_pid = int(ready["descendant_pid"])
        assert not _wait_for_process_exit(descendant_pid, 0)
        assert process.stdin is not None
        process.stdin.close()
        assert process.wait(timeout=10) in {0, 1}
        assert _wait_for_process_exit(descendant_pid, 5_000), (
            "parent stdin EOF did not cancel the AppContainer child tree"
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if descendant_pid is not None and not _wait_for_process_exit(descendant_pid, 0):
            _terminate_process(descendant_pid)
        _revoke(layout)


@pytest.mark.parametrize(
    ("max_processes", "expected_allowed"),
    [(1, False), (2, True)],
)
def test_job_active_process_limit(
    layout: _Layout,
    max_processes: int,
    expected_allowed: bool,
) -> None:
    shutil.copy2(PROBE_FIXTURE, layout.package / "probe.py")
    result = _run_child_with_open_stdin(
        _run_command(
            layout,
            [
                str(BASE_PYTHON),
                "-I",
                "-B",
                str(layout.package / "probe.py"),
                "spawn-once",
            ],
            max_processes=max_processes,
        )
    )
    assert result.returncode == 0, result.stderr
    payload = cast(dict[str, Any], json.loads(result.stdout))
    assert payload["allowed"] is expected_allowed
