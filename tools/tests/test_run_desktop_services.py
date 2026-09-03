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
    assert "CHATWAIFU_SECURITY__CAPABILITY_TOKEN" not in environment


def test_runtime_environment_injects_capability_token_when_present() -> None:
    environment = desktop_services._runtime_environment(
        {},
        runtime_port=41001,
        stt_port=None,
        stt_token=None,
        tts_profiles={},
        ports={"runtime": 41001},
        tokens={"runtime": "test-runtime-capability-token"},
    )
    assert environment["CHATWAIFU_SECURITY__CAPABILITY_TOKEN"] == "test-runtime-capability-token"


def test_bootstrap_line_is_machine_readable_with_token(capsys: Any) -> None:
    desktop_services._write_bootstrap(
        "http://127.0.0.1:41001",
        1234,
        {"runtime": 41001, "stt": 41002, "qwen3_tts_mlx": 41003},
        token="desktop-token-xyz",
    )

    line = capsys.readouterr().out.strip()
    assert line.startswith(desktop_services.BOOTSTRAP_PREFIX)
    payload = json.loads(line.removeprefix(desktop_services.BOOTSTRAP_PREFIX))
    assert payload["type"] == "runtime.ready"
    assert payload["runtime_url"] == "http://127.0.0.1:41001"
    assert payload["workers"] == ["qwen3_tts_mlx", "stt"]
    assert payload["token"] == "desktop-token-xyz"


def test_bootstrap_line_is_machine_readable_without_token(capsys: Any) -> None:
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
    assert payload["token"] is None


def test_dev_service_lifecycle_stops_owned_runtime(
    monkeypatch: Any,
) -> None:
    events: list[str] = []

    class _Process:
        pid = 4321
        returncode = 0

        def poll(self) -> int:
            return 0

    def no_profiles(**_kwargs: object) -> dict[str, dict[str, object]]:
        return {}

    def no_stt(**_kwargs: object) -> None:
        return None

    def runtime_port(*_args: object, **_kwargs: object) -> dict[str, int]:
        return {"runtime": 41001}

    def ignore(*_args: object, **_kwargs: object) -> None:
        return None

    def spawn(*_args: object, **_kwargs: object) -> _Process:
        return _Process()

    monkeypatch.setattr(desktop_services, "_load_available_tts_profiles", no_profiles)
    monkeypatch.setattr(desktop_services, "_resolve_stt_worker_python", no_stt)
    monkeypatch.setattr(desktop_services, "_allocate_ports", runtime_port)
    monkeypatch.setattr(desktop_services, "_start_parent_watchdog", ignore)
    monkeypatch.setattr(desktop_services.signal, "signal", ignore)
    monkeypatch.setattr(desktop_services.subprocess, "Popen", spawn)
    monkeypatch.setattr(desktop_services, "_wait_for_url", ignore)
    monkeypatch.setattr(desktop_services, "_write_bootstrap", ignore)

    def stop(_processes: object) -> None:
        events.append("runtime.stop")

    monkeypatch.setattr(desktop_services, "_stop_processes", stop)

    assert desktop_services.main() == 0
    assert events == ["runtime.stop"]


def test_dev_service_lifecycle_probes_authenticated_endpoint_before_bootstrap(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    class _Process:
        pid = 4321
        returncode = 0

        def poll(self) -> int:
            return 0

    def no_profiles(**_kwargs: object) -> dict[str, dict[str, object]]:
        return {}

    def no_stt(**_kwargs: object) -> None:
        return None

    def runtime_port(*_args: object, **_kwargs: object) -> dict[str, int]:
        return {"runtime": 41001}

    def ignore(*_args: object, **_kwargs: object) -> None:
        return None

    def spawn(*_args: object, **_kwargs: object) -> _Process:
        return _Process()

    def record_wait(
        url: str,
        _proc: object,
        _label: str,
        timeout_seconds: float = 20,
        headers: dict[str, str] | None = None,
    ) -> None:
        calls.append((url, headers))

    bootstrap_received: list[dict[str, object]] = []

    def record_bootstrap(
        url: str, pid: int, ports: dict[str, int], token: str | None = None
    ) -> None:
        bootstrap_received.append({"url": url, "pid": pid, "ports": ports, "token": token})

    monkeypatch.setattr(desktop_services, "_load_available_tts_profiles", no_profiles)
    monkeypatch.setattr(desktop_services, "_resolve_stt_worker_python", no_stt)
    monkeypatch.setattr(desktop_services, "_allocate_ports", runtime_port)
    monkeypatch.setattr(desktop_services, "_start_parent_watchdog", ignore)
    monkeypatch.setattr(desktop_services.signal, "signal", ignore)
    monkeypatch.setattr(desktop_services.subprocess, "Popen", spawn)
    monkeypatch.setattr(desktop_services, "_wait_for_url", record_wait)
    monkeypatch.setattr(desktop_services, "_write_bootstrap", record_bootstrap)
    monkeypatch.setattr(desktop_services, "_stop_processes", ignore)

    assert desktop_services.main() == 0

    assert len(calls) == 2
    assert calls[0][0] == "http://127.0.0.1:41001/v1/runtime/health"
    assert calls[0][1] is None
    assert calls[1][0] == "http://127.0.0.1:41001/v1/characters"
    assert calls[1][1] is not None
    token = bootstrap_received[0]["token"]
    assert token is not None
    assert calls[1][1]["Authorization"] == f"Bearer {token}"


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


def test_windows_process_probe_does_not_send_a_console_signal(monkeypatch: Any) -> None:
    probes: list[int] = []
    signals: list[tuple[int, int]] = []

    def probe(process_id: int) -> bool:
        probes.append(process_id)
        return False

    def record_signal(process_id: int, signal_number: int) -> None:
        signals.append((process_id, signal_number))

    monkeypatch.setattr(desktop_services, "_windows_process_exists", probe)
    monkeypatch.setattr(desktop_services.os, "kill", record_signal)

    assert desktop_services._process_exists(4242, platform_name="nt") is False
    assert probes == [4242]
    assert signals == []
