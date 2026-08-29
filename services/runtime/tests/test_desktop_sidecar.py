"""Packaged desktop Runtime path and fallback behavior."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from chatwaifu_runtime.desktop_sidecar import _run_plugin_python, prepare_environment
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
