import base64
import io
import wave
from uuid import uuid4

import pytest
from chatwaifu_model_worker import (
    SttTranscriptionRequest,
    SttWorkerCapabilities,
    TtsPcmFrame,
    TtsStreamStart,
    TtsSynthesisRequest,
    TtsSynthesisResult,
    TtsWorkerCapabilities,
    WorkerHealth,
    WorkerRuntimeDiagnostics,
    pack_tts_pcm_frame,
    unpack_tts_pcm_frame,
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


def test_stt_worker_capabilities_are_provider_neutral() -> None:
    capabilities = SttWorkerCapabilities(
        provider_id="faster-whisper",
        display_name="faster-whisper · CPU",
        model="base",
        languages=["zh", "ja", "en"],
    )

    assert capabilities.supports_partial is False
    assert capabilities.supports_word_timestamps is False
    assert capabilities.local_only is True


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
        style="温柔、稍微害羞",
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
    assert request.style == "温柔、稍微害羞"


def test_tts_worker_capabilities_are_provider_neutral() -> None:
    capabilities = TtsWorkerCapabilities(
        provider_id="qwen3_tts_mlx",
        display_name="Qwen3-TTS · MLX",
        model="Qwen3-TTS-0.6B",
        languages=["zh", "ja"],
        supports_voice_cloning=True,
        native_streaming=True,
        stream_protocols=["pcm.v2"],
    )

    assert capabilities.output_formats == ["wav"]
    assert capabilities.local_only is True
    assert capabilities.stream_protocols == ["pcm.v2"]


def test_worker_health_can_carry_cuda_runtime_evidence() -> None:
    health = WorkerHealth(
        status="ready",
        worker_id="tts-qwen-cuda-test",
        model_loaded=True,
        model="nene-qwen3-0.6b",
        queue_depth=0,
        device="cuda:0",
        runtime_diagnostics=WorkerRuntimeDiagnostics(
            torch_version="2.7.1+cu126",
            torch_cuda_version="12.6",
            cuda_available=True,
            cuda_device_index=0,
            cuda_device_name="NVIDIA GeForce RTX 3090",
            cuda_compute_capability="8.6",
            cuda_total_memory_bytes=25_769_803_776,
            cuda_free_memory_bytes=20_000_000_000,
            cuda_memory_allocated_bytes=4_000_000_000,
            cuda_memory_reserved_bytes=4_500_000_000,
            model_device="cuda:0",
            model_parameter_devices=["cuda:0"],
        ),
    )

    payload = health.model_dump(mode="json")
    assert payload["runtime_diagnostics"]["cuda_compute_capability"] == "8.6"
    assert payload["runtime_diagnostics"]["model_parameter_devices"] == ["cuda:0"]


def test_tts_v2_pcm_frame_round_trips_generation_identity() -> None:
    request = TtsSynthesisRequest(
        request_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        job_id=uuid4(),
        text="欢迎回来。",
        language="zh",
        voice_id="nene",
        speaker_id=0,
        speed=1.0,
    )
    start = TtsStreamStart(request=request)
    frame = TtsPcmFrame(
        generation_id=request.generation_id,
        job_id=request.job_id,
        sequence=7,
        sample_rate=24_000,
        channels=1,
        pcm16=b"\x01\x00" * 240,
    )

    decoded = unpack_tts_pcm_frame(pack_tts_pcm_frame(frame))

    assert start.schema_version == "2.0"
    assert decoded == frame


def test_tts_v2_pcm_frame_rejects_unaligned_payload() -> None:
    with pytest.raises(ValueError, match="aligned"):
        pack_tts_pcm_frame(
            TtsPcmFrame(
                generation_id=uuid4(),
                job_id=uuid4(),
                sequence=0,
                sample_rate=24_000,
                channels=1,
                pcm16=b"odd",
            )
        )


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
