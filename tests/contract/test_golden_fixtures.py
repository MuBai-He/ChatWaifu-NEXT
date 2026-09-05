import json
from pathlib import Path

from chatwaifu_protocol.channels import (
    ChannelDeliveryAcknowledgement,
    ChannelInboundTextMessage,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
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


def test_channel_presentation_policy_defaults_and_parity() -> None:
    # 1. Omitted profile on empty dict defaults to single_text
    policy_default = ChannelPresentationPolicy.model_validate({})
    assert policy_default.profile == ChannelPresentationProfile.SINGLE_TEXT
    assert policy_default.profile == "single_text"
    assert policy_default.max_parts == 3
    assert policy_default.preferred_chars_per_part == 60
    assert policy_default.soft_max_chars_per_part == 120
    assert policy_default.cadence_enabled is True
    assert policy_default.min_delay_ms == 800
    assert policy_default.max_delay_ms == 3000
    assert policy_default.total_cadence_delay_ceiling_ms == 6000

    # 2. Partial policy overrides fields while maintaining single_text
    policy_partial = ChannelPresentationPolicy.model_validate(
        {"max_parts": 5, "min_delay_ms": 1200, "cadence_enabled": False}
    )
    assert policy_partial.profile == ChannelPresentationProfile.SINGLE_TEXT
    assert policy_partial.max_parts == 5
    assert policy_partial.min_delay_ms == 1200
    assert policy_partial.cadence_enabled is False

    # 3. Explicit instant_message profile is preserved
    policy_im = ChannelPresentationPolicy.model_validate(
        {"profile": "instant_message", "max_parts": 2}
    )
    assert policy_im.profile == ChannelPresentationProfile.INSTANT_MESSAGE
    assert policy_im.profile == "instant_message"
    assert policy_im.max_parts == 2
