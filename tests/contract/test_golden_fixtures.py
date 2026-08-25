import json
from pathlib import Path

from chatwaifu_protocol.commands import TextSendCommand
from chatwaifu_protocol.events import GENERIC_CORE_EVENT_TYPES, SessionCreatedEvent
from chatwaifu_protocol.media import AudioFrameHeader
from chatwaifu_protocol.registry import create_default_registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "protocol" / "v1"


def load_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_python_event_fixture_parses_through_registry() -> None:
    event = create_default_registry().parse_event(
        load_fixture("python-session-created-event.json")  # type: ignore[arg-type]
    )
    assert isinstance(event, SessionCreatedEvent)
    assert event.payload.character_id == "default-character"


def test_python_generic_event_catalog_fixture_matches_protocol_source() -> None:
    assert load_fixture("python-generic-core-event-types.json") == list(GENERIC_CORE_EVENT_TYPES)


def test_typescript_command_fixture_parses_in_python() -> None:
    command = create_default_registry().parse_command(
        load_fixture("typescript-text-send-command.json")  # type: ignore[arg-type]
    )
    assert isinstance(command, TextSendCommand)
    assert command.payload.text == "你好，Hikari"


def test_binary_audio_header_fixture_round_trips() -> None:
    header = AudioFrameHeader.model_validate(load_fixture("audio-frame-header.json"))
    assert AudioFrameHeader.model_validate_json(header.model_dump_json()) == header
