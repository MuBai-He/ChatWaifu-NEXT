"""Headers for high-frequency media transported outside the event store."""

import json
from typing import Literal
from uuid import UUID

from pydantic import Field

from chatwaifu_protocol.base import ProtocolModel

MAX_MEDIA_HEADER_BYTES = 16_384


class AudioFrameHeader(ProtocolModel):
    stream_id: UUID
    generation_id: UUID | None = None
    sequence: int = Field(ge=0)
    pts_ms: int = Field(ge=0)
    duration_ms: int = Field(gt=0, le=10_000)
    codec: Literal["pcm_s16le", "opus"]
    sample_rate: int = Field(gt=0, le=384_000)
    channels: int = Field(gt=0, le=8)
    byte_length: int = Field(gt=0, le=16_777_216)
    end_of_stream: bool = False


class VideoFrameHeader(ProtocolModel):
    stream_id: UUID
    generation_id: UUID | None = None
    sequence: int = Field(ge=0)
    pts_ms: int = Field(ge=0)
    codec: Literal["jpeg", "png", "h264", "vp8"]
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    byte_length: int = Field(gt=0, le=67_108_864)
    end_of_stream: bool = False


def encode_audio_frame_header(header: AudioFrameHeader) -> bytes:
    """Encode the JSON control header that precedes a future binary audio body."""

    encoded = header.model_dump_json().encode()
    if len(encoded) > MAX_MEDIA_HEADER_BYTES:
        raise ValueError("audio frame header exceeds size limit")
    return encoded


def decode_audio_frame_header(encoded: bytes) -> AudioFrameHeader:
    """Decode and validate an untrusted JSON control header from bytes."""

    if len(encoded) > MAX_MEDIA_HEADER_BYTES:
        raise ValueError("audio frame header exceeds size limit")
    try:
        value: object = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid audio frame header") from error
    return AudioFrameHeader.model_validate(value)
