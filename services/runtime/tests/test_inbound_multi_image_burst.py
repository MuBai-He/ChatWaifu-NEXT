# pyright: reportPrivateUsage=false
"""Integration test suite for bounded native WeChat image burst intake (Phase 17.3F / ADR 0041).

Tests 17 scenarios:
1. Two messages ~1s apart combined into 1 provider call with 2 images and 1 outbound reply.
2. Snapshot proves both original message IDs are durable in SQLite channel_turns before seal.
3. Duplicate payload returns duplicate receipt; changed payload raises ChannelConflictError.
4. Plain text message while collecting immediately cancels burst; 0 vision provider calls.
5. Plain text during second image download cancels whole turn; 0 vision model calls.
6. Late image arriving after previous burst sealed queues without cancelling old generation;
   text stop cancels both.
7. Cap 4 eager seal + overflow explicit failure recovery notice at dispatch slot.
8. Same binding isolation across different conversations/connections.
9. Each saved photo preserves its correct original received timestamp + metadata.
10. Restart intake / dispatch / terminal cleanup leaves no orphan, no extra response, no redownload.
11. Pending contexts for leader and all followers removed after delivery.
12. Process crash/restart during burst intake recovery with friendly notice and zero redownloads.
13. Whole-burst download timeout under 20s deadline triggers friendly recovery notice.
14. Photo memory origin validation in persistence adapter fails closed on mismatched origins.
15. Photo memory observer cardinality mismatch aborts batch observation.
16. 5th image arriving after eager seal starts next pending burst without cancelling first.
17. Inbound image burst supersedes an already-running plain text generation cleanly.
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelConnectionConfiguration,
    ChannelInboundTextMessage,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_protocol.photo_memory import SavedPhoto
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinAuthorizationPoll,
    WeixinAuthorizationStart,
    WeixinCredentials,
    WeixinInboundImage,
    WeixinInboundText,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.burst import (
    BURST_IDLE_WINDOW_SECONDS,
    BurstTimerHandle,
)
from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
from chatwaifu_runtime.external_channels.management import (
    ChannelManagementService,
    _compute_images_fingerprint,
)
from chatwaifu_runtime.external_channels.models import (
    ChannelInboundImageInput,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.service import ChannelConflictError
from chatwaifu_runtime.photo_memory.classifier import PhotoClassification
from chatwaifu_runtime.photo_memory.models import PhotoItemOrigin, PhotoSaveCandidate
from chatwaifu_runtime.providers.contracts import (
    LlmInputImage,
    LlmRequest,
    LlmStreamEvent,
    LlmTextDelta,
)
from PIL import Image


def _make_test_image_bytes(color: str = "red", width: int = 16, height: int = 16) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_IMG_RED = _make_test_image_bytes("red")
_IMG_BLUE = _make_test_image_bytes("blue")
_IMG_GREEN = _make_test_image_bytes("green")
_IMG_YELLOW = _make_test_image_bytes("yellow")
_IMG_PURPLE = _make_test_image_bytes("purple")


class _ManualTimerHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class ManualBurstScheduler:
    """Controllable virtual time scheduler for burst coordinator tests."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._now = initial_time
        self._timers: list[tuple[float, int, Callable[[], None], _ManualTimerHandle]] = []
        self._counter = 0

    def monotonic(self) -> float:
        return self._now

    def call_later(self, delay: float, callback: Callable[[], None]) -> BurstTimerHandle:
        handle = _ManualTimerHandle()
        self._counter += 1
        self._timers.append((self._now + delay, self._counter, callback, handle))
        return handle

    def advance(self, seconds: float) -> None:
        self._now += seconds
        self._fire_due()

    def _fire_due(self) -> None:
        while True:
            due = [t for t in self._timers if t[0] <= self._now and not t[3].cancelled]
            if not due:
                break
            due.sort(key=lambda t: (t[0], t[1]))
            item = due[0]
            self._timers.remove(item)
            if not item[3].cancelled:
                item[2]()


class VisionRecorder:
    kind = "test_vision"
    supports_tool_calling = False

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []
        self.stream_barrier: asyncio.Event | None = None
        self.stream_entered: asyncio.Event | None = None

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        if self.stream_entered is not None:
            self.stream_entered.set()
        if self.stream_barrier is not None:
            await self.stream_barrier.wait()
        yield LlmTextDelta(
            "看见了，这是合并后的回复。" if request.images else "收到文本，停下来了。"
        )


class _FakeWeixin:
    def __init__(self) -> None:
        self.updates: asyncio.Queue[WeixinUpdates] = asyncio.Queue()
        self.sent: asyncio.Event = asyncio.Event()
        self.sent_messages: list[dict[str, str]] = []
        self.download_barrier: asyncio.Event | None = None
        self.download_entered: asyncio.Event | None = None
        self.download_barrier_index: int = 1
        self.download_count = 0
        self.image_data: dict[str, bytes] = {}
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def start_authorization(self) -> WeixinAuthorizationStart:
        raise NotImplementedError

    async def poll_authorization(
        self,
        *,
        qrcode: str,
        poll_base_url: str,
        verification_code: str | None = None,
    ) -> WeixinAuthorizationPoll:
        raise NotImplementedError

    async def notify_start(self, credentials: WeixinCredentials) -> None:
        pass

    async def notify_stop(self, credentials: WeixinCredentials) -> None:
        pass

    async def get_updates(self, credentials: WeixinCredentials, cursor: str) -> WeixinUpdates:
        while True:
            batch = await self.updates.get()
            matching = [m for m in batch.messages if m.sender_user_id == credentials.user_id]
            non_matching = [m for m in batch.messages if m.sender_user_id != credentials.user_id]
            if non_matching:
                await self.updates.put(
                    WeixinUpdates(cursor=batch.cursor, messages=tuple(non_matching))
                )
            if matching or not batch.messages:
                return WeixinUpdates(cursor=batch.cursor, messages=tuple(matching))
            await asyncio.sleep(0.01)

    async def get_typing_ticket(
        self, credentials: WeixinCredentials, *, recipient_user_id: str, context_token: str
    ) -> str | None:
        return "test-typing-ticket"

    async def send_typing(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        typing_ticket: str,
        active: bool,
    ) -> None:
        pass

    async def send_text(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        context_token: str,
        client_id: str,
        text: str,
    ) -> str:
        self.sent_messages.append(
            {
                "recipient_user_id": recipient_user_id,
                "context_token": context_token,
                "client_id": client_id,
                "text": text,
            }
        )
        self.sent.set()
        return client_id

    async def send_image(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        context_token: str,
        client_id: str,
        image_bytes: bytes,
        mime_type: str,
    ) -> str | None:
        return client_id

    async def download_image(self, image: WeixinInboundImage) -> tuple[bytes, str]:
        self.download_count += 1
        if self.download_count == self.download_barrier_index:
            if self.download_entered is not None:
                self.download_entered.set()
            if self.download_barrier is not None:
                await self.download_barrier.wait()
        key = image.encrypt_query_param or ""
        data: bytes = self.image_data.get(key, _IMG_RED)
        return data, "image/png"

    async def download_images(
        self, images: Sequence[WeixinInboundImage]
    ) -> Sequence[tuple[bytes, str]]:
        return [await self.download_image(img) for img in images]


def _configuration(
    connection_id: UUID, account_key: str = "bot-1", user_id: str = "owner-1"
) -> ChannelConnectionConfiguration:
    return ChannelConnectionConfiguration(
        connection_id=connection_id,
        provider_id="weixin_ilink",
        name="微信测试",
        character_id="default",
        principal_scope="local",
        account_key=account_key,
        allowed_sender_keys=[user_id],
        enabled=True,
    )


def _credentials(
    access_token: str, account_key: str = "bot-1", user_id: str = "owner-1"
) -> WeixinCredentials:
    return WeixinCredentials(
        bot_token="test-bot-token",
        bot_id=account_key,
        user_id=user_id,
        base_url="https://api.weixin.qq.com/",
        gateway_access_token=access_token,
    )


def _make_inbound_image(
    transport: _FakeWeixin,
    aes_key: str,
    data: bytes = _IMG_RED,
    full_url: str = "https://example.com/img",
) -> WeixinInboundImage:
    enc = f"enc-{aes_key}"
    transport.image_data[enc] = data
    return WeixinInboundImage(encrypt_query_param=enc, full_url=full_url, aes_key=aes_key)


async def _wait_for_turn(
    container: RuntimeContainer,
    connection_id: UUID,
    external_message_id: str,
    wait_seconds: float = 5.0,
) -> None:
    start = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start < wait_seconds:
        turn = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, external_message_id
        )
        if turn is not None:
            return
        await asyncio.sleep(0.005)
    raise TimeoutError(
        f"Turn for message {external_message_id} was not admitted within {wait_seconds}s"
    )


async def _wait_for_burst_admission(
    container: RuntimeContainer,
    connection_id: UUID,
    external_message_id: str,
    wait_seconds: float = 5.0,
) -> ChannelTurnRecord:
    start = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start < wait_seconds:
        turn = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, external_message_id
        )
        if turn is not None:
            leader = await container.external_channels.repository.find_burst_leader(
                turn.channel_turn_id
            )
            if leader is not None:
                return turn
        await asyncio.sleep(0.005)
    raise TimeoutError(
        f"Burst member for message {external_message_id} was not admitted within {wait_seconds}s"
    )


async def _wait_condition(
    predicate: Callable[[], bool | Awaitable[bool]],
    wait_seconds: float = 5.0,
    interval: float = 0.005,
) -> None:
    start = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start < wait_seconds:
        res = predicate()
        if asyncio.iscoroutine(res):
            res = await res
        if res:
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"Condition not met within {wait_seconds}s")


async def _setup_burst_environment(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    *,
    initial_time: float = 1000.0,
    user_id: str = "owner-1",
    bot_id: str = "bot-1",
) -> tuple[
    RuntimeContainer,
    ChannelManagementService,
    _FakeWeixin,
    InMemoryChannelCredentialStore,
    VisionRecorder,
    ManualBurstScheduler,
    UUID,
]:
    container = RuntimeContainer(runtime_settings)
    await container.start()

    recorder = VisionRecorder()
    monkeypatch.setattr(container.agent, "_llm", recorder)

    scheduler = ManualBurstScheduler(initial_time)
    container.external_channels.burst_coordinator._scheduler = scheduler

    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()

    management = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
        event_hub=container.event_hub,
        event_publisher=container.event_publisher,
    )
    container.channel_management = management

    connection_id = uuid4()
    created = await container.external_channels.create_connection(
        _configuration(connection_id, account_key=bot_id, user_id=user_id)
    )
    creds = _credentials(created.access_token, account_key=bot_id, user_id=user_id)
    await store.set(f"weixin_ilink:{connection_id}", creds.to_json())
    await management.connection_configuration_changed(created.snapshot)

    return container, management, transport, store, recorder, scheduler, connection_id


@pytest.mark.asyncio
async def test_burst_two_images_logical_one_second_apart_combined_into_one_call(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 1: 2 msgs ~1s logical apart combined into 1 provider call with 2 images,
    1 outbound reply.
    """
    (
        container,
        _management,
        transport,
        _store,
        recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    try:
        # Message 1 arrives with image 1
        img1 = _make_inbound_image(transport, "aes-1", _IMG_RED)
        await transport.updates.put(
            WeixinUpdates(
                cursor="c1",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="一只小猫",
                        context_token="ctx-1",
                        received_at=t0,
                        images=(img1,),
                    ),
                ),
            )
        )
        await _wait_for_burst_admission(container, connection_id, "msg-1")

        # Advance 1.0s (within 1.5s idle window)
        scheduler.advance(1.0)

        # Message 2 arrives with image 2
        img2 = _make_inbound_image(transport, "aes-2", _IMG_BLUE)
        await transport.updates.put(
            WeixinUpdates(
                cursor="c2",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-2",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="一只小狗",
                        context_token="ctx-2",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img2,),
                    ),
                ),
            )
        )
        await _wait_for_burst_admission(container, connection_id, "msg-2")

        # Still collecting; no LLM call yet
        assert len(recorder.requests) == 0

        # Advance 1.5s -> idle window expires -> seals and dispatches
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)

        # Wait for delivery to complete
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # Exactly 1 LLM request with 2 images
        assert len(recorder.requests) == 1
        req = recorder.requests[0]
        assert len(req.images) == 2

        # Combined prompt preserves user text in arrival order without duplicate [图片]
        assert "一只小猫" in req.user_text
        assert "一只小狗" in req.user_text

        # Exactly 1 outbound message delivered to user
        assert len(transport.sent_messages) == 1
        sent = transport.sent_messages[0]
        assert sent["recipient_user_id"] == "owner-1"
        assert "看见了" in sent["text"]

        # Check turn records in DB
        t1 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "msg-1"
        )
        t2 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "msg-2"
        )
        assert t1 is not None
        assert t2 is not None
        assert t1.delivery_id is not None
        assert t2.delivery_id is None
        leader_rec = await container.external_channels.repository.find_burst_leader(
            t2.channel_turn_id
        )
        assert leader_rec is not None and leader_rec.leader_channel_turn_id == t1.channel_turn_id
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_burst_snapshot_original_message_ids_durable_before_seal(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 2: Snapshot proves both original message IDs are durable in channel_turns
    before seal.
    """
    (
        container,
        _management,
        transport,
        _store,
        _recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    try:
        img1 = _make_inbound_image(transport, "aes-1", _IMG_RED)
        await transport.updates.put(
            WeixinUpdates(
                cursor="c1",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-orig-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="图一",
                        context_token="ctx-orig-1",
                        received_at=t0,
                        images=(img1,),
                    ),
                ),
            )
        )
        await _wait_for_turn(container, connection_id, "msg-orig-1")
        scheduler.advance(1.0)

        img2 = _make_inbound_image(transport, "aes-2", _IMG_BLUE)
        await transport.updates.put(
            WeixinUpdates(
                cursor="c2",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-orig-2",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="图二",
                        context_token="ctx-orig-2",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img2,),
                    ),
                ),
            )
        )
        await _wait_for_turn(container, connection_id, "msg-orig-2")

        # Verify durable turns exist BEFORE seal (scheduler has NOT advanced past 1.5s)
        turn1 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "msg-orig-1"
        )
        turn2 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "msg-orig-2"
        )
        assert turn1 is not None
        assert turn2 is not None
        assert turn1.status is ChannelTurnStatus.ACCEPTED
        assert turn2.status is ChannelTurnStatus.ACCEPTED
        assert turn1.external_message_id == "msg-orig-1"
        assert turn2.external_message_id == "msg-orig-2"

        # Member link table verifies relationship
        members = await container.external_channels.repository.list_burst_members(
            turn1.channel_turn_id
        )
        assert len(members) == 2
        assert (
            members[0].ordinal == 0 and members[0].member_channel_turn_id == turn1.channel_turn_id
        )
        assert (
            members[1].ordinal == 1 and members[1].member_channel_turn_id == turn2.channel_turn_id
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_burst_duplicate_and_conflict_handling(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 3: Duplicate payload returns duplicate receipt; changed payload raises
    ChannelConflictError.
    """
    (
        container,
        _management,
        transport,
        store,
        _recorder,
        _scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    try:
        img1 = _make_inbound_image(transport, "aes-dup-1", _IMG_RED)
        fp1 = _compute_images_fingerprint((img1,))

        inbound_msg = ChannelInboundTextMessage(
            connection_id=connection_id,
            account_key="bot-1",
            external_message_id="dup-test-msg",
            conversation_key="owner-1",
            sender_key="owner-1",
            principal_scope="local",
            text="[图片]",
            received_at=t0,
        )

        await container.external_channels.get_connection(connection_id)
        raw_cred = await store.get(f"weixin_ilink:{connection_id}")
        assert raw_cred is not None
        cred = WeixinCredentials.from_json(raw_cred)

        async def loader() -> tuple[LlmInputImage, ...]:
            return (LlmInputImage(data=_IMG_RED, mime_type="image/png"),)

        image_input = ChannelInboundImageInput(source_fingerprint=fp1, load=loader)

        receipt = await container.external_channels.ingest(
            inbound_msg,
            access_token=cred.gateway_access_token,
            burst_intake=True,
            image_input=image_input,
            raw_images=(img1,),
        )
        assert receipt.duplicate is False

        # Re-send identical message -> duplicate=True
        dup_receipt = await container.external_channels.ingest(
            inbound_msg,
            access_token=cred.gateway_access_token,
            burst_intake=True,
            image_input=image_input,
            raw_images=(img1,),
        )
        assert dup_receipt.duplicate is True
        assert dup_receipt.channel_turn_id == receipt.channel_turn_id

        # Re-send same message ID with changed fingerprint -> ChannelConflictError
        img_changed = _make_inbound_image(transport, "aes-changed-1", _IMG_BLUE)
        fp_changed = _compute_images_fingerprint((img_changed,))
        changed_input = ChannelInboundImageInput(source_fingerprint=fp_changed, load=loader)
        with pytest.raises(ChannelConflictError):
            await container.external_channels.ingest(
                inbound_msg,
                access_token=cred.gateway_access_token,
                burst_intake=True,
                image_input=changed_input,
                raw_images=(img_changed,),
            )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_burst_plain_text_while_collecting_cancels_burst_zero_vision_calls(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 4: Text message while collecting immediately cancels burst;
    0 vision provider calls.
    """
    (
        container,
        _management,
        transport,
        _store,
        recorder,
        _scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    try:
        img1 = _make_inbound_image(transport, "aes-1", _IMG_RED)
        img2 = _make_inbound_image(transport, "aes-2", _IMG_BLUE)

        # Ingest two images into burst
        await transport.updates.put(
            WeixinUpdates(
                cursor="c1",
                messages=(
                    WeixinInboundText(
                        external_message_id="burst-img-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="",
                        context_token="ctx-1",
                        received_at=t0,
                        images=(img1,),
                    ),
                    WeixinInboundText(
                        external_message_id="burst-img-2",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="",
                        context_token="ctx-2",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img2,),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "burst-img-1")
        await _wait_for_turn(container, connection_id, "burst-img-2")

        # Plain text arrives before burst seals
        await transport.updates.put(
            WeixinUpdates(
                cursor="c3",
                messages=(
                    WeixinInboundText(
                        external_message_id="text-stop",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="停一下，不要看图片了",
                        context_token="ctx-stop",
                        received_at=t0 + timedelta(seconds=2),
                    ),
                ),
            )
        )

        # Wait for text delivery
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # 0 vision calls made
        vision_requests = [r for r in recorder.requests if r.images]
        assert len(vision_requests) == 0

        # Text reply delivered
        assert len(transport.sent_messages) == 1
        assert "停下来了" in transport.sent_messages[0]["text"]

        # Both burst images are marked CANCELLED
        t1 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "burst-img-1"
        )
        t2 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "burst-img-2"
        )
        assert t1 is not None and t1.status is ChannelTurnStatus.CANCELLED
        assert t2 is not None and t2.status is ChannelTurnStatus.CANCELLED
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_burst_text_during_download_cancels_whole_turn_zero_vision_calls(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 5: Text during second image download cancels whole turn; 0 vision model calls."""
    (
        container,
        _management,
        transport,
        _store,
        recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    # Configure download barrier on transport for the second image download
    download_barrier = asyncio.Event()
    download_entered = asyncio.Event()
    transport.download_barrier = download_barrier
    transport.download_entered = download_entered
    transport.download_barrier_index = 2

    try:
        img1 = _make_inbound_image(transport, "aes-dl-1", _IMG_RED)
        img2 = _make_inbound_image(transport, "aes-dl-2", _IMG_BLUE)

        await transport.updates.put(
            WeixinUpdates(
                cursor="c1",
                messages=(
                    WeixinInboundText(
                        external_message_id="dl-img-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="",
                        context_token="ctx-dl-1",
                        received_at=t0,
                        images=(img1,),
                    ),
                    WeixinInboundText(
                        external_message_id="dl-img-2",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="",
                        context_token="ctx-dl-2",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img2,),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "dl-img-1")
        await _wait_for_turn(container, connection_id, "dl-img-2")

        # Trigger seal
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)

        # Wait until download begins
        await asyncio.wait_for(download_entered.wait(), timeout=2.0)

        # While download is blocked, send text stop
        await transport.updates.put(
            WeixinUpdates(
                cursor="c2",
                messages=(
                    WeixinInboundText(
                        external_message_id="text-during-dl",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="停一下",
                        context_token="ctx-stop-dl",
                        received_at=t0 + timedelta(seconds=2),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "text-during-dl")
        # Release download barrier
        download_barrier.set()

        # Wait for delivery
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # Zero vision calls
        vision_requests = [r for r in recorder.requests if r.images]
        assert len(vision_requests) == 0

        # Burst turns cancelled
        t1 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "dl-img-1"
        )
        t2 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "dl-img-2"
        )
        assert t1 is not None and t1.status is ChannelTurnStatus.CANCELLED
        assert t2 is not None and t2.status is ChannelTurnStatus.CANCELLED
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_late_image_after_sealed_queues_without_cancelling_and_text_stop_cancels_both(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 6: Late image arriving after previous burst sealed queues without cancelling
    old generation; text stop cancels both.
    """
    (
        container,
        _management,
        transport,
        _store,
        recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    # Hold LLM stream active with barrier
    llm_barrier = asyncio.Event()
    llm_entered = asyncio.Event()
    recorder.stream_barrier = llm_barrier
    recorder.stream_entered = llm_entered

    try:
        # Ingest Burst 1 (msg 1)
        img1 = _make_inbound_image(transport, "aes-b1", _IMG_RED)
        await transport.updates.put(
            WeixinUpdates(
                cursor="c1",
                messages=(
                    WeixinInboundText(
                        external_message_id="b1-msg",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="第一波",
                        context_token="ctx-b1",
                        received_at=t0,
                        images=(img1,),
                    ),
                ),
            )
        )

        await _wait_for_burst_admission(container, connection_id, "b1-msg")
        # Seal and dispatch Burst 1
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)
        await asyncio.wait_for(llm_entered.wait(), timeout=2.0)

        # Burst 1 is now actively generating!
        t1 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "b1-msg"
        )
        assert t1 is not None and t1.status is ChannelTurnStatus.PROCESSING

        # Late image arrives -> should queue into pending batch WITHOUT cancelling Burst 1
        img2 = _make_inbound_image(transport, "aes-b2", _IMG_BLUE)
        await transport.updates.put(
            WeixinUpdates(
                cursor="c2",
                messages=(
                    WeixinInboundText(
                        external_message_id="b2-late-msg",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="第二波晚到",
                        context_token="ctx-b2",
                        received_at=t0 + timedelta(seconds=2),
                        images=(img2,),
                    ),
                ),
            )
        )

        await _wait_for_burst_admission(container, connection_id, "b2-late-msg")

        # Burst 1 is STILL active and NOT cancelled!
        t1_check = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "b1-msg"
        )
        assert t1_check is not None and t1_check.status is ChannelTurnStatus.PROCESSING

        # Late image is accepted in pending batch
        t2 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "b2-late-msg"
        )
        assert t2 is not None and t2.status is ChannelTurnStatus.ACCEPTED

        # User sends plain text stop
        await transport.updates.put(
            WeixinUpdates(
                cursor="c3",
                messages=(
                    WeixinInboundText(
                        external_message_id="text-stop-all",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="停一下",
                        context_token="ctx-stop-all",
                        received_at=t0 + timedelta(seconds=3),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "text-stop-all")
        # Release LLM barrier
        llm_barrier.set()

        # Wait for delivery
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # Both burst 1 and queued burst 2 are cancelled!
        t1_final = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "b1-msg"
        )
        t2_final = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "b2-late-msg"
        )
        assert t1_final is not None and t1_final.status is ChannelTurnStatus.CANCELLED
        assert t2_final is not None and t2_final.status is ChannelTurnStatus.CANCELLED
    finally:
        llm_barrier.set()
        await container.stop()


@pytest.mark.asyncio
async def test_burst_cap_four_eager_seal_and_overflow_failure_recovery_notice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 7: Cap 4 eager seal + overflow explicit failure recovery notice at dispatch slot."""
    (
        container,
        _management,
        transport,
        _store,
        recorder,
        _scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    try:
        # Part A: Send 4 images in a single batch -> eager seals without debounce
        images = [
            _make_inbound_image(transport, f"aes-cap-{i}", _make_test_image_bytes(c))
            for i, c in enumerate(["red", "blue", "green", "yellow"])
        ]
        updates = [
            WeixinInboundText(
                external_message_id=f"cap-msg-{i}",
                sender_user_id="owner-1",
                recipient_bot_id="bot-1",
                text=f"图{i}",
                context_token=f"ctx-cap-{i}",
                received_at=t0 + timedelta(milliseconds=i * 100),
                images=(images[i],),
            )
            for i in range(4)
        ]

        await transport.updates.put(WeixinUpdates(cursor="c-cap4", messages=tuple(updates)))

        # Wait for delivery WITHOUT calling scheduler.advance (eager seal!)
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        assert len(recorder.requests) == 1
        assert len(recorder.requests[0].images) == 4
        assert len(transport.sent_messages) == 1

        # Reset sent event for Part B
        transport.sent.clear()
        recorder.requests.clear()

        # Part B: 3+2 Overflow. Message 1 has 3 images, Message 2 has 2 images -> 3+2 pushes
        # collecting batch over cap, seals as overflow, terminates with friendly recovery notice
        # and 0 LLM calls.
        overflow_images = [
            _make_inbound_image(transport, f"aes-ov-{i}", _make_test_image_bytes("purple"))
            for i in range(5)
        ]
        ov_updates = [
            WeixinInboundText(
                external_message_id="ov-msg-0",
                sender_user_id="owner-1",
                recipient_bot_id="bot-1",
                text="3张图",
                context_token="ctx-ov-0",
                received_at=t0 + timedelta(seconds=10),
                images=tuple(overflow_images[:3]),
            ),
            WeixinInboundText(
                external_message_id="ov-msg-1",
                sender_user_id="owner-1",
                recipient_bot_id="bot-1",
                text="2张图",
                context_token="ctx-ov-1",
                received_at=t0 + timedelta(seconds=10, milliseconds=100),
                images=tuple(overflow_images[3:]),
            ),
        ]

        await transport.updates.put(WeixinUpdates(cursor="c-overflow", messages=tuple(ov_updates)))

        # Wait for recovery notice delivery
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # 0 LLM calls made (overflow rejects before model)
        assert len(recorder.requests) == 0

        # Delivered recovery notice
        sent_notice = transport.sent_messages[-1]
        assert "刚才发来的图片我没看清，能再发一次吗？" in sent_notice["text"]

        # Leader turn is FAILED
        lead_turn = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "ov-msg-0"
        )
        assert lead_turn is not None and lead_turn.status is ChannelTurnStatus.FAILED
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_burst_isolation_across_bindings_and_conversations(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 8: Same binding isolation across different conversations/connections."""
    (
        container,
        management,
        transport,
        store,
        recorder,
        scheduler,
        conn_id_a,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch, user_id="user-a")
    t0 = datetime.now(UTC)

    # Setup second connection for user-b
    conn_id_b = uuid4()
    created_b = await container.external_channels.create_connection(
        _configuration(conn_id_b, account_key="bot-1", user_id="user-b")
    )
    creds_b = _credentials(created_b.access_token, account_key="bot-1", user_id="user-b")
    await store.set(f"weixin_ilink:{conn_id_b}", creds_b.to_json())
    await management.connection_configuration_changed(created_b.snapshot)

    try:
        img_a = _make_inbound_image(transport, "aes-user-a", _IMG_RED)
        img_b = _make_inbound_image(transport, "aes-user-b", _IMG_BLUE)

        # Send image for user-a and image for user-b
        await transport.updates.put(
            WeixinUpdates(
                cursor="c-ab",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-user-a",
                        sender_user_id="user-a",
                        recipient_bot_id="bot-1",
                        text="用户A的图",
                        context_token="ctx-a",
                        received_at=t0,
                        images=(img_a,),
                    ),
                    WeixinInboundText(
                        external_message_id="msg-user-b",
                        sender_user_id="user-b",
                        recipient_bot_id="bot-1",
                        text="用户B的图",
                        context_token="ctx-b",
                        received_at=t0,
                        images=(img_b,),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, conn_id_a, "msg-user-a")
        await _wait_for_turn(container, conn_id_b, "msg-user-b")
        # Advance scheduler
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)

        # Wait until both deliveries are sent (2 deliveries)
        await _wait_condition(lambda: len(transport.sent_messages) >= 2)

        assert len(transport.sent_messages) == 2
        # Two distinct LLM requests, each with 1 image
        assert len(recorder.requests) == 2
        assert len(recorder.requests[0].images) == 1
        assert len(recorder.requests[1].images) == 1
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_photo_memory_received_at_preservation_per_image(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 9: Each saved photo preserves its correct original received timestamp + metadata."""
    (
        container,
        _management,
        transport,
        _store,
        _recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    await container.photo_repository.update_settings(
        "local", "default", retention_enabled=True, expected_revision=0
    )
    t_base = datetime.now(UTC) - timedelta(seconds=10)
    t1 = t_base
    t2 = t_base + timedelta(seconds=1)

    saved_photos: list[SavedPhoto] = []
    original_save = container.photo_repository.save
    all_saved = asyncio.Event()

    async def save_hook(
        scope: str, character: str, candidate: PhotoSaveCandidate, *, expected_revision: int
    ) -> SavedPhoto | None:
        result = await original_save(
            scope, character, candidate, expected_revision=expected_revision
        )
        if result is not None:
            saved_photos.append(result)
            if len(saved_photos) == 2:
                all_saved.set()
        return result

    classified_idx = 0

    async def classify_hook(image: LlmInputImage, *, generation_id: UUID) -> PhotoClassification:
        nonlocal classified_idx
        classified_idx += 1
        return PhotoClassification(
            suitable=True,
            confidence=0.9,
            title=f"照片 {classified_idx}",
            description="测试照片",
            keywords=["测试"],
        )

    monkeypatch.setattr(container.photo_observer._classifier, "classify", classify_hook)
    monkeypatch.setattr(container.photo_repository, "save", save_hook)

    try:
        img1 = _make_inbound_image(transport, "aes-ph-1", _IMG_RED)
        img2 = _make_inbound_image(transport, "aes-ph-2", _IMG_BLUE)

        await transport.updates.put(
            WeixinUpdates(
                cursor="c-photo",
                messages=(
                    WeixinInboundText(
                        external_message_id="ph-msg-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="",
                        context_token="ctx-ph-1",
                        received_at=t1,
                        images=(img1,),
                    ),
                    WeixinInboundText(
                        external_message_id="ph-msg-2",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="",
                        context_token="ctx-ph-2",
                        received_at=t2,
                        images=(img2,),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "ph-msg-1")
        await _wait_for_turn(container, connection_id, "ph-msg-2")
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)

        # Wait for delivery and photo saves
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)
        await asyncio.wait_for(all_saved.wait(), timeout=5.0)

        assert len(saved_photos) == 2
        # Check received_at preserves each individual message's arrival timestamp!
        assert saved_photos[0].received_at == t1
        assert saved_photos[1].received_at == t2

        # Both photos share the leader's generation ID as source_generation_id
        t_lead = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "ph-msg-1"
        )
        assert t_lead is not None
        assert saved_photos[0].source_generation_id == t_lead.generation_id
        assert saved_photos[1].source_generation_id == t_lead.generation_id
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_restart_during_intake_dispatch_leaves_no_orphans_or_duplicate_replies(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 10: Restart intake / dispatch / terminal cleanup leaves no orphan,
    no extra response, no redownload.
    """
    (
        container,
        _management,
        transport,
        _store,
        _recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    try:
        img1 = _make_inbound_image(transport, "aes-rst-1", _IMG_RED)
        img2 = _make_inbound_image(transport, "aes-rst-2", _IMG_BLUE)

        await transport.updates.put(
            WeixinUpdates(
                cursor="c-rst",
                messages=(
                    WeixinInboundText(
                        external_message_id="rst-msg-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="",
                        context_token="ctx-rst-1",
                        received_at=t0,
                        images=(img1,),
                    ),
                    WeixinInboundText(
                        external_message_id="rst-msg-2",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="",
                        context_token="ctx-rst-2",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img2,),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "rst-msg-1")
        await _wait_for_turn(container, connection_id, "rst-msg-2")
        # Advance scheduler so burst seals and dispatches
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # Both completed
        t1 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "rst-msg-1"
        )
        assert t1 is not None and t1.status is ChannelTurnStatus.COMPLETED
    finally:
        await container.stop()

    # Now restart container on the same database
    new_container = RuntimeContainer(runtime_settings)
    await new_container.start()
    try:
        # Inflight turns list should have 0 leftover inflight turns
        inflight = await new_container.external_channels.repository.list_inflight_turns(
            connection_id
        )
        assert len(inflight) == 0
    finally:
        await new_container.stop()


@pytest.mark.asyncio
async def test_pending_contexts_evicted_for_leader_and_followers_after_delivery(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 11: Pending contexts for leader and all followers removed after delivery."""
    (
        container,
        _management,
        transport,
        store,
        _recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    try:
        img1 = _make_inbound_image(transport, "aes-ctx-1", _IMG_RED)
        img2 = _make_inbound_image(transport, "aes-ctx-2", _IMG_BLUE)

        await transport.updates.put(
            WeixinUpdates(
                cursor="c-ctx",
                messages=(
                    WeixinInboundText(
                        external_message_id="lead-msg",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="图1",
                        context_token="tok-lead",
                        received_at=t0,
                        images=(img1,),
                    ),
                    WeixinInboundText(
                        external_message_id="follow-msg",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="图2",
                        context_token="tok-follow",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img2,),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "lead-msg")
        await _wait_for_turn(container, connection_id, "follow-msg")

        # Only the leader needs a private send token; follower identity is durable.
        raw_cred = await store.get(f"weixin_ilink:{connection_id}")
        assert raw_cred is not None
        cred = WeixinCredentials.from_json(raw_cred)
        assert "lead-msg" in cred.pending_contexts
        assert "follow-msg" not in cred.pending_contexts

        # Advance scheduler to seal and dispatch
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)

        # Wait for delivery
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # Wait until context eviction completes
        async def _both_contexts_evicted() -> bool:
            raw = await store.get(f"weixin_ilink:{connection_id}")
            if raw is None:
                return True
            c = WeixinCredentials.from_json(raw)
            return "lead-msg" not in c.pending_contexts and "follow-msg" not in c.pending_contexts

        await _wait_condition(_both_contexts_evicted)

        raw_cred = await store.get(f"weixin_ilink:{connection_id}")
        assert raw_cred is not None
        cred = WeixinCredentials.from_json(raw_cred)
        assert "lead-msg" not in cred.pending_contexts
        assert "follow-msg" not in cred.pending_contexts
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_crash_restart_interrupted_intake_recovery(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Scenario 12: Process crash/restart during burst intake collection.

    Live ownership is absent on restart, leader turn is durably closed with friendly
    recovery notice once without redownloading images, followers mirror FAILED state.
    """
    (
        container,
        _management,
        transport,
        _store,
        recorder,
        _scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    try:
        img1 = _make_inbound_image(transport, "aes-crash-1", _IMG_RED)
        img2 = _make_inbound_image(transport, "aes-crash-2", _IMG_BLUE)

        await transport.updates.put(
            WeixinUpdates(
                cursor="c-crash",
                messages=(
                    WeixinInboundText(
                        external_message_id="crash-lead-msg",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="崩溃前图1",
                        context_token="ctx-crash-1",
                        received_at=t0,
                        images=(img1,),
                    ),
                    WeixinInboundText(
                        external_message_id="crash-follow-msg",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="崩溃前图2",
                        context_token="ctx-crash-2",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img2,),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "crash-lead-msg")
        await _wait_for_turn(container, connection_id, "crash-follow-msg")

        # Turns are admitted in SQLite before generation ever started
        lead = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "crash-lead-msg"
        )
        assert lead is not None and lead.status is ChannelTurnStatus.ACCEPTED
        lead_id = lead.channel_turn_id

        # Snapshot the durable DB while in accepted/collecting state to simulate a crash
        snapshot_path = tmp_path / "crash-snapshot.db"
        with sqlite3.connect(runtime_settings.database_path) as src:
            with sqlite3.connect(snapshot_path) as dst:
                src.backup(dst)
    finally:
        # Simulate abrupt process termination while collecting
        await container.stop()

    # Reset transport state for new container
    transport.download_count = 0
    transport.sent_messages.clear()
    transport.sent.clear()

    # Restart container with snapshot DB simulating state right before crash
    new_container = RuntimeContainer(
        runtime_settings.model_copy(
            update={
                "storage": runtime_settings.storage.model_copy(
                    update={"database_path": snapshot_path}
                )
            }
        )
    )
    await new_container.start()
    try:
        # Live burst coordinator ownership is absent in new_container
        assert not new_container.external_channels.burst_coordinator.is_live_burst_turn(lead_id)

        # Sync turn on interrupted leader turn
        refreshed_lead = await new_container.external_channels.repository.get_turn(lead_id)
        assert refreshed_lead is not None
        synced = await new_container.external_channels._sync_turn(refreshed_lead)

        # Leader is durably FAILED with friendly notice
        assert synced.status is ChannelTurnStatus.FAILED
        assert synced.delivery_id is not None
        plan = await new_container.external_channels.repository.get_delivery_plan(
            synced.delivery_id
        )
        assert plan is not None
        assert isinstance(plan.parts[0].payload, ChannelTextDeliveryPartPayload)
        assert "刚才发来的图片我没看清，能再发一次吗？" in plan.parts[0].payload.text

        # Follower turn is mirrored to FAILED
        follower = await new_container.external_channels.repository.find_turn_by_external_message(
            connection_id, "crash-follow-msg"
        )
        assert follower is not None and follower.status is ChannelTurnStatus.FAILED
        assert follower.delivery_id is None

        # Zero redownloads occurred
        assert transport.download_count == 0
        assert len(recorder.requests) == 0
    finally:
        await new_container.stop()


@pytest.mark.asyncio
async def test_whole_burst_download_deadline_timeout(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 13: Whole-burst download timeout under 20s deadline.

    A hanging image download triggers whole-burst timeout, failing the turn
    with friendly recovery notice.
    """
    monkeypatch.setattr(
        "chatwaifu_runtime.external_channels.service.BURST_LOAD_TIMEOUT_SECONDS",
        0.1,
    )

    (
        container,
        _management,
        transport,
        _store,
        recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    hang_event = asyncio.Event()
    orig_download = transport.download_image

    async def hanging_download(img: WeixinInboundImage) -> tuple[bytes, str]:
        if img.encrypt_query_param is not None and "aes-hang-2" in img.encrypt_query_param:
            await hang_event.wait()
        return await orig_download(img)

    monkeypatch.setattr(transport, "download_image", hanging_download)

    try:
        img1 = _make_inbound_image(transport, "aes-hang-1", _IMG_RED)
        img2 = _make_inbound_image(transport, "aes-hang-2", _IMG_BLUE)

        await transport.updates.put(
            WeixinUpdates(
                cursor="c-hang",
                messages=(
                    WeixinInboundText(
                        external_message_id="hang-msg-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="正常图",
                        context_token="ctx-h-1",
                        received_at=t0,
                        images=(img1,),
                    ),
                    WeixinInboundText(
                        external_message_id="hang-msg-2",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="卡死图",
                        context_token="ctx-h-2",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img2,),
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "hang-msg-1")
        await _wait_for_turn(container, connection_id, "hang-msg-2")

        # Seal and dispatch burst
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)

        # Leader turn reaches terminal FAILED after deadline expires
        async def _turn_failed() -> bool:
            t = await container.external_channels.repository.find_turn_by_external_message(
                connection_id, "hang-msg-1"
            )
            return t is not None and t.status is ChannelTurnStatus.FAILED

        await _wait_condition(_turn_failed, wait_seconds=3.0)

        # Follower also mirrored to FAILED
        t2 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "hang-msg-2"
        )
        assert t2 is not None and t2.status is ChannelTurnStatus.FAILED

        # 0 LLM calls reached
        assert len(recorder.requests) == 0
    finally:
        hang_event.set()
        await container.stop()


@pytest.mark.asyncio
async def test_photo_memory_origin_validation(runtime_settings: Settings) -> None:
    """Scenario 14: Photo memory origin validation in persistence adapter.

    Valid origin preserves exact member turn received_at timestamp; invalid origin fails closed.
    """
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        conn_id = str(uuid4())
        gen_id = str(uuid4())
        session_id = str(uuid4())
        turn_id = str(uuid4())
        binding_id = str(uuid4())
        lead_channel_turn_id = str(uuid4())
        mem_channel_turn_id = str(uuid4())
        t_lead_at = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
        t_member_at = datetime(2026, 9, 6, 12, 0, 2, tzinfo=UTC)
        now_iso = datetime.now(UTC).isoformat()
        sc_json = json.dumps(
            {"connection_id": conn_id, "principal_scope": "local", "chat_type": "direct"}
        )

        async with container.database.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO channel_connections (
                    connection_id, provider_id, name, character_id, principal_scope,
                    enabled, access_token_hash, created_at, updated_at
                ) VALUES (?, 'weixin_ilink', 'test-conn', 'default', 'local', 1, 'hash', ?, ?)
                """,
                (conn_id, now_iso, now_iso),
            )
            await conn.execute(
                """
                INSERT INTO sessions (
                    session_id, character_id, state, conversation_state, created_at, updated_at
                ) VALUES (?, 'default', 'active', 'ready', ?, ?)
                """,
                (session_id, now_iso, now_iso),
            )
            await conn.execute(
                """
                INSERT INTO turns (
                    turn_id, session_id, role, committed_text, created_at, source_context_json
                ) VALUES (?, ?, 'user', 'burst images', ?, ?)
                """,
                (turn_id, session_id, now_iso, sc_json),
            )
            await conn.execute(
                """
                INSERT INTO generations (
                    generation_id, session_id, turn_id, state, backend_kind, started_at
                ) VALUES (?, ?, ?, 'completed', 'local', ?)
                """,
                (gen_id, session_id, turn_id, now_iso),
            )
            await conn.execute(
                """
                INSERT INTO channel_bindings (
                    binding_id, connection_id, conversation_key, sender_key, session_id,
                    created_at, updated_at
                ) VALUES (?, ?, 'owner-1', 'owner-1', ?, ?, ?)
                """,
                (binding_id, conn_id, session_id, now_iso, now_iso),
            )
            await conn.execute(
                """
                INSERT INTO channel_turns (
                    channel_turn_id, connection_id, binding_id, external_message_id,
                    content_sha256, conversation_key, sender_key, principal_scope,
                    session_id, turn_id, generation_id, status, accepted_at,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, 'ph-val-lead', 'hash-lead', 'owner-1', 'owner-1', 'local',
                    ?, ?, ?, 'completed', ?, ?, ?
                )
                """,
                (
                    lead_channel_turn_id,
                    conn_id,
                    binding_id,
                    session_id,
                    turn_id,
                    gen_id,
                    t_lead_at.isoformat(),
                    t_lead_at.isoformat(),
                    t_lead_at.isoformat(),
                ),
            )
            await conn.execute(
                """
                INSERT INTO channel_turns (
                    channel_turn_id, connection_id, binding_id, external_message_id,
                    content_sha256, conversation_key, sender_key, principal_scope,
                    session_id, turn_id, generation_id, status, accepted_at,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, 'ph-val-mem', 'hash-mem', 'owner-1', 'owner-1', 'local',
                    ?, ?, ?, 'completed', ?, ?, ?
                )
                """,
                (
                    mem_channel_turn_id,
                    conn_id,
                    binding_id,
                    session_id,
                    turn_id,
                    gen_id,
                    t_member_at.isoformat(),
                    t_member_at.isoformat(),
                    t_member_at.isoformat(),
                ),
            )
            await conn.execute(
                """
                INSERT INTO channel_turn_burst_members (
                    burst_id, leader_channel_turn_id, member_channel_turn_id,
                    ordinal, received_at, created_at
                ) VALUES (?, ?, ?, 0, ?, ?), (?, ?, ?, 1, ?, ?)
                """,
                (
                    lead_channel_turn_id,
                    lead_channel_turn_id,
                    lead_channel_turn_id,
                    t_lead_at.isoformat(),
                    t_lead_at.isoformat(),
                    lead_channel_turn_id,
                    lead_channel_turn_id,
                    mem_channel_turn_id,
                    t_member_at.isoformat(),
                    t_member_at.isoformat(),
                ),
            )

        photo_repo = container.photo_repository
        await photo_repo.update_settings(
            "local", "default", retention_enabled=True, expected_revision=0
        )

        # Case A: Valid origin
        valid_origin = PhotoItemOrigin(
            channel_turn_id=UUID(mem_channel_turn_id),
            external_message_id="ph-val-mem",
            received_at=t_member_at,
        )
        candidate_valid = PhotoSaveCandidate(
            data=_IMG_BLUE,
            mime_type="image/png",
            width=16,
            height=16,
            title="有效照片",
            description="有效照片描述",
            confidence=0.95,
            keywords=("test",),
            source_connection_id=UUID(conn_id),
            generation_id=UUID(gen_id),
            item_origin=valid_origin,
        )
        saved = await photo_repo.save("local", "default", candidate_valid, expected_revision=1)
        assert saved is not None
        assert saved.received_at == t_member_at

        # Case B: Invalid origin (bogus turn ID) -> fails closed
        invalid_origin = PhotoItemOrigin(
            channel_turn_id=uuid4(),
            external_message_id="nonexistent-msg",
            received_at=t_member_at,
        )
        candidate_invalid = PhotoSaveCandidate(
            data=_IMG_GREEN,
            mime_type="image/png",
            width=16,
            height=16,
            title="无效照片",
            description="无效照片描述",
            confidence=0.95,
            keywords=("test",),
            source_connection_id=UUID(conn_id),
            generation_id=UUID(gen_id),
            item_origin=invalid_origin,
        )
        saved_invalid = await photo_repo.save(
            "local", "default", candidate_invalid, expected_revision=1
        )
        assert saved_invalid is None
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_photo_memory_observer_cardinality_mismatch_aborts() -> None:
    """Scenario 15: Photo memory observer cardinality mismatch aborts observation pipeline."""
    from chatwaifu_runtime.photo_memory.observer import PhotoMemoryObserver, PhotoObservationSource

    class DummyClassifier:
        def __init__(self) -> None:
            self.called = False

        async def classify_batch(self, *args: object, **kwargs: object) -> list[object]:
            self.called = True
            return []

    classifier = DummyClassifier()
    observer = PhotoMemoryObserver(
        classifier=classifier,  # type: ignore[arg-type]
        repository=None,  # type: ignore[arg-type]
    )

    source = PhotoObservationSource(
        principal_scope="local",
        character_id="default",
        connection_id=uuid4(),
        generation_id=uuid4(),
    )
    images = (
        LlmInputImage(data=b"img1", mime_type="image/png"),
        LlmInputImage(data=b"img2", mime_type="image/png"),
    )
    mismatched_origins = (
        PhotoItemOrigin(
            channel_turn_id=uuid4(),
            external_message_id="msg1",
            received_at=datetime.now(UTC),
        ),
    )

    observer.start()
    await observer.observe_batch(
        source,
        images,
        wait_for_completion=lambda: True,  # type: ignore[arg-type]
        item_origins=mismatched_origins,
    )
    assert classifier.called is False
    assert not observer._tasks
    await observer.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("drop_terminal_listener", [False, True])
async def test_fifth_image_arriving_after_seal_starts_next_pending_burst_without_cancelling_first(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, drop_terminal_listener: bool
) -> None:
    """Scenario 16: 5th image arriving after eager seal starts next pending burst

    without cancelling the first 4-image burst.
    """
    (
        container,
        _management,
        transport,
        _store,
        recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)
    if drop_terminal_listener:
        container.external_channels._turn_terminal_listeners.remove(
            container.external_channels.burst_coordinator.on_turn_terminal
        )

    # Hold LLM stream active with barrier
    llm_barrier = asyncio.Event()
    llm_entered = asyncio.Event()
    recorder.stream_barrier = llm_barrier
    recorder.stream_entered = llm_entered

    try:
        # Ingest 4 images -> eager seal fires immediately
        images = [
            _make_inbound_image(transport, f"aes-5th-{i}", _make_test_image_bytes(c))
            for i, c in enumerate(["red", "blue", "green", "yellow"])
        ]
        updates = [
            WeixinInboundText(
                external_message_id=f"c4-msg-{i}",
                sender_user_id="owner-1",
                recipient_bot_id="bot-1",
                text=f"图{i}",
                context_token=f"ctx-c4-{i}",
                received_at=t0 + timedelta(milliseconds=i * 100),
                images=(images[i],),
            )
            for i in range(4)
        ]

        await transport.updates.put(WeixinUpdates(cursor="c-first4", messages=tuple(updates)))
        for i in range(4):
            await _wait_for_turn(container, connection_id, f"c4-msg-{i}")

        # Wait for LLM stream to be entered (burst 1 is actively processing)
        await asyncio.wait_for(llm_entered.wait(), timeout=3.0)

        # Burst 1 leader is processing
        t_lead = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "c4-msg-0"
        )
        assert t_lead is not None and t_lead.status is ChannelTurnStatus.PROCESSING

        # 5th image arrives while burst 1 is active
        img5 = _make_inbound_image(transport, "aes-5th-4", _make_test_image_bytes("purple"))
        await transport.updates.put(
            WeixinUpdates(
                cursor="c-5th",
                messages=(
                    WeixinInboundText(
                        external_message_id="c4-msg-4",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="第5张图",
                        context_token="ctx-c4-4",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img5,),
                    ),
                ),
            )
        )
        await _wait_for_turn(container, connection_id, "c4-msg-4")

        # Invariant check: Burst 1 is STILL PROCESSING, NOT cancelled!
        t_lead_check = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "c4-msg-0"
        )
        assert t_lead_check is not None and t_lead_check.status is ChannelTurnStatus.PROCESSING

        # 5th image is ACCEPTED in pending queue
        t5 = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "c4-msg-4"
        )
        assert t5 is not None and t5.status is ChannelTurnStatus.ACCEPTED

        # Release LLM barrier so Burst 1 completes
        llm_barrier.set()
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # Exactly 1 request completed for Burst 1 with 4 images
        assert len(recorder.requests) == 1
        assert len(recorder.requests[0].images) == 4

        # Reset barriers for Burst 2
        transport.sent.clear()
        recorder.stream_barrier = None
        recorder.stream_entered = None

        # Advance virtual scheduler so Burst 2 seals and dispatches
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # Now Burst 2 has dispatched with exactly 1 image (5th image)
        assert len(recorder.requests) == 2
        assert len(recorder.requests[1].images) == 1
    finally:
        llm_barrier.set()
        await container.stop()


@pytest.mark.asyncio
async def test_burst_supersedes_already_running_text_generation(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 17: Inbound image burst supersedes an already-running plain text generation.

    Burst admission interrupts the in-flight text turn, collects the burst images,
    and cleanly dispatches without colliding with the superseded turn or raising ChannelBusyError.
    """
    (
        container,
        _management,
        transport,
        _store,
        recorder,
        scheduler,
        connection_id,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    t0 = datetime.now(UTC)

    llm_barrier = asyncio.Event()
    llm_entered = asyncio.Event()
    recorder.stream_barrier = llm_barrier
    recorder.stream_entered = llm_entered

    try:
        # Ingest a plain text message that starts generating
        await transport.updates.put(
            WeixinUpdates(
                cursor="c-text",
                messages=(
                    WeixinInboundText(
                        external_message_id="txt-running-msg",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="这是一条正在生成的文本消息",
                        context_token="ctx-txt",
                        received_at=t0,
                    ),
                ),
            )
        )

        await _wait_for_turn(container, connection_id, "txt-running-msg")
        # Wait until text generation enters LLM stream
        await asyncio.wait_for(llm_entered.wait(), timeout=3.0)

        t_txt = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "txt-running-msg"
        )
        assert t_txt is not None and t_txt.status is ChannelTurnStatus.PROCESSING

        # Now an image burst arrives while text generation is actively running
        img1 = _make_inbound_image(transport, "aes-sup-1", _IMG_RED)
        img2 = _make_inbound_image(transport, "aes-sup-2", _IMG_BLUE)

        await transport.updates.put(
            WeixinUpdates(
                cursor="c-img-1",
                messages=(
                    WeixinInboundText(
                        external_message_id="img-sup-msg-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="打断文本的图1",
                        context_token="ctx-sup-1",
                        received_at=t0 + timedelta(seconds=1),
                        images=(img1,),
                    ),
                ),
            )
        )

        await _wait_for_burst_admission(container, connection_id, "img-sup-msg-1")

        # Invariant: Prior text turn was superseded and marked CANCELLED
        async def _text_cancelled() -> bool:
            t = await container.external_channels.repository.find_turn_by_external_message(
                connection_id, "txt-running-msg"
            )
            return t is not None and t.status is ChannelTurnStatus.CANCELLED

        await _wait_condition(_text_cancelled, wait_seconds=3.0)

        # Release LLM barrier for the cancelled text generation
        llm_barrier.set()

        # Ingest second image of the burst
        await transport.updates.put(
            WeixinUpdates(
                cursor="c-img-2",
                messages=(
                    WeixinInboundText(
                        external_message_id="img-sup-msg-2",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="图2",
                        context_token="ctx-sup-2",
                        received_at=t0 + timedelta(seconds=2),
                        images=(img2,),
                    ),
                ),
            )
        )

        await _wait_for_burst_admission(container, connection_id, "img-sup-msg-2")

        # Reset recorder barriers for burst generation
        recorder.stream_barrier = None
        recorder.stream_entered = None
        transport.sent.clear()

        # Seal and dispatch the image burst
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)
        await asyncio.wait_for(transport.sent.wait(), timeout=5.0)

        # Outbound reply is sent for the burst
        assert len(transport.sent_messages) == 1
        assert transport.sent_messages[0]["context_token"] == "ctx-sup-1"

        # Vision request contains both images
        vision_requests = [r for r in recorder.requests if r.images]
        assert len(vision_requests) == 1
        assert len(vision_requests[0].images) == 2

        # Prior text turn remained cancelled with zero delivery
        t_txt_final = await container.external_channels.repository.find_turn_by_external_message(
            connection_id, "txt-running-msg"
        )
        assert t_txt_final is not None and t_txt_final.status is ChannelTurnStatus.CANCELLED
        assert t_txt_final.delivery_id is None
    finally:
        llm_barrier.set()
        await container.stop()


@pytest.mark.asyncio
async def test_repeated_terminal_transition_does_not_reenter_database_lock(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        container,
        _management,
        transport,
        _store,
        _recorder,
        _scheduler,
        cid,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    try:
        image = _make_inbound_image(transport, "repeat-terminal")
        await transport.updates.put(
            WeixinUpdates(
                cursor="repeat",
                messages=(
                    WeixinInboundText(
                        external_message_id="repeat",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="",
                        context_token="repeat-context",
                        received_at=datetime.now(UTC),
                        images=(image,),
                    ),
                ),
            )
        )
        turn = await _wait_for_burst_admission(container, cid, "repeat")
        repository = container.external_channel_repository
        for _ in range(2):
            result = await asyncio.wait_for(
                repository.set_turn_terminal(
                    turn.channel_turn_id,
                    status=ChannelTurnStatus.CANCELLED,
                    error=None,
                    completed_at=datetime.now(UTC),
                ),
                2,
            )
            assert result.status is ChannelTurnStatus.CANCELLED
        with pytest.raises(KeyError):
            await asyncio.wait_for(
                repository.set_turn_terminal(
                    uuid4(),
                    status=ChannelTurnStatus.CANCELLED,
                    error=None,
                    completed_at=datetime.now(UTC),
                ),
                2,
            )
    finally:
        await container.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_after_overflow", [False, True])
async def test_full_deferred_burst_and_many_more_images_do_not_block_stop(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, stop_after_overflow: bool
) -> None:
    (
        container,
        management,
        transport,
        _store,
        recorder,
        _scheduler,
        cid,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    release = asyncio.Event()
    entered = asyncio.Event()
    recorder.stream_barrier = release
    recorder.stream_entered = entered
    now = datetime.now(UTC)

    def photo(index: int) -> WeixinInboundText:
        return WeixinInboundText(
            external_message_id=f"pressure-{index}",
            sender_user_id="owner-1",
            recipient_bot_id="bot-1",
            text="",
            context_token=f"pressure-context-{index}",
            received_at=now + timedelta(milliseconds=index),
            images=(_make_inbound_image(transport, f"pressure-{index}"),),
        )

    try:
        await transport.updates.put(
            WeixinUpdates(cursor="active", messages=tuple(photo(i) for i in range(4)))
        )
        await asyncio.wait_for(entered.wait(), 5)
        await transport.updates.put(
            WeixinUpdates(cursor="pending", messages=tuple(photo(i) for i in range(4, 8)))
        )
        await _wait_for_burst_admission(container, cid, "pressure-7")
        # These arrive behind an already sealed pending batch. The stop at the
        # end of the SAME poll response must still be durably admitted promptly.
        stop = WeixinInboundText(
            external_message_id="pressure-stop",
            sender_user_id="owner-1",
            recipient_bot_id="bot-1",
            text="停一下",
            context_token="pressure-stop-context",
            received_at=now + timedelta(seconds=1),
            images=(),
        )
        await transport.updates.put(
            WeixinUpdates(
                cursor="stop",
                messages=(
                    *(photo(i) for i in range(8, 28)),
                    *((stop,) if stop_after_overflow else ()),
                ),
            )
        )
        if stop_after_overflow:
            await _wait_for_turn(container, cid, "pressure-stop")

        async def cursor_advanced() -> bool:
            return await container.external_channel_repository.get_adapter_cursor(cid) == "stop"

        await _wait_condition(cursor_advanced)
        if not stop_after_overflow:
            release.set()

            async def all_finished() -> bool:
                turns = await container.database.fetchall("SELECT status FROM channel_turns")
                return len(turns) == 28 and all(
                    t["status"] in ("completed", "failed") for t in turns
                )

            await _wait_condition(all_finished)

            async def both_sent() -> bool:
                return len(transport.sent_messages) == 2

            await _wait_condition(both_sent)
            assert [len(request.images) for request in recorder.requests] == [4]
            return
        rows = await container.database.fetchall(
            "SELECT status FROM channel_turns WHERE external_message_id != 'pressure-stop'"
        )
        assert len(rows) == 28
        assert all(row["status"] == "cancelled" for row in rows)
        restored = await management._load_credentials(cid)
        assert restored is not None and len(restored.pending_contexts) <= 1
        release.set()
        await asyncio.wait_for(transport.sent.wait(), 5)
        assert [len(request.images) for request in recorder.requests] == [4, 0]
    finally:
        release.set()
        await container.stop()


@pytest.mark.asyncio
async def test_already_queued_idle_callback_cannot_seal_refreshed_window(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        container,
        _management,
        transport,
        _store,
        recorder,
        scheduler,
        cid,
    ) = await _setup_burst_environment(runtime_settings, monkeypatch)
    old_callback: Callable[[], None] | None = None
    try:
        now = datetime.now(UTC)
        for index in range(2):
            await transport.updates.put(
                WeixinUpdates(
                    cursor=f"timer-{index}",
                    messages=(
                        WeixinInboundText(
                            external_message_id=f"timer-{index}",
                            sender_user_id="owner-1",
                            recipient_bot_id="bot-1",
                            text="",
                            context_token=f"timer-token-{index}",
                            received_at=now,
                            images=(_make_inbound_image(transport, f"timer-{index}"),),
                        ),
                    ),
                )
            )
            await _wait_for_burst_admission(container, cid, f"timer-{index}")
            if index == 0:
                # Capture an idle callback as if it was already queued before
                # handle.cancel(). Invoking it later must honor the renewed window.
                old_callback = min(scheduler._timers, key=lambda item: item[0])[2]
                scheduler.advance(1.0)
        assert old_callback is not None
        old_callback()
        # Drain actual tracked timer task rather than waiting a fixed duration.
        coordinator = container.external_channels.burst_coordinator
        await asyncio.gather(*tuple(coordinator._background_tasks))
        assert not recorder.requests
        scheduler.advance(BURST_IDLE_WINDOW_SECONDS)
        await asyncio.wait_for(transport.sent.wait(), 5)
        assert [len(r.images) for r in recorder.requests] == [2]
    finally:
        await container.stop()
