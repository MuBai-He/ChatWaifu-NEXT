# pyright: reportPrivateUsage=false
"""Focused integration tests for gateway inbound image handling (Phase 17.3A)."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartStatus,
    ChannelInboundTextMessage,
    ChannelMessageKind,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationTurnOptions
from chatwaifu_runtime.external_channels.models import (
    ChannelBindingRecord,
    ChannelConnectionRecord,
    ChannelInboundImageInput,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.service import (
    _IMAGE_FAILURE_RECOVERY_TEXT,
    ChannelConflictError,
    ChannelPolicyError,
    ExternalChannelService,
    _message_digest,
)
from chatwaifu_runtime.providers.contracts import (
    LlmInputImage,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)


def _valid_fingerprint(seed: str = "test") -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def test_channel_inbound_image_input_validation() -> None:
    valid_fp = _valid_fingerprint("valid")

    async def dummy_load() -> LlmInputImage:
        return LlmInputImage(data=b"123", mime_type="image/png")

    inp = ChannelInboundImageInput(source_fingerprint=valid_fp, load=dummy_load)
    assert inp.source_fingerprint == valid_fp
    assert "load" not in repr(inp)

    with pytest.raises(ValueError, match="64-character lowercase hex"):
        ChannelInboundImageInput(source_fingerprint=valid_fp.upper(), load=dummy_load)

    with pytest.raises(ValueError, match="64-character lowercase hex"):
        ChannelInboundImageInput(source_fingerprint="abc123", load=dummy_load)

    with pytest.raises(ValueError, match="64-character lowercase hex"):
        ChannelInboundImageInput(source_fingerprint="z" * 64, load=dummy_load)


def test_message_digest_domain_separated_preserves_text_digest() -> None:
    now = datetime.now(UTC)
    msg = ChannelInboundTextMessage(
        connection_id=uuid4(),
        account_key="bot1",
        external_message_id="msg-1",
        conversation_key="user1",
        sender_key="user1",
        principal_scope="owner",
        chat_type=ChannelChatType.DIRECT,
        text="hello world",
        received_at=now,
    )

    text_digest = _message_digest(msg, image_fingerprint=None)
    text_digest_default = _message_digest(msg)
    assert text_digest == text_digest_default

    fp1 = _valid_fingerprint("img1")
    fp2 = _valid_fingerprint("img2")
    img_digest1 = _message_digest(msg, image_fingerprint=fp1)
    img_digest2 = _message_digest(msg, image_fingerprint=fp2)

    assert img_digest1 != text_digest
    assert img_digest1 != img_digest2


def test_validate_ingress_requires_image_capability() -> None:
    service = ExternalChannelService(
        repository=MagicMock(),
        conversation_repository=MagicMock(),
        sessions=MagicMock(),
        conversation=MagicMock(),
        characters=MagicMock(),
        event_hub=MagicMock(),
        publisher=MagicMock(),
    )
    conn_config = ChannelConnectionConfiguration(
        connection_id=uuid4(),
        provider_id="weixin_ilink",
        name="Test",
        character_id="default",
        principal_scope="owner",
        account_key="bot1",
        allowed_sender_keys=["user1"],
    )
    conn_record = ChannelConnectionRecord(
        configuration=conn_config,
        status=MagicMock(),
        access_token_hash="hash",
        last_error=None,
        last_seen_at=None,
        revision=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    msg = ChannelInboundTextMessage(
        connection_id=conn_config.connection_id,
        account_key="bot1",
        external_message_id="msg-1",
        conversation_key="user1",
        sender_key="user1",
        principal_scope="owner",
        chat_type=ChannelChatType.DIRECT,
        text="[图片]",
        received_at=datetime.now(UTC),
    )

    service._validate_ingress(conn_record, msg, has_image=True)

    no_img_provider = MagicMock()
    no_img_provider.capabilities.chat_types = [ChannelChatType.DIRECT]
    no_img_provider.capabilities.inbound_message_kinds = [ChannelMessageKind.TEXT]
    service._providers["no_img"] = no_img_provider
    no_img_conn = ChannelConnectionRecord(
        configuration=conn_config.model_copy(update={"provider_id": "no_img"}),
        status=MagicMock(),
        access_token_hash="hash",
        last_error=None,
        last_seen_at=None,
        revision=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(ChannelPolicyError, match="does not allow image messages"):
        service._validate_ingress(no_img_conn, msg, has_image=True)


@pytest.mark.asyncio
async def test_duplicate_returns_before_load_and_conflict_rejected() -> None:
    repo = AsyncMock()
    conv = AsyncMock()
    service = ExternalChannelService(
        repository=repo,
        conversation_repository=AsyncMock(),
        sessions=AsyncMock(),
        conversation=conv,
        characters=MagicMock(),
        event_hub=MagicMock(),
        publisher=MagicMock(),
    )
    conn_id = uuid4()
    conn = ChannelConnectionRecord(
        configuration=ChannelConnectionConfiguration(
            connection_id=conn_id,
            provider_id="weixin_ilink",
            name="Test",
            character_id="default",
            principal_scope="owner",
            account_key="bot1",
            allowed_sender_keys=["user1"],
        ),
        status=MagicMock(),
        access_token_hash=hashlib.sha256(b"tok").hexdigest(),
        last_error=None,
        last_seen_at=None,
        revision=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo.get_connection.return_value = conn

    msg = ChannelInboundTextMessage(
        connection_id=conn_id,
        account_key="bot1",
        external_message_id="msg-dup",
        conversation_key="user1",
        sender_key="user1",
        principal_scope="owner",
        chat_type=ChannelChatType.DIRECT,
        text="[图片]",
        received_at=datetime.now(UTC),
    )
    fp = _valid_fingerprint("v1")
    load_mock = AsyncMock()
    image_input = ChannelInboundImageInput(source_fingerprint=fp, load=load_mock)

    session_id = uuid4()
    binding_id = uuid4()
    existing_turn = ChannelTurnRecord(
        channel_turn_id=uuid4(),
        connection_id=conn_id,
        binding_id=binding_id,
        external_message_id="msg-dup",
        content_sha256=_message_digest(msg, image_fingerprint=fp),
        account_key="bot1",
        conversation_key="user1",
        chat_type=ChannelChatType.DIRECT,
        conversation_label=None,
        sender_key="user1",
        sender_display_name="User 1",
        principal_scope="owner",
        session_id=session_id,
        turn_id=uuid4(),
        generation_id=uuid4(),
        status=ChannelTurnStatus.COMPLETED,
        reply_text="hello",
        error=None,
        delivery_id=None,
        delivery_status=None,
        revision=1,
        accepted_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    repo.find_turn_by_external_message.return_value = existing_turn
    binding = ChannelBindingRecord(
        binding_id=binding_id,
        connection_id=conn_id,
        conversation_key="user1",
        sender_key="user1",
        session_id=session_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo.find_binding.return_value = binding

    receipt = await service.ingest(msg, access_token="tok", image_input=image_input)
    assert receipt.duplicate is True
    assert load_mock.call_count == 0
    assert conv.submit_text.call_count == 0

    different_fp = _valid_fingerprint("v2")
    image_input_conflict = ChannelInboundImageInput(source_fingerprint=different_fp, load=load_mock)
    with pytest.raises(ChannelConflictError, match="different message content"):
        await service.ingest(msg, access_token="tok", image_input=image_input_conflict)
    assert load_mock.call_count == 0


@pytest.mark.asyncio
async def test_unauthorized_sender_never_calls_load() -> None:
    repo = AsyncMock()
    service = ExternalChannelService(
        repository=repo,
        conversation_repository=AsyncMock(),
        sessions=AsyncMock(),
        conversation=AsyncMock(),
        characters=MagicMock(),
        event_hub=MagicMock(),
        publisher=MagicMock(),
    )
    conn_id = uuid4()
    conn = ChannelConnectionRecord(
        configuration=ChannelConnectionConfiguration(
            connection_id=conn_id,
            provider_id="weixin_ilink",
            name="Test",
            character_id="default",
            principal_scope="owner",
            account_key="bot1",
            allowed_sender_keys=["authorized_user"],
        ),
        status=MagicMock(),
        access_token_hash=hashlib.sha256(b"tok").hexdigest(),
        last_error=None,
        last_seen_at=None,
        revision=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repo.get_connection.return_value = conn

    msg = ChannelInboundTextMessage(
        connection_id=conn_id,
        account_key="bot1",
        external_message_id="msg-unauth",
        conversation_key="stranger",
        sender_key="stranger",
        principal_scope="owner",
        chat_type=ChannelChatType.DIRECT,
        text="[图片]",
        received_at=datetime.now(UTC),
    )
    load_mock = AsyncMock()
    image_input = ChannelInboundImageInput(
        source_fingerprint=_valid_fingerprint("stranger"),
        load=load_mock,
    )
    with pytest.raises(ChannelPolicyError, match="not in the owner allowlist"):
        await service.ingest(msg, access_token="tok", image_input=image_input)
    assert load_mock.call_count == 0


@pytest.mark.asyncio
async def test_cancellation_blocked_load(runtime_settings: Settings) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    load_entered = asyncio.Event()
    load_cancelled = asyncio.Event()

    async def blocking_load() -> LlmInputImage:
        load_entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            load_cancelled.set()
            raise
        return LlmInputImage(data=b"dummy", mime_type="image/png")

    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(
            ChannelConnectionConfiguration(
                connection_id=connection_id,
                provider_id="weixin_ilink",
                name="Test",
                character_id="default",
                principal_scope="local",
                account_key="bot1",
                allowed_sender_keys=["user1"],
            )
        )
        msg = ChannelInboundTextMessage(
            connection_id=connection_id,
            account_key="bot1",
            external_message_id="msg-img-block",
            conversation_key="user1",
            sender_key="user1",
            principal_scope="local",
            chat_type=ChannelChatType.DIRECT,
            text="[图片]",
            received_at=datetime.now(UTC),
        )
        fp = _valid_fingerprint("blocked-load")
        image_input = ChannelInboundImageInput(source_fingerprint=fp, load=blocking_load)

        receipt = await container.external_channels.ingest(
            msg, access_token=created.access_token, image_input=image_input
        )
        await asyncio.wait_for(load_entered.wait(), timeout=5)
        cancel_receipt = await container.external_channels.interrupt(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            reason="test_cancel_blocked_load",
        )
        assert cancel_receipt.accepted
        await asyncio.wait_for(load_cancelled.wait(), timeout=5)
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert snapshot.status is ChannelTurnStatus.CANCELLED
        assert snapshot.delivery_id is None
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_image_input_error_recovery_notice_survives_restart_and_next_text(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)

    orig_submit_text = container.conversation.submit_text

    async def mock_submit_text(
        session_id: UUID,
        text: str,
        *,
        options: ConversationTurnOptions | None = None,
        turn_id: UUID | None = None,
        generation_id: UUID | None = None,
    ):
        accepted = await orig_submit_text(
            session_id, text, options=options, turn_id=turn_id, generation_id=generation_id
        )
        # Simulate vision agent failing generation with error_code image_input_error
        await container.conversation._failed(
            accepted,
            StructuredError(
                code="image_input_error",
                message="vision model rejected image input",
                retryable=False,
                component="conversation",
            ),
            error_code="image_input_error",
            recovery_text=_IMAGE_FAILURE_RECOVERY_TEXT,
        )
        return accepted

    monkeypatch.setattr(container.conversation, "submit_text", mock_submit_text)
    await container.start()
    connection_id = uuid4()
    created = await container.external_channels.create_connection(
        ChannelConnectionConfiguration(
            connection_id=connection_id,
            provider_id="weixin_ilink",
            name="Test",
            character_id="default",
            principal_scope="local",
            account_key="bot1",
            allowed_sender_keys=["user1"],
        )
    )
    msg = ChannelInboundTextMessage(
        connection_id=connection_id,
        account_key="bot1",
        external_message_id="img-fail-msg",
        conversation_key="user1",
        sender_key="user1",
        principal_scope="local",
        chat_type=ChannelChatType.DIRECT,
        text="[图片]",
        received_at=datetime.now(UTC),
    )
    fp = _valid_fingerprint("img-fail")
    image_input = ChannelInboundImageInput(
        source_fingerprint=fp,
        load=AsyncMock(return_value=LlmInputImage(data=b"raw", mime_type="image/png")),
    )

    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=created.access_token, image_input=image_input
        )
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert snapshot.status is ChannelTurnStatus.FAILED
        assert snapshot.delivery_id is not None

        plan = await container.external_channel_repository.get_delivery_plan(snapshot.delivery_id)
        assert plan is not None and len(plan.parts) == 1
        assert isinstance(plan.parts[0].payload, ChannelTextDeliveryPartPayload)
        assert plan.parts[0].payload.text == _IMAGE_FAILURE_RECOVERY_TEXT
        stable_id = plan.parts[0].provider_client_id
    finally:
        await container.stop()

    # Restart container
    recovered = RuntimeContainer(runtime_settings)
    await recovered.start()
    try:
        plan = await recovered.external_channel_repository.get_delivery_plan(snapshot.delivery_id)
        assert plan is not None and plan.parts[0].provider_client_id == stable_id
        assert isinstance(plan.parts[0].payload, ChannelTextDeliveryPartPayload)
        assert plan.parts[0].payload.text == _IMAGE_FAILURE_RECOVERY_TEXT

        claim = await recovered.external_channel_repository.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=plan.delivery_id, lease_id=uuid4(), lease_seconds=30
            ),
            claimed_at=datetime.now(UTC),
        )
        assert claim is not None and claim.part is not None and claim.part.lease_id is not None
        ack = ChannelDeliveryPartAcknowledgement(
            delivery_id=plan.delivery_id,
            part_id=claim.part.part_id,
            lease_id=claim.part.lease_id,
            status=ChannelDeliveryPartStatus.DELIVERED,
            acknowledged_at=datetime.now(UTC),
        )
        await recovered.external_channel_repository.acknowledge_delivery_part(
            ack, updated_at=datetime.now(UTC)
        )

        seen_requests: list[LlmRequest] = []

        async def succeed_next(request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
            seen_requests.append(request)
            yield LlmTextDelta("收到图片重发了。")
            yield LlmResponseCompleted("stop")

        monkeypatch.setattr(recovered.model_configurations.chat, "stream", succeed_next)
        next_message = ChannelInboundTextMessage(
            connection_id=connection_id,
            account_key="bot1",
            external_message_id="subsequent-inbound-text",
            conversation_key="user1",
            sender_key="user1",
            principal_scope="local",
            chat_type=ChannelChatType.DIRECT,
            text="我重新发一下文字",
            received_at=datetime.now(UTC),
        )
        next_receipt = await recovered.external_channels.ingest(
            next_message, access_token=created.access_token
        )
        next_turn = await recovered.external_channels.wait_for_turn(
            connection_id,
            next_receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert next_turn.status is ChannelTurnStatus.COMPLETED
        assert len(seen_requests) == 1
        assert any(
            role == "assistant" and text == _IMAGE_FAILURE_RECOVERY_TEXT
            for role, text in seen_requests[0].history
        )
    finally:
        await recovered.stop()
