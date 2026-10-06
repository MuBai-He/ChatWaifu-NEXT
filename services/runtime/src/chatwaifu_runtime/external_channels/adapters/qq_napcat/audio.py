"""Ephemeral WAV-to-text preparation for a durably admitted owner QQ turn."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import struct
import wave
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from chatwaifu_runtime.external_channels.models import (
    ChannelInboundAudioInput,
    ChannelTranscriptionIdentity,
)
from chatwaifu_runtime.realtime.contracts import SttBackend, SttRequest, VoiceTurnIdentity

from .client import NapCatError, validate_image_file_ref
from .messages import NapCatRecordReference

MAX_RECORD_BYTES = 5 * 1024 * 1024
MAX_RECORD_SECONDS = 60
MAX_TRANSCRIPT_CHARACTERS = 20_000
STT_CANCEL_TIMEOUT_SECONDS = 2.0


class NapCatRecordTransport(Protocol):
    async def download_record(self, file_ref: str, *, max_bytes: int) -> bytes: ...


@dataclass(frozen=True, slots=True)
class RecordPcm16:
    audio: bytes = field(repr=False)
    sample_rate: int
    channels: int


def decode_record_wav(data: bytes) -> RecordPcm16:
    """Validate a complete bounded RIFF/WAVE in memory and discard all metadata."""
    if not data or len(data) > MAX_RECORD_BYTES:
        raise ValueError("QQ WAV is empty or oversized")
    if (
        len(data) < 12
        or data[:4] != b"RIFF"
        or data[8:12] != b"WAVE"
        or struct.unpack_from("<I", data, 4)[0] != len(data) - 8
    ):
        raise ValueError("QQ WAV has an invalid RIFF envelope")
    offset = 12
    formats = 0
    audio_chunks = 0
    audio_size = 0
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("QQ WAV has a truncated chunk header")
        kind = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        end = offset + 8 + size
        if end > len(data):
            raise ValueError("QQ WAV has an incomplete chunk")
        if kind == b"fmt ":
            formats += 1
        elif kind == b"data":
            audio_chunks += 1
            audio_size = size
        offset = end + size % 2
    if offset != len(data) or formats != 1 or audio_chunks != 1:
        raise ValueError("QQ WAV has an unsupported chunk layout")
    with wave.open(io.BytesIO(data), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        frames = wav.getnframes()
        if (
            wav.getcomptype() != "NONE"
            or wav.getsampwidth() != 2
            or not 8_000 <= sample_rate <= 48_000
            or channels not in {1, 2}
            or not 0 < frames <= sample_rate * MAX_RECORD_SECONDS
            or audio_size != frames * channels * 2
        ):
            raise ValueError("QQ WAV must be bounded uncompressed PCM16")
        audio = wav.readframes(frames + 1)
        if len(audio) != frames * channels * 2:
            raise ValueError("QQ WAV has incomplete PCM16 frames")
        return RecordPcm16(audio=audio, sample_rate=sample_rate, channels=channels)


class NapCatAudioTranscriber:
    """One fail-fast preparation slot shared by this manager's QQ connections."""

    def __init__(self, backend: SttBackend) -> None:
        self._backend = backend
        self._busy = False

    async def prepare(
        self,
        transport: NapCatRecordTransport,
        record: NapCatRecordReference,
        identity: ChannelTranscriptionIdentity,
    ) -> str:
        if self._busy:
            raise NapCatError("QQ voice transcription is busy; please send it again")
        self._busy = True
        stt_started = False
        stt_completed = False
        try:
            validate_image_file_ref(record.file_ref)
            if (
                self._backend.kind != "faster_whisper_worker"
                or record.invalid_reason is not None
                or (record.file_size is not None and not 0 < record.file_size <= MAX_RECORD_BYTES)
            ):
                raise ValueError("QQ record or local STT backend is unavailable")
            data = await transport.download_record(record.file_ref, max_bytes=MAX_RECORD_BYTES)
            pcm = await asyncio.to_thread(decode_record_wav, data)
            stt_started = True
            result = await self._backend.transcribe(
                SttRequest(
                    identity=VoiceTurnIdentity(
                        session_id=identity.session_id,
                        turn_id=identity.turn_id,
                        generation_id=identity.generation_id,
                        utterance_id=uuid4(),
                        audio_stream_id=uuid4(),
                    ),
                    audio=pcm.audio,
                    sample_rate=pcm.sample_rate,
                    channels=pcm.channels,
                    language="zh",
                )
            )
            stt_completed = True
            text = result.text.strip() if result is not None else ""
            if not text or len(text) > MAX_TRANSCRIPT_CHARACTERS:
                raise ValueError("QQ voice transcript is empty or oversized")
            return text
        except asyncio.CancelledError:
            raise
        except Exception:
            raise NapCatError("QQ voice could not be transcribed; please send it again") from None
        finally:
            try:
                if stt_started and not stt_completed:
                    try:
                        async with asyncio.timeout(STT_CANCEL_TIMEOUT_SECONDS):
                            await self._backend.cancel(identity.generation_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # Cancellation is best effort and bounded; no user content is logged.
                        pass
            finally:
                self._busy = False


def audio_input(
    transport: NapCatRecordTransport,
    record: NapCatRecordReference,
    stt: NapCatAudioTranscriber,
) -> ChannelInboundAudioInput:
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "file": record.file_ref,
                "file_size": record.file_size,
                "invalid_reason": record.invalid_reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    async def load(identity: ChannelTranscriptionIdentity) -> str:
        return await stt.prepare(transport, record, identity)

    return ChannelInboundAudioInput(source_fingerprint=fingerprint, load=load)
