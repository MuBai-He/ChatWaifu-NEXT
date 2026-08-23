"""Run the loopback Runtime and Web client as one supervised local demo."""

import argparse
import os
import secrets
import shutil
import signal
import socket
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
STT_WORKER = ROOT / "workers" / "asr-faster-whisper"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ChatWaifu NEXT basic demo")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args()
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv is required; install uv and run `make demo` again")
    try:
        pnpm = resolve_pnpm()
    except PnpmToolError as error:
        parser.error(str(error))

    environment = environment_with_pnpm(pnpm)
    environment["PYTHONUNBUFFERED"] = "1"
    print("Checking Python workspace dependencies...", flush=True)
    python_install = subprocess.run(
        [uv, "sync", "--all-packages", "--all-groups"],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if python_install.returncode != 0:
        return python_install.returncode
    print("Checking Web dependencies...", flush=True)
    dependency_install = subprocess.run(
        [str(pnpm), "install", "--frozen-lockfile"],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if dependency_install.returncode != 0:
        return dependency_install.returncode

    print("Checking isolated local STT worker...", flush=True)
    worker_install = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "setup_stt_worker.py")],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if worker_install.returncode != 0:
        return worker_install.returncode

    stt_port = _find_free_loopback_port()
    stt_token = secrets.token_urlsafe(32)
    worker_environment = environment.copy()
    worker_environment.update(
        {
            "CHATWAIFU_STT_WORKER_HOST": "127.0.0.1",
            "CHATWAIFU_STT_WORKER_PORT": str(stt_port),
            "CHATWAIFU_STT_WORKER_TOKEN": stt_token,
            "CHATWAIFU_STT_WORKER_MODEL": "base",
            "CHATWAIFU_STT_WORKER_MODEL_DIR": str(ROOT / ".local" / "models" / "faster-whisper"),
            "CHATWAIFU_STT_WORKER_DEVICE": "cpu",
            "CHATWAIFU_STT_WORKER_COMPUTE_TYPE": "int8",
            "CHATWAIFU_STT_WORKER_PRELOAD": "true",
        }
    )
    runtime_environment = environment.copy()
    runtime_environment.update(
        {
            "CHATWAIFU_STT__PROVIDER": "faster_whisper_worker",
            "CHATWAIFU_STT__WORKER_URL": f"http://127.0.0.1:{stt_port}",
            "CHATWAIFU_STT__WORKER_TOKEN": stt_token,
            "CHATWAIFU_STT__LANGUAGE": "zh",
        }
    )

    processes: list[subprocess.Popen[bytes]] = []
    try:
        print(
            "Loading faster-whisper base (the first run downloads about 150 MB)...",
            flush=True,
        )
        stt_worker = subprocess.Popen(
            [str(_stt_worker_python()), "-m", "chatwaifu_asr_worker.main"],
            cwd=ROOT,
            env=worker_environment,
            start_new_session=True,
        )
        processes.append(stt_worker)
        _wait_for_url(
            f"http://127.0.0.1:{stt_port}/v1/health",
            stt_worker,
            "Local STT worker",
            timeout_seconds=180,
            headers={"Authorization": f"Bearer {stt_token}"},
        )
        runtime = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "run_runtime.py")],
            cwd=ROOT,
            env=runtime_environment,
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
    url: str,
    process: subprocess.Popen[bytes],
    label: str,
    timeout_seconds: float = 20,
    headers: dict[str, str] | None = None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"{label} exited during startup with code {return_code}")
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=0.5) as response:
                if 200 <= response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise TimeoutError(f"{label} did not become ready at {url}")


def _find_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _stt_worker_python() -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    path = STT_WORKER / ".venv" / directory / executable
    if not path.exists():
        raise RuntimeError(f"Local STT worker interpreter is missing: {path}")
    return path


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
