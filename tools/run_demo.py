"""Run the loopback Runtime and Web client as one supervised local demo."""

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import cast

from pnpm_tool import PnpmToolError, environment_with_pnpm, resolve_pnpm

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_HEALTH = "http://127.0.0.1:8765/v1/runtime/health"
WEB_URL = "http://127.0.0.1:5173/"
STT_WORKER = ROOT / "workers" / "asr-faster-whisper"
NEURAL_TTS_SETUP = ROOT / "tools" / "setup_neural_tts_workers.py"
TTS_PROFILE_PATH = ROOT / ".local" / "config" / "tts-profiles.toml"


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
    print("Checking isolated Qwen/GPT-SoVITS workers...", flush=True)
    tts_install = subprocess.run(
        [sys.executable, str(NEURAL_TTS_SETUP)],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if tts_install.returncode != 0:
        return tts_install.returncode
    tts_profiles = _load_tts_profiles()

    stt_port = _find_free_loopback_port()
    stt_token = secrets.token_urlsafe(32)
    tts_ports: dict[str, int] = {}
    tts_tokens: dict[str, str] = {}
    allocated_ports = {stt_port}
    for provider_id in ("qwen3_tts_mlx", "gpt_sovits"):
        port = _find_free_loopback_port()
        while port in allocated_ports:
            port = _find_free_loopback_port()
        allocated_ports.add(port)
        tts_ports[provider_id] = port
        tts_tokens[provider_id] = secrets.token_urlsafe(32)
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
            # Clear the legacy single-provider override from an older local .env.
            "CHATWAIFU_TTS__PROVIDER": "null",
            "CHATWAIFU_TTS__DEFAULT_PROVIDER": "qwen3_tts_mlx",
            "CHATWAIFU_TTS__WORKERS": json.dumps(
                {
                    provider_id: {
                        "url": f"http://127.0.0.1:{tts_ports[provider_id]}",
                        "token": tts_tokens[provider_id],
                        "display_name": str(profile["display_name"]),
                        "model": str(profile["model"]),
                        "languages": ["zh", "ja", "en"],
                        "supports_voice_cloning": not (
                            provider_id == "qwen3_tts_mlx" and profile.get("qwen_voice")
                        ),
                        "supports_style": False,
                        "supports_speed": False,
                        "supports_pitch": False,
                        "native_streaming": provider_id == "qwen3_tts_mlx",
                    }
                    for provider_id, profile in tts_profiles.items()
                },
                ensure_ascii=False,
            ),
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
        for provider_id, profile in tts_profiles.items():
            print(f"Starting {profile['display_name']} worker (lazy model load)...", flush=True)
            tts_worker = subprocess.Popen(
                [
                    str(_profile_python(profile)),
                    "-m",
                    "chatwaifu_tts_neural_worker.main",
                ],
                cwd=ROOT,
                env=_tts_worker_environment(
                    environment,
                    provider_id,
                    profile,
                    tts_ports[provider_id],
                    tts_tokens[provider_id],
                ),
                start_new_session=True,
            )
            processes.append(tts_worker)
            _wait_for_url(
                f"http://127.0.0.1:{tts_ports[provider_id]}/v1/health",
                tts_worker,
                str(profile["display_name"]),
                timeout_seconds=30,
                headers={"Authorization": f"Bearer {tts_tokens[provider_id]}"},
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


def _worker_python(worker: Path) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    path = worker / ".venv" / directory / executable
    if not path.exists():
        raise RuntimeError(f"Local worker interpreter is missing: {path}")
    return path


def _stt_worker_python() -> Path:
    return _worker_python(STT_WORKER)


def _load_tts_profiles() -> dict[str, dict[str, object]]:
    if not TTS_PROFILE_PATH.exists():
        raise RuntimeError(
            f"Local neural TTS profile is missing: {TTS_PROFILE_PATH}. "
            "Copy config/tts-profiles.example.toml and fill local model/reference paths."
        )
    with TTS_PROFILE_PATH.open("rb") as profile_file:
        loaded = tomllib.load(profile_file)
    profiles: dict[str, dict[str, object]] = {}
    required_common = {
        "environment",
        "display_name",
        "model",
        "vendor_dir",
        "reference_audio",
        "reference_text",
        "reference_language",
    }
    for provider_id in ("qwen3_tts_mlx", "gpt_sovits"):
        raw = loaded.get(provider_id)
        if not isinstance(raw, dict):
            raise RuntimeError(f"TTS profile [{provider_id}] is missing")
        raw_profile = cast(dict[str, object], raw)
        missing = required_common - raw_profile.keys()
        if provider_id == "qwen3_tts_mlx":
            missing |= {"model_dir"} - raw_profile.keys()
        else:
            missing |= {"gpt_weights", "sovits_weights"} - raw_profile.keys()
        if missing:
            raise RuntimeError(
                f"TTS profile [{provider_id}] is missing: {', '.join(sorted(missing))}"
            )
        profile = dict(raw_profile)
        for key in (
            "environment",
            "vendor_dir",
            "model_dir",
            "gpt_weights",
            "sovits_weights",
            "reference_audio",
        ):
            value = profile.get(key)
            if isinstance(value, str):
                path = Path(value).expanduser()
                profile[key] = path if path.is_absolute() else ROOT / path
        for key in ("environment", "vendor_dir", "reference_audio"):
            _require_path(provider_id, key, profile[key])
        for key in ("model_dir", "gpt_weights", "sovits_weights"):
            if key in profile:
                _require_path(provider_id, key, profile[key])
        profiles[provider_id] = profile
    return profiles


def _require_path(provider_id: str, key: str, value: object) -> None:
    if not isinstance(value, Path) or not value.exists():
        raise RuntimeError(f"TTS profile [{provider_id}] {key} does not exist: {value}")


def _profile_python(profile: dict[str, object]) -> Path:
    environment_root = profile["environment"]
    if not isinstance(environment_root, Path):
        raise RuntimeError("TTS profile environment must resolve to a local path")
    executable = environment_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.exists():
        raise RuntimeError(f"TTS environment interpreter is missing: {executable}")
    return executable


def _tts_worker_environment(
    base: dict[str, str],
    provider_id: str,
    profile: dict[str, object],
    port: int,
    token: str,
) -> dict[str, str]:
    environment = base.copy()
    values: dict[str, object] = {
        "HOST": "127.0.0.1",
        "PORT": port,
        "TOKEN": token,
        "BACKEND": provider_id,
        "PROVIDER_ID": provider_id,
        "DISPLAY_NAME": profile["display_name"],
        "WORKER_ID": f"tts-{provider_id}",
        "MODEL": profile["model"],
        "VENDOR_DIR": profile["vendor_dir"],
        "REFERENCE_AUDIO": profile["reference_audio"],
        "REFERENCE_TEXT": profile["reference_text"],
        "REFERENCE_LANGUAGE": profile["reference_language"],
        "DEVICE": profile.get("device", "mlx" if provider_id == "qwen3_tts_mlx" else "cpu"),
        "PRELOAD": "false",
        "STREAMING_INTERVAL": profile.get("streaming_interval", 0.5),
        "TEMPERATURE": profile.get("temperature", 0.7),
    }
    for key in ("model_dir", "gpt_weights", "sovits_weights"):
        if key in profile:
            values[key.upper()] = profile[key]
    if "qwen_voice" in profile:
        values["QWEN_VOICE"] = profile["qwen_voice"]
    prefix = "CHATWAIFU_NEURAL_TTS_WORKER_"
    environment.update({f"{prefix}{key}": str(value) for key, value in values.items()})
    return environment


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
