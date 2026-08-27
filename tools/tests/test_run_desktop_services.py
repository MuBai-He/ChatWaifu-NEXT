from __future__ import annotations

import importlib
import json
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
