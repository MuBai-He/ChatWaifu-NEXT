# Starlette's TestClient methods inherit partially untyped httpx compatibility overloads.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
import base64
import threading
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import pytest
from chatwaifu_model_worker import SttTranscriptionRequest, SttTranscriptionResult
from fastapi.testclient import TestClient

from chatwaifu_asr_worker.config import WorkerSettings
from chatwaifu_asr_worker.main import create_app
from chatwaifu_asr_worker.service import TranscriptionEngine, TranscriptionService


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeEngine(TranscriptionEngine):
    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None,
    ) -> tuple[str, str | None]:
        assert audio.dtype == np.float32
        return "你好, 语音回合。", language


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=True,
    )
    service = TranscriptionService(settings, engine_factory=lambda _: FakeEngine())
    with TestClient(create_app(settings, service)) as test_client:
        yield test_client


def test_worker_requires_ephemeral_token(client: TestClient) -> None:
    assert client.get("/v1/health").status_code == 401
    health = client.get("/v1/health", headers={"Authorization": "Bearer test-token"})
    assert health.status_code == 200
    assert health.json()["model_loaded"] is True
    capabilities = client.get("/v1/capabilities", headers={"Authorization": "Bearer test-token"})
    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "schema_version": "1.0",
        "provider_id": "faster-whisper",
        "display_name": "faster-whisper · 本地",
        "model": "base",
        "languages": ["zh", "ja", "en"],
        "supports_partial": False,
        "supports_word_timestamps": False,
        "local_only": True,
    }


def test_worker_transcribes_pcm_with_generation_identity(client: TestClient) -> None:
    identifiers = {
        "request_id": str(uuid4()),
        "session_id": str(uuid4()),
        "turn_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "job_id": str(uuid4()),
    }
    response = client.post(
        "/v1/transcribe",
        headers={"Authorization": "Bearer test-token"},
        json={
            **identifiers,
            "audio_base64": base64.b64encode(b"\x00\x01" * 16_000).decode("ascii"),
            "sample_rate": 16_000,
            "channels": 1,
            "language": "zh",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["generation_id"] == identifiers["generation_id"]
    assert result["text"] == "你好, 语音回合。"
    assert result["provider"] == "faster-whisper"


def test_worker_unloads_idle_model_and_loads_again_on_demand(client: TestClient) -> None:
    headers = {"Authorization": "Bearer test-token"}
    assert client.post("/v1/model/unload", headers=headers).json() == {"unloaded": True}
    assert client.get("/v1/health", headers=headers).json()["model_loaded"] is False

    identifiers = {
        "request_id": str(uuid4()),
        "session_id": str(uuid4()),
        "turn_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "job_id": str(uuid4()),
    }
    response = client.post(
        "/v1/transcribe",
        headers=headers,
        json={
            **identifiers,
            "audio_base64": base64.b64encode(b"\x00\x01" * 160).decode("ascii"),
            "sample_rate": 16_000,
            "channels": 1,
            "language": "zh",
        },
    )

    assert response.status_code == 200
    assert client.get("/v1/health", headers=headers).json()["model_loaded"] is True


class BlockingEngine(TranscriptionEngine):
    def __init__(self) -> None:
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.second_started = threading.Event()
        self._lock = threading.Lock()
        self._calls = 0
        self.concurrent = 0
        self.max_concurrent = 0

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None,
    ) -> tuple[str, str | None]:
        with self._lock:
            self._calls += 1
            call = self._calls
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if call == 1:
                self.first_started.set()
                self.release_first.wait(timeout=2)
                return "迟到旧结果", language
            self.second_started.set()
            return "第二轮", language
        finally:
            with self._lock:
                self.concurrent -= 1


def _request(*, generation_id: UUID | None = None) -> SttTranscriptionRequest:
    return SttTranscriptionRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=generation_id or uuid4(),
        job_id=uuid4(),
        audio_base64=base64.b64encode(b"\x00\x01" * 160).decode("ascii"),
        sample_rate=16_000,
        channels=1,
        language="zh",
    )


@pytest.mark.anyio
async def test_cancelled_native_transcription_never_overlaps_next_generation(
    tmp_path: Path,
) -> None:
    settings = WorkerSettings(
        token="test-token",  # pyright: ignore[reportArgumentType]
        model_dir=tmp_path,
        preload=False,
    )
    engine = BlockingEngine()
    service = TranscriptionService(settings, engine_factory=lambda _: engine)
    first = _request()
    second = _request()
    first_task = asyncio.create_task(service.transcribe(first))
    second_task: asyncio.Task[SttTranscriptionResult] | None = None
    try:
        assert await asyncio.to_thread(engine.first_started.wait, 1)
        assert service.cancel(first.generation_id) is True
        with pytest.raises(asyncio.CancelledError):
            await first_task
        assert await service.unload() is False

        second_task = asyncio.create_task(service.transcribe(second))
        await asyncio.sleep(0)
        assert engine.second_started.is_set() is False

        engine.release_first.set()
        result = await asyncio.wait_for(second_task, timeout=1)
        assert result.text == "第二轮"
        assert engine.max_concurrent == 1
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
