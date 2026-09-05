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
    monkeypatch.setattr(
        container.agent, "_llm", BlockedVision() if scenario == "cancel" else VisionRecorder()
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
