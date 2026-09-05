# pyright: reportPrivateUsage=false
"""Verify real Character planning reaches the image adapter through durable scheduling."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelImageDeliveryPartPayload,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
)
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinCredentials,
    WeixinInboundText,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
from chatwaifu_runtime.external_channels.management import ChannelManagementService
from chatwaifu_runtime.external_channels.models import DeliveryTransitionResult
from test_channel_management import _configuration, _credentials, _FakeWeixin


class _ImageTransport(_FakeWeixin):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[str, str, str, bytes, str]] = []

    async def send_image(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        context_token: str,
        client_id: str,
        image_bytes: bytes,
        mime_type: str,
    ) -> str:
        del credentials
        self.images.append((recipient_user_id, context_token, client_id, image_bytes, mime_type))
        return client_id


async def test_character_plan_to_catalog_to_durable_image_send(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _ImageTransport()
    management = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
    )
    container.channel_management = management
    image_acknowledged = asyncio.Event()
    results: list[DeliveryTransitionResult] = []
    original_ack = container.external_channel_repository.acknowledge_delivery_part

    async def observe_ack(
        acknowledgement: ChannelDeliveryPartAcknowledgement,
        *,
        updated_at: datetime,
    ) -> DeliveryTransitionResult:
        result = await original_ack(acknowledgement, updated_at=updated_at)
        if result.part is not None and isinstance(
            result.part.payload, ChannelImageDeliveryPartPayload
        ):
            results.append(result)
            image_acknowledged.set()
        return result

    monkeypatch.setattr(
        container.external_channel_repository, "acknowledge_delivery_part", observe_ack
    )
    await container.start()
    try:
        connection_id = uuid4()
        config = _configuration(connection_id).model_copy(
            update={
                "presentation_policy": ChannelPresentationPolicy(
                    profile=ChannelPresentationProfile.INSTANT_MESSAGE,
                    stickers_enabled=True,
                    cadence_enabled=False,
                ),
            }
        )
        created = await container.external_channels.create_connection(config, access_token="g" * 43)
        await store.set(f"weixin_ilink:{connection_id}", _credentials("g" * 43).to_json())
        await management.connection_configuration_changed(created.snapshot)
        await transport.updates.put(
            WeixinUpdates(
                cursor="sticker-cursor",
                messages=(
                    WeixinInboundText(
                        external_message_id="sticker-message",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="喜欢你，摸摸头",
                        context_token="sticker-context",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )
        await asyncio.wait_for(image_acknowledged.wait(), timeout=10)
        assert len(results) == 1
        result = results[0]
        assert result.plan.status is ChannelDeliveryStatus.DELIVERED
        assert all(p.status is ChannelDeliveryPartStatus.DELIVERED for p in result.plan.parts)
        assert len(transport.images) == 1
        assert transport.sent_messages
        image = transport.images[0]
        assert image[:2] == ("owner-1", "sticker-context")
        part = result.part
        assert part is not None and isinstance(part.payload, ChannelImageDeliveryPartPayload)
        assert part.payload.sticker_id == "kitten_shy"
        assert hashlib.sha256(image[3]).hexdigest() == part.payload.sha256
        assert image[2] == part.provider_client_id
        assert image[4] == "image/png"
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "sticker-message"
        )
        assert turn is not None
        original_plan = await container.conversation_repository.generation_response_plan(
            turn.generation_id
        )
        assert original_plan is not None and original_plan.expression == "shy"
        # A later event with the same generation but wrong turn must not replace its plan.
        await container.event_store.append(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "session_id": turn.session_id,
                    "turn_id": uuid4(),
                    "generation_id": turn.generation_id,
                    "event_type": "character.response_planned",
                    "source": "test",
                    "occurred_at": datetime.now(UTC),
                    "privacy": "private",
                    "payload": {
                        "plan": original_plan.model_copy(
                            update={"expression": "happy"}
                        ).model_dump()
                    },
                }
            )
        )
        assert (
            await container.conversation_repository.generation_response_plan(turn.generation_id)
            == original_plan
        )
        assert await container.conversation_repository.generation_response_plan(uuid4()) is None
    finally:
        await container.stop()


async def test_stop_cancels_old_image_without_decorating_new_answer(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _ImageTransport()
    management = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
    )
    container.channel_management = management
    text_acks: asyncio.Queue[DeliveryTransitionResult] = asyncio.Queue()
    original_ack = container.external_channel_repository.acknowledge_delivery_part

    async def observe_ack(
        acknowledgement: ChannelDeliveryPartAcknowledgement,
        *,
        updated_at: datetime,
    ) -> DeliveryTransitionResult:
        result = await original_ack(acknowledgement, updated_at=updated_at)
        if result.part is not None and result.part.ordinal == 0:
            text_acks.put_nowait(result)
        return result

    monkeypatch.setattr(
        container.external_channel_repository, "acknowledge_delivery_part", observe_ack
    )
    await container.start()
    try:
        # Reproduce carried-over positive affect through the real Character service.
        warmup = await container.sessions.create_session("default")
        await container.character_kernel.observe_user_turn(
            session_id=warmup.session_id,
            turn_id=uuid4(),
            generation_id=uuid4(),
            character_id="default",
            text="今天很开心",
        )
        connection_id = uuid4()
        config = _configuration(connection_id).model_copy(
            update={
                "presentation_policy": ChannelPresentationPolicy(
                    profile=ChannelPresentationProfile.INSTANT_MESSAGE,
                    stickers_enabled=True,
                    min_delay_ms=8000,
                    max_delay_ms=8000,
                    total_cadence_delay_ceiling_ms=16000,
                ),
            }
        )
        created = await container.external_channels.create_connection(config, access_token="g" * 43)
        await store.set(f"weixin_ilink:{connection_id}", _credentials("g" * 43).to_json())
        await management.connection_configuration_changed(created.snapshot)
        for message_id, text in [("affection", "喜欢你，摸摸头"), ("stop", "停一下")]:
            await transport.updates.put(
                WeixinUpdates(
                    cursor=message_id,
                    messages=(
                        WeixinInboundText(
                            external_message_id=message_id,
                            sender_user_id="owner-1",
                            recipient_bot_id="bot-1",
                            text=text,
                            context_token=message_id,
                            received_at=datetime.now(UTC),
                        ),
                    ),
                )
            )
            result = await asyncio.wait_for(text_acks.get(), timeout=5)
            if message_id == "affection":
                assert len(result.plan.parts) == 2
                assert result.plan.parts[1].status is ChannelDeliveryPartStatus.PENDING
            else:
                assert len(result.plan.parts) == 1
                assert result.plan.status is ChannelDeliveryStatus.DELIVERED
        old_turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "affection"
        )
        assert old_turn is not None and old_turn.delivery_id is not None
        old_plan = await container.external_channel_repository.get_delivery_plan(
            old_turn.delivery_id
        )
        assert old_plan is not None
        assert old_plan.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
        assert old_plan.parts[1].status is ChannelDeliveryPartStatus.CANCELLED
        stop_turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "stop"
        )
        assert stop_turn is not None
        stop_plan = await container.conversation_repository.generation_response_plan(
            stop_turn.generation_id
        )
        assert stop_plan is not None
        assert stop_plan.intent == "answer" and stop_plan.expression == "happy"
        assert not transport.images
    finally:
        await container.stop()
