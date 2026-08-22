from datetime import UTC, datetime
from uuid import UUID

import pytest
from chatwaifu_protocol.events import GenericCoreEvent, SessionCreatedEvent, SessionCreatedPayload
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


def test_accepts_declared_core_event_without_pretending_its_future_payload_is_specialized() -> None:
    raw = make_event().model_dump(mode="json")
    raw["event_type"] = "assistant.playback_stopped"
    raw["payload"] = {"reason": "completed"}

    parsed = create_default_registry().parse_event(raw)

    assert isinstance(parsed, GenericCoreEvent)
    assert parsed.event_type == "assistant.playback_stopped"
