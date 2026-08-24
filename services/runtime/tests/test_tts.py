"""TTS provider boundary regression tests."""

import asyncio
from pathlib import Path

import pytest
from chatwaifu_runtime.providers import tts as tts_module
from chatwaifu_runtime.providers.tts import MacOsSayTtsProvider


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = 0
        self.stdin: bytes | None = None

    async def communicate(self, stdin: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin = stdin
        return b"", b""

    def terminate(self) -> None:
        self.returncode = -15

    async def wait(self) -> int:
        return self.returncode or 0


@pytest.mark.asyncio
async def test_macos_say_reads_untrusted_text_from_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object], _FakeProcess]] = []

    async def fake_create_subprocess_exec(*command: str, **options: object) -> _FakeProcess:
        process = _FakeProcess()
        calls.append((command, options, process))
        return process

    def fake_which(command: str) -> str:
        return f"/usr/bin/{command}"

    def fake_wave_duration(_path: Path) -> int:
        return 321

    monkeypatch.setattr(tts_module.sys, "platform", "darwin")
    monkeypatch.setattr(tts_module.shutil, "which", fake_which)
    monkeypatch.setattr(tts_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(tts_module, "_wave_duration_ms", fake_wave_duration)

    provider = MacOsSayTtsProvider(
        voice="Tingting", sample_rate=24_000, rate=190, timeout_seconds=1
    )
    text = "-- 这不是 say 选项\n- 第二行也必须按原文合成"
    destination = tmp_path / "speech.wav"

    result = await provider.synthesize(text, destination)

    say_command, say_options, say_process = calls[0]
    assert say_command == (
        "say",
        "-v",
        "Tingting",
        "-r",
        "190",
        "-o",
        str(destination.with_suffix(".aiff")),
        "-f",
        "-",
    )
    assert text not in say_command
    assert say_options["stdin"] == asyncio.subprocess.PIPE
    assert say_process.stdin == text.encode("utf-8")

    _, convert_options, convert_process = calls[1]
    assert convert_options["stdin"] == asyncio.subprocess.DEVNULL
    assert convert_process.stdin is None
    assert result.duration_ms == 321
