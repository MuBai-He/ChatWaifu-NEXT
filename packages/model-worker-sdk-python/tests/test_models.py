import base64
import io
import wave
from uuid import uuid4

import pytest
from chatwaifu_model_worker import (
    SttTranscriptionRequest,
    TtsSynthesisRequest,
    TtsSynthesisResult,
)
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


def test_tts_request_and_wave_result_keep_full_generation_identity() -> None:
    request_id = uuid4()
    session_id = uuid4()
    turn_id = uuid4()
    generation_id = uuid4()
    job_id = uuid4()
    request = TtsSynthesisRequest(
        request_id=request_id,
        session_id=session_id,
        turn_id=turn_id,
        generation_id=generation_id,
        job_id=job_id,
        text="欢迎回来。",
        language="zh",
        voice_id="ayachi-nene-demo-zh",
        speaker_id=3,
        speed=1.04,
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\x00\x00" * 240)
    audio = buffer.getvalue()
    result = TtsSynthesisResult(
        request_id=request_id,
        session_id=session_id,
        turn_id=turn_id,
        generation_id=generation_id,
        job_id=job_id,
        audio_base64=base64.b64encode(audio).decode("ascii"),
        sample_rate=24_000,
        duration_ms=10,
        provider="sherpa-onnx-kokoro",
        model="kokoro-multi-lang-v1_1",
        speaker_id=request.speaker_id,
    )

    assert result.audio_bytes() == audio
    assert result.generation_id == request.generation_id


def test_tts_result_rejects_non_wave_audio() -> None:
    with pytest.raises(ValidationError):
        TtsSynthesisResult(
            request_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            generation_id=uuid4(),
            job_id=uuid4(),
            audio_base64=base64.b64encode(b"not a wave asset").decode("ascii"),
            sample_rate=24_000,
            duration_ms=10,
            provider="sherpa-onnx-kokoro",
            model="kokoro-multi-lang-v1_1",
            speaker_id=3,
        )
