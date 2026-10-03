"""QQ record preparation is lazy, memory-only, bounded and cancellable."""

from __future__ import annotations

import asyncio
import io
import struct
import wave
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.external_channels.adapters.qq_napcat import audio
from chatwaifu_runtime.external_channels.adapters.qq_napcat.audio import (
    NapCatAudioTranscriber,
    audio_input,
    decode_record_wav,
)
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatError
from chatwaifu_runtime.external_channels.adapters.qq_napcat.messages import NapCatRecordReference
from chatwaifu_runtime.external_channels.models import ChannelTranscriptionIdentity
from chatwaifu_runtime.realtime.contracts import SttRequest, SttResult
from chatwaifu_runtime.runtime_skills.voice_intent import requests_voice


def wav_data(*, rate: int = 24_000, channels: int = 1, width: int = 2, frames: int = 240) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x00" * (frames * channels * width))
    return output.getvalue()


def identity() -> ChannelTranscriptionIdentity:
    return ChannelTranscriptionIdentity(session_id=uuid4(), turn_id=uuid4(), generation_id=uuid4())


@dataclass
class Transport:
    data: bytes = field(default_factory=wav_data, repr=False)
    error: Exception | None = None
    calls: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])

    async def download_record(self, file_ref: str, *, max_bytes: int) -> bytes:
        self.calls.append((file_ref, max_bytes))
        if self.error is not None:
            raise self.error
        return self.data


@dataclass
class Backend:
    kind: str = "faster_whisper_worker"
    result: SttResult | None = field(
        default_factory=lambda: SttResult("  请用语音回复我  ", "zh", "faster-whisper")
    )
    error: Exception | None = None
    requests: list[SttRequest] = field(default_factory=list[SttRequest])
    cancellations: list[UUID] = field(default_factory=list[UUID])
    entered: asyncio.Event | None = None
    release: asyncio.Event | None = None
    cancel_release: asyncio.Event | None = None

    async def transcribe(self, request: SttRequest) -> SttResult | None:
        self.requests.append(request)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return self.result

    async def cancel(self, generation_id: UUID) -> None:
        self.cancellations.append(generation_id)
        if self.cancel_release is not None:
            await self.cancel_release.wait()

    async def deactivate(self) -> bool:
        return False

    async def close(self) -> None:
        return None


async def test_lazy_preparation_preserves_identity_and_fresh_transcript() -> None:
    transport, backend = Transport(), Backend()
    transcriber = NapCatAudioTranscriber(backend)
    record = NapCatRecordReference("voice.silk", 123)
    attachment = audio_input(transport, record, transcriber)
    assert not transport.calls and not backend.requests
    assert (
        attachment.source_fingerprint
        == audio_input(transport, record, transcriber).source_fingerprint
    )
    assert (
        attachment.source_fingerprint
        != audio_input(
            transport, NapCatRecordReference("other.silk", 123), transcriber
        ).source_fingerprint
    )
    assert (
        attachment.source_fingerprint
        != audio_input(
            transport, NapCatRecordReference("voice.silk", 124), transcriber
        ).source_fingerprint
    )
    assert "voice.silk" not in repr(attachment)
    admitted = identity()
    text = await attachment.load(admitted)
    assert text == "请用语音回复我" and requests_voice(text)
    assert transport.calls == [("voice.silk", 5 * 1024 * 1024)]
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert (
        request.identity.session_id,
        request.identity.turn_id,
        request.identity.generation_id,
    ) == (admitted.session_id, admitted.turn_id, admitted.generation_id)
    assert request.audio == b"\x00" * 480
    assert request.sample_rate == 24_000 and request.channels == 1 and request.language == "zh"
    assert not backend.cancellations


@pytest.mark.parametrize(
    "record",
    [
        NapCatRecordReference("../private.silk"),
        NapCatRecordReference("voice.silk", 0),
        NapCatRecordReference("voice.silk", 5 * 1024 * 1024 + 1),
        NapCatRecordReference("voice.silk", invalid_reason="invalid_size"),
    ],
)
async def test_bad_record_metadata_performs_no_provider_or_stt_call(
    record: NapCatRecordReference,
) -> None:
    transport, backend = Transport(), Backend()
    with pytest.raises(NapCatError, match="could not be transcribed"):
        await audio_input(transport, record, NapCatAudioTranscriber(backend)).load(identity())
    assert not transport.calls and not backend.requests


@pytest.mark.parametrize("kind", ["disabled", "cloud", "external_upload"])
async def test_unavailable_or_nonlocal_backend_performs_no_download(kind: str) -> None:
    transport, backend = Transport(), Backend(kind=kind)
    with pytest.raises(NapCatError):
        await audio_input(
            transport, NapCatRecordReference("voice.silk"), NapCatAudioTranscriber(backend)
        ).load(identity())
    assert not transport.calls and not backend.requests


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x02#!SILK_V3native-record",
        b"RIFFinvalid",
        wav_data(frames=0),
        wav_data(rate=7999),
        wav_data(rate=48001),
        wav_data(channels=3),
        wav_data(width=1),
        wav_data(width=3),
        wav_data(rate=8000, frames=8000 * 60 + 1),
        wav_data()[:-1],
        wav_data() + b"private-trailing-content",
        b"0" * (5 * 1024 * 1024 + 1),
    ],
)
async def test_invalid_or_oversized_wav_never_reaches_stt(data: bytes) -> None:
    transport, backend = Transport(data), Backend()
    with pytest.raises(NapCatError):
        await audio_input(
            transport, NapCatRecordReference("voice.silk"), NapCatAudioTranscriber(backend)
        ).load(identity())
    assert len(transport.calls) == 1 and not backend.requests


def test_wav_accepts_bounded_stereo_and_drops_metadata_chunks() -> None:
    original = wav_data(rate=48_000, channels=2, frames=120)
    junk = b"private-metadata"
    chunk = b"JUNK" + struct.pack("<I", len(junk)) + junk
    data = original[:12] + chunk + original[12:]
    data = data[:4] + struct.pack("<I", len(data) - 8) + data[8:]
    pcm = decode_record_wav(data)
    assert pcm.audio == b"\x00" * 480 and pcm.sample_rate == 48_000 and pcm.channels == 2
    assert "private" not in repr(pcm) and junk not in pcm.audio


def test_wav_rejects_multiple_data_chunks_and_incomplete_declared_frames() -> None:
    original = wav_data()
    duplicate = original + b"data" + struct.pack("<I", 2) + b"\x00\x00"
    duplicate = duplicate[:4] + struct.pack("<I", len(duplicate) - 8) + duplicate[8:]
    incomplete = original[:-2]
    incomplete = incomplete[:4] + struct.pack("<I", len(incomplete) - 8) + incomplete[8:]
    for data in (duplicate, incomplete):
        with pytest.raises(ValueError):
            decode_record_wav(data)


@pytest.mark.parametrize(
    "result",
    [
        None,
        SttResult(" \n ", "zh", "faster-whisper"),
        SttResult("x" * 20_001, "zh", "faster-whisper"),
    ],
)
async def test_empty_and_oversized_transcripts_fail_without_inventing_text(
    result: SttResult | None,
) -> None:
    backend = Backend(result=result)
    with pytest.raises(NapCatError):
        await audio_input(
            Transport(), NapCatRecordReference("voice.silk"), NapCatAudioTranscriber(backend)
        ).load(identity())
    assert len(backend.requests) == 1


async def test_stt_failures_are_sanitized_and_cancel_the_actual_generation() -> None:
    backend = Backend(error=RuntimeError("private-filename-and-token"))
    admitted = identity()
    with pytest.raises(NapCatError) as failure:
        await audio_input(
            Transport(), NapCatRecordReference("voice.silk"), NapCatAudioTranscriber(backend)
        ).load(admitted)
    assert "private" not in str(failure.value)
    assert backend.cancellations == [admitted.generation_id]


async def test_manager_scoped_capacity_fails_fast_and_cancellation_releases_it() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    backend = Backend(entered=entered, release=release)
    transcriber = NapCatAudioTranscriber(backend)
    first_identity = identity()
    attachment = audio_input(Transport(), NapCatRecordReference("voice.silk"), transcriber)

    async def load() -> str:
        return await attachment.load(first_identity)

    task = asyncio.create_task(load())
    await asyncio.wait_for(entered.wait(), timeout=1)
    other_transport = Transport()
    with pytest.raises(NapCatError, match="busy"):
        await audio_input(other_transport, NapCatRecordReference("other.silk"), transcriber).load(
            identity()
        )
    assert not other_transport.calls and len(backend.requests) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.cancellations == [first_identity.generation_id]
    backend.release = None
    assert await attachment.load(identity()) == "请用语音回复我"


async def test_worker_cancel_cleanup_has_its_own_bound_and_preserves_cancelled_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    backend = Backend(entered=entered, release=asyncio.Event(), cancel_release=asyncio.Event())
    monkeypatch.setattr(audio, "STT_CANCEL_TIMEOUT_SECONDS", 0.01)
    attachment = audio_input(
        Transport(), NapCatRecordReference("voice.silk"), NapCatAudioTranscriber(backend)
    )

    async def load() -> str:
        return await attachment.load(identity())

    task = asyncio.create_task(load())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert len(backend.cancellations) == 1
