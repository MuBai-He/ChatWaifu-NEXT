"""Bootstrap, real channel plans and SQLite delivery evidence drive repeat avoidance."""
# pyright: reportPrivateUsage=false

import asyncio
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelImageDeliveryPartPayload,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTurnStatus,
)
from chatwaifu_protocol.sticker_library import StickerUsageHistory
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.models import ChannelTurnRecord
from chatwaifu_runtime.persistence.sqlite_external_channels import SQLiteExternalChannelRepository
from chatwaifu_runtime.providers.contracts import LlmRequest, LlmStreamEvent, LlmTextDelta
from PIL import Image
from test_external_channels import _configuration, _message
from test_learned_sticker_delivery import _seed_learned_sticker
from test_sticker_repeat_avoidance import Usage
from test_sticker_repository import _init_db, _seed_source_chain
from test_sticker_usage import _ack_next


async def seed_candidates(container: RuntimeContainer) -> list[str]:
    for color in ("red", "blue"):
        image = io.BytesIO()
        Image.new("RGB", (2, 2), color).save(image, format="PNG")
        await _seed_learned_sticker(
            container.database,
            container,
            principal_scope="local",
            character_id="default",
            expression="happy",
            label=f"捏脸小猫 {color}",
            description="小猫捏脸",
            image_bytes=image.getvalue(),
        )
    return [
        x.sticker_id
        for x in (await container.sticker_repository.snapshot("local", "default")).items
    ]


async def test_delivery_changes_next_equivalent_selection_across_restart(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        order = await seed_candidates(container)
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
        created = await container.external_channels.create_connection(config)
        for index, expected in enumerate([order[0], order[1], order[0]]):
            receipt = await container.external_channels.ingest(
                _message(connection_id, external_message_id=f"repeat-{index}", text="捏捏我"),
                access_token=created.access_token,
            )
            turn = await container.external_channels.wait_for_turn(
                connection_id,
                receipt.channel_turn_id,
                wait_seconds=5,
            )
            assert turn.status is ChannelTurnStatus.COMPLETED
            assert turn.delivery_id is not None
            parts = await container.external_channel_repository.list_delivery_parts(
                turn.delivery_id
            )
            images = [
                p.payload for p in parts if isinstance(p.payload, ChannelImageDeliveryPartPayload)
            ]
            assert len(images) == 1 and images[0].sticker_id == expected
            # A selected/pending image does not count; only actual adapter acknowledgements do.
            before = await container.sticker_usage.history("local", "default", {})
            assert sum(p.status == "delivered" for p in before.items) == index
            await _ack_next(container.external_channel_repository, turn.delivery_id)
            await _ack_next(container.external_channel_repository, turn.delivery_id)
            history = await container.sticker_usage.history("local", "default", {})
            assert sum(p.status == "delivered" for p in history.items) == index + 1
            # Reconstruct every service and DB connection; no in-memory usage counters survive.
            await container.stop()
            container = RuntimeContainer(runtime_settings)
            await container.start()
            assert await container.sticker_usage.history("local", "default", {}) == history
        await container.sticker_repository.delete("local", "default", order[0])
        history = await container.sticker_usage.history("local", "default", {})
        assert all(p.sticker_id != order[0] for p in history.items)
    finally:
        await container.stop()


async def test_cancelling_fence_rejects_late_plan_even_after_restart(tmp_path: Path) -> None:
    database = await _init_db(tmp_path)
    try:
        _, generation = await _seed_source_chain(database, generation_status="processing")
        row = await database.fetchone(
            "SELECT channel_turn_id FROM channel_turns WHERE generation_id = ?", (generation,)
        )
        assert row is not None
        turn_id = UUID(row["channel_turn_id"])
        repository = SQLiteExternalChannelRepository(database)
        await repository.set_turn_cancelling(turn_id, updated_at=datetime.now(UTC))
        await database.close()
        await database.open()
        repository = SQLiteExternalChannelRepository(database)
        result = await repository.complete_turn(
            turn_id, reply_text="迟到的旧回复", delivery_id=uuid4(), completed_at=datetime.now(UTC)
        )
        assert result.turn.status is ChannelTurnStatus.CANCELLING
        assert result.turn.delivery_id is None
        assert result.persisted_events == ()
        assert await database.fetchall("SELECT * FROM channel_deliveries") == []
    finally:
        await database.close()


async def test_stop_while_reading_history_cannot_publish_old_image(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        await seed_candidates(container)
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
        created = await container.external_channels.create_connection(config)
        usage = Usage(StickerUsageHistory())
        usage.hang = True
        container.sticker_library._usage = usage
        old = await container.external_channels.ingest(
            _message(connection_id, external_message_id="before-stop", text="捏捏我"),
            access_token=created.access_token,
        )
        async with asyncio.timeout(5):
            await usage.entered.wait()
            stopped = await container.external_channels.ingest(
                _message(connection_id, external_message_id="stop", text="停一下"),
                access_token=created.access_token,
                supersede_inflight=True,
            )
            await usage.cancelled.wait()
        previous = await container.external_channels.wait_for_turn(
            connection_id,
            old.channel_turn_id,
            wait_seconds=5,
        )
        assert previous.status is ChannelTurnStatus.CANCELLED
        assert previous.delivery_id is None
        result = await container.external_channels.wait_for_turn(
            connection_id,
            stopped.channel_turn_id,
            wait_seconds=5,
        )
        assert result.status is ChannelTurnStatus.COMPLETED
        assert result.delivery_id is not None
        parts = await container.external_channel_repository.list_delivery_parts(result.delivery_id)
        assert not any(isinstance(p.payload, ChannelImageDeliveryPartPayload) for p in parts)
    finally:
        await container.stop()


async def test_cancelled_turn_is_not_released_before_generation_stops(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def hanging_model(request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        entered.set()
        try:
            await asyncio.Event().wait()
            yield LlmTextDelta("late")
        finally:
            stopped.set()

    monkeypatch.setattr(container.model_configurations.chat, "stream", hanging_model)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        original = container.external_channel_repository.set_turn_cancelling
        checked = asyncio.Event()

        async def interleaved_sync(turn_id: UUID, *, updated_at: datetime) -> ChannelTurnRecord:
            turn = await original(turn_id, updated_at=updated_at)
            # Simulate a status reader between the durable fence and generation.cancel().
            snapshot = await container.external_channels.wait_for_turn(
                connection_id,
                turn_id,
                wait_seconds=0,
            )
            assert snapshot.status is ChannelTurnStatus.CANCELLING
            assert not stopped.is_set()
            checked.set()
            return turn

        monkeypatch.setattr(
            container.external_channel_repository, "set_turn_cancelling", interleaved_sync
        )
        receipt = await container.external_channels.ingest(
            _message(connection_id),
            access_token=created.access_token,
        )
        async with asyncio.timeout(5):
            await entered.wait()
            cancelled = await container.external_channels.interrupt(
                connection_id,
                receipt.channel_turn_id,
                access_token=created.access_token,
                reason="test stop",
            )
        assert checked.is_set() and stopped.is_set()
        assert cancelled.accepted and cancelled.status is ChannelTurnStatus.CANCELLED
    finally:
        await container.stop()
