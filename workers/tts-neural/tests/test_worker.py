"""Unified worker authentication, discovery, WAV, unload, and cancellation tests."""

# Starlette TestClient inherits partially untyped httpx compatibility overloads.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
import io
import sys
import threading
import wave
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import numpy as np
import pytest
from chatwaifu_model_worker import (
    TtsStreamStart,
    TtsSynthesisRequest,
    unpack_tts_pcm_frame,
)
from fastapi.testclient import TestClient
from numpy.typing import NDArray

from chatwaifu_tts_neural_worker.config import WorkerSettings
from chatwaifu_tts_neural_worker.engines import (
    EnginePcmChunk,
    QwenMlxEngine,
    QwenTorchEngine,
    SynthesisCancelled,
    SynthesisEngine,
    collect_torch_runtime_diagnostics,
)
from chatwaifu_tts_neural_worker.main import create_app
from chatwaifu_tts_neural_worker.service import SynthesisService


def _wave_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\x00\x00" * 240)
    return buffer.getvalue()


class FakeEngine(SynthesisEngine):
    def __init__(self) -> None:
        self.unloaded = False

    @property
    def device(self) -> str:
        return "test"

    def synthesize(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> tuple[bytes, int, int]:
        assert request.text == "欢迎回来。"
        assert not cancel_event.is_set()
        return _wave_bytes(), 24_000, 10

    def stream_pcm(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> Iterator[EnginePcmChunk]:
        assert request.text == "欢迎回来。"
        assert not cancel_event.is_set()
        yield EnginePcmChunk(pcm16=b"\x01\x00" * 120, sample_rate=24_000)
        yield EnginePcmChunk(pcm16=b"\x02\x00" * 120, sample_rate=24_000)

    def cancel(self) -> None:
        return None

    def unload(self) -> None:
        self.unloaded = True


@pytest.fixture
def settings(tmp_path: Path) -> WorkerSettings:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(_wave_bytes())
    return WorkerSettings(
        port=8767,
        token="test-token",  # pyright: ignore[reportArgumentType]
        backend="qwen3_tts_mlx",
        provider_id="qwen3_tts_mlx",
        display_name="Qwen3-TTS · MLX",
        worker_id="tts-qwen-test",
        model="qwen-test",
        vendor_dir=tmp_path,
        model_dir=tmp_path,
        reference_audio=reference,
        reference_text="参考文本。",
        preload=False,
    )


@pytest.fixture
def client(settings: WorkerSettings) -> Iterator[TestClient]:
    service = SynthesisService(settings, engine_factory=lambda _: FakeEngine())
    with TestClient(create_app(settings, service)) as test_client:
        yield test_client


def test_worker_discovers_one_standard_provider(client: TestClient) -> None:
    assert client.get("/v1/health").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    health = client.get("/v1/health", headers=headers).json()
    capabilities = client.get("/v1/capabilities", headers=headers).json()

    assert health["model_loaded"] is False
    assert capabilities["provider_id"] == "qwen3_tts_mlx"
    assert capabilities["languages"] == ["zh", "ja", "en"]
    assert capabilities["supports_voice_cloning"] is True
    assert capabilities["supports_speed"] is False
    assert capabilities["native_streaming"] is True


class FakeCustomVoiceQwenModel:
    def __init__(self) -> None:
        self.config = SimpleNamespace(tts_model_type="custom_voice")
        self.sample_rate = 24_000
        self.speech_tokenizer = None
        self.calls: list[dict[str, object]] = []

    def get_supported_speakers(self) -> list[str]:
        return ["ayachi_nene_local"]

    def generate(self, **kwargs: object) -> Iterator[SimpleNamespace]:
        self.calls.append(kwargs)
        yield SimpleNamespace(
            audio=np.ones(2_400, dtype=np.float32) * 0.1,
            sample_rate=self.sample_rate,
        )


def test_qwen_custom_voice_uses_checkpoint_speaker_without_reference_clone(
    settings: WorkerSettings,
) -> None:
    model = FakeCustomVoiceQwenModel()
    custom_settings = settings.model_copy(update={"qwen_voice": "ayachi_nene_local"})
    engine = QwenMlxEngine(custom_settings, model_loader=lambda _: model)
    request = TtsSynthesisRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        job_id=uuid4(),
        text="欢迎回来。",
        language="zh",
        voice_id="ayachi_nene_local",
        speaker_id=0,
        speed=1.0,
    )

    audio, sample_rate, duration_ms = engine.synthesize(request, threading.Event())

    assert audio.startswith(b"RIFF")
    assert sample_rate == 24_000
    assert duration_ms == 100
    assert model.calls[0]["voice"] == "ayachi_nene_local"
    assert "ref_audio" not in model.calls[0]
    assert "ref_text" not in model.calls[0]
    service = SynthesisService(custom_settings, engine_factory=lambda _: engine)
    assert service.capabilities().supports_voice_cloning is False


def test_qwen_custom_voice_rejects_missing_profile_speaker(settings: WorkerSettings) -> None:
    model = FakeCustomVoiceQwenModel()
    with pytest.raises(RuntimeError, match="requires qwen_voice"):
        QwenMlxEngine(settings, model_loader=lambda _: model)


class FakeTorchQwenModel:
    def __init__(self, *, cancel_event: threading.Event | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._cancel_event = cancel_event
        self.device = "cuda:0"
        parameter = SimpleNamespace(device="cuda:0")
        self.model = SimpleNamespace(parameters=lambda: iter((parameter,)))

    def get_supported_speakers(self) -> list[str]:
        return ["ayachi_nene_local"]

    def generate_custom_voice(self, **kwargs: object) -> tuple[list[NDArray[np.float32]], int]:
        self.calls.append(("custom", kwargs))
        if self._cancel_event is not None:
            self._cancel_event.set()
        return [np.ones(2_400, dtype=np.float32) * 0.1], 24_000

    def generate_voice_clone(self, **kwargs: object) -> tuple[list[NDArray[np.float32]], int]:
        self.calls.append(("clone", kwargs))
        return [np.ones(1_200, dtype=np.float32) * 0.1], 24_000


def _torch_settings(tmp_path: Path, *, qwen_voice: str | None) -> WorkerSettings:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(_wave_bytes())
    return WorkerSettings(
        port=8767,
        token="test-token",  # pyright: ignore[reportArgumentType]
        backend="qwen3_tts_torch",
        provider_id="qwen3_tts_torch",
        display_name="Qwen3-TTS · CUDA",
        worker_id="tts-qwen-cuda-test",
        model="nene-qwen3-0.6b",
        model_dir=tmp_path,
        qwen_voice=qwen_voice,
        reference_audio=None if qwen_voice else reference,
        reference_text=None if qwen_voice else "参考文本。",
        device="cuda:0",
        preload=False,
    )


def test_qwen_torch_custom_voice_uses_trained_speaker_and_complete_wave(
    tmp_path: Path,
) -> None:
    settings = _torch_settings(tmp_path, qwen_voice="ayachi_nene_local")
    model = FakeTorchQwenModel()
    engine = QwenTorchEngine(settings, model_loader=lambda _: model)
    request = TtsSynthesisRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        job_id=uuid4(),
        text="欢迎回来。",
        language="zh",
        voice_id="ayachi_nene_local",
        speaker_id=0,
        speed=1.0,
    )

    audio, sample_rate, duration_ms = engine.synthesize(request, threading.Event())

    assert audio.startswith(b"RIFF")
    assert sample_rate == 24_000
    assert duration_ms == 100
    method, options = model.calls[0]
    assert method == "custom"
    assert options["speaker"] == "ayachi_nene_local"
    assert options["language"] == "Chinese"
    assert "ref_audio" not in options
    capabilities = SynthesisService(settings, engine_factory=lambda _: engine).capabilities()
    assert capabilities.supports_voice_cloning is False
    assert capabilities.native_streaming is False
    assert capabilities.stream_protocols == []


def test_qwen_torch_base_uses_reference_voice_clone(tmp_path: Path) -> None:
    settings = _torch_settings(tmp_path, qwen_voice=None)
    model = FakeTorchQwenModel()
    engine = QwenTorchEngine(settings, model_loader=lambda _: model)
    request = TtsSynthesisRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        job_id=uuid4(),
        text="おかえりなさい。",
        language="ja",
        voice_id="reference",
        speaker_id=0,
        speed=1.0,
    )

    engine.synthesize(request, threading.Event())

    method, options = model.calls[0]
    assert method == "clone"
    assert options["language"] == "Japanese"
    assert options["ref_audio"] == str(settings.reference_audio)
    assert options["ref_text"] == "参考文本。"


def test_qwen_torch_drops_native_result_after_generation_is_cancelled(
    tmp_path: Path,
) -> None:
    cancel_event = threading.Event()
    settings = _torch_settings(tmp_path, qwen_voice="ayachi_nene_local")
    model = FakeTorchQwenModel(cancel_event=cancel_event)
    engine = QwenTorchEngine(settings, model_loader=lambda _: model)
    request = TtsSynthesisRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        job_id=uuid4(),
        text="欢迎回来。",
        language="zh",
        voice_id="ayachi_nene_local",
        speaker_id=0,
        speed=1.0,
    )

    with pytest.raises(SynthesisCancelled):
        engine.synthesize(request, cancel_event)


def test_qwen_torch_rejects_model_parameters_on_the_wrong_device(tmp_path: Path) -> None:
    settings = _torch_settings(tmp_path, qwen_voice="ayachi_nene_local")
    model = FakeTorchQwenModel()
    parameter = SimpleNamespace(device="cpu")
    model.model = SimpleNamespace(parameters=lambda: iter((parameter,)))

    with pytest.raises(RuntimeError, match="not entirely on cuda:0"):
        QwenTorchEngine(settings, model_loader=lambda _: model)


def test_qwen_torch_runtime_diagnostics_come_from_torch_and_loaded_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def get_device_capability(_device_index: int) -> tuple[int, int]:
        return (8, 6)

    def get_device_name(_device_index: int) -> str:
        return "NVIDIA GeForce RTX 3090"

    def mem_get_info(_device_index: int) -> tuple[int, int]:
        return (20_000_000_000, 25_769_803_776)

    def memory_allocated(_device_index: int) -> int:
        return 4_000_000_000

    def memory_reserved(_device_index: int) -> int:
        return 4_500_000_000

    def device(_device_name: str) -> SimpleNamespace:
        return SimpleNamespace(index=0)

    settings = _torch_settings(tmp_path, qwen_voice="ayachi_nene_local")
    engine = QwenTorchEngine(settings, model_loader=lambda _: FakeTorchQwenModel())
    fake_cuda = SimpleNamespace(
        is_available=lambda: True,
        current_device=lambda: 0,
        get_device_capability=get_device_capability,
        get_device_name=get_device_name,
        mem_get_info=mem_get_info,
        memory_allocated=memory_allocated,
        memory_reserved=memory_reserved,
    )
    fake_torch = SimpleNamespace(
        __version__="2.7.1+cu126",
        version=SimpleNamespace(cuda="12.6"),
        cuda=fake_cuda,
        device=device,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    diagnostics = collect_torch_runtime_diagnostics(settings, engine)

    assert diagnostics is not None
    assert diagnostics["torch_version"] == "2.7.1+cu126"
    assert diagnostics["torch_cuda_version"] == "12.6"
    assert diagnostics["cuda_device_name"] == "NVIDIA GeForce RTX 3090"
    assert diagnostics["cuda_compute_capability"] == "8.6"
    assert diagnostics["cuda_total_memory_bytes"] == 25_769_803_776
    assert diagnostics["model_device"] == "cuda:0"
    assert diagnostics["model_parameter_devices"] == ["cuda:0"]


def test_qwen_torch_validates_target_accelerator_before_reporting_ready(
    tmp_path: Path,
) -> None:
    settings = _torch_settings(tmp_path, qwen_voice="ayachi_nene_local")
    validated: list[WorkerSettings] = []
    service = SynthesisService(
        settings,
        engine_factory=lambda _: FakeEngine(),
        startup_validator=validated.append,
    )

    async def exercise() -> None:
        await service.start()
        assert service.health().model_loaded is False
        await service.close()

    asyncio.run(exercise())

    assert validated == [settings]


def test_qwen_torch_accelerator_failure_prevents_worker_startup(tmp_path: Path) -> None:
    settings = _torch_settings(tmp_path, qwen_voice="ayachi_nene_local")

    def fail(_: WorkerSettings) -> None:
        raise RuntimeError("CUDA driver is unavailable")

    service = SynthesisService(
        settings,
        engine_factory=lambda _: FakeEngine(),
        startup_validator=fail,
    )

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="CUDA driver is unavailable"):
            await service.start()
        await service.close()

    asyncio.run(exercise())


def test_worker_returns_identity_scoped_wave_and_unloads(client: TestClient) -> None:
    identifiers = {
        "request_id": str(uuid4()),
        "session_id": str(uuid4()),
        "turn_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "job_id": str(uuid4()),
    }
    headers = {"Authorization": "Bearer test-token"}
    response = client.post(
        "/v1/synthesize",
        headers=headers,
        json={
            **identifiers,
            "text": "欢迎回来。",
            "language": "zh",
            "voice_id": "local-character",
            "speaker_id": 0,
            "speed": 1.0,
        },
    )

    assert response.status_code == 200
    assert response.json()["generation_id"] == identifiers["generation_id"]
    assert client.get("/v1/health", headers=headers).json()["model_loaded"] is True
    assert client.post("/v1/model/unload", headers=headers).json()["unloaded"] is True
    assert client.get("/v1/health", headers=headers).json()["model_loaded"] is False


def test_worker_v2_streams_ordered_identity_scoped_pcm(client: TestClient) -> None:
    request = TtsSynthesisRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        job_id=uuid4(),
        text="欢迎回来。",
        language="zh",
        voice_id="local-character",
        speaker_id=0,
        speed=1.0,
    )
    with client.websocket_connect(
        "/v2/stream/tts", headers={"Authorization": "Bearer test-token"}
    ) as websocket:
        websocket.send_text(TtsStreamStart(request=request).model_dump_json())
        ready = websocket.receive_json()
        first = unpack_tts_pcm_frame(websocket.receive_bytes())
        second = unpack_tts_pcm_frame(websocket.receive_bytes())
        completed = websocket.receive_json()

    assert ready["event"] == "tts.stream.ready"
    assert UUID(ready["generation_id"]) == request.generation_id
    assert (first.sequence, second.sequence) == (0, 1)
    assert first.generation_id == second.generation_id == request.generation_id
    assert first.job_id == second.job_id == request.job_id
    assert completed["event"] == "tts.stream.completed"
    assert completed["chunk_count"] == 2
    assert completed["duration_ms"] == 10


class BlockingEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = False

    def synthesize(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> tuple[bytes, int, int]:
        self.started.set()
        self.release.wait(timeout=2)
        if cancel_event.is_set():
            raise asyncio.CancelledError
        return super().synthesize(request, cancel_event)

    def cancel(self) -> None:
        self.cancelled = True
        self.release.set()


class SerializedBlockingStreamEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.cancel_calls = 0

    def stream_pcm(
        self, request: TtsSynthesisRequest, cancel_event: threading.Event
    ) -> Iterator[EnginePcmChunk]:
        if request.text == "第一轮":
            self.first_started.set()
            while not self.release_first.wait(timeout=0.01):
                if cancel_event.is_set():
                    raise asyncio.CancelledError
        if cancel_event.is_set():
            raise asyncio.CancelledError
        yield EnginePcmChunk(pcm16=b"\x01\x00" * 120, sample_rate=24_000)

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.release_first.set()


@pytest.mark.asyncio
async def test_service_cancels_generation_and_rejects_late_audio(
    settings: WorkerSettings,
) -> None:
    engine = BlockingEngine()
    service = SynthesisService(settings, engine_factory=lambda _: engine)
    request = TtsSynthesisRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        job_id=uuid4(),
        text="欢迎回来。",
        language="zh",
        voice_id="local-character",
        speaker_id=0,
        speed=1.0,
    )
    task = asyncio.create_task(service.synthesize(request))
    try:
        assert await asyncio.to_thread(engine.started.wait, 1)
        assert service.cancel(request.generation_id) is True
        with pytest.raises(asyncio.CancelledError):
            await task
        assert engine.cancelled is True
    finally:
        engine.release.set()
        await service.close()


@pytest.mark.asyncio
async def test_cancelling_queued_generation_does_not_cancel_active_engine_job(
    settings: WorkerSettings,
) -> None:
    engine = SerializedBlockingStreamEngine()
    service = SynthesisService(settings, engine_factory=lambda _: engine)

    def request(text: str) -> TtsSynthesisRequest:
        return TtsSynthesisRequest(
            request_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
            job_id=uuid4(),
            text=text,
            language="zh",
            voice_id="local-character",
            speaker_id=0,
            speed=1.0,
        )

    first = request("第一轮")
    second = request("第二轮")
    first_chunks: list[EnginePcmChunk] = []

    async def collect(value: TtsSynthesisRequest, target: list[EnginePcmChunk]) -> None:
        async for chunk in service.stream(value):
            target.append(chunk)

    first_task = asyncio.create_task(collect(first, first_chunks))
    second_task: asyncio.Task[None] | None = None
    try:
        assert await asyncio.to_thread(engine.first_started.wait, 1)
        second_task = asyncio.create_task(collect(second, []))
        await asyncio.sleep(0)

        assert service.cancel(second.generation_id) is True
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(second_task, timeout=1)
        assert engine.cancel_calls == 0
        assert first_task.done() is False

        engine.release_first.set()
        await asyncio.wait_for(first_task, timeout=1)
        assert len(first_chunks) == 1
        assert engine.cancel_calls == 0
    finally:
        engine.release_first.set()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (first_task, second_task) if task is not None),
            return_exceptions=True,
        )
        await service.close()
