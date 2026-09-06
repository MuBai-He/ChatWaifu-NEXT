"""Privacy-safe inbound batch observation tests for adapter and channel management."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from chatwaifu_protocol.channels import (
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import (
    WeixinILinkClient,
)
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinAuthorizationPoll,
    WeixinAuthorizationStart,
    WeixinAuthorizationState,
    WeixinCredentials,
    WeixinInboundBatchObservation,
    WeixinInboundImage,
    WeixinInboundText,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.credentials import (
    InMemoryChannelCredentialStore,
)
from chatwaifu_runtime.external_channels.management import (
    ChannelManagementService,
    _PendingEnrollment,
)
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.demo_llm import DemoLlmProvider


def _credentials(gateway_access_token: str | None = None) -> WeixinCredentials:
    return WeixinCredentials(
        bot_token="provider-token",
        bot_id="bot-1",
        user_id="owner-1",
        base_url="https://api.weixin.qq.com/",
        gateway_access_token=gateway_access_token or ("g" * 43),
    )


class _FakeWeixin:
    def __init__(self) -> None:
        self.updates: asyncio.Queue[WeixinUpdates] = asyncio.Queue()
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.sent = asyncio.Event()
        self.sent_messages: list[dict[str, str]] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    async def start_authorization(self) -> WeixinAuthorizationStart:
        return WeixinAuthorizationStart(qrcode="opaque-qr", qr_code_content="qr-content")

    async def poll_authorization(
        self,
        *,
        qrcode: str,
        poll_base_url: str,
        verification_code: str | None = None,
    ) -> WeixinAuthorizationPoll:
        del qrcode, poll_base_url, verification_code
        return WeixinAuthorizationPoll(state=WeixinAuthorizationState.CONFIRMED, user_id="owner-1")

    async def notify_start(self, credentials: WeixinCredentials) -> None:
        del credentials
        self.started.set()

    async def notify_stop(self, credentials: WeixinCredentials) -> None:
        del credentials
        self.stopped.set()

    async def get_updates(self, credentials: WeixinCredentials, cursor: str) -> WeixinUpdates:
        del credentials, cursor
        return await self.updates.get()

    async def get_typing_ticket(
        self, credentials: WeixinCredentials, *, recipient_user_id: str, context_token: str
    ) -> str | None:
        del credentials, recipient_user_id, context_token
        return "test-typing-ticket"

    async def send_typing(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        typing_ticket: str,
        active: bool,
    ) -> None:
        del credentials, recipient_user_id, typing_ticket, active

    async def send_text(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        context_token: str,
        client_id: str,
        text: str,
    ) -> str:
        del credentials
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
        del credentials, recipient_user_id, context_token, image_bytes, mime_type
        return client_id

    async def download_image(
        self,
        image: WeixinInboundImage,
    ) -> tuple[bytes, str]:
        del image
        return b"", "image/png"

    async def download_images(
        self,
        images: Sequence[WeixinInboundImage],
    ) -> Sequence[tuple[bytes, str]]:
        return [await self.download_image(img) for img in images]


def _configuration(connection_id: UUID) -> ChannelConnectionConfiguration:
    return ChannelConnectionConfiguration(
        connection_id=connection_id,
        provider_id="weixin_ilink",
        name="我的微信",
        character_id="default",
        principal_scope="local",
        account_key="bot-1",
        allowed_sender_keys=["owner-1"],
        enabled=True,
    )


def _replace_management(
    container: RuntimeContainer,
    store: InMemoryChannelCredentialStore,
    transport: _FakeWeixin,
) -> ChannelManagementService:
    service = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
        event_hub=container.event_hub,
        event_publisher=container.event_publisher,
    )
    container.channel_management = service
    return service


# ===========================================================================
# 1. Adapter client tests with mock HTTP
# ===========================================================================


@pytest.mark.asyncio
async def test_get_updates_unknown_item_type() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "get_updates_buf": "cursor-after-unknown",
                "msgs": [
                    {
                        "message_id": 1001,
                        "message_type": 1,
                        "message_state": 2,
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "context_token": "ctx-token-1",
                        "create_time_ms": 1_788_000_000_000,
                        "item_list": [
                            {"type": 47, "emoji_item": {"md5": "abc123md5", "len": 12345}}
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        updates = await WeixinILinkClient(http).get_updates(_credentials(), "cursor-before")

    assert updates.cursor == "cursor-after-unknown"
    assert len(updates.messages) == 0
    assert updates.observation is not None
    assert updates.observation.raw_count == 1
    assert updates.observation.accepted_count == 0
    assert updates.observation.ignored_count == 1
    assert updates.observation.message_type_counts == {"1": 1}
    assert updates.observation.item_type_counts == {"47": 1}
    assert updates.observation.rejection_reasons == {"unsupported_item_types": 1}


@pytest.mark.asyncio
async def test_get_updates_known_static_image() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "get_updates_buf": "cursor-after-img",
                "msgs": [
                    {
                        "message_id": 1002,
                        "message_type": 1,
                        "message_state": 2,
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "context_token": "ctx-token-img",
                        "create_time_ms": 1_788_000_000_000,
                        "item_list": [
                            {
                                "type": 2,
                                "image_item": {
                                    "full_url": "https://example.com/photo.jpg",
                                    "aes_key": "dummy-key",
                                },
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        updates = await WeixinILinkClient(http).get_updates(_credentials(), "cursor-before")

    assert updates.cursor == "cursor-after-img"
    assert len(updates.messages) == 1
    assert updates.observation is not None
    assert updates.observation.raw_count == 1
    assert updates.observation.accepted_count == 1
    assert updates.observation.ignored_count == 0
    assert updates.observation.message_type_counts == {"1": 1}
    assert updates.observation.item_type_counts == {"2": 1}
    assert updates.observation.rejection_reasons == {}


@pytest.mark.asyncio
async def test_get_updates_animated_gif_item_type_2_no_actual_cdn() -> None:
    cdn_called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cdn_called
        if "getupdates" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "ret": 0,
                    "get_updates_buf": "cursor-after-gif",
                    "msgs": [
                        {
                            "message_id": 1003,
                            "message_type": 1,
                            "message_state": 2,
                            "from_user_id": "owner-1",
                            "to_user_id": "bot-1",
                            "context_token": "ctx-token-gif",
                            "create_time_ms": 1_788_000_000_000,
                            "item_list": [
                                {
                                    "type": 2,
                                    "image_item": {
                                        "full_url": "https://cdn.example.com/sticker.gif",
                                        "aeskey": "testaeskey123456",
                                    },
                                }
                            ],
                        }
                    ],
                },
            )
        cdn_called = True
        return httpx.Response(200, content=b"GIF89a...")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        updates = await WeixinILinkClient(http).get_updates(_credentials(), "cursor-before")

    assert not cdn_called, "get_updates must not call CDN"
    assert updates.cursor == "cursor-after-gif"
    assert len(updates.messages) == 1
    assert updates.observation is not None
    assert updates.observation.raw_count == 1
    assert updates.observation.accepted_count == 1
    assert updates.observation.ignored_count == 0
    assert updates.observation.message_type_counts == {"1": 1}
    assert updates.observation.item_type_counts == {"2": 1}


@pytest.mark.asyncio
async def test_get_updates_echo_wrong_message_state_group_ignored_vs_unsupported() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "get_updates_buf": "cursor-5-msgs",
                "msgs": [
                    # 1. Echo: message_type 2
                    {
                        "message_id": 2001,
                        "message_type": 2,
                        "message_state": 2,
                        "from_user_id": "bot-1",
                        "to_user_id": "owner-1",
                        "item_list": [{"type": 1, "text_item": {"text": "echo from bot"}}],
                    },
                    # 2. Wrong message_state: message_state 1
                    {
                        "message_id": 2002,
                        "message_type": 1,
                        "message_state": 1,
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "item_list": [{"type": 1, "text_item": {"text": "unconfirmed state"}}],
                    },
                    # 3. Group: group_id present
                    {
                        "message_id": 2003,
                        "message_type": 1,
                        "message_state": 2,
                        "group_id": "test-group@chatroom",
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "item_list": [{"type": 1, "text_item": {"text": "group chat msg"}}],
                    },
                    # 4. Unsupported message_type: message_type 5
                    {
                        "message_id": 2004,
                        "message_type": 5,
                        "message_state": 2,
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "item_list": [{"type": 1, "text_item": {"text": "system notice"}}],
                    },
                    # 5. Unsupported item_type: type 99
                    {
                        "message_id": 2005,
                        "message_type": 1,
                        "message_state": 2,
                        "context_token": "token-2005",
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "item_list": [{"type": 99, "unknown_item": {"data": 123}}],
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        updates = await WeixinILinkClient(http).get_updates(_credentials(), "cursor-before")

    assert len(updates.messages) == 0
    obs = updates.observation
    assert obs is not None
    assert obs.raw_count == 5
    assert obs.accepted_count == 0
    assert obs.ignored_count == 5

    # Filtered / ignored reasons
    assert obs.rejection_reasons["echo_ignored"] == 1
    assert obs.rejection_reasons["wrong_message_state"] == 1
    assert obs.rejection_reasons["group_ignored"] == 1

    # Unsupported wire formats
    assert obs.rejection_reasons["unsupported_message_type"] == 1
    assert obs.rejection_reasons["unsupported_item_types"] == 1


@pytest.mark.asyncio
async def test_get_updates_mixed_batch() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "get_updates_buf": "cursor-mixed",
                "msgs": [
                    # 1. Valid text
                    {
                        "message_id": 3001,
                        "message_type": 1,
                        "message_state": 2,
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "context_token": "ctx-1",
                        "create_time_ms": 1_788_000_000_000,
                        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
                    },
                    # 2. Valid image
                    {
                        "message_id": 3002,
                        "message_type": 1,
                        "message_state": 2,
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "context_token": "ctx-2",
                        "create_time_ms": 1_788_000_000_000,
                        "item_list": [
                            {"type": 2, "image_item": {"full_url": "https://example.com/cat.jpg"}}
                        ],
                    },
                    # 3. Unknown item type 47
                    {
                        "message_id": 3003,
                        "message_type": 1,
                        "message_state": 2,
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "context_token": "ctx-3",
                        "create_time_ms": 1_788_000_000_000,
                        "item_list": [{"type": 47, "sticker": {"id": 1}}],
                    },
                    # 4. Echo
                    {
                        "message_id": 3004,
                        "message_type": 2,
                        "message_state": 2,
                        "from_user_id": "bot-1",
                        "to_user_id": "owner-1",
                        "item_list": [{"type": 1, "text_item": {"text": "bot echo"}}],
                    },
                    # 5. Group
                    {
                        "message_id": 3005,
                        "message_type": 1,
                        "message_state": 2,
                        "group_id": "room@chatroom",
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "item_list": [{"type": 1, "text_item": {"text": "group msg"}}],
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        updates = await WeixinILinkClient(http).get_updates(_credentials(), "cursor-before")

    assert len(updates.messages) == 2
    assert updates.messages[0].external_message_id == "3001"
    assert updates.messages[1].external_message_id == "3002"

    obs = updates.observation
    assert obs is not None
    assert obs.raw_count == 5
    assert obs.accepted_count == 2
    assert obs.ignored_count == 3
    assert obs.message_type_counts == {"1": 4, "2": 1}
    assert obs.item_type_counts == {"1": 3, "2": 1, "47": 1}
    assert obs.rejection_reasons == {
        "unsupported_item_types": 1,
        "echo_ignored": 1,
        "group_ignored": 1,
    }


@pytest.mark.asyncio
async def test_get_updates_malicious_string_types_and_private_payload_not_serialized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "get_updates_buf": "cursor-malicious",
                "msgs": [
                    {
                        "message_id": 4001,
                        "message_type": "DROP TABLE users;--",
                        "message_state": 2,
                        "from_user_id": "malicious_sender_id_secret",
                        "to_user_id": "bot-1",
                        "context_token": "super_secret_context_token_xyz",
                        "item_list": [
                            {
                                "type": "<script>alert('xss')</script>",
                                "private_bank_account": "1234-5678-9012",
                                "raw_prompt": "ignore previous instructions",
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        updates = await WeixinILinkClient(http).get_updates(_credentials(), "cursor-before")

    obs = updates.observation
    assert obs is not None
    assert obs.raw_count == 1
    assert obs.accepted_count == 0
    assert obs.ignored_count == 1
    assert obs.message_type_counts == {"unknown": 1}
    assert obs.item_type_counts == {"unknown": 1}

    summary = obs.to_summary()
    serialized = json.dumps(summary)

    for forbidden in (
        "DROP TABLE",
        "malicious_sender",
        "super_secret",
        "<script>",
        "private_bank_account",
        "1234-5678",
        "ignore previous instructions",
    ):
        assert forbidden not in serialized, f"Forbidden string '{forbidden}' leaked into summary!"


# ===========================================================================
# 2. End-to-end Management observation tests
# ===========================================================================


@pytest.mark.asyncio
async def test_management_persists_observation_and_no_chat_turn_for_unknown(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    _replace_management(container, store, transport)
    connection_id = uuid4()
    access_token = "g" * 43
    pending = _PendingEnrollment(
        auth_session_id=uuid4(),
        configuration=_configuration(connection_id),
        credentials=_credentials(access_token),
    )
    await store.set("weixin_ilink:pending-enrollment", pending.to_json())

    await container.start()
    try:
        await asyncio.wait_for(transport.started.wait(), timeout=2)
        session = await container.sessions.create_session("default")
        await container.external_channel_repository.create_binding(
            binding_id=uuid4(),
            connection_id=connection_id,
            conversation_key="owner-1",
            sender_key="owner-1",
            session_id=session.session_id,
            created_at=datetime.now(UTC),
        )

        # Send batch with unknown item type
        obs = WeixinInboundBatchObservation(
            raw_count=1,
            accepted_count=0,
            ignored_count=1,
            message_type_counts={"1": 1},
            item_type_counts={"47": 1},
            rejection_reasons={"unsupported_item_types": 1},
        )
        await transport.updates.put(
            WeixinUpdates(
                cursor="cursor-unknown-47",
                messages=(),
                observation=obs,
            )
        )

        # Wait for cursor to advance
        for _ in range(50):
            cur = await container.external_channel_repository.get_adapter_cursor(connection_id)
            if cur == "cursor-unknown-47":
                break
            await asyncio.sleep(0.02)

        assert (
            await container.external_channel_repository.get_adapter_cursor(connection_id)
            == "cursor-unknown-47"
        )

        # Verify: event is persisted in SQLite
        db = container.database
        rows = await db.fetchall(
            "SELECT * FROM events WHERE event_type = 'channel.inbound_batch_observed'"
        )
        assert len(rows) == 1
        event_row = rows[0]
        payload = json.loads(str(event_row["payload_json"]))
        assert payload["connection_id"] == str(connection_id)
        assert payload["summary"]["raw_count"] == 1
        assert payload["summary"]["accepted_count"] == 0
        assert payload["summary"]["ignored_count"] == 1
        assert payload["summary"]["item_type_counts"] == {"47": 1}
        assert payload["summary"]["rejection_reasons"] == {"unsupported_item_types": 1}

        # Verify: NO channel turns were created!
        turns = await container.external_channel_repository.list_inflight_turns(connection_id)
        assert len(turns) == 0

        # Verify: connection status remains READY
        conn = await container.external_channels.get_connection(connection_id)
        assert conn.status is ChannelConnectionStatus.READY

        # Verify: no messages sent to user
        assert len(transport.sent_messages) == 0
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_management_ordinary_supported_turn_unchanged(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    _replace_management(container, store, transport)
    connection_id = uuid4()
    access_token = "g" * 43
    pending = _PendingEnrollment(
        auth_session_id=uuid4(),
        configuration=_configuration(connection_id),
        credentials=_credentials(access_token),
    )
    await store.set("weixin_ilink:pending-enrollment", pending.to_json())

    # Mock LLM provider to complete cleanly
    async def fake_stream(request: LlmRequest) -> Any:
        del request
        yield LlmTextDelta(text="你好，主人！")
        yield LlmResponseCompleted(finish_reason="stop")

    monkeypatch.setattr(DemoLlmProvider, "stream", fake_stream)

    await container.start()
    try:
        await asyncio.wait_for(transport.started.wait(), timeout=2)
        session = await container.sessions.create_session("default")
        await container.external_channel_repository.create_binding(
            binding_id=uuid4(),
            connection_id=connection_id,
            conversation_key="owner-1",
            sender_key="owner-1",
            session_id=session.session_id,
            created_at=datetime.now(UTC),
        )

        obs = WeixinInboundBatchObservation(
            raw_count=1,
            accepted_count=1,
            ignored_count=0,
            message_type_counts={"1": 1},
            item_type_counts={"1": 1},
            rejection_reasons={},
        )
        await transport.updates.put(
            WeixinUpdates(
                cursor="cursor-valid-turn",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-valid-1",
                        sender_user_id="owner-1",
                        recipient_bot_id="bot-1",
                        text="你好",
                        context_token="ctx-valid-1",
                        received_at=datetime.now(UTC),
                    ),
                ),
                observation=obs,
            )
        )

        for _ in range(50):
            cur = await container.external_channel_repository.get_adapter_cursor(connection_id)
            if cur == "cursor-valid-turn":
                break
            await asyncio.sleep(0.02)

        assert (
            await container.external_channel_repository.get_adapter_cursor(connection_id)
            == "cursor-valid-turn"
        )

        # Observation event was persisted
        db = container.database
        rows = await db.fetchall(
            "SELECT * FROM events WHERE event_type = 'channel.inbound_batch_observed'"
        )
        assert len(rows) == 1
        payload = json.loads(str(rows[0]["payload_json"]))
        assert payload["summary"]["accepted_count"] == 1
        assert payload["summary"]["raw_count"] == 1

        # And a channel turn was created and admitted for the valid text message
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "msg-valid-1"
        )
        assert turn is not None
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_management_empty_batches_no_event_spam(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    _replace_management(container, store, transport)
    connection_id = uuid4()
    access_token = "g" * 43
    pending = _PendingEnrollment(
        auth_session_id=uuid4(),
        configuration=_configuration(connection_id),
        credentials=_credentials(access_token),
    )
    await store.set("weixin_ilink:pending-enrollment", pending.to_json())

    await container.start()
    try:
        await asyncio.wait_for(transport.started.wait(), timeout=2)
        session = await container.sessions.create_session("default")
        await container.external_channel_repository.create_binding(
            binding_id=uuid4(),
            connection_id=connection_id,
            conversation_key="owner-1",
            sender_key="owner-1",
            session_id=session.session_id,
            created_at=datetime.now(UTC),
        )

        # Send empty poll batch with observation raw_count=0
        obs = WeixinInboundBatchObservation(
            raw_count=0,
            accepted_count=0,
            ignored_count=0,
            message_type_counts={},
            item_type_counts={},
            rejection_reasons={},
        )
        await transport.updates.put(
            WeixinUpdates(
                cursor="cursor-empty-batch",
                messages=(),
                observation=obs,
            )
        )

        for _ in range(50):
            cur = await container.external_channel_repository.get_adapter_cursor(connection_id)
            if cur == "cursor-empty-batch":
                break
            await asyncio.sleep(0.02)

        assert (
            await container.external_channel_repository.get_adapter_cursor(connection_id)
            == "cursor-empty-batch"
        )

        # No event was persisted for empty poll
        db = container.database
        rows = await db.fetchall(
            "SELECT * FROM events WHERE event_type = 'channel.inbound_batch_observed'"
        )
        assert len(rows) == 0
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_management_observational_failure_does_not_compromise_cursor(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    _replace_management(container, store, transport)
    connection_id = uuid4()
    access_token = "g" * 43
    pending = _PendingEnrollment(
        auth_session_id=uuid4(),
        configuration=_configuration(connection_id),
        credentials=_credentials(access_token),
    )
    await store.set("weixin_ilink:pending-enrollment", pending.to_json())

    # Simulate EventPublisher failure during observation emission
    async def failing_emit(event: Any) -> Any:
        if getattr(event, "event_type", None) == "channel.inbound_batch_observed":
            raise RuntimeError("Injected observation persistence failure")
        return event

    monkeypatch.setattr(container.event_publisher, "emit", failing_emit)

    await container.start()
    try:
        await asyncio.wait_for(transport.started.wait(), timeout=2)
        session = await container.sessions.create_session("default")
        await container.external_channel_repository.create_binding(
            binding_id=uuid4(),
            connection_id=connection_id,
            conversation_key="owner-1",
            sender_key="owner-1",
            session_id=session.session_id,
            created_at=datetime.now(UTC),
        )

        obs = WeixinInboundBatchObservation(
            raw_count=1,
            accepted_count=0,
            ignored_count=1,
            message_type_counts={"1": 1},
            item_type_counts={"47": 1},
            rejection_reasons={"unsupported_item_types": 1},
        )
        await transport.updates.put(
            WeixinUpdates(
                cursor="cursor-resilient",
                messages=(),
                observation=obs,
            )
        )

        for _ in range(50):
            cur = await container.external_channel_repository.get_adapter_cursor(connection_id)
            if cur == "cursor-resilient":
                break
            await asyncio.sleep(0.02)

        # Cursor MUST still advance despite observational emission failure!
        assert (
            await container.external_channel_repository.get_adapter_cursor(connection_id)
            == "cursor-resilient"
        )

        # Connection status MUST remain READY
        conn = await container.external_channels.get_connection(connection_id)
        assert conn.status is ChannelConnectionStatus.READY
    finally:
        await container.stop()


def test_observation_histograms_are_bounded_and_immutable() -> None:
    from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import (
        _build_batch_observation,
    )

    obs = _build_batch_observation(
        [{"message_type": n, "item_list": [{"type": n}]} for n in range(100)],
        rejected_indices=set(),
        expected_bot_id="bot",
    )
    assert len(obs.message_type_counts) <= 32
    assert len(obs.item_type_counts) <= 32
    assert sum(obs.message_type_counts.values()) == 100
    assert sum(obs.item_type_counts.values()) == 100
    original = {"1": 1}
    frozen = WeixinInboundBatchObservation(1, 1, 0, message_type_counts=original)
    original["1"] = 99
    assert frozen.message_type_counts == {"1": 1}
    with pytest.raises(TypeError):
        frozen.message_type_counts["1"] = 2  # type: ignore[index]


@pytest.mark.asyncio
async def test_unbound_observation_never_creates_session_or_binding(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = _replace_management(container, store, transport)
    connection_id = uuid4()
    pending = _PendingEnrollment(
        auth_session_id=uuid4(),
        configuration=_configuration(connection_id),
        credentials=_credentials(),
    )
    await store.set("weixin_ilink:pending-enrollment", pending.to_json())
    await container.start()
    try:
        await asyncio.wait_for(transport.started.wait(), timeout=2)
        before = await container.database.fetchall("SELECT session_id FROM sessions")
        await management._record_inbound_batch_observation(
            connection_id,
            WeixinInboundBatchObservation(1, 0, 1),
        )
        assert await container.database.fetchall("SELECT session_id FROM sessions") == before
        assert (
            await container.external_channel_repository.find_binding(connection_id, "owner-1")
            is None
        )
        assert not transport.sent_messages
    finally:
        await container.stop()
