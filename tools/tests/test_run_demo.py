from __future__ import annotations

import importlib
import signal
import socket
import sys
from pathlib import Path
from typing import Any, cast

import pytest

TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT))
run_demo = cast(Any, importlib.import_module("run_demo"))


def test_demo_port_preflight_detects_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])

        assert run_demo._occupied_demo_ports((("Test", port),)) == (("Test", port),)


def test_main_rejects_occupied_port_before_dependency_setup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_dependency_setup(_name: str) -> str | None:
        raise AssertionError("dependency setup started")

    monkeypatch.setattr(run_demo, "_occupied_demo_ports", lambda: (("Runtime", 8765),))
    monkeypatch.setattr(run_demo.shutil, "which", fail_dependency_setup)
    monkeypatch.setattr(sys, "argv", ["run_demo.py", "--no-open"])

    with pytest.raises(SystemExit) as raised:
        run_demo.main()

    assert raised.value.code == 2
    assert "Runtime 8765" in capsys.readouterr().err


def test_termination_signal_enters_graceful_cleanup_path() -> None:
    with pytest.raises(run_demo.TerminationRequested):
        run_demo._raise_termination(signal.SIGTERM, None)
