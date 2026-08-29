"""Binary framing for Worker Protocol v2 PCM streams."""

import struct
from dataclasses import dataclass
from uuid import UUID

TTS_PCM_FRAME_MAGIC = b"CWT2"
TTS_PCM_FRAME_VERSION = 2
TTS_PCM_FRAME_MAX_PAYLOAD_BYTES = 4_000_000
_HEADER = struct.Struct("!4sB16s16sIIH")


@dataclass(frozen=True, slots=True)
class TtsPcmFrame:
    generation_id: UUID
    job_id: UUID
    sequence: int
    sample_rate: int
    channels: int
    pcm16: bytes


def pack_tts_pcm_frame(frame: TtsPcmFrame) -> bytes:
    _validate_frame(frame)
    return (
        _HEADER.pack(
            TTS_PCM_FRAME_MAGIC,
            TTS_PCM_FRAME_VERSION,
            frame.generation_id.bytes,
            frame.job_id.bytes,
            frame.sequence,
            frame.sample_rate,
            frame.channels,
        )
        + frame.pcm16
    )


def unpack_tts_pcm_frame(payload: bytes) -> TtsPcmFrame:
    if len(payload) <= _HEADER.size:
        raise ValueError("TTS PCM frame is missing audio payload")
    magic, version, generation, job, sequence, sample_rate, channels = _HEADER.unpack_from(payload)
    if magic != TTS_PCM_FRAME_MAGIC or version != TTS_PCM_FRAME_VERSION:
        raise ValueError("unsupported TTS PCM frame")
    frame = TtsPcmFrame(
        generation_id=UUID(bytes=generation),
        job_id=UUID(bytes=job),
        sequence=sequence,
        sample_rate=sample_rate,
        channels=channels,
        pcm16=payload[_HEADER.size :],
    )
    _validate_frame(frame)
    return frame


def _validate_frame(frame: TtsPcmFrame) -> None:
    if frame.sequence < 0 or frame.sequence > 0xFFFFFFFF:
        raise ValueError("TTS PCM sequence is out of range")
    if frame.sample_rate < 8_000 or frame.sample_rate > 48_000:
        raise ValueError("TTS PCM sample rate is out of range")
    if frame.channels not in (1, 2):
        raise ValueError("TTS PCM channels must be 1 or 2")
    if not frame.pcm16 or len(frame.pcm16) > TTS_PCM_FRAME_MAX_PAYLOAD_BYTES:
        raise ValueError("TTS PCM payload size is out of range")
    if len(frame.pcm16) % (frame.channels * 2):
        raise ValueError("TTS PCM payload is not aligned to PCM16 frames")
