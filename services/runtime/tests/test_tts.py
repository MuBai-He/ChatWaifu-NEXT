"""TTS provider boundary regression tests."""

import asyncio
import base64
import io
import json
import wave
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx2
import pytest
from chatwaifu_model_worker import TtsSynthesisResult
from chatwaifu_runtime.providers import tts as tts_module
from chatwaifu_runtime.providers.contracts import (
    SynthesisRequest,
    TtsPcmChunk,
    TtsProviderDescriptor,
    TtsStreamCompleted,
)
from chatwaifu_runtime.providers.tts import (
    MacOsSayTtsProvider,
    SherpaKokoroWorkerTtsProvider,
    WorkerTtsProvider,
)
from chatwaifu_runtime.providers.tts_aliyun import AliyunQwenRealtimeTtsProvider
from chatwaifu_runtime.providers.tts_aliyun_cosyvoice import (
    AliyunCosyVoiceRealtimeTtsProvider,
)
from chatwaifu_runtime.providers.tts_config import (
    AliyunCosyVoiceTtsConfiguration,
    AliyunTtsConfiguration,
    TtsConfigurationService,
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


class _FakeAliyunSocket:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = [json.dumps(event) for event in events]
        self.sent: list[dict[str, object]] = []
        self.waiting = asyncio.Event()

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))

    async def recv(self) -> str:
        if self.events:
            return self.events.pop(0)
        self.waiting.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _FakeAliyunConnection:
    def __init__(self, socket: _FakeAliyunSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> _FakeAliyunSocket:
        return self.socket

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeCosyVoiceSocket:
    def __init__(self, frames: list[str | bytes]) -> None:
        self.frames = list(frames)
        self.sent: list[dict[str, object]] = []
        self.waiting = asyncio.Event()

    async def send(self, value: str) -> None:
        parsed: object = json.loads(value)
        assert isinstance(parsed, dict)
        self.sent.append(cast(dict[str, object], parsed))

    async def recv(self) -> str | bytes:
        if self.frames:
            frame = self.frames.pop(0)
            if isinstance(frame, bytes):
                return frame
            header = cast(dict[str, object], self.sent[0]["header"])
            return json.dumps(
                {
                    "header": {
                        "task_id": header["task_id"],
                        "event": frame,
                        "attributes": {},
                    },
                    "payload": {},
                }
            )
        self.waiting.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _FakeCosyVoiceConnection:
    def __init__(self, socket: _FakeCosyVoiceSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> _FakeCosyVoiceSocket:
        return self.socket

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeTtsConfigurations:
    def __init__(self, config: AliyunTtsConfiguration, api_key: str | None = "secret") -> None:
        self.config = config
        self.key = api_key

    def get(self) -> AliyunTtsConfiguration:
        return self.config

    def api_key(self) -> str | None:
        return self.key


class _FakeCosyVoiceConfigurations:
    def __init__(
        self,
        config: AliyunCosyVoiceTtsConfiguration,
        api_key: str | None = "secret",
    ) -> None:
        self.config = config
        self.key = api_key

    def get_cosyvoice(self) -> AliyunCosyVoiceTtsConfiguration:
        return self.config

    def api_key(self, _provider_id: str) -> str | None:
        return self.key


def _voice_catalog_client(
    *,
    voice_id: str,
    target_model: str,
    status_code: int = 200,
) -> httpx2.AsyncClient:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/services/audio/tts/customization"
        assert request.headers["authorization"] == "Bearer secret"
        if status_code != 200:
            return httpx2.Response(status_code, json={"code": "InvalidApiKey"})
        return httpx2.Response(
            200,
            json={
                "output": {
                    "page_index": 0,
                    "page_size": 100,
                    "total_count": 1,
                    "voice_list": [
                        {
                            "voice": voice_id,
                            "target_model": target_model,
                            "language": "ja",
                        }
                    ],
                }
            },
        )

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def _cosyvoice_catalog_client(
    *,
    target_model: str,
    status: str = "OK",
) -> httpx2.AsyncClient:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/services/audio/tts/customization"
        assert request.headers["authorization"] == "Bearer secret"
        payload = cast(dict[str, object], json.loads(request.content))
        assert payload["model"] == "voice-enrollment"
        input_value = cast(dict[str, object], payload["input"])
        assert input_value["action"] == "query_voice"
        return httpx2.Response(
            200,
            json={
                "output": {
                    "target_model": target_model,
                    "status": status,
                }
            },
        )

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


@pytest.mark.asyncio
async def test_aliyun_realtime_tts_streams_pcm_and_persists_wave(tmp_path: Path) -> None:
    pcm_chunks = [b"\x00\x00\x10\x00", b"\x20\x00\x30\x00"]
    socket = _FakeAliyunSocket(
        [
            {"type": "session.created"},
            {"type": "session.updated"},
            *[
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(chunk).decode("ascii"),
                }
                for chunk in pcm_chunks
            ],
            {"type": "response.done", "response": {"status": "completed"}},
            {"type": "session.finished"},
        ]
    )
    connection_options: dict[str, object] = {}

    def connect(url: str, **options: object) -> _FakeAliyunConnection:
        connection_options.update({"url": url, **options})
        return _FakeAliyunConnection(socket)

    config = AliyunTtsConfiguration(enabled=True, updated_at=datetime.now(UTC))
    catalog_client = _voice_catalog_client(
        voice_id=config.voice_id,
        target_model=config.model,
    )
    provider = AliyunQwenRealtimeTtsProvider(
        cast(TtsConfigurationService, _FakeTtsConfigurations(config)),
        websocket_factory=connect,
        http_client=catalog_client,
    )
    request = _synthesis_request(tmp_path / "aliyun.wav")
    try:
        events = [event async for event in provider.stream(request)]
    finally:
        await provider.close()
        await catalog_client.aclose()

    chunks = [event for event in events if isinstance(event, TtsPcmChunk)]
    completed = cast(TtsStreamCompleted, events[-1])
    assert [chunk.pcm16 for chunk in chunks] == pcm_chunks
    assert all(chunk.native_streaming for chunk in chunks)
    assert request.destination.read_bytes()[:4] == b"RIFF"
    assert completed.result.provider_id == "aliyun_qwen_realtime"
    assert config.model in str(connection_options["url"])
    headers = cast(dict[str, str], connection_options["additional_headers"])
    assert headers["Authorization"] == "Bearer secret"
    assert [event["type"] for event in socket.sent] == [
        "session.update",
        "input_text_buffer.append",
        "input_text_buffer.commit",
        "session.finish",
    ]
    session = cast(dict[str, object], socket.sent[0]["session"])
    assert session["voice"] == config.voice_id
    assert session["response_format"] == "pcm"


@pytest.mark.asyncio
async def test_aliyun_realtime_tts_cancellation_never_commits_late_audio(
    tmp_path: Path,
) -> None:
    socket = _FakeAliyunSocket(
        [
            {"type": "session.created"},
            {"type": "session.updated"},
            {
                "type": "response.audio.delta",
                "delta": base64.b64encode(b"\x00\x00" * 16).decode("ascii"),
            },
        ]
    )

    def connect(_url: str, **_options: object) -> _FakeAliyunConnection:
        return _FakeAliyunConnection(socket)

    config = AliyunTtsConfiguration(enabled=True, updated_at=datetime.now(UTC))
    catalog_client = _voice_catalog_client(
        voice_id=config.voice_id,
        target_model=config.model,
    )
    provider = AliyunQwenRealtimeTtsProvider(
        cast(TtsConfigurationService, _FakeTtsConfigurations(config)),
        websocket_factory=connect,
        http_client=catalog_client,
    )
    request = _synthesis_request(tmp_path / "cancelled-aliyun.wav")

    async def consume() -> None:
        async for _event in provider.stream(request):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(socket.waiting.wait(), timeout=1)
    task.cancel("barge_in")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not request.destination.exists()
    await provider.close()
    await catalog_client.aclose()


@pytest.mark.asyncio
async def test_aliyun_realtime_tts_rejects_voice_bound_to_batch_model(
    tmp_path: Path,
) -> None:
    config = AliyunTtsConfiguration(enabled=True, updated_at=datetime.now(UTC))
    websocket_opened = False

    def connect(_url: str, **_options: object) -> _FakeAliyunConnection:
        nonlocal websocket_opened
        websocket_opened = True
        return _FakeAliyunConnection(_FakeAliyunSocket([]))

    catalog_client = _voice_catalog_client(
        voice_id=config.voice_id,
        target_model="qwen3-tts-vc-2026-01-22",
    )
    provider = AliyunQwenRealtimeTtsProvider(
        cast(TtsConfigurationService, _FakeTtsConfigurations(config)),
        websocket_factory=connect,
        http_client=catalog_client,
    )
    try:
        with pytest.raises(RuntimeError, match="音色与模型不匹配"):
            _ = [
                event
                async for event in provider.stream(_synthesis_request(tmp_path / "mismatch.wav"))
            ]
    finally:
        await provider.close()
        await catalog_client.aclose()

    assert websocket_opened is False
    assert not (tmp_path / "mismatch.wav").exists()


@pytest.mark.asyncio
async def test_aliyun_realtime_tts_reports_region_key_mismatch(tmp_path: Path) -> None:
    config = AliyunTtsConfiguration(enabled=True, updated_at=datetime.now(UTC))
    catalog_client = _voice_catalog_client(
        voice_id=config.voice_id,
        target_model=config.model,
        status_code=401,
    )
    provider = AliyunQwenRealtimeTtsProvider(
        cast(TtsConfigurationService, _FakeTtsConfigurations(config)),
        http_client=catalog_client,
    )
    try:
        with pytest.raises(RuntimeError, match="API Key 无法访问当前地域"):
            _ = [
                event
                async for event in provider.stream(
                    _synthesis_request(tmp_path / "wrong-region.wav")
                )
            ]
    finally:
        await provider.close()
        await catalog_client.aclose()


@pytest.mark.asyncio
async def test_cosyvoice_realtime_streams_binary_pcm_with_emotion_instruction(
    tmp_path: Path,
) -> None:
    pcm_chunks = [b"\x00\x00\x10\x00", b"\x20\x00\x30\x00"]
    socket = _FakeCosyVoiceSocket(
        [
            "task-started",
            "result-generated",
            pcm_chunks[0],
            "result-generated",
            pcm_chunks[1],
            "task-finished",
        ]
    )
    connection_options: dict[str, object] = {}

    def connect(url: str, **options: object) -> _FakeCosyVoiceConnection:
        connection_options.update({"url": url, **options})
        return _FakeCosyVoiceConnection(socket)

    config = AliyunCosyVoiceTtsConfiguration(
        enabled=True,
        voice_id="cosyvoice-v3.5-plus-test-voice",
        instruction="温柔自然。",
        updated_at=datetime.now(UTC),
    )
    catalog_client = _cosyvoice_catalog_client(target_model=config.model)
    provider = AliyunCosyVoiceRealtimeTtsProvider(
        cast(TtsConfigurationService, _FakeCosyVoiceConfigurations(config)),
        websocket_factory=connect,
        http_client=catalog_client,
    )
    request = _synthesis_request(tmp_path / "cosyvoice.wav")
    request = replace(
        request,
        style="带一点羞涩，像面对面聊天一样自然。",
    )
    try:
        events = [event async for event in provider.stream(request)]
    finally:
        await provider.close()
        await catalog_client.aclose()

    chunks = [event for event in events if isinstance(event, TtsPcmChunk)]
    completed = cast(TtsStreamCompleted, events[-1])
    assert [chunk.pcm16 for chunk in chunks] == pcm_chunks
    assert all(chunk.native_streaming for chunk in chunks)
    assert completed.result.provider_id == "aliyun_cosyvoice_realtime"
    assert request.destination.read_bytes()[:4] == b"RIFF"
    assert connection_options["url"] == "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    actions = [cast(dict[str, object], event["header"])["action"] for event in socket.sent]
    assert actions == ["run-task", "continue-task", "finish-task"]
    run_payload = cast(dict[str, object], socket.sent[0]["payload"])
    parameters = cast(dict[str, object], run_payload["parameters"])
    assert parameters["format"] == "pcm"
    assert parameters["instruction"] == ("温柔自然。 带一点羞涩，像面对面聊天一样自然。")


@pytest.mark.asyncio
async def test_cosyvoice_realtime_cancellation_discards_late_audio(
    tmp_path: Path,
) -> None:
    socket = _FakeCosyVoiceSocket(["task-started", "result-generated", b"\x00\x00" * 16])

    def connect(_url: str, **_options: object) -> _FakeCosyVoiceConnection:
        return _FakeCosyVoiceConnection(socket)

    config = AliyunCosyVoiceTtsConfiguration(
        enabled=True,
        voice_id="cosyvoice-v3.5-plus-test-voice",
        updated_at=datetime.now(UTC),
    )
    catalog_client = _cosyvoice_catalog_client(target_model=config.model)
    provider = AliyunCosyVoiceRealtimeTtsProvider(
        cast(TtsConfigurationService, _FakeCosyVoiceConfigurations(config)),
        websocket_factory=connect,
        http_client=catalog_client,
    )
    request = _synthesis_request(tmp_path / "cancelled-cosyvoice.wav")

    async def consume() -> None:
        async for _event in provider.stream(request):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(socket.waiting.wait(), timeout=1)
    task.cancel("barge_in")
    with pytest.raises(asyncio.CancelledError):
        await task
    await provider.close()
    await catalog_client.aclose()

    assert not request.destination.exists()
    cancel_payload = cast(dict[str, object], socket.sent[-1]["payload"])
    cancel_input = cast(dict[str, object], cancel_payload["input"])
    assert cancel_input["directive"] == "cancel"


@pytest.mark.asyncio
async def test_cosyvoice_realtime_rejects_mismatched_voice_before_websocket(
    tmp_path: Path,
) -> None:
    websocket_opened = False

    def connect(_url: str, **_options: object) -> _FakeCosyVoiceConnection:
        nonlocal websocket_opened
        websocket_opened = True
        return _FakeCosyVoiceConnection(_FakeCosyVoiceSocket([]))

    config = AliyunCosyVoiceTtsConfiguration(
        enabled=True,
        voice_id="cosyvoice-v3.5-plus-test-voice",
        updated_at=datetime.now(UTC),
    )
    catalog_client = _cosyvoice_catalog_client(target_model="cosyvoice-v3.5-flash")
    provider = AliyunCosyVoiceRealtimeTtsProvider(
        cast(TtsConfigurationService, _FakeCosyVoiceConfigurations(config)),
        websocket_factory=connect,
        http_client=catalog_client,
    )
    try:
        with pytest.raises(RuntimeError, match="音色与模型不匹配"):
            _ = [
                event
                async for event in provider.stream(
                    _synthesis_request(tmp_path / "mismatched-cosyvoice.wav")
                )
            ]
    finally:
        await provider.close()
        await catalog_client.aclose()

    assert websocket_opened is False


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
async def test_worker_capability_does_not_claim_end_to_end_native_streaming() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/capabilities"
        return httpx2.Response(
            200,
            json={
                "schema_version": "1.0",
                "provider_id": "qwen3_tts_mlx",
                "display_name": "Qwen3-TTS",
                "model": "local-qwen",
                "languages": ["zh", "ja"],
                "native_streaming": True,
                "output_formats": ["wav"],
                "local_only": True,
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    provider = WorkerTtsProvider(
        descriptor=TtsProviderDescriptor(
            provider_id="qwen3_tts_mlx",
            display_name="Qwen3-TTS",
            model="local-qwen",
            languages=("zh", "ja"),
            supports_voice_cloning=True,
            supports_style=False,
            supports_speed=False,
            supports_pitch=False,
            native_streaming=True,
        ),
        base_url="http://tts.test",
        token="ephemeral",
        timeout_seconds=1,
        client=client,
    )
    try:
        descriptor = await provider.refresh_descriptor()
    finally:
        await provider.close()

    assert descriptor.native_streaming is False


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
