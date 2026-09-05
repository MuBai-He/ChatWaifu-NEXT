# pyright: reportPrivateUsage=false
"""Native wire -> management -> real generation -> durable reply integration."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from chatwaifu_protocol.channels import ChannelDeliveryPartAcknowledgement, ChannelDeliveryStatus
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import WeixinILinkClient
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.image import encrypt_aes_128_ecb
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import WeixinInboundImage
from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
from chatwaifu_runtime.external_channels.management import ChannelManagementService
from chatwaifu_runtime.external_channels.models import DeliveryTransitionResult
from PIL import Image
from test_channel_management import _configuration, _credentials, _FakeWeixin
from test_inbound_image_lifecycle import VisionRecorder


class InboundTransport(_FakeWeixin):
    def __init__(self, wire: WeixinILinkClient) -> None:
        super().__init__()
        self.wire = wire
        self.download_count = 0

    async def download_image(self, image: WeixinInboundImage) -> tuple[bytes, str]:
        self.download_count += 1
        return await self.wire.download_image(image)


@pytest.mark.asyncio
async def test_native_image_to_reply_and_private_context_cleanup(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    plain = buffer.getvalue()
    key = b"0123456789abcdef"
    encrypted = encrypt_aes_128_ecb(plain, key)
    queried: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        queried.append(request.url.path)
        if request.url.path.endswith("getupdates"):
            return httpx.Response(
                200,
                json={
                    "ret": 0,
                    "get_updates_buf": "image-cursor",
                    "msgs": [
                        {
                            "message_type": 1,
                            "message_state": 2,
                            "message_id": 17031,
                            "from_user_id": "owner-1",
                            "to_user_id": "bot-1",
                            "context_token": "private-image-context",
                            "item_list": [
                                {
                                    "type": 2,
                                    "image_item": {
                                        "aeskey": key.hex(),
                                        "media": {"encrypt_query_param": "private-cdn-query"},
                                    },
                                }
                            ],
                        }
                    ],
                },
            )
        assert request.url.path == "/c2c/download"
        assert request.url.params["encrypted_query_param"] == "private-cdn-query"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(200, content=encrypted)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        wire = WeixinILinkClient(http)
        container = RuntimeContainer(runtime_settings)
        transport = InboundTransport(wire)
        store = InMemoryChannelCredentialStore()
        management = ChannelManagementService(
            container.external_channels,
            container.external_channel_repository,
            store,
            transport,
            event_hub=container.event_hub,
            event_publisher=container.event_publisher,
        )
        container.channel_management = management
        recorder = VisionRecorder()
        monkeypatch.setattr(container.agent, "_llm", recorder)
        acknowledged = asyncio.Event()
        original_ack = container.external_channel_repository.acknowledge_delivery_part
        results: list[DeliveryTransitionResult] = []

        async def observe_ack(
            acknowledgement: ChannelDeliveryPartAcknowledgement, *, updated_at: datetime
        ) -> DeliveryTransitionResult:
            result = await original_ack(acknowledgement, updated_at=updated_at)
            results.append(result)
            acknowledged.set()
            return result

        monkeypatch.setattr(
            container.external_channel_repository, "acknowledge_delivery_part", observe_ack
        )
        await container.start()
        try:
            conn_id = uuid4()
            created = await container.external_channels.create_connection(
                _configuration(conn_id), access_token="g" * 43
            )
            credentials = _credentials("g" * 43)
            await store.set(f"weixin_ilink:{conn_id}", credentials.to_json())
            await management.connection_configuration_changed(created.snapshot)
            updates = await wire.get_updates(credentials, "")
            await transport.updates.put(updates)
            await asyncio.wait_for(acknowledged.wait(), 5)
            assert len(results) == 1 and results[0].plan.status is ChannelDeliveryStatus.DELIVERED
            assert transport.download_count == 1
            assert recorder.requests[0].images[0].data == plain
            assert transport.sent_messages[0]["text"] == "看见了，一只小猫。"
            turn = await container.external_channel_repository.find_turn_by_external_message(
                conn_id, "17031"
            )
            assert turn is not None
            serialized = json.dumps(
                await container.event_store.read_stream(turn.session_id, limit=200), default=str
            )
            for secret in ("private-cdn-query", "private-image-context", key.hex()):
                assert secret not in serialized
            # Replayed provider updates are admitted idempotently, with no second download or reply.
            await management._process_updates(conn_id, updates)
            assert transport.download_count == 1 and len(transport.sent_messages) == 1
            restored = await management._load_credentials(conn_id)
            assert restored is not None and "17031" not in restored.pending_contexts
            assert queried == ["/ilink/bot/getupdates", "/c2c/download"]
        finally:
            await container.stop()
