from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.avatar import AvatarCue
from chatwaifu_protocol.channels import (
    ChannelAuthorizationSnapshot,
    ChannelAuthorizationStartRequest,
    ChannelAuthorizationStatus,
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionSnapshot,
    ChannelDeliveryAcknowledgement,
    ChannelDeliveryStatus,
    ChannelInboundTextMessage,
    ChannelProviderCapabilities,
)
from chatwaifu_protocol.character import AffectState, RelationshipState
from chatwaifu_protocol.events import UserSpeechStartedEvent, UserSpeechStartedPayload
from chatwaifu_protocol.media import (
    AudioFrameHeader,
    decode_audio_frame_header,
    encode_audio_frame_header,
)
from chatwaifu_protocol.memory import (
    MemoryChannelAttribution,
    MemoryProposal,
    MemoryRecordDraft,
    MemorySource,
)
from chatwaifu_protocol.skills import McpConnectionConfiguration, PluginTransport
from pydantic import ValidationError

NOW = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)


def test_memory_proposal_requires_existing_source_evidence() -> None:
    with pytest.raises(ValidationError):
        MemoryProposal(
            proposal_id=uuid4(),
            operation="add",
            candidate=MemoryRecordDraft(
                namespace="user/test/global",
                kind="semantic.fact",
                text="User prefers Chinese responses",
                observed_at=NOW,
                confidence=0.9,
                importance=0.7,
            ),
            evidence_event_ids=[],
            confidence=0.9,
            rationale="Explicit preference",
            created_at=NOW,
        )


def test_memory_update_requires_target_memory() -> None:
    with pytest.raises(ValidationError):
        MemoryProposal(
            proposal_id=uuid4(),
            operation="update",
            evidence_event_ids=[uuid4()],
            confidence=0.8,
            rationale="Correction",
            created_at=NOW,
        )


def test_memory_source_preserves_versioned_external_channel_attribution() -> None:
    attribution = MemoryChannelAttribution(
        provider_id="weixin_ilink",
        connection_id=uuid4(),
        account_key="wechat-owner-account",
        principal_scope="local",
        chat_type="direct",
        conversation_key="wechat-direct-owner",
        sender_key="wechat-owner-sender",
        received_at=NOW,
        conversation_label="与木白的微信私聊",
        sender_display_name="木白",
    )
    source = MemorySource(
        source_id=uuid4(),
        memory_id=uuid4(),
        source_event_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        source_kind="user_turn",
        created_at=NOW,
        channel_attribution=attribution,
    )

    assert source.channel_attribution is not None
    assert source.channel_attribution.schema_version == "1.0"
    assert source.channel_attribution.provider_id == "weixin_ilink"
    assert source.channel_attribution.received_at == NOW

    with pytest.raises(ValidationError):
        MemoryChannelAttribution.model_validate(
            {**attribution.model_dump(), "schema_version": "2.0"}
        )


def test_avatar_cue_rejects_renderer_parameter_like_intensity_overflow() -> None:
    with pytest.raises(ValidationError):
        AvatarCue(
            cue_id=uuid4(),
            kind="expression",
            name="happy",
            intensity=1.5,
        )


def test_character_states_reject_unbounded_model_deltas() -> None:
    with pytest.raises(ValidationError):
        AffectState(valence=2, updated_at=NOW)
    with pytest.raises(ValidationError):
        RelationshipState(affinity=-0.1, updated_at=NOW)


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


def test_speech_started_event_carries_future_generation_identity() -> None:
    event = UserSpeechStartedEvent(
        event_id=UUID("00000000-0000-4000-8000-000000000101"),
        session_id=UUID("00000000-0000-4000-8000-000000000201"),
        turn_id=UUID("00000000-0000-4000-8000-000000000301"),
        generation_id=UUID("00000000-0000-4000-8000-000000000401"),
        occurred_at=datetime(2026, 8, 24, 8, 0, tzinfo=UTC),
        source="runtime.realtime",
        payload=UserSpeechStartedPayload(
            utterance_id=UUID("00000000-0000-4000-8000-000000000501"),
            audio_stream_id=UUID("00000000-0000-4000-8000-000000000601"),
            sample_rate=16_000,
            channels=1,
        ),
    )

    assert event.payload.sample_rate == 16_000
    assert event.turn_id is not None
    assert event.generation_id is not None


@pytest.mark.parametrize(
    ("trust_level", "sandbox_mode", "network_policy"),
    [
        ("untrusted", "disabled", "allow"),
        ("trusted", "disabled", "deny"),
        ("trusted", "required", "loopback"),
    ],
)
def test_local_mcp_process_policy_rejects_unenforceable_combinations(
    trust_level: str,
    sandbox_mode: str,
    network_policy: str,
) -> None:
    with pytest.raises(ValidationError):
        PluginTransport.model_validate(
            {
                "command": ["server"],
                "trust_level": trust_level,
                "sandbox_mode": sandbox_mode,
                "network_policy": network_policy,
            }
        )
    with pytest.raises(ValidationError):
        McpConnectionConfiguration.model_validate(
            {
                "connection_id": uuid4(),
                "name": "Invalid local process",
                "transport": "stdio",
                "command": ["server"],
                "trust_level": trust_level,
                "sandbox_mode": sandbox_mode,
                "network_policy": network_policy,
            }
        )


def test_external_channel_message_has_explicit_stable_identity_fields() -> None:
    message = ChannelInboundTextMessage.model_validate(
        {
            "connection_id": uuid4(),
            "external_message_id": "message-001",
            "conversation_key": "direct-conversation-001",
            "sender_key": "sender-001",
            "principal_scope": "owner/local",
            "conversation_label": "不可信会话标签",
            "sender_display_name": "不可信昵称",
            "chat_type": "direct",
            "kind": "text",
            "text": "今天也请多关照。",
            "received_at": NOW,
            # Provider transport state must never cross the gateway contract.
            "context_token": "adapter-only",
            "qr_token": "adapter-only",
        }
    )

    assert message.schema_version == "1.0"
    assert message.external_message_id == "message-001"
    assert message.conversation_key == "direct-conversation-001"
    assert message.sender_key == "sender-001"
    assert message.principal_scope == "owner/local"
    assert "context_token" not in message.model_dump()
    assert "qr_token" not in message.model_dump()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("external_message_id", ""),
        ("conversation_key", ""),
        ("sender_key", ""),
        ("principal_scope", ""),
        ("kind", "image"),
    ],
)
def test_external_channel_message_rejects_ambiguous_or_unsupported_v1_identity(
    field: str, value: str
) -> None:
    payload: dict[str, object] = {
        "connection_id": uuid4(),
        "external_message_id": "message-001",
        "conversation_key": "direct-conversation-001",
        "sender_key": "sender-001",
        "principal_scope": "owner/local",
        "text": "你好",
        "received_at": NOW,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        ChannelInboundTextMessage.model_validate(payload)


def test_external_channel_protocol_reserves_group_without_advertising_it_by_default() -> None:
    message = ChannelInboundTextMessage(
        connection_id=uuid4(),
        external_message_id="message-001",
        conversation_key="group-conversation-001",
        sender_key="sender-001",
        principal_scope="owner/local",
        chat_type=ChannelChatType.GROUP,
        text="群聊协议字段只作未来兼容。",
        received_at=NOW,
    )

    assert message.chat_type == "group"
    assert ChannelProviderCapabilities().chat_types == ["direct"]


def test_channel_authorization_never_accepts_provider_credentials() -> None:
    request = ChannelAuthorizationStartRequest(
        provider_id="weixin_ilink",
        character_id="ayachi_nene",
    )

    assert request.principal_scope == "local"
    assert "token" not in request.model_dump()
    reparsed = ChannelAuthorizationStartRequest.model_validate(
        {**request.model_dump(), "bot_token": "must-not-cross-the-browser-boundary"}
    )
    assert "bot_token" not in reparsed.model_dump()


def test_channel_authorization_snapshot_enforces_lifecycle_invariants() -> None:
    active = ChannelAuthorizationSnapshot(
        auth_session_id=uuid4(),
        provider_id="weixin_ilink",
        status=ChannelAuthorizationStatus.PENDING,
        qr_code_content="https://example.invalid/opaque-qr-content",
        expires_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    assert active.verification_required is False

    with pytest.raises(ValidationError):
        ChannelAuthorizationSnapshot(
            auth_session_id=uuid4(),
            provider_id="weixin_ilink",
            status=ChannelAuthorizationStatus.VERIFICATION_REQUIRED,
            qr_code_content="https://example.invalid/opaque-qr-content",
            verification_required=False,
            expires_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )

    with pytest.raises(ValidationError):
        ChannelAuthorizationSnapshot(
            auth_session_id=uuid4(),
            provider_id="weixin_ilink",
            status=ChannelAuthorizationStatus.CONFIRMED,
            expires_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )


def test_channel_delivery_acknowledgement_validates_terminal_outcome() -> None:
    delivered = ChannelDeliveryAcknowledgement(
        delivery_id=uuid4(),
        channel_turn_id=uuid4(),
        lease_id=uuid4(),
        status=ChannelDeliveryStatus.DELIVERED,
        provider_message_id="provider-reply-001",
        acknowledged_at=NOW,
    )
    assert delivered.status == "delivered"

    with pytest.raises(ValidationError):
        ChannelDeliveryAcknowledgement(
            delivery_id=uuid4(),
            channel_turn_id=uuid4(),
            lease_id=uuid4(),
            status=ChannelDeliveryStatus.FAILED,
            acknowledged_at=NOW,
        )


def test_channel_connection_snapshot_revision_starts_at_one() -> None:
    configuration = ChannelConnectionConfiguration(
        connection_id=uuid4(),
        provider_id="example_direct",
        name="External direct channel",
        character_id="nene",
        principal_scope="owner/local",
        account_key="provider-account-001",
        allowed_sender_keys=["provider-sender-001"],
    )
    with pytest.raises(ValidationError):
        ChannelConnectionSnapshot(
            configuration=configuration,
            revision=0,
            capabilities=ChannelProviderCapabilities(),
            created_at=NOW,
            updated_at=NOW,
        )
