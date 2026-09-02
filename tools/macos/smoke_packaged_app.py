"""Launch a packaged macOS app and verify its embedded Runtime lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path


def main() -> int:
    arguments = _parser().parse_args()
    app = arguments.app.resolve()
    host = app / "Contents" / "MacOS" / "chatwaifu-desktop-host"
    runtime = app / "Contents" / "Resources" / "runtime-sidecar" / "chatwaifu-runtime"
    for executable in (host, runtime):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(f"packaged executable is unavailable: {executable}")

    with tempfile.TemporaryDirectory(prefix="chatwaifu-packaged-app-") as temporary:
        log_path = Path(temporary) / "host.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(host)],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        runtime_pid: int | None = None
        try:
            runtime_pid = _wait_for(
                lambda: _find_process(str(runtime), process.pid),
                timeout=arguments.timeout,
                description="embedded Runtime process",
                host=process,
                log_path=log_path,
            )
            port = _wait_for(
                lambda: _listener_port(runtime_pid),
                timeout=arguments.timeout,
                description="embedded Runtime listener",
                host=process,
                log_path=log_path,
            )
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/runtime/health", timeout=5
            ) as response:
                payload = json.load(response)
            if payload.get("status") != "ok":
                raise RuntimeError(f"unexpected Runtime health payload: {payload}")
            print(
                "Packaged macOS app smoke reached Runtime health: "
                f"host={process.pid} runtime={runtime_pid} port={port}"
            )
        finally:
            _terminate_host(process)
        if _process_exists(runtime_pid):
            os.kill(runtime_pid, signal.SIGTERM)
            raise RuntimeError(f"embedded Runtime survived host exit: pid={runtime_pid}")
    print("Packaged macOS app lifecycle smoke passed")
    return 0


def _find_process(executable: str, parent_id: int) -> int | None:
    output = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split(maxsplit=2)
        if len(fields) != 3:
            continue
        pid_text, parent_text, command = fields
        if int(parent_text) == parent_id and (
            command == executable or command.startswith(f"{executable} ")
        ):
            return int(pid_text)
    return None


def _listener_port(process_id: int) -> int | None:
    output = subprocess.run(
        ["lsof", "-nP", "-a", "-p", str(process_id), "-iTCP", "-sTCP:LISTEN"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout
    for line in output.splitlines()[1:]:
        for field in line.split():
            if field.startswith("127.0.0.1:"):
                return int(field.rsplit(":", 1)[1])
    return None


def _wait_for[T](
    probe: Callable[[], T | None],
    *,
    timeout: float,
    description: str,
    host: subprocess.Popen[bytes],
    log_path: Path,
) -> T:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value is not None:
            return value
        exit_code = host.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"packaged host exited before {description}: code={exit_code}\n"
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        time.sleep(0.1)
    raise RuntimeError(
        f"timed out waiting for {description}\n"
        f"{log_path.read_text(encoding='utf-8', errors='replace')}"
    )


def _terminate_host(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        killpg = getattr(os, "killpg", None)
        if callable(killpg):
            killpg(process.pid, signal.SIGTERM)
        else:
            process.kill()
        process.wait(timeout=10)


def _process_exists(process_id: int | None) -> bool:
    if process_id is None:
        return False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            os.kill(process_id, 0)
        except (ProcessLookupError, OSError):
            return False
        time.sleep(0.1)
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
