"""Real Conversation/SQLite image admission, cancellation and restart boundaries."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelConnectionConfiguration,
    ChannelInboundTextMessage,
    ChannelTurnStatus,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.models import ChannelInboundImageInput
from chatwaifu_runtime.external_channels.service import ChannelConflictError, ChannelPolicyError
from chatwaifu_runtime.providers.contracts import (
    LlmInputImage,
    LlmRequest,
    LlmStreamEvent,
    LlmTextDelta,
)

_IMAGE_BYTES = b"ephemeral-image-not-in-history"


class VisionRecorder:
    kind = "test_vision"
    supports_tool_calling = False

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        yield LlmTextDelta("看见了，一只小猫。" if request.images else "嗯，停下来了。")


def message(
    connection_id: UUID, external_id: str, text: str = "[图片]"
) -> ChannelInboundTextMessage:
    return ChannelInboundTextMessage(
        connection_id=connection_id,
        account_key="test-bot",
        external_message_id=external_id,
        conversation_key="owner",
        sender_key="owner",
        principal_scope="local",
        text=text,
        received_at=datetime.now(UTC),
    )


async def connect(container: RuntimeContainer) -> tuple[UUID, str]:
    conn_id = uuid4()
    created = await container.external_channels.create_connection(
        ChannelConnectionConfiguration(
            connection_id=conn_id,
            provider_id="weixin_ilink",
            name="图片验收",
            character_id="default",
            principal_scope="local",
            account_key="test-bot",
            allowed_sender_keys=["owner"],
        )
    )
    return conn_id, created.access_token


@pytest.mark.asyncio
async def test_real_image_admission_dedupe_privacy_and_next_turn(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)
    loads = 0
    entered = asyncio.Event()
    release = asyncio.Event()
    conn_id, token = await connect(container)
    msg = message(conn_id, "photo-one")

    async def load() -> LlmInputImage:
        nonlocal loads
        loads += 1
        # The real conversation generation exists before any media work.
        turn = await container.external_channels.repository.find_turn_by_external_message(
            conn_id, msg.external_message_id
        )
        assert turn is not None
        assert (
            await container.conversation_repository.generation_result(turn.generation_id)
            is not None
        )
        entered.set()
        await release.wait()
        return LlmInputImage(data=_IMAGE_BYTES, mime_type="image/png")

    image = ChannelInboundImageInput(hashlib.sha256(b"private-source").hexdigest(), load)
    try:
        rejected = msg.model_copy(update={"sender_key": "stranger"})
        with pytest.raises(ChannelPolicyError):
            await container.external_channels.ingest(
                rejected, access_token=token, image_input=image
            )
        assert loads == 0
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image
        )
        await asyncio.wait_for(entered.wait(), 3)
        assert not recorder.requests
        duplicate = await container.external_channels.ingest(
            msg, access_token=token, image_input=image
        )
        assert duplicate.duplicate and duplicate.channel_turn_id == receipt.channel_turn_id
        with pytest.raises(ChannelConflictError):
            await container.external_channels.ingest(
                msg,
                access_token=token,
                image_input=ChannelInboundImageInput("a" * 64, load),
            )
        release.set()
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.COMPLETED
        assert loads == 1
        assert recorder.requests[0].images[0].data == _IMAGE_BYTES
        events = await container.event_store.read_stream(result.session_id, limit=200)
        serialized = json.dumps(events, default=str)
        for secret in (
            _IMAGE_BYTES.decode(),
            base64.b64encode(_IMAGE_BYTES).decode(),
            "private-source",
        ):
            assert secret not in serialized
        next_receipt = await container.external_channels.ingest(
            message(conn_id, "next-text", "停一下"), access_token=token, supersede_inflight=True
        )
        next_result = await container.external_channels.wait_for_turn(
            conn_id, next_receipt.channel_turn_id, wait_seconds=5
        )
        assert next_result.status is ChannelTurnStatus.COMPLETED
        assert not recorder.requests[-1].images
        assert all("data:image" not in text for _, text in recorder.requests[-1].history)
    finally:
        release.set()
        await container.stop()


@pytest.mark.asyncio
async def test_new_text_cancels_blocked_image_before_provider(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def load() -> LlmInputImage:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    try:
        conn_id, token = await connect(container)
        receipt = await container.external_channels.ingest(
            message(conn_id, "slow-image"),
            access_token=token,
            image_input=ChannelInboundImageInput("b" * 64, load),
        )
        await asyncio.wait_for(entered.wait(), 3)
        following = await container.external_channels.ingest(
            message(conn_id, "stop", "停一下"), access_token=token, supersede_inflight=True
        )
        await asyncio.wait_for(cancelled.wait(), 3)
        old = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=3
        )
        new = await container.external_channels.wait_for_turn(
            conn_id, following.channel_turn_id, wait_seconds=3
        )
        assert old.status is ChannelTurnStatus.CANCELLED and old.delivery_id is None
        assert new.status is ChannelTurnStatus.COMPLETED
        assert len(recorder.requests) == 1 and not recorder.requests[0].images
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_restart_does_not_reexecute_ephemeral_image(
    runtime_settings: Settings, tmp_path: Path
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    entered = asyncio.Event()
    loads = 0

    async def load() -> LlmInputImage:
        nonlocal loads
        loads += 1
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    conn_id, token = await connect(container)
    msg = message(conn_id, "crashed-image")
    image = ChannelInboundImageInput("c" * 64, load)
    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image
        )
        await asyncio.wait_for(entered.wait(), 3)
        # Snapshot the durable DB while the generation is still running, simulating a crash.
        snapshot_path = tmp_path / "crash-snapshot.db"
        with sqlite3.connect(runtime_settings.database_path) as src:
            with sqlite3.connect(snapshot_path) as dst:
                src.backup(dst)
    finally:
        await container.stop()
    restarted = RuntimeContainer(
        runtime_settings.model_copy(
            update={
                "storage": runtime_settings.storage.model_copy(
                    update={"database_path": snapshot_path}
                )
            }
        )
    )
    await restarted.start()
    try:
        result = await restarted.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=3
        )
        assert result.status is ChannelTurnStatus.FAILED
        replay = await restarted.external_channels.ingest(
            msg, access_token=token, image_input=image
        )
        assert replay.duplicate and replay.channel_turn_id == receipt.channel_turn_id
        assert loads == 1
        next_receipt = await restarted.external_channels.ingest(
            message(conn_id, "after-restart", "你好"), access_token=token
        )
        next_result = await restarted.external_channels.wait_for_turn(
            conn_id, next_receipt.channel_turn_id, wait_seconds=5
        )
        assert next_result.status is ChannelTurnStatus.COMPLETED
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_image_failure_notice_matches_history_and_survives_replay(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    loads = 0

    async def load() -> LlmInputImage:
        nonlocal loads
        loads += 1
        raise ValueError("image decoding failed")

    conn_id, token = await connect(container)
    msg = message(conn_id, "bad-image")
    image = ChannelInboundImageInput("d" * 64, load)
    try:
        receipt = await container.external_channels.ingest(
            msg, access_token=token, image_input=image
        )
        result = await container.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=5
        )
        assert result.status is ChannelTurnStatus.FAILED and result.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(result.delivery_id)
        assert plan is not None and len(plan.parts) == 1
        notice = "这张图我刚才没看清，能再发一次吗？"
        assert plan.parts[0].payload.model_dump()["text"] == notice
        history = await container.conversation_repository.recent_history(
            result.session_id, uuid4(), limit=20
        )
        assert sum(entry.role == "assistant" and entry.text == notice for entry in history) == 1
    finally:
        await container.stop()
    restarted = RuntimeContainer(runtime_settings)
    await restarted.start()
    try:
        replay = await restarted.external_channels.ingest(
            msg, access_token=token, image_input=image
        )
        assert replay.duplicate and loads == 1
        replay_result = await restarted.external_channels.wait_for_turn(
            conn_id, receipt.channel_turn_id, wait_seconds=3
        )
        assert replay_result.delivery_id == result.delivery_id
        replay_plan = await restarted.external_channel_repository.get_delivery_plan(
            result.delivery_id
        )
        assert replay_plan is not None
        assert [part.part_id for part in replay_plan.parts] == [part.part_id for part in plan.parts]
        next_receipt = await restarted.external_channels.ingest(
            message(conn_id, "after-failure", "你好"), access_token=token
        )
        next_result = await restarted.external_channels.wait_for_turn(
            conn_id, next_receipt.channel_turn_id, wait_seconds=5
        )
        assert next_result.status is ChannelTurnStatus.COMPLETED
    finally:
        await restarted.stop()
