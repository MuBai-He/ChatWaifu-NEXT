"""Realtime media boundary tests that do not require a microphone."""

from chatwaifu_runtime.realtime.pipecat.processor import UtteranceBuffer
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
