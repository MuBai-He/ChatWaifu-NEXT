"""Run the loopback Runtime and Web client as one supervised local demo."""

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from pnpm_tool import PnpmToolError, environment_with_pnpm, resolve_pnpm

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_HEALTH = "http://127.0.0.1:8765/v1/runtime/health"
WEB_URL = "http://127.0.0.1:5173/"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ChatWaifu NEXT basic demo")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args()
    try:
        pnpm = resolve_pnpm()
    except PnpmToolError as error:
        parser.error(str(error))

    environment = environment_with_pnpm(pnpm)
    environment["PYTHONUNBUFFERED"] = "1"
    print("Checking Web dependencies...", flush=True)
    dependency_install = subprocess.run(
        [str(pnpm), "install", "--frozen-lockfile"],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if dependency_install.returncode != 0:
        return dependency_install.returncode

    processes: list[subprocess.Popen[bytes]] = []
    try:
        runtime = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "run_runtime.py")],
            cwd=ROOT,
            env=environment,
            start_new_session=True,
        )
        processes.append(runtime)
        _wait_for_url(RUNTIME_HEALTH, runtime, "Runtime")

        web = subprocess.Popen(
            [
                str(pnpm),
                "--filter",
                "@chatwaifu/web",
                "dev",
                "--host",
                "127.0.0.1",
                "--port",
                "5173",
            ],
            cwd=ROOT,
            env=environment,
            start_new_session=True,
        )
        processes.append(web)
        _wait_for_url(WEB_URL, web, "Web")
        print(f"\nChatWaifu NEXT is ready: {WEB_URL}")
        print("Press Ctrl+C to stop Runtime and Web.\n")
        if not args.no_open:
            webbrowser.open(WEB_URL)
        while all(process.poll() is None for process in processes):
            time.sleep(0.25)
        failed = next((process for process in processes if process.poll() not in {None, 0}), None)
        return failed.returncode if failed and failed.returncode is not None else 0
    except KeyboardInterrupt:
        return 0
    finally:
        _stop_processes(processes)


def _wait_for_url(
    url: str, process: subprocess.Popen[bytes], label: str, timeout_seconds: float = 20
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"{label} exited during startup with code {return_code}")
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if 200 <= response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise TimeoutError(f"{label} did not become ready at {url}")


def _stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    running = [process for process in processes if process.poll() is None]
    for process in running:
        _signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + 4
    for process in running:
        timeout = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait(timeout=2)


def _signal_group(process: subprocess.Popen[bytes], requested_signal: signal.Signals) -> None:
    if os.name == "posix":
        os.killpg(process.pid, requested_signal)
    else:
        process.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
