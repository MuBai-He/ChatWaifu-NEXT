import json
from pathlib import Path

from chatwaifu_protocol.channels import (
    ChannelDeliveryAcknowledgement,
    ChannelInboundTextMessage,
)
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


def test_python_channel_message_fixture_preserves_external_identity() -> None:
    message = ChannelInboundTextMessage.model_validate(
        load_fixture("python-channel-inbound-text-message.json")
    )
    assert message.external_message_id == "provider-message-001"
    assert message.account_key == "provider-account-001"
    assert message.conversation_key == "provider-direct-conversation-001"
    assert message.sender_key == "provider-sender-001"
    assert message.principal_scope == "owner/local"
    assert message.conversation_label == "与宁宁的测试会话"
    assert message.sender_display_name == "木白"


def test_typescript_channel_delivery_ack_fixture_parses_in_python() -> None:
    acknowledgement = ChannelDeliveryAcknowledgement.model_validate(
        load_fixture("typescript-channel-delivery-ack.json")
    )
    assert acknowledgement.status == "delivered"
    assert acknowledgement.provider_message_id == "provider-reply-001"
