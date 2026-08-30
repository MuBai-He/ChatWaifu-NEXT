"""Smoke a Windows worker pack from an extracted, toolchain-free environment."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from array import array
from pathlib import Path
from typing import cast

from chatwaifu_model_worker import (
    WorkerPackEntrypoint,
    WorkerPackManifest,
    install_archive,
)

BUFFER_SIZE = 4 * 1024 * 1024
PYTHON_ENVIRONMENT_KEYS = {
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "VIRTUAL_ENV",
    "UV_PROJECT",
    "UV_PROJECT_ENVIRONMENT",
}
PROXY_KEYS = {
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
}


def smoke_pack(
    archive: Path,
    *,
    kind: str,
    timeout: float,
    smoke_wav: Path | None,
    output_directory: Path,
) -> dict[str, object]:
    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chatwaifu-worker-pack-smoke-") as temporary:
        installed = install_archive(archive, Path(temporary) / "installed-packs")
        manifest = installed.manifest
        if manifest.worker.kind != kind:
            raise RuntimeError(f"Worker pack kind is {manifest.worker.kind!r}, expected {kind!r}")
        result = _smoke_extracted(
            manifest,
            installed.root,
            kind=kind,
            timeout=timeout,
            smoke_wav=smoke_wav,
            output_directory=output_directory,
        )
    return result


def _smoke_extracted(
    manifest: WorkerPackManifest,
    pack_root: Path,
    *,
    kind: str,
    timeout: float,
    smoke_wav: Path | None,
    output_directory: Path,
) -> dict[str, object]:
    entrypoint = manifest.worker.entrypoint
    executable = _resolve_pack_path(pack_root, entrypoint.executable)
    working_directory = (
        pack_root
        if entrypoint.working_directory == "."
        else _resolve_pack_path(pack_root, entrypoint.working_directory)
    )
    if not executable.is_file():
        raise RuntimeError(f"Worker pack executable does not exist: {executable}")
    if not working_directory.is_dir():
        raise RuntimeError(f"Worker pack working directory does not exist: {working_directory}")
    data_root = pack_root.parent / "data"
    config_root = pack_root.parent / "config"
    data_root.mkdir()
    config_root.mkdir()
    arguments = [
        _expand_placeholders(item, pack_root, data_root, config_root)
        for item in entrypoint.arguments
    ]
    environment = _worker_environment(entrypoint, pack_root, data_root, config_root, kind)
    port = _free_port()
    token = uuid.uuid4().hex + uuid.uuid4().hex
    prefix = "CHATWAIFU_STT_WORKER_" if kind == "stt" else "CHATWAIFU_NEURAL_TTS_WORKER_"
    environment[f"{prefix}HOST"] = "127.0.0.1"
    environment[f"{prefix}PORT"] = str(port)
    environment[f"{prefix}TOKEN"] = token
    log_path = output_directory / f"{manifest.pack_id}.log"

    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(executable), *arguments],
            cwd=working_directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        base_url = f"http://127.0.0.1:{port}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            health = _wait_for_health(
                process,
                base_url,
                entrypoint.health_path,
                headers,
                timeout,
                log_path,
            )
            capabilities = _get_json(
                f"{base_url}{entrypoint.capabilities_path}", headers, timeout=10
            )
            if kind == "tts":
                outputs = _smoke_tts(base_url, headers, output_directory, timeout)
                result: dict[str, object] = {
                    "health": health,
                    "capabilities": capabilities,
                    "outputs": [str(path) for path in outputs],
                }
            else:
                if smoke_wav is None:
                    raise RuntimeError("faster-whisper smoke requires --smoke-wav")
                transcript = _smoke_stt(base_url, headers, smoke_wav, timeout)
                result = {
                    "health": health,
                    "capabilities": capabilities,
                    "transcript": transcript,
                }
            unload = _post_json(f"{base_url}/v1/model/unload", headers, {}, timeout=30)
            result["unload"] = unload
            return result
        finally:
            _stop_process(process)
            _assert_listener_closed(port)


def _worker_environment(
    entrypoint: WorkerPackEntrypoint,
    pack_root: Path,
    data_root: Path,
    config_root: Path,
    kind: str,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in PYTHON_ENVIRONMENT_KEYS and key not in PROXY_KEYS
    }
    system_root = environment.get("SystemRoot") or environment.get("WINDIR")
    if os.name == "nt" and system_root:
        environment["PATH"] = os.pathsep.join((str(Path(system_root) / "System32"), system_root))
    environment.update(
        {
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_OFFLINE": "1",
            "NO_PROXY": "127.0.0.1,localhost",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    allowed_prefix = "CHATWAIFU_STT_WORKER_" if kind == "stt" else "CHATWAIFU_NEURAL_TTS_WORKER_"
    forbidden_dynamic = {f"{allowed_prefix}{suffix}" for suffix in ("HOST", "PORT", "TOKEN")}
    for raw_key, raw_value in entrypoint.environment.items():
        if not raw_key.startswith(allowed_prefix):
            raise RuntimeError(f"Worker pack environment key is outside its namespace: {raw_key}")
        if raw_key in forbidden_dynamic:
            raise RuntimeError(f"Dynamic worker endpoint setting may not enter a pack: {raw_key}")
        environment[raw_key] = _expand_placeholders(raw_value, pack_root, data_root, config_root)
    return environment


def _smoke_tts(
    base_url: str, headers: dict[str, str], output_directory: Path, timeout: float
) -> list[Path]:
    prompts = (("zh", "你好，我是绫地宁宁。"), ("ja", "こんにちは、綾地寧々です。"))
    outputs: list[Path] = []
    for language, text in prompts:
        identity = {name: str(uuid.uuid4()) for name in _identity_fields()}
        payload: dict[str, object] = {
            "schema_version": "1.0",
            **identity,
            "text": text,
            "language": language,
            "voice_id": "ayachi_nene_local",
            "speaker_id": 0,
            "speed": 1.0,
            "output_format": "wav",
        }
        response = _post_json(f"{base_url}/v1/synthesize", headers, payload, timeout=timeout)
        audio_base64 = response.get("audio_base64")
        if not isinstance(audio_base64, str):
            raise RuntimeError(f"TTS {language} smoke returned no audio")
        audio = base64.b64decode(audio_base64, validate=True)
        _assert_non_silent_wave(audio)
        output = output_directory / f"qwen3-tts-{language}.wav"
        output.write_bytes(audio)
        outputs.append(output)
    return outputs


def _smoke_stt(
    base_url: str,
    headers: dict[str, str],
    smoke_wav: Path,
    timeout: float,
) -> dict[str, object]:
    with wave.open(str(smoke_wav), "rb") as source:
        sample_width = source.getsampwidth()
        channels = source.getnchannels()
        sample_rate = source.getframerate()
        compression = source.getcomptype()
        audio = source.readframes(source.getnframes())
    if sample_width != 2 or channels not in (1, 2) or compression != "NONE":
        raise RuntimeError("Whisper smoke WAV must be uncompressed PCM16 mono or stereo")
    if not 8_000 <= sample_rate <= 48_000:
        raise RuntimeError("Whisper smoke WAV sample rate must be between 8 and 48 kHz")
    identity = {name: str(uuid.uuid4()) for name in _identity_fields()}
    response = _post_json(
        f"{base_url}/v1/transcribe",
        headers,
        {
            "schema_version": "1.0",
            **identity,
            "audio_base64": base64.b64encode(audio).decode("ascii"),
            "sample_rate": sample_rate,
            "channels": channels,
            "language": None,
        },
        timeout=timeout,
    )
    text = response.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(f"Whisper smoke returned an empty transcript: {response}")
    return response


def _wait_for_health(
    process: subprocess.Popen[str],
    base_url: str,
    health_path: str,
    headers: dict[str, str],
    timeout: float,
    log_path: Path,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_error = "worker has not accepted a health request"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"Worker exited before health ({return_code}):\n{_log_tail(log_path)}"
            )
        try:
            health = _get_json(f"{base_url}{health_path}", headers, timeout=2)
            if health.get("status") in {"ready", "busy"}:
                return health
            last_error = f"unexpected health status: {health}"
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            last_error = str(error)
        time.sleep(0.1)
    raise RuntimeError(f"Worker health timed out: {last_error}\n{_log_tail(log_path)}")


def _get_json(url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
    request = urllib.request.Request(url, headers=headers)
    with _opener().open(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Worker response is not a JSON object: {url}")
    return cast(dict[str, object], payload)


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, object],
    timeout: float,
) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with _opener().open(request, timeout=timeout) as response:
        body = json.load(response)
    if not isinstance(body, dict):
        raise RuntimeError(f"Worker response is not a JSON object: {url}")
    return cast(dict[str, object], body)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _assert_non_silent_wave(audio: bytes) -> None:
    with wave.open(io.BytesIO(audio), "rb") as source:
        if source.getsampwidth() != 2 or source.getnchannels() not in (1, 2):
            raise RuntimeError("TTS smoke output must be PCM16 WAV")
        if not 8_000 <= source.getframerate() <= 48_000:
            raise RuntimeError("TTS smoke output has an unsupported sample rate")
        samples = array("h", source.readframes(source.getnframes()))
    if len(samples) < 1_000 or max(abs(sample) for sample in samples) < 64:
        raise RuntimeError("TTS smoke output is empty or effectively silent")


def _resolve_pack_path(pack_root: Path, relative: str) -> Path:
    candidate = (pack_root / Path(relative.replace("/", os.sep))).resolve()
    try:
        candidate.relative_to(pack_root.resolve())
    except ValueError as error:
        raise RuntimeError(f"Worker pack path escapes its root: {relative}") from error
    return candidate


def _expand_placeholders(value: str, pack_root: Path, data_root: Path, config_root: Path) -> str:
    return (
        value.replace("${PACK_ROOT}", str(pack_root))
        .replace("${DATA_ROOT}", str(data_root))
        .replace("${CONFIG_ROOT}", str(config_root))
    )


def _identity_fields() -> tuple[str, ...]:
    return ("request_id", "session_id", "turn_id", "generation_id", "job_id")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _assert_listener_closed(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"Worker listener remained open after shutdown: {port}")


def _log_tail(path: Path, limit: int = 8_000) -> str:
    if not path.is_file():
        return "<no worker log>"
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        source.seek(max(0, source.tell() - limit))
        return source.read().decode("utf-8", errors="replace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--kind", choices=("stt", "tts"), required=True)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--smoke-wav", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    result = smoke_pack(
        arguments.archive.resolve(),
        kind=arguments.kind,
        timeout=arguments.timeout,
        smoke_wav=arguments.smoke_wav.resolve() if arguments.smoke_wav else None,
        output_directory=arguments.output_directory.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
