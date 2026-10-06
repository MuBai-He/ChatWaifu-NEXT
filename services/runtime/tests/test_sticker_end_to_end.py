# pyright: reportPrivateUsage=false
"""Verify real Character planning reaches the image adapter through durable scheduling."""

from __future__ import annotations

import asyncio
import hashlib
import io
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

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
    WeixinInboundImage,
    WeixinInboundText,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
from chatwaifu_runtime.external_channels.management import ChannelManagementService
from chatwaifu_runtime.external_channels.models import DeliveryTransitionResult
from chatwaifu_runtime.providers.contracts import LlmInputImage
from chatwaifu_runtime.sticker_library.classifier import StickerClassification
from PIL import Image
from provider_test_support import use_recording_provider
from test_channel_management import _configuration, _credentials, _FakeWeixin
from test_inbound_image_lifecycle import VisionRecorder


class _ImageTransport(_FakeWeixin):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[tuple[str, str, str, bytes, str]] = []
        self.inbound_images: dict[str, bytes] = {}

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

    async def download_image(
        self,
        image: WeixinInboundImage,
    ) -> tuple[bytes, str]:
        if image.encrypt_query_param and image.encrypt_query_param in self.inbound_images:
            return self.inbound_images[image.encrypt_query_param], "image/png"
        return await super().download_image(image)


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


@pytest.mark.parametrize("learned", [False, True])
async def test_stop_cancels_old_image_without_decorating_new_answer(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    learned: bool,
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
        if learned:
            from test_learned_sticker_delivery import _seed_learned_sticker

            await _seed_learned_sticker(
                container.database,
                container,
                principal_scope="local",
                character_id="default",
                expression="happy",
                label="捏脸互动",
                description="女孩捏另一位女孩的脸颊",
            )
        # Reproduce carried-over positive affect through the real Character service.
        warmup = await container.sessions.create_session("default")
        await container.character_kernel.observe_user_turn(
            session_id=warmup.session_id,
            turn_id=uuid4(),
            generation_id=uuid4(),
            character_id="default",
            text="今天很开心",
        )
        if learned:
            # Two neutral user turns decay affect; establish positive affect through
            # the real kernel so this regression still proves answer+happy is suppressed.
            for _ in range(2):
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
        for message_id, text in [
            ("affection", "捏捏我" if learned else "喜欢你，摸摸头"),
            ("stop", "停一下"),
        ]:
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


@pytest.mark.asyncio
async def test_stickers_disabled_permits_inbound_learning_without_sending(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direction 1: learning_enabled=True, channel stickers_enabled=False.

    Proves that with channel stickers_enabled disabled, inbound images are still
    ingested and saved into SQLite learned stickers, while outgoing delivery produces
    only text parts and sends no optional sticker image over transport.
    """
    container = RuntimeContainer(runtime_settings)
    use_recording_provider(monkeypatch, container.model_configurations, VisionRecorder())
    store = InMemoryChannelCredentialStore()
    transport = _ImageTransport()
    management = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
        sticker_catalog=container.sticker_catalog,
        sticker_library=container.sticker_library,
        event_hub=container.event_hub,
        event_publisher=container.event_publisher,
    )
    container.channel_management = management

    text_acknowledged = asyncio.Event()
    original_ack = container.external_channel_repository.acknowledge_delivery_part

    async def observe_ack(
        acknowledgement: ChannelDeliveryPartAcknowledgement,
        *,
        updated_at: datetime,
    ) -> DeliveryTransitionResult:
        result = await original_ack(acknowledgement, updated_at=updated_at)
        if result.part is not None:
            if (
                result.part.ordinal == 0
                and result.part.status is ChannelDeliveryPartStatus.DELIVERED
            ):
                text_acknowledged.set()
        return result

    monkeypatch.setattr(
        container.external_channel_repository, "acknowledge_delivery_part", observe_ack
    )

    async def classify(
        image: LlmInputImage, *, generation_id: UUID
    ) -> StickerClassification | None:
        return StickerClassification(
            suitable=True,
            confidence=0.99,
            label="害羞小猫",
            description="害羞的小猫表情",
            expression="shy",
        )

    monkeypatch.setattr(container.sticker_library._classifier, "classify", classify)

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer, format="PNG")
    img_bytes = buffer.getvalue()
    transport.inbound_images["param-learn-on-send-off"] = img_bytes

    await container.start()
    try:
        principal_scope = "local"
        character_id = "default"
        # Enable sticker learning on SQLite repository
        await container.sticker_repository.update_settings(
            principal_scope, character_id, learning_enabled=True, expected_revision=0
        )

        connection_id = uuid4()
        config = _configuration(connection_id).model_copy(
            update={
                "principal_scope": principal_scope,
                "character_id": character_id,
                "presentation_policy": ChannelPresentationPolicy(
                    profile=ChannelPresentationProfile.INSTANT_MESSAGE,
                    stickers_enabled=False,  # Sending disabled
                    cadence_enabled=False,
                ),
            }
        )
        created = await container.external_channels.create_connection(config, access_token="g" * 43)
        await store.set(f"weixin_ilink:{connection_id}", _credentials("g" * 43).to_json())
        await management.connection_configuration_changed(created.snapshot)

        await transport.updates.put(
            WeixinUpdates(
                cursor="cursor-learn-on-send-off",
                messages=(
                    WeixinInboundText(
                        external_message_id="learn-on-send-off-msg",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="喜欢你，摸摸头",
                        context_token="ctx-learn-on-send-off",
                        received_at=datetime.now(UTC),
                        images=(WeixinInboundImage(encrypt_query_param="param-learn-on-send-off"),),
                    ),
                ),
            )
        )

        await asyncio.wait_for(text_acknowledged.wait(), timeout=10.0)

        # Invariant 1: Inbound image was learned and persisted in SQLite snapshot
        tasks = [task for _, task in container.sticker_library._tasks.values()]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
        snapshot = await container.sticker_repository.snapshot(principal_scope, character_id)
        assert len(snapshot.items) == 1
        assert snapshot.items[0].source_connection_id == connection_id
        assert (
            await container.sticker_repository.get_image(
                principal_scope, character_id, snapshot.items[0].sticker_id
            )
            is not None
        )

        # Invariant 2: Outgoing delivery plan contains only text, without optional sticker
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "learn-on-send-off-msg"
        )
        assert turn is not None and turn.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert plan is not None
        assert plan.status is ChannelDeliveryStatus.DELIVERED
        assert len(plan.parts) == 1
        assert plan.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
        assert not any(isinstance(p.payload, ChannelImageDeliveryPartPayload) for p in plan.parts)

        # Invariant 3: Outgoing transport sent only text; images list is empty
        assert len(transport.sent_messages) >= 1
        assert len(transport.images) == 0
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_learning_disabled_permits_sending_without_inbound_learning(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direction 2: learning_enabled=False, channel stickers_enabled=True.

    Proves that with learning_enabled disabled, a new inbound image is not learned,
    yet an existing saved sticker is still matched and sent over transport when
    response planning selects it.
    """
    container = RuntimeContainer(runtime_settings)
    use_recording_provider(monkeypatch, container.model_configurations, VisionRecorder())
    store = InMemoryChannelCredentialStore()
    transport = _ImageTransport()
    management = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
        sticker_catalog=container.sticker_catalog,
        sticker_library=container.sticker_library,
        event_hub=container.event_hub,
        event_publisher=container.event_publisher,
    )
    container.channel_management = management

    image_acknowledged = asyncio.Event()
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
            image_acknowledged.set()
        return result

    monkeypatch.setattr(
        container.external_channel_repository, "acknowledge_delivery_part", observe_ack
    )

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "black").save(buffer, format="PNG")
    new_inbound_bytes = buffer.getvalue()
    transport.inbound_images["param-learn-off-send-on"] = new_inbound_bytes
    classify = AsyncMock()
    monkeypatch.setattr(container.sticker_library._classifier, "classify", classify)

    await container.start()
    try:
        principal_scope = "local"
        character_id = "default"
        # Seed an existing learned sticker matching 'shy' expression
        from test_learned_sticker_delivery import _seed_learned_sticker

        saved_id, expected_sha, _, _ = await _seed_learned_sticker(
            container.database,
            container,
            principal_scope=principal_scope,
            character_id=character_id,
            expression="shy",
            label="预存小猫",
            description="已保存的害羞小猫",
        )

        initial_snapshot = await container.sticker_repository.snapshot(
            principal_scope, character_id
        )
        assert len(initial_snapshot.items) == 1
        assert initial_snapshot.items[0].sticker_id == saved_id

        # Explicitly disable learning on the repository
        await container.sticker_repository.update_settings(
            principal_scope,
            character_id,
            learning_enabled=False,
            expected_revision=initial_snapshot.settings.revision,
        )

        connection_id = uuid4()
        config = _configuration(connection_id).model_copy(
            update={
                "principal_scope": principal_scope,
                "character_id": character_id,
                "presentation_policy": ChannelPresentationPolicy(
                    profile=ChannelPresentationProfile.INSTANT_MESSAGE,
                    stickers_enabled=True,  # Sending enabled
                    cadence_enabled=False,
                ),
            }
        )
        created = await container.external_channels.create_connection(config, access_token="g" * 43)
        await store.set(f"weixin_ilink:{connection_id}", _credentials("g" * 43).to_json())
        await management.connection_configuration_changed(created.snapshot)

        # Inbound message carries BOTH text ("喜欢你，摸摸头" -> shy) AND a new inbound image
        await transport.updates.put(
            WeixinUpdates(
                cursor="cursor-learn-off-send-on",
                messages=(
                    WeixinInboundText(
                        external_message_id="learn-off-send-on-msg",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="喜欢你，摸摸头",
                        context_token="ctx-learn-off-send-on",
                        received_at=datetime.now(UTC),
                        images=(WeixinInboundImage(encrypt_query_param="param-learn-off-send-on"),),
                    ),
                ),
            )
        )

        await asyncio.wait_for(image_acknowledged.wait(), timeout=10.0)

        # Invariant 1: The new inbound image was NOT learned; library items unchanged
        tasks = [task for _, task in container.sticker_library._tasks.values()]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
        after_snapshot = await container.sticker_repository.snapshot(principal_scope, character_id)
        assert len(after_snapshot.items) == 1
        assert after_snapshot.items[0].sticker_id == saved_id
        assert after_snapshot.items[0].sha256 == expected_sha
        classify.assert_not_awaited()

        # Invariant 2: Delivery plan contains both text and the already saved sticker
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "learn-off-send-on-msg"
        )
        assert turn is not None and turn.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert plan is not None
        assert plan.status is ChannelDeliveryStatus.DELIVERED
        assert len(plan.parts) == 2
        assert plan.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
        assert plan.parts[1].status is ChannelDeliveryPartStatus.DELIVERED
        assert isinstance(plan.parts[1].payload, ChannelImageDeliveryPartPayload)
        assert plan.parts[1].payload.sticker_id == saved_id
        assert plan.parts[1].payload.sha256 == expected_sha

        # Invariant 3: Transport delivered the image corresponding to the saved sticker
        assert len(transport.images) == 1
        image_call = transport.images[0]
        assert image_call[0] == "owner-1"
        assert hashlib.sha256(image_call[3]).hexdigest() == expected_sha
    finally:
        await container.stop()
