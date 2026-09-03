"""Exercise a frozen Runtime through its real bootstrap and HTTP boundaries."""

from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import TextIO, cast

BOOTSTRAP_PREFIX = "CHATWAIFU_BOOTSTRAP "
ROOT = Path(__file__).resolve().parents[1]
PYTHON_ENVIRONMENT_KEYS = {
    "NLTK_DATA",
    "PYTHONHOME",
    "PYTHONIOENCODING",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "PYTHONUTF8",
    "VIRTUAL_ENV",
}


def main() -> int:
    arguments = _parser().parse_args()
    executable = arguments.executable.resolve()
    if not executable.is_file():
        raise RuntimeError(f"Frozen Runtime does not exist: {executable}")
    with tempfile.TemporaryDirectory(prefix="chatwaifu-runtime-smoke-") as temporary:
        root = Path(temporary)
        runtime_url = _smoke_runtime(executable, root, arguments.timeout)
        _smoke_plugin_runner(executable, root)
    print(f"Frozen Runtime smoke passed: {runtime_url}")
    return 0


def _smoke_runtime(executable: Path, root: Path, timeout: float) -> str:
    environment = _clean_environment()
    environment.update(
        {
            "CHATWAIFU_CONFIG_DIR": str(root / "config"),
            "CHATWAIFU_DATA_DIR": str(root / "data"),
            "CHATWAIFU_LLM__PROVIDER": "demo",
            "CHATWAIFU_LLM__DEMO_CHUNK_DELAY_MS": "0",
            "CHATWAIFU_TTS__PROVIDER": "fake",
            "CHATWAIFU_STT__PROVIDER": "disabled",
        }
    )
    process = subprocess.Popen(
        [str(executable)],
        cwd=root,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: queue.Queue[tuple[str, str]] = queue.Queue()
    _pump(cast(TextIO, process.stdout), "stdout", lines)
    _pump(cast(TextIO, process.stderr), "stderr", lines)
    observed: list[str] = []
    runtime_url = ""
    runtime_token: str | None = None
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and process.poll() is None:
            try:
                source, line = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            observed.append(f"[{source}] {line}")
            if source == "stdout" and line.startswith(BOOTSTRAP_PREFIX):
                payload = cast(dict[str, object], json.loads(line.removeprefix(BOOTSTRAP_PREFIX)))
                runtime_url = str(payload["runtime_url"])
                runtime_token = cast(str | None, payload.get("token"))
                break
        if not runtime_url:
            raise RuntimeError(
                "Frozen Runtime did not publish bootstrap:\n" + "\n".join(observed[-100:])
            )
        health = cast(dict[str, object], _get_json(f"{runtime_url}/v1/runtime/health"))
        if health.get("status") != "ok":
            raise RuntimeError(f"Frozen Runtime health is not ok: {health}")
        characters = cast(
            dict[str, object],
            _get_json(f"{runtime_url}/v1/characters", token=runtime_token),
        )
        character_items = cast(list[dict[str, object]], characters.get("items", []))
        if not character_items:
            raise RuntimeError(f"Frozen Runtime has no packaged characters: {characters}")
        skills = cast(
            dict[str, object],
            _get_json(f"{runtime_url}/v1/skills", token=runtime_token),
        )
        items = cast(list[dict[str, object]], skills.get("items", []))
        if not any(item.get("skill_id") == "runtime.status" for item in items):
            raise RuntimeError(f"Frozen Runtime has no built-in runtime.status skill: {skills}")
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    _assert_listener_closed(runtime_url)
    return runtime_url


def _smoke_plugin_runner(executable: Path, root: Path) -> None:
    _smoke_packaged_channel_dependencies(executable, root)
    output = root / "plugin-result.txt"
    script = root / "plugin.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('plugin-ok', encoding='utf-8')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(executable), "--plugin-python", str(script), str(output)],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or output.read_text(encoding="utf-8") != "plugin-ok":
        raise RuntimeError(
            f"Frozen plugin runner failed ({result.returncode}): {result.stderr[-2_000:]}"
        )

    mcp_server = ROOT / "plugins" / "examples" / "local-echo" / "server.py"
    requests: list[dict[str, object]] = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "local_echo", "arguments": {"text": "你好，宁宁"}},
        },
    ]
    environment = _clean_environment()
    environment["PYTHONIOENCODING"] = "ascii"
    mcp = subprocess.run(
        [str(executable), "--plugin-python", str(mcp_server)],
        cwd=root,
        env=environment,
        input="".join(json.dumps(request, ensure_ascii=False) + "\n" for request in requests),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if mcp.returncode != 0:
        raise RuntimeError(f"Frozen MCP stdio failed ({mcp.returncode}): {mcp.stderr[-2_000:]}")
    responses = [json.loads(line) for line in mcp.stdout.splitlines() if line.strip()]
    if len(responses) != 2:
        raise RuntimeError(f"Frozen MCP handshake returned unexpected output: {mcp.stdout!r}")
    response = cast(dict[str, object], responses[1])
    result_payload = cast(dict[str, object], response["result"])
    structured = cast(dict[str, object], result_payload["structuredContent"])
    if structured.get("echo") != "你好，宁宁":
        raise RuntimeError(f"Frozen MCP UTF-8 round trip failed: {response}")

    _smoke_silero_vad(executable, root)


def _smoke_packaged_channel_dependencies(executable: Path, root: Path) -> None:
    """Prove native-channel dependencies survive PyInstaller collection.

    ``keyring`` discovers platform backends from package metadata and dynamic
    imports, while ``httpx`` needs its bundled CA roots for TLS. Importing only
    the top-level packages is therefore weaker than exercising both discovery
    paths inside the frozen interpreter.
    """

    script = root / "channel-dependencies-smoke.py"
    script.write_text(
        "import asyncio\n"
        "import ssl\n"
        "import sys\n"
        "import certifi\n"
        "import httpx\n"
        "import keyring\n"
        "\n"
        "async def probe_httpx():\n"
        "    context = ssl.create_default_context(cafile=certifi.where())\n"
        "    async with httpx.AsyncClient(verify=context, trust_env=False) as client:\n"
        "        assert client is not None\n"
        "\n"
        "asyncio.run(probe_httpx())\n"
        "backend = keyring.get_keyring()\n"
        "backend_module = type(backend).__module__\n"
        "assert backend_module.startswith('keyring.backends.'), backend_module\n"
        "if sys.platform in {'darwin', 'win32'}:\n"
        "    assert float(backend.priority) > 0, backend_module\n"
        "print(backend_module)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(executable), "--plugin-python", str(script)],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Frozen channel dependency probe failed "
            f"({result.returncode}): {result.stderr[-2_000:]}"
        )


def _smoke_silero_vad(executable: Path, root: Path) -> None:
    script = root / "silero-smoke.py"
    script.write_text(
        "from math import isfinite\n"
        "from pipecat.audio.vad.silero import SileroVADAnalyzer\n"
        "analyzer = SileroVADAnalyzer(sample_rate=16000)\n"
        "confidence = float(analyzer.voice_confidence(bytes(1024)))\n"
        "assert isfinite(confidence) and 0.0 <= confidence <= 1.0\n"
        "print(f'SILERO_OK {confidence:.6f}')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(executable), "--plugin-python", str(script)],
        cwd=root,
        env=_clean_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or "SILERO_OK " not in result.stdout:
        raise RuntimeError(
            f"Frozen Silero VAD failed ({result.returncode}): {result.stderr[-2_000:]}"
        )


def _clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CHATWAIFU_") and key not in PYTHON_ENVIRONMENT_KEYS
    }


def _pump(stream: TextIO, source: str, destination: queue.Queue[tuple[str, str]]) -> None:
    def read() -> None:
        for line in stream:
            destination.put((source, line.rstrip("\r\n")))

    threading.Thread(target=read, name=f"sidecar-smoke-{source}", daemon=True).start()


def _get_json(url: str, token: str | None = None) -> dict[str, object] | list[object]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return cast(dict[str, object] | list[object], json.load(response))


def _assert_listener_closed(runtime_url: str) -> None:
    if not runtime_url:
        return
    port = int(runtime_url.rsplit(":", 1)[1])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"Frozen Runtime listener remained open on port {port}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
