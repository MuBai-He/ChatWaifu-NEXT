"""Packaged desktop Runtime path and fallback behavior."""

# pyright: reportPrivateUsage=false, reportUnknownVariableType=false

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from chatwaifu_model_worker import InstalledWorkerPack
from chatwaifu_model_worker import pack_installer as pack_installer_module
from chatwaifu_runtime.desktop_sidecar import (
    _run_plugin_python,
    _run_worker_pack_command,
    prepare_environment,
)
from chatwaifu_runtime.runtime_skills.transports import _resolve_command


def test_packaged_environment_separates_resources_from_writable_data(tmp_path: Path) -> None:
    resources = tmp_path / "runtime-resources"
    config = tmp_path / "user" / "config"
    data = tmp_path / "user" / "data"
    environment = {
        "CHATWAIFU_CONFIG_DIR": str(config),
        "CHATWAIFU_DATA_DIR": str(data),
    }

    prepared = prepare_environment(environment, frozen=True, resource_root=resources)

    assert prepared["CHATWAIFU_RESOURCE_ROOT"] == str(resources)
    assert prepared["CHATWAIFU_CHARACTERS_DIR"] == str(resources / "characters")
    assert prepared["CHATWAIFU_SKILLS_DIR"] == str(resources / "skills")
    assert prepared["NLTK_DATA"] == str(resources / "nltk_data")
    assert prepared["CHATWAIFU_CONFIG_DIR"] == str(config)
    assert prepared["CHATWAIFU_DATA_DIR"] == str(data)
    assert config.is_dir()
    assert data.is_dir()


def test_packaged_environment_starts_without_optional_local_models(tmp_path: Path) -> None:
    environment = {
        "CHATWAIFU_CONFIG_DIR": str(tmp_path / "config"),
        "CHATWAIFU_DATA_DIR": str(tmp_path / "data"),
    }

    prepared = prepare_environment(
        environment,
        frozen=True,
        resource_root=tmp_path / "resources",
    )

    assert prepared["CHATWAIFU_STT__PROVIDER"] == "disabled"
    assert prepared["CHATWAIFU_TTS__PROVIDER"] == "fake"
    assert prepared["CHATWAIFU_TTS__DEFAULT_PROVIDER"] == "fake"
    assert prepared["CHATWAIFU_TTS__WORKERS"] == "{}"


def test_packaged_plugin_python_executes_declared_script(tmp_path: Path) -> None:
    output = tmp_path / "result.txt"
    script = tmp_path / "plugin.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')\n",
        encoding="utf-8",
    )

    result = _run_plugin_python([str(script), str(output), "插件已运行"])

    assert result == 0
    assert output.read_text(encoding="utf-8") == "插件已运行"


def test_frozen_runtime_worker_pack_role_lists_the_owner_data_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment = {
        "CHATWAIFU_CONFIG_DIR": str(tmp_path / "config"),
        "CHATWAIFU_DATA_DIR": str(tmp_path / "data"),
    }

    result = _run_worker_pack_command(["list"], environment)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"action": "listed", "packs": [], "errors": []}


def test_frozen_runtime_repairs_and_reactivates_an_invalid_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = tmp_path / "candidate.cwpack"
    archive.write_bytes(b"verified elsewhere")
    data_root = tmp_path / "data"
    config_root = tmp_path / "config"
    installed = cast(
        InstalledWorkerPack,
        SimpleNamespace(
            root=data_root / "worker-packs" / "test-qwen" / "0.1.0",
            manifest=SimpleNamespace(
                pack_id="test-qwen",
                version="0.1.0",
                worker=SimpleNamespace(kind="tts"),
            ),
        ),
    )
    calls: list[tuple[str, Path]] = []

    def repair(candidate: Path, root: Path) -> InstalledWorkerPack:
        assert candidate == archive
        calls.append(("repair", root))
        return installed

    def activate(
        pack_id: str,
        *,
        version: str | None = None,
        root: Path,
        config_root: Path,
    ) -> tuple[InstalledWorkerPack, Path]:
        assert (pack_id, version) == ("test-qwen", "0.1.0")
        calls.append(("activate", root))
        return installed, config_root / "local-ai-selection.json"

    monkeypatch.setattr(pack_installer_module, "repair_archive", repair)
    monkeypatch.setattr(pack_installer_module, "activate_pack", activate)
    environment: dict[str, str] = {
        "CHATWAIFU_CONFIG_DIR": str(config_root),
        "CHATWAIFU_DATA_DIR": str(data_root),
    }

    result = _run_worker_pack_command(["repair", str(archive)], environment)

    assert result == 0
    assert calls == [
        ("repair", data_root / "worker-packs"),
        ("activate", data_root / "worker-packs"),
    ]
    payload = cast(dict[str, Any], json.loads(capsys.readouterr().out))
    assert payload["action"] == "repaired_and_activated"
    assert payload["restart_required"] is True


def test_frozen_runtime_routes_python_plugins_to_plugin_role(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = tmp_path / "server.py"
    script.write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    command, arguments = _resolve_command(
        ["python", "server.py"],
        tmp_path,
        restrict_to_root=True,
    )

    assert command == sys.executable
    assert arguments == ["--plugin-python", str(script)]


def test_packaged_plugin_stdio_is_unbuffered_utf8() -> None:
    server = (
        Path(__file__).resolve().parents[3] / "plugins" / "examples" / "local-echo" / "server.py"
    )
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "local_echo",
                "arguments": {"text": "你好，宁宁"},
            },
        },
    ]
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "ascii"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "chatwaifu_runtime.desktop_sidecar",
            "--plugin-python",
            str(server),
        ],
        input="".join(json.dumps(item, ensure_ascii=False) + "\n" for item in requests),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert responses[1]["result"]["structuredContent"]["echo"] == "你好，宁宁"
