from datetime import UTC, datetime
from uuid import UUID

import pytest
from chatwaifu_protocol.events import (
    AssistantPlaybackStoppedEvent,
    SessionCreatedEvent,
    SessionCreatedPayload,
)
from chatwaifu_protocol.registry import (
    UnknownMessageType,
    UnsupportedSchemaVersion,
    create_default_registry,
)
from pydantic import ValidationError


def make_event() -> SessionCreatedEvent:
    return SessionCreatedEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000101"),
        session_id=UUID("00000000-0000-4000-8000-000000000201"),
        sequence=1,
        occurred_at=datetime(2026, 8, 23, 8, 0, tzinfo=UTC),
        source="test",
        payload=SessionCreatedPayload(character_id="default-character"),
    )


def test_parses_registered_event_and_preserves_uuid_datetime() -> None:
    parsed = create_default_registry().parse_event(make_event().model_dump_json())

    assert isinstance(parsed, SessionCreatedEvent)
    assert parsed.session_id == UUID("00000000-0000-4000-8000-000000000201")
    assert parsed.occurred_at == datetime(2026, 8, 23, 8, 0, tzinfo=UTC)


def test_rejects_unknown_schema_major_version() -> None:
    raw = make_event().model_dump(mode="json")
    raw["schema_version"] = "2.0"

    with pytest.raises(UnsupportedSchemaVersion):
        create_default_registry().parse_event(raw)


def test_accepts_unknown_optional_field_in_supported_major() -> None:
    raw = make_event().model_dump(mode="json")
    raw["schema_version"] = "1.8"
    raw["future_hint"] = True

    parsed = create_default_registry().parse_event(raw)

    assert parsed.schema_version == "1.8"


def test_rejects_unknown_event_type_and_invalid_payload() -> None:
    raw = make_event().model_dump(mode="json")
    raw["event_type"] = "session.future_event"
    with pytest.raises(UnknownMessageType):
        create_default_registry().parse_event(raw)

    raw = make_event().model_dump(mode="json")
    raw["payload"] = {}
    with pytest.raises(ValidationError):
        create_default_registry().parse_event(raw)


def test_parses_playback_ack_event_as_a_specialized_contract() -> None:
    raw = make_event().model_dump(mode="json")
    raw["event_type"] = "assistant.playback_stopped"
    raw["generation_id"] = "00000000-0000-4000-8000-000000000301"
    raw["payload"] = {
        "stream_id": "00000000-0000-4000-8000-000000000401",
        "segment_id": "00000000-0000-4000-8000-000000000402",
        "played_pts_ms": 1840,
        "buffered_ms": 0,
        "client_clock_ms": 12040,
        "transport": "audio_element",
        "reason": "ended",
        "completed": True,
    }

    parsed = create_default_registry().parse_event(raw)

    assert isinstance(parsed, AssistantPlaybackStoppedEvent)
    assert parsed.event_type == "assistant.playback_stopped"
    assert parsed.payload.played_pts_ms == 1840


def test_rejects_incomplete_playback_ack_payload() -> None:
    raw = make_event().model_dump(mode="json")
    raw["event_type"] = "assistant.playback_progress"
    raw["payload"] = {"played_pts_ms": 100}

    with pytest.raises(ValidationError):
        create_default_registry().parse_event(raw)


def test_parses_cloud_egress_receipt_event() -> None:
    raw = make_event().model_dump(mode="json")
    raw["event_type"] = "cloud.egress_receipt"
    raw["payload"] = {
        "provider_backend_id": "fake",
        "patch_id": "00000000-0000-4000-8000-000000000701",
        "component_kinds": ["persona", "memory"],
        "memory_record_ids": ["00000000-0000-4000-8000-000000000702"],
        "byte_count": 256,
        "estimated_tokens": 64,
        "policy_decision": "allow",
        "approved_by": "user",
        "scope": "session",
        "occurred_at": "2026-08-23T08:00:00Z",
    }
    parsed = create_default_registry().parse_event(raw)
    assert parsed.event_type == "cloud.egress_receipt"


def test_parses_cloud_egress_blocked_event() -> None:
    raw = make_event().model_dump(mode="json")
    raw["event_type"] = "cloud.egress_blocked"
    raw["payload"] = {
        "provider_backend_id": "fake",
        "policy_decision": "deny",
        "reason": "Cloud egress denied by policy",
        "occurred_at": "2026-08-23T08:00:00Z",
    }
    parsed = create_default_registry().parse_event(raw)
    assert parsed.event_type == "cloud.egress_blocked"
