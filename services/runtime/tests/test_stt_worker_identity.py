"""Every worker result must belong to the exact admitted STT request."""

import json
from typing import cast
from uuid import uuid4

import httpx2
import pytest
from chatwaifu_runtime.realtime.contracts import SttRequest, VoiceTurnIdentity
from chatwaifu_runtime.realtime.stt import FasterWhisperWorkerSttBackend


@pytest.mark.parametrize(
    "field", ["session_id", "turn_id", "generation_id", "request_id", "job_id"]
)
async def test_stt_worker_rejects_mismatched_identity_even_with_other_ids_preserved(
    field: str,
) -> None:
    identity = VoiceTurnIdentity(
        session_id=uuid4(),
        utterance_id=uuid4(),
        audio_stream_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
    )

    async def handler(request: httpx2.Request) -> httpx2.Response:
        body = cast(dict[str, object], json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-local-worker-token"
        returned = {
            key: body[key]
            for key in (
                "schema_version",
                "session_id",
                "turn_id",
                "generation_id",
                "request_id",
                "job_id",
            )
        }
        returned[field] = str(uuid4())
        returned.update(
            text="不得进入会话的结果",
            language="zh",
            confidence=None,
            duration_ms=20,
            provider="faster-whisper",
        )
        return httpx2.Response(200, json=returned)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    backend = FasterWhisperWorkerSttBackend(
        base_url="http://local-worker.invalid",
        token="test-local-worker-token",
        timeout_seconds=1,
        client=client,
    )
    try:
        with pytest.raises(RuntimeError, match="mismatched"):
            await backend.transcribe(
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
