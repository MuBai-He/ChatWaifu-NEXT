import base64
from uuid import uuid4

import pytest
from chatwaifu_model_worker import SttTranscriptionRequest
from pydantic import ValidationError


def test_stt_request_round_trips_pcm_with_full_generation_identity() -> None:
    audio = b"\x00\x01" * 320
    request = SttTranscriptionRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        job_id=uuid4(),
        audio_base64=base64.b64encode(audio).decode("ascii"),
        sample_rate=16_000,
        channels=1,
        language="zh",
    )

    assert request.audio_bytes() == audio
    assert request.schema_version == "1.0"


def test_stt_request_rejects_misaligned_pcm() -> None:
    with pytest.raises(ValidationError):
        SttTranscriptionRequest(
            request_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
            job_id=uuid4(),
            audio_base64=base64.b64encode(b"three").decode("ascii"),
            sample_rate=16_000,
            channels=1,
        )
