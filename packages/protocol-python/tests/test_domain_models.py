from datetime import UTC, datetime
from uuid import uuid4

import pytest
from chatwaifu_protocol.avatar import AvatarCue
from chatwaifu_protocol.media import (
    AudioFrameHeader,
    decode_audio_frame_header,
    encode_audio_frame_header,
)
from chatwaifu_protocol.memory import MemoryProposal, MemoryRecordDraft
from pydantic import ValidationError

NOW = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)


def test_memory_proposal_requires_existing_source_evidence() -> None:
    with pytest.raises(ValidationError):
        MemoryProposal(
            proposal_id=uuid4(),
            operation="add",
            candidate=MemoryRecordDraft(
                namespace="user/test/global",
                kind="semantic",
                text="User prefers Chinese responses",
                observed_at=NOW,
                confidence=0.9,
                importance=0.7,
            ),
            evidence_event_ids=[],
            confidence=0.9,
            rationale="Explicit preference",
        )


def test_memory_update_requires_target_memory() -> None:
    with pytest.raises(ValidationError):
        MemoryProposal(
            proposal_id=uuid4(),
            operation="update",
            evidence_event_ids=[uuid4()],
            confidence=0.8,
            rationale="Correction",
        )


def test_avatar_cue_rejects_renderer_parameter_like_intensity_overflow() -> None:
    with pytest.raises(ValidationError):
        AvatarCue(
            cue_id=uuid4(),
            kind="expression",
            name="happy",
            intensity=1.5,
        )


def test_audio_frame_header_round_trips_with_generation_identity() -> None:
    generation_id = uuid4()
    header = AudioFrameHeader(
        stream_id=uuid4(),
        generation_id=generation_id,
        sequence=3,
        pts_ms=60,
        duration_ms=20,
        codec="pcm_s16le",
        sample_rate=16_000,
        channels=1,
        byte_length=640,
    )

    encoded = encode_audio_frame_header(header)
    parsed = decode_audio_frame_header(encoded)

    assert parsed == header
    assert parsed.generation_id == generation_id
    assert isinstance(encoded, bytes)
