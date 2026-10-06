"""Actual generation and SQLite fences for asynchronous opt-in image learning."""
# pyright: reportPrivateUsage=false

import asyncio
import io
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from chatwaifu_protocol.channels import ChannelTurnStatus
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.models import ChannelInboundImageInput
from chatwaifu_runtime.providers.contracts import (
    LlmInputImage,
    LlmRequest,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.sticker_library.classifier import StickerClassification
from PIL import Image
from provider_test_support import use_recording_provider
from test_inbound_image_lifecycle import VisionRecorder, connect, message


class BlockedVision:
    kind = "blocked_vision"
    supports_tool_calling = False

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        await asyncio.Event().wait()
        yield LlmTextDelta("unreachable")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["off", "accept", "photo", "disable", "delete", "cancel"])
async def test_learning_source_and_revision_fences(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    container = RuntimeContainer(runtime_settings)
    use_recording_provider(
        monkeypatch,
        container.model_configurations,
        BlockedVision() if scenario == "cancel" else VisionRecorder(),
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def classify(
        image: LlmInputImage, *, generation_id: UUID
    ) -> StickerClassification | None:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return (
            None
            if scenario == "photo"
            else StickerClassification(
                suitable=True,
                confidence=0.99,
                label="开心小猫",
                description="开心的小猫",
                expression="happy",
            )
        )

    monkeypatch.setattr(container.sticker_library._classifier, "classify", classify)
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer, format="PNG")

    async def load() -> LlmInputImage:
        return LlmInputImage(data=buffer.getvalue(), mime_type="image/png")

    await container.start()
    try:
        connection_id, token = await connect(container)
        if scenario != "off":
            await container.sticker_repository.update_settings(
                "local", "default", learning_enabled=True, expected_revision=0
            )
        receipt = await container.external_channels.ingest(
            message(connection_id, "learning-image"),
            access_token=token,
            image_input=ChannelInboundImageInput(source_fingerprint="a" * 64, load=load),
        )
        if scenario == "off":
            await container.external_channels.wait_for_turn(
                connection_id, receipt.channel_turn_id, wait_seconds=5
            )
            assert calls == 0
        else:
            await asyncio.wait_for(entered.wait(), 5)
            tasks = [task for _, task in container.sticker_library._tasks.values()]
            assert len(tasks) == 1
            assert (await container.sticker_repository.snapshot("local", "default")).items == []
            if scenario == "cancel":
                await container.external_channels.interrupt(
                    connection_id, receipt.channel_turn_id, access_token=token, reason="test stop"
                )
                assert tasks[0].cancelled()
            else:
                finished = await container.external_channels.wait_for_turn(
                    connection_id, receipt.channel_turn_id, wait_seconds=5
                )
                assert finished.status is ChannelTurnStatus.COMPLETED
                if scenario == "disable":
                    await container.sticker_repository.update_settings(
                        "local", "default", learning_enabled=False, expected_revision=1
                    )
                elif scenario == "delete":
                    await container.sticker_repository.delete(
                        "local", "default", "learned_" + "0" * 32
                    )
                release.set()
                await asyncio.wait_for(asyncio.gather(*tasks), 5)
        snapshot = await container.sticker_repository.snapshot("local", "default")
        assert len(snapshot.items) == (1 if scenario == "accept" else 0)
        if snapshot.items:
            assert snapshot.items[0].source_connection_id == connection_id
            assert (
                await container.sticker_repository.get_image(
                    "other-owner", "default", snapshot.items[0].sticker_id
                )
                is None
            )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_learning_revision_fence_on_real_sticker_delete(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    use_recording_provider(monkeypatch, container.model_configurations, VisionRecorder())

    turn2_entered = asyncio.Event()
    turn2_release = asyncio.Event()
    classify_calls = 0

    async def classify(
        image: LlmInputImage, *, generation_id: UUID
    ) -> StickerClassification | None:
        nonlocal classify_calls
        classify_calls += 1
        if classify_calls == 1:
            return StickerClassification(
                suitable=True,
                confidence=0.98,
                label="第一只小猫",
                description="第一只小猫表情",
                expression="happy",
            )
        turn2_entered.set()
        await turn2_release.wait()
        return StickerClassification(
            suitable=True,
            confidence=0.99,
            label="第二只小猫",
            description="第二只小猫表情",
            expression="happy",
        )

    monkeypatch.setattr(container.sticker_library._classifier, "classify", classify)

    buffer1 = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(buffer1, format="PNG")
    img1_bytes = buffer1.getvalue()

    buffer2 = io.BytesIO()
    Image.new("RGB", (16, 16), "black").save(buffer2, format="PNG")
    img2_bytes = buffer2.getvalue()

    async def load1() -> LlmInputImage:
        return LlmInputImage(data=img1_bytes, mime_type="image/png")

    async def load2() -> LlmInputImage:
        return LlmInputImage(data=img2_bytes, mime_type="image/png")

    await container.start()
    try:
        connection_id, token = await connect(container)
        await container.sticker_repository.update_settings(
            "local", "default", learning_enabled=True, expected_revision=0
        )

        # 1. Ingest turn 1 to learn and physically persist a real, source-grounded sticker.
        receipt1 = await container.external_channels.ingest(
            message(connection_id, "real-image-turn-1"),
            access_token=token,
            image_input=ChannelInboundImageInput(source_fingerprint="1" * 64, load=load1),
        )
        finished1 = await container.external_channels.wait_for_turn(
            connection_id, receipt1.channel_turn_id, wait_seconds=5
        )
        assert finished1.status is ChannelTurnStatus.COMPLETED

        tasks1 = [task for _, task in container.sticker_library._tasks.values()]
        if tasks1:
            await asyncio.wait_for(asyncio.gather(*tasks1), 5)

        snap1 = await container.sticker_repository.snapshot("local", "default")
        assert len(snap1.items) == 1
        saved_sticker = snap1.items[0]
        assert saved_sticker.source_connection_id == connection_id
        saved_id = saved_sticker.sticker_id
        saved_img = await container.sticker_repository.get_image("local", "default", saved_id)
        assert saved_img is not None
        assert len(saved_img) > 0

        # 2. Ingest turn 2 to start a second in-flight classifier.
        receipt2 = await container.external_channels.ingest(
            message(connection_id, "real-image-turn-2"),
            access_token=token,
            image_input=ChannelInboundImageInput(source_fingerprint="2" * 64, load=load2),
        )
        await asyncio.wait_for(turn2_entered.wait(), 5)
        tasks2 = [task for _, task in container.sticker_library._tasks.values()]
        assert len(tasks2) == 1

        finished2 = await container.external_channels.wait_for_turn(
            connection_id, receipt2.channel_turn_id, wait_seconds=5
        )
        assert finished2.status is ChannelTurnStatus.COMPLETED

        # 3. Delete existing sticker while turn 2 classification is in-flight.
        delete_result = await container.sticker_repository.delete("local", "default", saved_id)
        assert delete_result.deleted is True
        assert await container.sticker_repository.get_image("local", "default", saved_id) is None

        # 4. Release in-flight classifier: revision fence rejects save.
        turn2_release.set()
        await asyncio.wait_for(asyncio.gather(*tasks2), 5)

        # 5. Final snapshot has neither old nor new asset.
        final_snapshot = await container.sticker_repository.snapshot("local", "default")
        assert len(final_snapshot.items) == 0
        assert await container.sticker_repository.get_image("local", "default", saved_id) is None
    finally:
        await container.stop()
