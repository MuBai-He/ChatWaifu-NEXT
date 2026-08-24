"""Unified worker authentication, discovery, WAV, unload, and cancellation tests."""

# Starlette TestClient inherits partially untyped httpx compatibility overloads.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
import io
import threading
import wave
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_model_worker import TtsSynthesisRequest
from fastapi.testclient import TestClient

from chatwaifu_tts_neural_worker.config import WorkerSettings
from chatwaifu_tts_neural_worker.engines import SynthesisEngine
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
