from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT))
desktop_services = cast(Any, importlib.import_module("run_desktop_services"))


def test_allocated_desktop_ports_are_unique(monkeypatch: Any) -> None:
    values = iter((41001, 41002, 41002, 41003, 41004))
    monkeypatch.setattr(desktop_services, "_find_free_loopback_port", lambda: next(values))

    ports = desktop_services._allocate_ports(("qwen3_tts_mlx", "gpt_sovits"))

    assert ports == {
        "runtime": 41001,
        "stt": 41002,
        "qwen3_tts_mlx": 41003,
        "gpt_sovits": 41004,
    }


def test_optional_workers_can_leave_only_the_runtime_port(monkeypatch: Any) -> None:
    monkeypatch.setattr(desktop_services, "_find_free_loopback_port", lambda: 41001)

    ports = desktop_services._allocate_ports((), include_stt=False)

    assert ports == {"runtime": 41001}


def test_optional_worker_mode_accepts_explicit_truthy_values() -> None:
    for value in ("1", "true", "TRUE", "yes", "on"):
        assert desktop_services._optional_local_workers_enabled(
            {"CHATWAIFU_DESKTOP_OPTIONAL_LOCAL_WORKERS": value}
        )
    assert desktop_services._optional_local_workers_enabled({}) is False


def test_missing_optional_tts_profile_degrades_to_no_local_workers(
    monkeypatch: Any,
) -> None:
    def missing_profiles() -> dict[str, dict[str, object]]:
        raise RuntimeError("profile missing")

    monkeypatch.setattr(desktop_services, "_load_tts_profiles", missing_profiles)

    assert desktop_services._load_available_tts_profiles(optional=True) == {}


def test_runtime_environment_uses_safe_fallback_without_local_workers() -> None:
    environment = desktop_services._runtime_environment(
        {},
        runtime_port=41001,
        stt_port=None,
        stt_token=None,
        tts_profiles={},
        ports={"runtime": 41001},
        tokens={},
    )

    assert environment["CHATWAIFU_STT__PROVIDER"] == "disabled"
    assert environment["CHATWAIFU_TTS__PROVIDER"] == "fake"
    assert environment["CHATWAIFU_TTS__DEFAULT_PROVIDER"] == "fake"
    assert json.loads(environment["CHATWAIFU_TTS__WORKERS"]) == {}
    assert "CHATWAIFU_STT__WORKER_TOKEN" not in environment


def test_bootstrap_line_is_machine_readable_and_secret_free(capsys: Any) -> None:
    desktop_services._write_bootstrap(
        "http://127.0.0.1:41001",
        1234,
        {"runtime": 41001, "stt": 41002, "qwen3_tts_mlx": 41003},
    )

    line = capsys.readouterr().out.strip()
    assert line.startswith(desktop_services.BOOTSTRAP_PREFIX)
    payload = json.loads(line.removeprefix(desktop_services.BOOTSTRAP_PREFIX))
    assert payload["type"] == "runtime.ready"
    assert payload["runtime_url"] == "http://127.0.0.1:41001"
    assert payload["workers"] == ["qwen3_tts_mlx", "stt"]
    assert "token" not in line.casefold()


def test_parent_watchdog_requires_a_real_supervisor_pid(monkeypatch: Any) -> None:
    started: list[tuple[object, ...]] = []

    class _Thread:
        def __init__(self, *, args: tuple[object, ...], **_kwargs: object) -> None:
            started.append(args)

        def start(self) -> None:
            return None

    monkeypatch.setattr(desktop_services.threading, "Thread", _Thread)

    desktop_services._start_parent_watchdog({"CHATWAIFU_DESKTOP_PARENT_PID": str(os.getppid())})

    assert started == [(os.getppid(),)]
    assert desktop_services._process_exists(os.getpid()) is True
