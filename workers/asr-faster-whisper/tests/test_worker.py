# Starlette's TestClient methods inherit partially untyped httpx compatibility overloads.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import base64
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient

from chatwaifu_asr_worker.config import WorkerSettings
from chatwaifu_asr_worker.main import create_app
from chatwaifu_asr_worker.service import TranscriptionEngine, TranscriptionService


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
