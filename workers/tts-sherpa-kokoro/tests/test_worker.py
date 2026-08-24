"""Kokoro worker authentication, identity, WAV, and cancellation tests."""

# Starlette TestClient inherits partially untyped httpx compatibility overloads.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
import base64
import io
import threading
import wave
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_model_worker import TtsSynthesisRequest
from fastapi.testclient import TestClient

from chatwaifu_tts_worker.config import WorkerSettings
from chatwaifu_tts_worker.main import create_app
from chatwaifu_tts_worker.service import SynthesisEngine, SynthesisService


def _wave_bytes(duration_ms: int = 50, sample_rate: int = 24_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * (sample_rate * duration_ms // 1000))
    return buffer.getvalue()


class FakeEngine(SynthesisEngine):
    def synthesize(self, text: str, *, speaker_id: int, speed: float) -> tuple[bytes, int, int]:
        assert text == "欢迎回来。"
        assert speaker_id == 3
        assert speed == pytest.approx(1.04)
        return _wave_bytes(), 24_000, 50


@pytest.fixture
def settings(tmp_path: Path) -> WorkerSettings:
    return WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=True,
    )


@pytest.fixture
def client(settings: WorkerSettings) -> Iterator[TestClient]:
    service = SynthesisService(settings, engine_factory=lambda _: FakeEngine())
    with TestClient(create_app(settings, service)) as test_client:
        yield test_client


def test_worker_requires_ephemeral_token(client: TestClient) -> None:
    assert client.get("/v1/health").status_code == 401
    health = client.get("/v1/health", headers={"Authorization": "Bearer test-token"})
    assert health.status_code == 200
    assert health.json()["model_loaded"] is True


def test_worker_returns_generation_scoped_wave(client: TestClient) -> None:
    identifiers = {
        "request_id": str(uuid4()),
        "session_id": str(uuid4()),
        "turn_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "job_id": str(uuid4()),
    }
    response = client.post(
        "/v1/synthesize",
        headers={"Authorization": "Bearer test-token"},
        json={
            **identifiers,
            "text": "欢迎回来。",
            "language": "zh",
            "voice_id": "ayachi-nene-demo-zh",
            "speaker_id": 3,
            "speed": 1.04,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["generation_id"] == identifiers["generation_id"]
    assert result["speaker_id"] == 3
    assert base64.b64decode(result["audio_base64"])[:4] == b"RIFF"


class BlockingEngine(SynthesisEngine):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def synthesize(self, text: str, *, speaker_id: int, speed: float) -> tuple[bytes, int, int]:
        del text, speaker_id, speed
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test engine was not released")
        return _wave_bytes(), 24_000, 50


@pytest.mark.asyncio
async def test_worker_cancels_generation_without_returning_late_audio(
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
        text="这句话会被打断。",
        language="zh",
        voice_id="ayachi-nene-demo-zh",
        speaker_id=3,
        speed=1.04,
    )
    task = asyncio.create_task(service.synthesize(request))
    try:
        started = await asyncio.to_thread(engine.started.wait, 1)
        assert started is True
        assert service.cancel(request.generation_id) is True
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        engine.release.set()
        await service.close()
