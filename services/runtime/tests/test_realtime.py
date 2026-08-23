"""Realtime media boundary tests that do not require a microphone."""

import json
from uuid import uuid4

import httpx2
import pytest
from chatwaifu_runtime.realtime.contracts import SttRequest, VoiceTurnIdentity
from chatwaifu_runtime.realtime.pipecat.processor import UtteranceBuffer
from chatwaifu_runtime.realtime.stt import FasterWhisperWorkerSttBackend
from fastapi.testclient import TestClient


def test_utterance_buffer_keeps_preroll_and_bounds_recording() -> None:
    buffer = UtteranceBuffer(
        sample_rate=1000,
        channels=1,
        pre_roll_ms=100,
        max_seconds=1,
    )
    buffer.push(b"a" * 120)
    buffer.push(b"b" * 120)
    buffer.start()
    buffer.push(b"c" * 2_000)

    audio = buffer.finish()

    assert audio.startswith(b"b")
    assert len(audio) == 2_000


def test_webrtc_offer_requires_an_existing_session(client: TestClient) -> None:
    response = client.post(
        "/v1/sessions/00000000-0000-4000-8000-000000000099/webrtc/offer",
        json={"sdp": "not-used-for-an-unknown-session", "type": "offer"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"


@pytest.mark.asyncio
async def test_stt_worker_adapter_preserves_generation_identity_and_auth() -> None:
    identity = VoiceTurnIdentity(
        session_id=uuid4(),
        utterance_id=uuid4(),
        audio_stream_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
    )

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer secret-token"
        body = json.loads(request.content)
        assert body["generation_id"] == str(identity.generation_id)
        return httpx2.Response(
            200,
            json={
                "schema_version": "1.0",
                "request_id": body["request_id"],
                "session_id": body["session_id"],
                "turn_id": body["turn_id"],
                "generation_id": body["generation_id"],
                "job_id": body["job_id"],
                "text": "真实语音输入",
                "language": "zh",
                "confidence": None,
                "duration_ms": 20,
                "provider": "faster-whisper",
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    backend = FasterWhisperWorkerSttBackend(
        base_url="http://worker.local",
        token="secret-token",
        timeout_seconds=5,
        client=client,
    )
    try:
        result = await backend.transcribe(
            SttRequest(
                identity=identity,
                audio=b"\x00\x00" * 320,
                sample_rate=16_000,
                channels=1,
                language="zh",
            )
        )
    finally:
        await backend.close()

    assert result is not None
    assert result.text == "真实语音输入"
    assert result.provider == "faster-whisper"
