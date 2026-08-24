"""TTS provider boundary regression tests."""

import asyncio
import base64
import io
import json
import wave
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
import pytest
from chatwaifu_model_worker import TtsSynthesisResult
from chatwaifu_runtime.providers import tts as tts_module
from chatwaifu_runtime.providers.contracts import SynthesisRequest
from chatwaifu_runtime.providers.tts import (
    MacOsSayTtsProvider,
    SherpaKokoroWorkerTtsProvider,
)


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


def _wave_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\x00\x00" * 240)
    return buffer.getvalue()


def _synthesis_request(destination: Path) -> SynthesisRequest:
    return SynthesisRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        segment_id=uuid4(),
        text="欢迎回来。",
        destination=destination,
        language="zh",
        voice_id="ayachi-nene-demo-zh",
        speaker_id=3,
        speed=1.04,
    )


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

    result = await provider.synthesize(
        SynthesisRequest(
            session_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
            segment_id=uuid4(),
            text=text,
            destination=destination,
            language="zh",
            voice_id="ayachi-nene-demo-zh",
            speaker_id=3,
            speed=1.04,
        )
    )

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


@pytest.mark.asyncio
async def test_kokoro_worker_adapter_validates_identity_and_writes_wave(tmp_path: Path) -> None:
    synthesis = _synthesis_request(tmp_path / "worker.wav")

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/synthesize"
        payload = json.loads(request.content)
        result = TtsSynthesisResult(
            request_id=UUID(str(payload["request_id"])),
            session_id=synthesis.session_id,
            turn_id=synthesis.turn_id,
            generation_id=synthesis.generation_id,
            job_id=synthesis.segment_id,
            audio_base64=base64.b64encode(_wave_bytes()).decode("ascii"),
            sample_rate=24_000,
            duration_ms=10,
            provider="sherpa-onnx-kokoro",
            model="kokoro-multi-lang-v1_1",
            speaker_id=3,
        )
        return httpx2.Response(200, json=result.model_dump(mode="json"))

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    provider = SherpaKokoroWorkerTtsProvider(
        base_url="http://tts.test", token="ephemeral", timeout_seconds=1, client=client
    )
    try:
        result = await provider.synthesize(synthesis)
    finally:
        await provider.close()

    assert synthesis.destination.read_bytes()[:4] == b"RIFF"
    assert result.sample_rate == 24_000
    assert result.duration_ms == 10


@pytest.mark.asyncio
async def test_kokoro_worker_adapter_rejects_mismatched_identity(tmp_path: Path) -> None:
    synthesis = _synthesis_request(tmp_path / "mismatched.wav")

    async def handler(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        result = TtsSynthesisResult(
            request_id=UUID(str(payload["request_id"])),
            session_id=uuid4(),
            turn_id=synthesis.turn_id,
            generation_id=synthesis.generation_id,
            job_id=synthesis.segment_id,
            audio_base64=base64.b64encode(_wave_bytes()).decode("ascii"),
            sample_rate=24_000,
            duration_ms=10,
            provider="sherpa-onnx-kokoro",
            model="kokoro-multi-lang-v1_1",
            speaker_id=3,
        )
        return httpx2.Response(200, json=result.model_dump(mode="json"))

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    provider = SherpaKokoroWorkerTtsProvider(
        base_url="http://tts.test", token="ephemeral", timeout_seconds=1, client=client
    )
    try:
        with pytest.raises(RuntimeError, match="mismatched request identity"):
            await provider.synthesize(synthesis)
    finally:
        await provider.close()

    assert not synthesis.destination.exists()


@pytest.mark.asyncio
async def test_kokoro_worker_adapter_propagates_cancel_to_generation(tmp_path: Path) -> None:
    synthesis = _synthesis_request(tmp_path / "cancelled.wav")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/synthesize":
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("cancelled synthesis returned late audio")
        assert request.url.path == f"/v1/jobs/{synthesis.generation_id}/cancel"
        cancelled.set()
        return httpx2.Response(200, json={"cancelled": True})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    provider = SherpaKokoroWorkerTtsProvider(
        base_url="http://tts.test", token="ephemeral", timeout_seconds=1, client=client
    )
    task = asyncio.create_task(provider.synthesize(synthesis))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel("test_interruption")
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert not synthesis.destination.exists()
    finally:
        await provider.close()
