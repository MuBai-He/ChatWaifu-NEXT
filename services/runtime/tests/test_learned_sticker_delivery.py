# pyright: reportPrivateUsage=false
"""Verify durable delivery of learned stickers with recovery and delete races."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import UTC, datetime
from typing import Any
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
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.sticker_library.models import StickerExpression, StickerSaveCandidate
from test_channel_management import _configuration, _credentials, _FakeWeixin

# 1x1 transparent PNG bytes used across unit and integration tests.
_LEARNED_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class _RecordedImageTransport(_FakeWeixin):
    """Transport recording sent images and texts for regression assertions."""

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


async def _seed_learned_sticker(
    database: Database,
    container: RuntimeContainer,
    *,
    principal_scope: str,
    character_id: str,
    expression: StickerExpression,
    label: str = "已学习小猫",
    description: str = "用于测试的学习表情",
    image_bytes: bytes = _LEARNED_PNG_BYTES,
) -> tuple[str, str, UUID, UUID]:
    """Seed a valid, completed turn and persist an accepted learned sticker in SQLite.

    Ensures the source connection and turn chain are physically valid and completed
    so SqliteStickerLibraryRepository.save succeeds under strict source validation rules.
    """
    source_conn_id = uuid4()
    source_gen_id = uuid4()
    source_session_id = uuid4()
    source_turn_id = uuid4()
    source_binding_id = uuid4()
    source_channel_turn_id = uuid4()
    now_iso = datetime.now(UTC).isoformat()

    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO channel_connections (
                connection_id, provider_id, name, character_id, principal_scope,
                enabled, access_token_hash, created_at, updated_at, deleted_at
            ) VALUES (?, 'weixin_ilink', 'seed-conn', ?, ?, 1, 'seed-hash', ?, ?, NULL)
            """,
            (str(source_conn_id), character_id, principal_scope, now_iso, now_iso),
        )
        await conn.execute(
            """
            INSERT INTO sessions (
                session_id, character_id, state, conversation_state, created_at, updated_at
            ) VALUES (?, ?, 'active', 'ready', ?, ?)
            """,
            (str(source_session_id), character_id, now_iso, now_iso),
        )
        await conn.execute(
            """
            INSERT INTO turns (
                turn_id, session_id, role, created_at
            ) VALUES (?, ?, 'user', ?)
            """,
            (str(source_turn_id), str(source_session_id), now_iso),
        )
        await conn.execute(
            """
            INSERT INTO channel_bindings (
                binding_id, connection_id, conversation_key, sender_key, session_id,
                created_at, updated_at
            ) VALUES (?, ?, 'seed-conv', 'seed-sender', ?, ?, ?)
            """,
            (str(source_binding_id), str(source_conn_id), str(source_session_id), now_iso, now_iso),
        )
        await conn.execute(
            """
            INSERT INTO channel_turns (
                channel_turn_id, connection_id, binding_id, external_message_id,
                content_sha256, conversation_key, sender_key, principal_scope,
                session_id, turn_id, generation_id, status, accepted_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'seed-ext-msg', 'seed-hash', 'seed-conv', 'seed-sender',
                     ?, ?, ?, ?, 'completed', ?, ?, ?)
            """,
            (
                str(source_channel_turn_id),
                str(source_conn_id),
                str(source_binding_id),
                principal_scope,
                str(source_session_id),
                str(source_turn_id),
                str(source_gen_id),
                now_iso,
                now_iso,
                now_iso,
            ),
        )

    # Enable learning on the repository settings
    settings = await container.sticker_repository.get_settings(principal_scope, character_id)
    if not settings.learning_enabled:
        settings = await container.sticker_repository.update_settings(
            principal_scope,
            character_id,
            learning_enabled=True,
            expected_revision=settings.revision,
        )

    # Save accepted learned sticker asset
    saved = await container.sticker_repository.save(
        principal_scope,
        character_id,
        StickerSaveCandidate(
            data=image_bytes,
            label=label,
            description=description,
            expression=expression,
            source_connection_id=source_conn_id,
            generation_id=source_gen_id,
        ),
        expected_revision=settings.revision,
    )
    assert saved is not None, "Failed to seed deterministic learned sticker asset"
    return saved.sticker_id, saved.sha256, source_conn_id, source_gen_id


@pytest.mark.asyncio
async def test_learned_sticker_delivery_and_plan_recovery(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 1: Learned sticker selected for real Character ResponsePlan reaches Weixin.

    Validates that:
    - Real Character ResponsePlan (triggered by affectionate user input '喜欢你，摸摸头' -> shy)
      selects the learned sticker asset when matched by expression.
    - Delivery plan is durably persisted with text (Part 0) and learned image (Part 1).
    - Actual send_image transport receives exact image bytes and frozen SHA256 hash.
    - Delivery plan reaches DELIVERED status.
    - Persisted asset and response plan remain recoverable after execution.
    """
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _RecordedImageTransport()
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
        principal_scope = "local"
        character_id = "default"
        # Seed an accepted learned sticker matching 'shy' expression
        learned_id, expected_sha, _, _ = await _seed_learned_sticker(
            container.database,
            container,
            principal_scope=principal_scope,
            character_id=character_id,
            expression="shy",
            label="害羞小猫",
            description="害羞时使用的学习表情包",
        )

        connection_id = uuid4()
        config = _configuration(connection_id).model_copy(
            update={
                "principal_scope": principal_scope,
                "character_id": character_id,
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

        # Inbound message triggers real Character kernel response planning ('喜欢你，摸摸头' -> shy)
        await transport.updates.put(
            WeixinUpdates(
                cursor="learned-cursor-1",
                messages=(
                    WeixinInboundText(
                        external_message_id="learned-msg-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="喜欢你，摸摸头",
                        context_token="learned-ctx-1",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait for image part delivery acknowledgment
        await asyncio.wait_for(image_acknowledged.wait(), timeout=10.0)

        # Invariant checks: delivery transition result
        assert len(results) == 1
        result = results[0]
        assert result.plan.status is ChannelDeliveryStatus.DELIVERED
        assert all(p.status is ChannelDeliveryPartStatus.DELIVERED for p in result.plan.parts)
        assert len(result.plan.parts) == 2

        # Invariant checks: transport calls
        assert len(transport.images) == 1
        assert len(transport.sent_messages) >= 1
        image_call = transport.images[0]
        assert image_call[0] == "owner-1"
        assert image_call[1] == "learned-ctx-1"
        assert image_call[4] == "image/png"

        # Verify exact image bytes and frozen SHA256 match
        part = result.part
        assert part is not None and isinstance(part.payload, ChannelImageDeliveryPartPayload)
        assert part.payload.sticker_id == learned_id
        assert part.payload.sticker_id.startswith("learned_")
        assert part.payload.sha256 == expected_sha
        assert hashlib.sha256(image_call[3]).hexdigest() == expected_sha
        assert image_call[3] == _LEARNED_PNG_BYTES
        assert image_call[2] == part.provider_client_id

        # Invariant checks: durable recovery from repository
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "learned-msg-1"
        )
        assert turn is not None and turn.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert plan is not None
        assert plan.status is ChannelDeliveryStatus.DELIVERED
        assert plan.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
        assert plan.parts[1].status is ChannelDeliveryPartStatus.DELIVERED
        assert isinstance(plan.parts[1].payload, ChannelImageDeliveryPartPayload)
        assert plan.parts[1].payload.sticker_id == learned_id
        assert plan.parts[1].payload.sha256 == expected_sha

        # Recovery of response plan
        recovered_plan = await container.conversation_repository.generation_response_plan(
            turn.generation_id
        )
        assert recovered_plan is not None
        assert recovered_plan.expression == "shy"
        assert recovered_plan.intent == "reassure"

        # Recovery of persisted image bytes from sticker library
        recovered_bytes = await container.sticker_repository.get_image(
            principal_scope, character_id, learned_id, expected_sha256=expected_sha
        )
        assert recovered_bytes == _LEARNED_PNG_BYTES
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_delete_learned_asset_while_queued_fails_image_terminal_without_resend(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 2: Delete learned asset while optional image part is queued.

    Validates that:
    - Text part (Part 0) is delivered first.
    - While optional image part (Part 1) is queued, the learned asset is deleted from SQLite.
    - When scheduler attempts to deliver the image part, image loading fails fatal.
    - The image part reaches terminal FAILED status without retry or crash.
    - No image bytes are sent over the transport.
    - Text part is not duplicated or resent.
    - Parent delivery plan resolves to DELIVERED (since only an optional part failed,
      per multipart delivery specification).
    """
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _RecordedImageTransport()
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

    text_delivered = asyncio.Event()
    image_failed = asyncio.Event()
    seen_part_acks: list[DeliveryTransitionResult] = []
    original_ack = container.external_channel_repository.acknowledge_delivery_part

    async def observe_ack(
        acknowledgement: ChannelDeliveryPartAcknowledgement,
        *,
        updated_at: datetime,
    ) -> DeliveryTransitionResult:
        result = await original_ack(acknowledgement, updated_at=updated_at)
        if result.part is not None:
            seen_part_acks.append(result)
            if (
                result.part.ordinal == 0
                and result.part.status is ChannelDeliveryPartStatus.DELIVERED
            ):
                text_delivered.set()
            elif (
                result.part.ordinal == 1 and result.part.status is ChannelDeliveryPartStatus.FAILED
            ):
                image_failed.set()
        return result

    monkeypatch.setattr(
        container.external_channel_repository, "acknowledge_delivery_part", observe_ack
    )

    # Intercept part 1 execution to delete the asset while part 1 is claimed/in-flight
    original_execute = management.execute_weixin_delivery_part
    deleted_asset_event = asyncio.Event()

    async def intercept_execute(
        connection_id: UUID,
        plan: Any,
        part: Any,
    ) -> Any:
        if part.ordinal == 1 and isinstance(part.payload, ChannelImageDeliveryPartPayload):
            # Delete the learned sticker from SQLite right before management loads the image bytes
            del_res = await container.sticker_repository.delete(
                "local", "default", part.payload.sticker_id
            )
            assert del_res.deleted, "Expected asset to be physically deleted"
            deleted_asset_event.set()
        return await original_execute(connection_id, plan, part)

    monkeypatch.setattr(management, "execute_weixin_delivery_part", intercept_execute)

    await container.start()
    try:
        principal_scope = "local"
        character_id = "default"
        learned_id, _, _, _ = await _seed_learned_sticker(
            container.database,
            container,
            principal_scope=principal_scope,
            character_id=character_id,
            expression="shy",
            label="待删除小猫",
            description="将在队列中删除的表情",
        )

        connection_id = uuid4()
        config = _configuration(connection_id).model_copy(
            update={
                "principal_scope": principal_scope,
                "character_id": character_id,
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

        # Inbound message that produces a 2-part delivery (text + learned sticker)
        await transport.updates.put(
            WeixinUpdates(
                cursor="learned-cursor-2",
                messages=(
                    WeixinInboundText(
                        external_message_id="learned-msg-2",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="喜欢你，摸摸头",
                        context_token="learned-ctx-2",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Wait for text part to be delivered
        await asyncio.wait_for(text_delivered.wait(), timeout=10.0)
        assert len(transport.sent_messages) == 1, "Text must be sent exactly once"

        # Wait for asset deletion to happen in flight
        await asyncio.wait_for(deleted_asset_event.wait(), timeout=10.0)

        # Wait for the optional image part to transition to FAILED
        await asyncio.wait_for(image_failed.wait(), timeout=10.0)

        # Invariant checks:
        # 1. Transport never received any image send calls
        assert len(transport.images) == 0, "Deleted sticker must never be sent to transport"

        # 2. Text was sent exactly once (no duplicate delivery)
        assert len(transport.sent_messages) == 1, "Text must not be resent or duplicated"

        # 3. Durable state verification from repository
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "learned-msg-2"
        )
        assert turn is not None and turn.delivery_id is not None
        durable_plan = await container.external_channel_repository.get_delivery_plan(
            turn.delivery_id
        )
        assert durable_plan is not None
        assert len(durable_plan.parts) == 2

        part_0 = durable_plan.parts[0]
        assert part_0.status is ChannelDeliveryPartStatus.DELIVERED
        assert part_0.ordinal == 0

        part_1 = durable_plan.parts[1]
        assert part_1.status is ChannelDeliveryPartStatus.FAILED
        assert part_1.ordinal == 1
        assert not part_1.required
        assert part_1.last_error is not None
        assert part_1.last_error.code == "sticker_load_failed"

        # 4. Optional part failure leaves parent delivery plan DELIVERED
        assert durable_plan.status is ChannelDeliveryStatus.DELIVERED

        # 5. Asset is truly absent in repository
        assert (
            await container.sticker_repository.get_image(principal_scope, character_id, learned_id)
            is None
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_pending_learned_image_survives_runtime_restart_without_resending_text(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    first_transport = _RecordedImageTransport()
    first.channel_management = ChannelManagementService(
        first.external_channels,
        first.external_channel_repository,
        store,
        first_transport,
        sticker_library=first.sticker_library,
    )
    text_committed = asyncio.Event()
    original_ack = first.external_channel_repository.acknowledge_delivery_part

    async def hold_after_text_commit(
        acknowledgement: ChannelDeliveryPartAcknowledgement, *, updated_at: datetime
    ) -> DeliveryTransitionResult:
        result = await original_ack(acknowledgement, updated_at=updated_at)
        if result.part is not None and result.part.ordinal == 0:
            text_committed.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(
        first.external_channel_repository, "acknowledge_delivery_part", hold_after_text_commit
    )
    await first.start()
    connection_id = uuid4()
    try:
        learned_id, expected_hash, _, _ = await _seed_learned_sticker(
            first.database, first, principal_scope="local", character_id="default", expression="shy"
        )
        config = _configuration(connection_id).model_copy(
            update={
                "presentation_policy": ChannelPresentationPolicy(
                    profile=ChannelPresentationProfile.INSTANT_MESSAGE,
                    stickers_enabled=True,
                    cadence_enabled=False,
                )
            }
        )
        created = await first.external_channels.create_connection(config, access_token="g" * 43)
        await store.set(f"weixin_ilink:{connection_id}", _credentials("g" * 43).to_json())
        await first.channel_management.connection_configuration_changed(created.snapshot)
        await first_transport.updates.put(
            WeixinUpdates(
                cursor="restart-learning",
                messages=(
                    WeixinInboundText(
                        external_message_id="restart-learned",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="喜欢你，摸摸头",
                        context_token="restart-context",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )
        await asyncio.wait_for(text_committed.wait(), 5)
        assert len(first_transport.sent_messages) == 1 and first_transport.images == []
        turn = await first.external_channel_repository.find_turn_by_external_message(
            connection_id, "restart-learned"
        )
        assert turn is not None and turn.delivery_id is not None
        pending = await first.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert pending is not None
        frozen = pending.parts[1]
        assert isinstance(frozen.payload, ChannelImageDeliveryPartPayload)
        assert frozen.payload.sticker_id == learned_id
        assert frozen.status is ChannelDeliveryPartStatus.PENDING
    finally:
        await first.stop()

    restarted = RuntimeContainer(runtime_settings)
    second_transport = _RecordedImageTransport()
    restarted.channel_management = ChannelManagementService(
        restarted.external_channels,
        restarted.external_channel_repository,
        store,
        second_transport,
        sticker_library=restarted.sticker_library,
    )
    delivered = asyncio.Event()
    restarted_ack = restarted.external_channel_repository.acknowledge_delivery_part

    async def observe_recovered_ack(
        acknowledgement: ChannelDeliveryPartAcknowledgement, *, updated_at: datetime
    ) -> DeliveryTransitionResult:
        result = await restarted_ack(acknowledgement, updated_at=updated_at)
        if result.part is not None and result.part.part_id == frozen.part_id:
            assert result.part.status is ChannelDeliveryPartStatus.DELIVERED
            delivered.set()
        return result

    monkeypatch.setattr(
        restarted.external_channel_repository, "acknowledge_delivery_part", observe_recovered_ack
    )
    await restarted.start()
    try:
        await asyncio.wait_for(delivered.wait(), 5)
        assert second_transport.sent_messages == []
        assert len(second_transport.images) == 1
        image = second_transport.images[0]
        assert image[2] == frozen.provider_client_id
        assert hashlib.sha256(image[3]).hexdigest() == expected_hash
    finally:
        await restarted.stop()
