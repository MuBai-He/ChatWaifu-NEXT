"""Service-level and end-to-end multipart delivery tests (Phase 17.1A)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelConnectionConfiguration,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelInboundTextMessage,
    ChannelPresentationPolicy,
    ChannelTextDeliveryPartPayload,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinAuthorizationPoll,
    WeixinAuthorizationStart,
    WeixinAuthorizationState,
    WeixinCredentials,
    WeixinInboundText,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
from chatwaifu_runtime.external_channels.management import ChannelManagementService
from chatwaifu_runtime.external_channels.service import (
    SingleTextDeliveryPlanFactory,
)
from chatwaifu_runtime.main import create_app
from httpx import ASGITransport, AsyncClient


class _ThreePartsPlanFactory:
    """Test plan factory that splits reply text into three sequential text parts."""

    def create_parts(
        self,
        reply_text: str,
        policy: ChannelPresentationPolicy | None = None,
    ) -> tuple[ChannelDeliveryPartDraft, ...]:
        return (
            ChannelDeliveryPartDraft(
                ordinal=0,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(
                    kind=ChannelDeliveryPartKind.TEXT,
                    text=f"[Part 1/3] {reply_text}",
                ),
                required=True,
                delay_after_ms=0,
                not_before_at=None,
            ),
            ChannelDeliveryPartDraft(
                ordinal=1,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(
                    kind=ChannelDeliveryPartKind.TEXT,
                    text="[Part 2/3] Second bubble",
                ),
                required=True,
                delay_after_ms=0,
                not_before_at=None,
            ),
            ChannelDeliveryPartDraft(
                ordinal=2,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(
                    kind=ChannelDeliveryPartKind.TEXT,
                    text="[Part 3/3] Third bubble",
                ),
                required=True,
                delay_after_ms=0,
                not_before_at=None,
            ),
        )


class _FakeWeixin:
    def __init__(self) -> None:
        self.updates: asyncio.Queue[WeixinUpdates] = asyncio.Queue()
        self.sent_messages: list[dict[str, str]] = []
        self.sent = asyncio.Event()

    async def close(self) -> None:
        pass

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
        return WeixinAuthorizationPoll(state=WeixinAuthorizationState.CONFIRMED)

    async def notify_start(self, credentials: WeixinCredentials) -> None:
        del credentials

    async def notify_stop(self, credentials: WeixinCredentials) -> None:
        del credentials

    async def get_updates(self, credentials: WeixinCredentials, cursor: str) -> WeixinUpdates:
        del credentials, cursor
        return await self.updates.get()

    async def get_typing_ticket(
        self, credentials: WeixinCredentials, *, recipient_user_id: str, context_token: str
    ) -> str | None:
        return None

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
        return f"prov-msg-{client_id}"

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
        return f"prov-img-{client_id}"


def _configuration(connection_id: UUID) -> ChannelConnectionConfiguration:
    return ChannelConnectionConfiguration(
        connection_id=connection_id,
        provider_id="weixin_ilink",
        name="我的微信",
        character_id="default",
        principal_scope="local",
        account_key="bot-owner",
        allowed_sender_keys=["sender-user-1"],
        enabled=True,
    )


def _message(connection_id: UUID, external_message_id: str = "msg-1") -> ChannelInboundTextMessage:
    return ChannelInboundTextMessage(
        connection_id=connection_id,
        external_message_id=external_message_id,
        principal_scope="local",
        account_key="bot-owner",
        conversation_key="user-conversation-1",
        sender_key="sender-user-1",
        sender_display_name="木白",
        text="请问今天天气怎么样？",
        received_at=datetime.now(UTC),
    )


def _credentials(access_token: str) -> WeixinCredentials:
    return WeixinCredentials(
        bot_token="test-bot-token",
        bot_id="bot-owner",
        user_id="sender-user-1",
        base_url="https://api.weixin.qq.com/",
        gateway_access_token=access_token,
    )


def test_single_text_delivery_plan_factory_defaults() -> None:
    factory = SingleTextDeliveryPlanFactory()
    parts = factory.create_parts("Hello world")
    assert len(parts) == 1
    assert parts[0].ordinal == 0
    assert parts[0].kind is ChannelDeliveryPartKind.TEXT
    assert isinstance(parts[0].payload, ChannelTextDeliveryPartPayload)
    assert parts[0].payload.text == "Hello world"
    assert parts[0].required is True
    assert parts[0].delay_after_ms == 0
    assert parts[0].not_before_at is None

    empty_parts = factory.create_parts("")
    assert len(empty_parts) == 1
    assert isinstance(empty_parts[0].payload, ChannelTextDeliveryPartPayload)
    assert empty_parts[0].payload.text == "(empty reply)"


@pytest.mark.asyncio
async def test_multipart_service_flow_and_events(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    container.external_channels.delivery_plan_factory = _ThreePartsPlanFactory()
    emitted_events: list[Any] = []
    original_publish = container.event_publisher.publish_persisted

    async def _capture_event(event: Any) -> Any:
        emitted_events.append(event)
        return await original_publish(event)

    monkeypatch.setattr(container.event_publisher, "publish_persisted", _capture_event)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        receipt = await container.external_channels.ingest(
            _message(connection_id, external_message_id="multipart-flow-1"),
            access_token=created.access_token,
        )
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert snapshot.delivery_id is not None
        delivery_id = snapshot.delivery_id

        # Verify plan snapshot
        plan = await container.external_channels.get_delivery_plan(
            connection_id, delivery_id, access_token=created.access_token
        )
        assert plan.part_count == 3
        assert plan.delivered_part_count == 0
        assert plan.next_pending_ordinal == 0
        assert plan.status is ChannelDeliveryStatus.PENDING
        assert len(plan.parts) == 3
        assert plan.parts[0].provider_client_id == f"chatwaifu-{delivery_id.hex}-000"
        assert plan.parts[1].provider_client_id == f"chatwaifu-{delivery_id.hex}-001"
        assert plan.parts[2].provider_client_id == f"chatwaifu-{delivery_id.hex}-002"

        # Check delivery_plan_created event was emitted
        created_events = [
            e
            for e in emitted_events
            if getattr(e, "event_type", None) == "channel.delivery_plan_created"
        ]
        assert len(created_events) == 1
        assert created_events[0].payload["part_count"] == 3

        # Claim and deliver part 0
        lease_0 = uuid4()
        p0 = await container.external_channels.claim_next_delivery_part(
            connection_id,
            delivery_id,
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=lease_0, lease_seconds=30
            ),
            access_token=created.access_token,
        )
        assert p0 is not None
        assert p0.ordinal == 0
        assert p0.status is ChannelDeliveryPartStatus.SENDING

        ack_0 = await container.external_channels.acknowledge_delivery_part(
            connection_id,
            delivery_id,
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=p0.part_id,
                lease_id=lease_0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="prov-p0",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=created.access_token,
        )
        assert ack_0.status is ChannelDeliveryPartStatus.DELIVERED

        # Plan still pending/in-progress
        plan = await container.external_channels.get_delivery_plan(
            connection_id, delivery_id, access_token=created.access_token
        )
        assert plan.delivered_part_count == 1
        assert plan.next_pending_ordinal == 1
        assert plan.status is ChannelDeliveryStatus.PENDING

        # Claim and deliver part 1
        lease_1 = uuid4()
        p1 = await container.external_channels.claim_next_delivery_part(
            connection_id,
            delivery_id,
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=lease_1, lease_seconds=30
            ),
            access_token=created.access_token,
        )
        assert p1 is not None
        assert p1.ordinal == 1

        await container.external_channels.acknowledge_delivery_part(
            connection_id,
            delivery_id,
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=p1.part_id,
                lease_id=lease_1,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="prov-p1",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=created.access_token,
        )

        # Claim and deliver part 2 (final part)
        lease_2 = uuid4()
        p2 = await container.external_channels.claim_next_delivery_part(
            connection_id,
            delivery_id,
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=lease_2, lease_seconds=30
            ),
            access_token=created.access_token,
        )
        assert p2 is not None
        assert p2.ordinal == 2

        await container.external_channels.acknowledge_delivery_part(
            connection_id,
            delivery_id,
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=p2.part_id,
                lease_id=lease_2,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="prov-p2",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=created.access_token,
        )

        # Plan is now DELIVERED
        plan = await container.external_channels.get_delivery_plan(
            connection_id, delivery_id, access_token=created.access_token
        )
        assert plan.status is ChannelDeliveryStatus.DELIVERED
        assert plan.delivered_part_count == 3
        assert plan.next_pending_ordinal is None

        # Verify completed event was emitted
        completed_events = [
            e
            for e in emitted_events
            if getattr(e, "event_type", None) == "channel.delivery_plan_completed"
        ]
        assert len(completed_events) == 1
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_multipart_tail_cancellation_service_level(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    container.external_channels.delivery_plan_factory = _ThreePartsPlanFactory()
    emitted_events: list[Any] = []
    original_publish = container.event_publisher.publish_persisted

    async def _capture_event(event: Any) -> Any:
        emitted_events.append(event)
        return await original_publish(event)

    monkeypatch.setattr(container.event_publisher, "publish_persisted", _capture_event)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        receipt = await container.external_channels.ingest(
            _message(connection_id, external_message_id="tail-cancel-1"),
            access_token=created.access_token,
        )
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert snapshot.delivery_id is not None
        delivery_id = snapshot.delivery_id

        # Deliver Part 0
        lease_0 = uuid4()
        p0 = await container.external_channels.claim_next_delivery_part(
            connection_id,
            delivery_id,
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=lease_0, lease_seconds=30
            ),
            access_token=created.access_token,
        )
        assert p0 is not None
        await container.external_channels.acknowledge_delivery_part(
            connection_id,
            delivery_id,
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=p0.part_id,
                lease_id=lease_0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="p0-done",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=created.access_token,
        )

        # Cancel remaining tail parts (Part 1 and 2)
        plan_cancelled = await container.external_channels.cancel_remaining_delivery_parts(
            connection_id,
            delivery_id,
            ChannelDeliveryPartsCancelRequest(
                reason="User interrupted conversation with new message",
                requested_at=datetime.now(UTC),
            ),
            access_token=created.access_token,
        )
        assert plan_cancelled.status is ChannelDeliveryStatus.CANCELLED
        assert plan_cancelled.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
        assert plan_cancelled.parts[1].status is ChannelDeliveryPartStatus.CANCELLED
        assert plan_cancelled.parts[2].status is ChannelDeliveryPartStatus.CANCELLED
        assert plan_cancelled.delivered_part_count == 1

        # No more parts can be claimed
        assert (
            await container.external_channels.claim_next_delivery_part(
                connection_id,
                delivery_id,
                ChannelDeliveryPartClaimRequest(
                    delivery_id=delivery_id, part_id=None, lease_id=uuid4(), lease_seconds=30
                ),
                access_token=created.access_token,
            )
            is None
        )

        # Verify cancel event emitted
        cancel_events = [
            e
            for e in emitted_events
            if getattr(e, "event_type", None) == "channel.delivery_plan_cancelled"
        ]
        assert len(cancel_events) == 1
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_weixin_management_loop_multipart_sequential_delivery(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    container.external_channels.delivery_plan_factory = _ThreePartsPlanFactory()
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
    )
    container.channel_management = management
    await container.start()

    connection_id = uuid4()
    access_token = "g" * 43
    created = await container.external_channels.create_connection(
        _configuration(connection_id), access_token=access_token
    )
    await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())

    cursor_advanced = asyncio.Event()
    original_set_cursor = container.external_channel_repository.set_adapter_cursor

    async def observed_set_cursor(
        target_connection_id: UUID,
        *,
        cursor: str,
        updated_at: datetime,
    ) -> None:
        await original_set_cursor(
            target_connection_id,
            cursor=cursor,
            updated_at=updated_at,
        )
        cursor_advanced.set()

    monkeypatch.setattr(
        container.external_channel_repository,
        "set_adapter_cursor",
        observed_set_cursor,
    )

    await management.connection_configuration_changed(created.snapshot)
    await transport.updates.put(
        WeixinUpdates(
            cursor="cursor-multi-after",
            messages=(
                WeixinInboundText(
                    external_message_id="msg-multi-1",
                    sender_user_id="sender-user-1",
                    recipient_bot_id="bot-owner",
                    text="讲个故事吧",
                    context_token="story-context-token",
                    received_at=datetime.now(UTC),
                ),
            ),
        )
    )

    try:
        await asyncio.wait_for(cursor_advanced.wait(), timeout=10)

        # With decoupled architecture, cursor advances immediately upon durable admission.
        # Wait for background scheduler to deliver all 3 parts.
        for _ in range(50):
            if len(transport.sent_messages) >= 3:
                break
            await asyncio.sleep(0.1)

        # All 3 parts must have been sent in sequential order
        assert len(transport.sent_messages) == 3
        part_0_sent = transport.sent_messages[0]
        part_1_sent = transport.sent_messages[1]
        part_2_sent = transport.sent_messages[2]

        assert "[Part 1/3]" in part_0_sent["text"]
        assert "[Part 2/3]" in part_1_sent["text"]
        assert "[Part 3/3]" in part_2_sent["text"]

        # All parts share the same context token and recipient
        assert part_0_sent["context_token"] == "story-context-token"
        assert part_1_sent["context_token"] == "story-context-token"
        assert part_2_sent["context_token"] == "story-context-token"
        assert part_0_sent["recipient_user_id"] == "sender-user-1"
        assert part_1_sent["recipient_user_id"] == "sender-user-1"
        assert part_2_sent["recipient_user_id"] == "sender-user-1"

        # Stable unique client IDs with ordinal suffix
        assert part_0_sent["client_id"].endswith("-000")
        assert part_1_sent["client_id"].endswith("-001")
        assert part_2_sent["client_id"].endswith("-002")

        # After all parts delivered, context cleared and cursor advanced
        serialized: str | None = None
        for _ in range(50):
            serialized = await store.get(f"weixin_ilink:{connection_id}")
            if (
                serialized is not None
                and WeixinCredentials.from_json(serialized).pending_contexts == {}
            ):
                break
            await asyncio.sleep(0.1)
        assert serialized is not None
        assert WeixinCredentials.from_json(serialized).pending_contexts == {}
        assert (
            await container.external_channel_repository.get_adapter_cursor(connection_id)
            == "cursor-multi-after"
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_weixin_management_loop_mismatched_image_payload_fails_closed(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _FakeWeixin()
    management = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
    )
    container.channel_management = management

    from dataclasses import replace

    from chatwaifu_runtime.external_channels.models import DeliveryTransitionResult

    original_claim = container.external_channel_repository.claim_next_delivery_part

    async def _claim_with_mismatched_payload(*args: Any, **kwargs: Any) -> Any:
        res = await original_claim(*args, **kwargs)
        if res is not None and res.part is not None:
            fake_part = replace(res.part, kind=ChannelDeliveryPartKind.IMAGE)
            return DeliveryTransitionResult(
                plan=res.plan,
                part=fake_part,
                applied=res.applied,
                persisted_events=res.persisted_events,
            )
        return res

    monkeypatch.setattr(
        container.external_channel_repository,
        "claim_next_delivery_part",
        _claim_with_mismatched_payload,
    )
    await container.start()

    connection_id = uuid4()
    access_token = "g" * 43
    created = await container.external_channels.create_connection(
        _configuration(connection_id), access_token=access_token
    )
    await store.set(f"weixin_ilink:{connection_id}", _credentials(access_token).to_json())

    await management.connection_configuration_changed(created.snapshot)
    await transport.updates.put(
        WeixinUpdates(
            cursor="cursor-fail",
            messages=(
                WeixinInboundText(
                    external_message_id="msg-unsupported-1",
                    sender_user_id="sender-user-1",
                    recipient_bot_id="bot-owner",
                    text="发个图片",
                    context_token="img-context-token",
                    received_at=datetime.now(UTC),
                ),
            ),
        )
    )

    try:
        # Wait until turn is completed and delivery attempted
        turn = None
        for _ in range(50):
            turn = await container.external_channel_repository.find_turn_by_external_message(
                connection_id, "msg-unsupported-1"
            )
            if turn and turn.delivery_id:
                plan = await container.external_channel_repository.get_delivery_plan(
                    turn.delivery_id
                )
                if plan and plan.status is ChannelDeliveryStatus.FAILED:
                    break
            await asyncio.sleep(0.1)

        assert turn is not None
        assert turn.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert plan is not None
        assert plan.status is ChannelDeliveryStatus.FAILED
        assert plan.parts[0].status is ChannelDeliveryPartStatus.FAILED
        assert plan.parts[0].last_error is not None
        assert plan.parts[0].last_error.code == "invalid_sticker_payload"

        # Transport should not have sent any text
        assert len(transport.sent_messages) == 0
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_multipart_http_api_endpoints(runtime_settings: Settings) -> None:
    app = create_app(runtime_settings)
    container = app.state.container
    container.external_channels.delivery_plan_factory = _ThreePartsPlanFactory()
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        receipt = await container.external_channels.ingest(
            _message(connection_id, external_message_id="http-api-turn"),
            access_token=created.access_token,
        )
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert snapshot.delivery_id is not None
        delivery_id = snapshot.delivery_id

        headers = {"Authorization": f"Bearer {created.access_token}"}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            # 1. GET plan without auth -> 401
            res = await client.get(
                f"/v1/channel-connections/{connection_id}/deliveries/{delivery_id}/plan"
            )
            assert res.status_code == 401

            # 2. GET plan with auth -> 200
            res = await client.get(
                f"/v1/channel-connections/{connection_id}/deliveries/{delivery_id}/plan",
                headers=headers,
            )
            assert res.status_code == 200
            plan_data = res.json()
            assert plan_data["delivery_id"] == str(delivery_id)
            assert plan_data["part_count"] == 3
            assert len(plan_data["parts"]) == 3

            # 3. GET parts -> 200
            res = await client.get(
                f"/v1/channel-connections/{connection_id}/deliveries/{delivery_id}/parts",
                headers=headers,
            )
            assert res.status_code == 200
            parts_data = res.json()
            assert len(parts_data) == 3
            assert parts_data[0]["ordinal"] == 0

            # 4. POST claim -> 200
            lease_id = uuid4()
            claim_payload = {
                "delivery_id": str(delivery_id),
                "lease_id": str(lease_id),
                "lease_seconds": 30,
            }
            res = await client.post(
                f"/v1/channel-connections/{connection_id}/deliveries/{delivery_id}/parts/claim",
                headers=headers,
                json=claim_payload,
            )
            assert res.status_code == 200
            part_0_data = res.json()
            assert part_0_data["status"] == "sending"
            assert part_0_data["ordinal"] == 0

            # 5. POST ack -> 200
            ack_payload = {
                "delivery_id": str(delivery_id),
                "part_id": part_0_data["part_id"],
                "lease_id": str(lease_id),
                "status": "delivered",
                "provider_message_id": "http-p0-msg",
                "acknowledged_at": datetime.now(UTC).isoformat(),
            }
            res = await client.post(
                f"/v1/channel-connections/{connection_id}/deliveries/{delivery_id}/parts/ack",
                headers=headers,
                json=ack_payload,
            )
            assert res.status_code == 200
            acked_part = res.json()
            assert acked_part["status"] == "delivered"

            # 6. POST cancel -> 200
            cancel_payload = {
                "reason": "Test HTTP cancel",
                "requested_at": datetime.now(UTC).isoformat(),
            }
            res = await client.post(
                f"/v1/channel-connections/{connection_id}/deliveries/{delivery_id}/parts/cancel",
                headers=headers,
                json=cancel_payload,
            )
            assert res.status_code == 200
            cancelled_plan = res.json()
            assert cancelled_plan["status"] == "cancelled"
            assert cancelled_plan["delivered_part_count"] == 1
    finally:
        await container.stop()
