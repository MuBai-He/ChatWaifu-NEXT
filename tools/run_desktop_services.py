"""Run the local Runtime and model workers as one desktop-owned sidecar stack."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from types import FrameType

from run_demo import (
    ROOT,
    _find_free_loopback_port,
    _load_tts_profiles,
    _profile_python,
    _stop_processes,
    _stt_worker_python,
    _tts_worker_environment,
    _wait_for_url,
)

BOOTSTRAP_PREFIX = "CHATWAIFU_BOOTSTRAP "
STACK_VERSION = "1.0"


class TerminationRequested(Exception):
    """Translate process termination into the normal child cleanup path."""


def main() -> int:
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    tts_profiles = _load_tts_profiles()
    ports = _allocate_ports(tuple(tts_profiles))
    tokens = {name: secrets.token_urlsafe(32) for name in ports if name != "runtime"}
    processes: list[subprocess.Popen[bytes]] = []

    signal.signal(signal.SIGTERM, _raise_termination)
    hangup = getattr(signal, "SIGHUP", None)
    if isinstance(hangup, signal.Signals):
        signal.signal(hangup, _raise_termination)
    _start_parent_watchdog(environment)

    try:
        stt_environment = environment.copy()
        stt_environment.update(
            {
                "CHATWAIFU_STT_WORKER_HOST": "127.0.0.1",
                "CHATWAIFU_STT_WORKER_PORT": str(ports["stt"]),
                "CHATWAIFU_STT_WORKER_TOKEN": tokens["stt"],
                "CHATWAIFU_STT_WORKER_MODEL": "base",
                "CHATWAIFU_STT_WORKER_MODEL_DIR": str(
                    ROOT / ".local" / "models" / "faster-whisper"
                ),
                "CHATWAIFU_STT_WORKER_DEVICE": "cpu",
                "CHATWAIFU_STT_WORKER_COMPUTE_TYPE": "int8",
                # Desktop startup stays light; the first intentional voice turn wakes ASR.
                "CHATWAIFU_STT_WORKER_PRELOAD": "false",
            }
        )
        stt_worker = subprocess.Popen(
            [str(_stt_worker_python()), "-m", "chatwaifu_asr_worker.main"],
            cwd=ROOT,
            env=stt_environment,
            start_new_session=os.name == "posix",
        )
        processes.append(stt_worker)
        _wait_for_url(
            f"http://127.0.0.1:{ports['stt']}/v1/health",
            stt_worker,
            "Local STT worker",
            timeout_seconds=30,
            headers={"Authorization": f"Bearer {tokens['stt']}"},
        )

        for provider_id, profile in tts_profiles.items():
            worker = subprocess.Popen(
                [str(_profile_python(profile)), "-m", "chatwaifu_tts_neural_worker.main"],
                cwd=ROOT,
                env=_tts_worker_environment(
                    environment,
                    provider_id,
                    profile,
                    ports[provider_id],
                    tokens[provider_id],
                ),
                start_new_session=os.name == "posix",
            )
            processes.append(worker)
            _wait_for_url(
                f"http://127.0.0.1:{ports[provider_id]}/v1/health",
                worker,
                str(profile["display_name"]),
                timeout_seconds=30,
                headers={"Authorization": f"Bearer {tokens[provider_id]}"},
            )

        runtime_environment = _runtime_environment(
            environment,
            runtime_port=ports["runtime"],
            stt_port=ports["stt"],
            stt_token=tokens["stt"],
            tts_profiles=tts_profiles,
            ports=ports,
            tokens=tokens,
        )
        runtime = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "run_runtime.py")],
            cwd=ROOT,
            env=runtime_environment,
            start_new_session=os.name == "posix",
        )
        processes.append(runtime)
        runtime_url = f"http://127.0.0.1:{ports['runtime']}"
        _wait_for_url(f"{runtime_url}/v1/runtime/health", runtime, "Runtime")
        _write_bootstrap(runtime_url, runtime.pid, ports)

        while all(process.poll() is None for process in processes):
            time.sleep(0.25)
        failed = next((item for item in processes if item.poll() not in {None, 0}), None)
        return failed.returncode if failed and failed.returncode is not None else 0
    except (KeyboardInterrupt, TerminationRequested):
        return 0
    finally:
        _stop_processes(processes)


def _allocate_ports(provider_ids: tuple[str, ...]) -> dict[str, int]:
    names = ("runtime", "stt", *provider_ids)
    ports: dict[str, int] = {}
    allocated: set[int] = set()
    for name in names:
        port = _find_free_loopback_port()
        while port in allocated:
            port = _find_free_loopback_port()
        allocated.add(port)
        ports[name] = port
    return ports


def _runtime_environment(
    base: dict[str, str],
    *,
    runtime_port: int,
    stt_port: int,
    stt_token: str,
    tts_profiles: dict[str, dict[str, object]],
    ports: dict[str, int],
    tokens: dict[str, str],
) -> dict[str, str]:
    environment = base.copy()
    environment.update(
        {
            "CHATWAIFU_RUNTIME__PORT": str(runtime_port),
            "CHATWAIFU_STT__PROVIDER": "faster_whisper_worker",
            "CHATWAIFU_STT__WORKER_URL": f"http://127.0.0.1:{stt_port}",
            "CHATWAIFU_STT__WORKER_TOKEN": stt_token,
            "CHATWAIFU_STT__LANGUAGE": "zh",
            "CHATWAIFU_TTS__PROVIDER": "null",
            "CHATWAIFU_TTS__DEFAULT_PROVIDER": "qwen3_tts_mlx",
            "CHATWAIFU_TTS__WORKERS": json.dumps(
                {
                    provider_id: {
                        "url": f"http://127.0.0.1:{ports[provider_id]}",
                        "token": tokens[provider_id],
                        "display_name": str(profile["display_name"]),
                        "model": str(profile["model"]),
                        "languages": ["zh", "ja", "en"],
                        "supports_voice_cloning": not (
                            provider_id == "qwen3_tts_mlx" and profile.get("qwen_voice")
                        ),
                        "supports_style": False,
                        "supports_speed": False,
                        "supports_pitch": False,
                        # Worker protocol v1 still returns a complete WAV.
                        "native_streaming": False,
                    }
                    for provider_id, profile in tts_profiles.items()
                },
                ensure_ascii=False,
            ),
        }
    )
    return environment


def _write_bootstrap(runtime_url: str, pid: int, ports: dict[str, int]) -> None:
    payload = {
        "schema_version": STACK_VERSION,
        "type": "runtime.ready",
        "runtime_url": runtime_url,
        "pid": pid,
        "workers": sorted(name for name in ports if name != "runtime"),
    }
    print(f"{BOOTSTRAP_PREFIX}{json.dumps(payload, separators=(',', ':'))}", flush=True)


def _raise_termination(signum: int, _frame: FrameType | None) -> None:
    raise TerminationRequested(signum)


def _start_parent_watchdog(environment: dict[str, str]) -> None:
    raw_parent_pid = environment.get("CHATWAIFU_DESKTOP_PARENT_PID", "").strip()
    if not raw_parent_pid:
        return
    try:
        parent_pid = int(raw_parent_pid)
    except ValueError as error:
        raise RuntimeError("CHATWAIFU_DESKTOP_PARENT_PID must be an integer") from error
    if parent_pid <= 1 or parent_pid == os.getpid():
        raise RuntimeError("CHATWAIFU_DESKTOP_PARENT_PID is not a valid supervisor")
    threading.Thread(
        target=_watch_parent,
        args=(parent_pid,),
        name="desktop-parent-watchdog",
        daemon=True,
    ).start()


def _watch_parent(parent_pid: int) -> None:
    while _process_exists(parent_pid):
        time.sleep(0.5)
    os.kill(os.getpid(), signal.SIGTERM)


def _process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if __name__ == "__main__":
    raise SystemExit(main())
