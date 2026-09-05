"""Comprehensive tests for ChannelDeliveryScheduler, Crash Recovery, Retry,

Legacy APIs, Transactional Event Rollback, Early Cursor Advance,
and Tail Cancellation (Phase 17.1A).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiosqlite
import pytest
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelDeliveryAcknowledgement,
    ChannelDeliveryClaimRequest,
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
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_protocol.events import EventModel
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinAuthorizationPoll,
    WeixinAuthorizationStart,
    WeixinAuthorizationState,
    WeixinCredentials,
    WeixinInboundText,
    WeixinPendingContext,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
from chatwaifu_runtime.external_channels.management import (
    ChannelManagementService,
)
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.scheduler import (
    ChannelDeliveryScheduler,
    DeliveryPartExecutionResult,
    DeliveryPartExecutor,
    DeliveryPartOutcome,
)
from chatwaifu_runtime.external_channels.service import (
    ChannelDeliveryMultipartConflictError,
)
from chatwaifu_runtime.main import create_app
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    SQLiteExternalChannelRepository,
)
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError


class _ThreePartsPlanFactory:
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


class _RecordingExecutor(DeliveryPartExecutor):
    def __init__(self, outcome: DeliveryPartOutcome = DeliveryPartOutcome.DELIVERED) -> None:
        self.outcome = outcome
        self.executed_parts: list[tuple[UUID, int, str]] = []
        self.execution_event = asyncio.Event()

    async def execute_part(
        self,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
    ) -> DeliveryPartExecutionResult:
        self.executed_parts.append((plan.delivery_id, part.ordinal, part.provider_client_id))
        self.execution_event.set()
        if self.outcome is DeliveryPartOutcome.DELIVERED:
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.DELIVERED,
                provider_message_id=f"prov-{part.provider_client_id}",
            )
        elif self.outcome is DeliveryPartOutcome.RETRYABLE_ERROR:
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.RETRYABLE_ERROR,
                error=StructuredError(
                    code="rate_limit_exceeded",
                    message="Temporary rate limit hit",
                    retryable=True,
                    component="external_channels",
                ),
            )
        else:
            return DeliveryPartExecutionResult(
                outcome=DeliveryPartOutcome.FATAL_ERROR,
                error=StructuredError(
                    code="fatal_delivery_error",
                    message="Permanent rejection",
                    retryable=False,
                    component="external_channels",
                ),
            )


class _FailingEventStore(EventStore):
    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self.fail = False

    async def append_in_transaction[EventT: EventModel](
        self, connection: aiosqlite.Connection, event: EventT
    ) -> EventT:
        if self.fail:
            raise RuntimeError("simulated EventStore write failure")
        return await super().append_in_transaction(connection, event)


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
        account_key="bot-owner",
        external_message_id=external_message_id,
        conversation_key="chat-user-1",
        sender_key="sender-user-1",
        principal_scope="local",
        chat_type=ChannelChatType.DIRECT,
        text="宁宁，下午好！",
        received_at=datetime.now(UTC),
    )


async def _setup_test_environment(
    tmp_path: Path,
) -> tuple[Database, SQLiteExternalChannelRepository, EventStore, EventHub, UUID, UUID, UUID]:
    db_path = tmp_path / "scheduler_test.db"
    storage = StorageConfig(database_path=db_path)
    database = Database(db_path, storage)
    await database.open()

    event_store = EventStore(database)
    event_hub = EventHub()
    repo = SQLiteExternalChannelRepository(database, event_store)

    now = datetime.now(UTC)
    connection_id = uuid4()
    session_id = uuid4()
    binding_id = uuid4()

    await repo.create_connection(
        _configuration(connection_id),
        access_token_hash="hash-123",
        created_at=now,
    )

    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions(
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'default', 'ready', 'idle', 0, 1, ?, ?)
            """,
            (str(session_id), now.isoformat(), now.isoformat()),
        )

    await repo.create_binding(
        binding_id=binding_id,
        connection_id=connection_id,
        conversation_key="chat-user-1",
        sender_key="sender-user-1",
        session_id=session_id,
        created_at=now,
    )

    return database, repo, event_store, event_hub, connection_id, session_id, binding_id


async def _create_plan_with_parts(
    repo: SQLiteExternalChannelRepository,
    connection_id: UUID,
    session_id: UUID,
    binding_id: UUID,
    texts: tuple[str, ...],
    *,
    created_at: datetime | None = None,
) -> tuple[ChannelTurnRecord, UUID]:
    now = created_at if created_at is not None else datetime.now(UTC)
    turn_id = uuid4()
    delivery_id = uuid4()

    turn = ChannelTurnRecord(
        channel_turn_id=turn_id,
        connection_id=connection_id,
        binding_id=binding_id,
        external_message_id=f"ext-{uuid4().hex[:8]}",
        content_sha256="fake-sha",
        account_key="bot-owner",
        conversation_key="chat-user-1",
        chat_type=ChannelChatType.DIRECT,
        conversation_label="Test Chat",
        sender_key="sender-user-1",
        sender_display_name="User",
        principal_scope="local",
        session_id=session_id,
        turn_id=uuid4(),
        generation_id=uuid4(),
        status=ChannelTurnStatus.ACCEPTED,
        reply_text=None,
        error=None,
        delivery_id=None,
        delivery_status=None,
        revision=0,
        accepted_at=now,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    await repo.create_turn(turn)

    draft_parts = tuple(
        ChannelDeliveryPartDraft(
            ordinal=idx,
            kind=ChannelDeliveryPartKind.TEXT,
            payload=ChannelTextDeliveryPartPayload(
                kind=ChannelDeliveryPartKind.TEXT,
                text=text,
            ),
            required=True,
            delay_after_ms=0,
            not_before_at=None,
        )
        for idx, text in enumerate(texts)
    )

    res = await repo.complete_turn(
        turn_id,
        reply_text=" ".join(texts),
        delivery_id=delivery_id,
        completed_at=now,
        parts=draft_parts,
    )
    return res.turn, delivery_id


@pytest.mark.asyncio
async def test_real_reboot_recovery_unexpired_lease_to_automatic_delivery(tmp_path: Path) -> None:
    """Requirement 4: Runtime restarts while lease is unexpired; once expired,
    recovers to pending and automatically delivers all parts without new messages.
    """
    (
        database,
        repo,
        event_store,
        event_hub,
        connection_id,
        session_id,
        binding_id,
    ) = await _setup_test_environment(tmp_path)
    publisher = EventPublisher(event_store, event_hub)
    try:
        # Create a 2-part delivery plan
        _turn, delivery_id = await _create_plan_with_parts(
            repo, connection_id, session_id, binding_id, ("Part 0 text", "Part 1 text")
        )

        # Simulate Part 0 was claimed right before crash with lease expiring in 0.4s
        now = datetime.now(UTC)
        lease_id = uuid4()
        claim = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=lease_id,
            lease_seconds=5,
        )
        claim_res = await repo.claim_next_delivery_part(claim, claimed_at=now)
        assert claim_res is not None
        assert claim_res.part is not None
        assert claim_res.part.status is ChannelDeliveryPartStatus.SENDING

        # Manually set lease_expires_at to now + 0.4s
        async with database.transaction() as conn:
            await conn.execute(
                "UPDATE channel_delivery_parts SET lease_expires_at = ? WHERE part_id = ?",
                ((now + timedelta(seconds=0.4)).isoformat(), str(claim_res.part.part_id)),
            )

        # Simulate Runtime reboot: start a fresh Scheduler
        executor = _RecordingExecutor(outcome=DeliveryPartOutcome.DELIVERED)
        scheduler = ChannelDeliveryScheduler(
            repository=repo,
            executor=executor,
            publisher=publisher,
            event_hub=event_hub,
            connection_id=connection_id,
            lease_seconds=5,
            poll_interval_seconds=0.1,
        )

        await scheduler.start()
        try:
            # Immediately after startup, while lease is unexpired (0.1s):
            await asyncio.sleep(0.1)
            assert len(executor.executed_parts) == 0

            # Wait for lease to expire and scheduler to step
            plan: ChannelDeliveryPlanRecord | None = None
            for _ in range(30):
                plan = await repo.get_delivery_plan(delivery_id)
                if plan and plan.status is ChannelDeliveryStatus.DELIVERED:
                    break
                await asyncio.sleep(0.1)

            assert plan is not None
            assert plan.status is ChannelDeliveryStatus.DELIVERED
            assert plan.delivered_part_count == 2
            assert len(executor.executed_parts) == 2

            # Verify Part 0 executed first with stable client ID, then Part 1
            p0_exec = executor.executed_parts[0]
            p1_exec = executor.executed_parts[1]
            assert p0_exec[1] == 0
            assert p0_exec[2] == f"chatwaifu-{delivery_id.hex}-000"
            assert p1_exec[1] == 1
            assert p1_exec[2] == f"chatwaifu-{delivery_id.hex}-001"
        finally:
            await scheduler.stop()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_automatic_wakeup_on_plan_created(tmp_path: Path) -> None:
    """Requirement 3 & 14: Scheduler wakes immediately on channel.delivery_plan_created
    via EventHub without waiting for long poll intervals.
    """
    (
        database,
        repo,
        event_store,
        event_hub,
        connection_id,
        session_id,
        binding_id,
    ) = await _setup_test_environment(tmp_path)
    publisher = EventPublisher(event_store, event_hub)
    try:
        executor = _RecordingExecutor(outcome=DeliveryPartOutcome.DELIVERED)
        # Configure scheduler with huge poll interval: 300 seconds
        scheduler = ChannelDeliveryScheduler(
            repository=repo,
            executor=executor,
            publisher=publisher,
            event_hub=event_hub,
            connection_id=connection_id,
            poll_interval_seconds=300.0,
        )
        await scheduler.start()
        try:
            now = datetime.now(UTC)
            turn_id = uuid4()
            delivery_id = uuid4()
            turn = ChannelTurnRecord(
                channel_turn_id=turn_id,
                connection_id=connection_id,
                binding_id=binding_id,
                external_message_id="ext-wakeup-1",
                content_sha256="fake-sha",
                account_key="bot-owner",
                conversation_key="chat-user-1",
                chat_type=ChannelChatType.DIRECT,
                conversation_label="Test Chat",
                sender_key="sender-user-1",
                sender_display_name="User",
                principal_scope="local",
                session_id=session_id,
                turn_id=uuid4(),
                generation_id=uuid4(),
                status=ChannelTurnStatus.ACCEPTED,
                reply_text=None,
                error=None,
                delivery_id=None,
                delivery_status=None,
                revision=0,
                accepted_at=now,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            await repo.create_turn(turn)
            parts = (
                ChannelDeliveryPartDraft(
                    ordinal=0,
                    kind=ChannelDeliveryPartKind.TEXT,
                    payload=ChannelTextDeliveryPartPayload(
                        kind=ChannelDeliveryPartKind.TEXT, text="Wakeup bubble"
                    ),
                    required=True,
                    delay_after_ms=0,
                    not_before_at=None,
                ),
            )
            res = await repo.complete_turn(
                turn_id,
                reply_text="Wakeup bubble",
                delivery_id=delivery_id,
                completed_at=now,
                parts=parts,
            )
            for ev in res.persisted_events:
                await publisher.publish_persisted(ev)

            # Wait a short moment (< 1.5s) - should wake up immediately and deliver
            await asyncio.wait_for(executor.execution_event.wait(), timeout=2.0)

            plan = await repo.get_delivery_plan(delivery_id)
            assert plan is not None
            assert plan.status is ChannelDeliveryStatus.DELIVERED
            assert len(executor.executed_parts) == 1
        finally:
            await scheduler.stop()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_retryable_error_backoff_and_max_attempts(tmp_path: Path) -> None:
    """Requirement 5: Retryable error defers part with not_before_at exponential backoff,
    and marks FAILED once max_attempts is exceeded.
    """
    (
        database,
        repo,
        event_store,
        event_hub,
        connection_id,
        session_id,
        binding_id,
    ) = await _setup_test_environment(tmp_path)
    publisher = EventPublisher(event_store, event_hub)
    try:
        _turn, delivery_id = await _create_plan_with_parts(
            repo, connection_id, session_id, binding_id, ("Retry part text",)
        )

        executor = _RecordingExecutor(outcome=DeliveryPartOutcome.RETRYABLE_ERROR)
        scheduler = ChannelDeliveryScheduler(
            repository=repo,
            executor=executor,
            publisher=publisher,
            event_hub=event_hub,
            connection_id=connection_id,
            max_attempts=3,
            initial_backoff_seconds=0.05,
            poll_interval_seconds=0.05,
        )

        await scheduler.start()
        try:
            plan: ChannelDeliveryPlanRecord | None = None
            for _ in range(50):
                plan = await repo.get_delivery_plan(delivery_id)
                if plan and plan.status is ChannelDeliveryStatus.FAILED:
                    break
                await asyncio.sleep(0.05)

            assert plan is not None
            assert plan.status is ChannelDeliveryStatus.FAILED
            assert plan.parts[0].status is ChannelDeliveryPartStatus.FAILED
            assert plan.parts[0].attempt == 3
            assert plan.parts[0].last_error is not None
            assert plan.parts[0].last_error.code == "rate_limit_exceeded"
            assert len(executor.executed_parts) == 3
        finally:
            await scheduler.stop()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_legacy_whole_delivery_api_single_and_multipart(runtime_settings: Settings) -> None:
    """Requirement 9: Legacy whole-delivery API only supports single-part plans and delegates
    internally to part API; multipart calls must return 409 ChannelDeliveryMultipartConflictError
    with zero side-effects.
    """
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        access_token = created.access_token

        # Case 1: Single-part plan (default factory)
        receipt1 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="legacy-single-msg"),
            access_token=access_token,
        )
        snap1 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt1.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap1.delivery_id is not None
        del1_id = snap1.delivery_id

        # Claim legacy whole delivery -> 200
        lease1 = uuid4()
        claimed1 = await container.external_channels.claim_delivery(
            connection_id,
            del1_id,
            ChannelDeliveryClaimRequest(
                channel_turn_id=snap1.channel_turn_id,
                delivery_id=del1_id,
                lease_id=lease1,
                lease_seconds=30,
            ),
            access_token=access_token,
        )
        assert claimed1.status is ChannelDeliveryStatus.SENDING

        # Acknowledge legacy whole delivery -> 200
        acked1 = await container.external_channels.acknowledge_delivery(
            connection_id,
            del1_id,
            ChannelDeliveryAcknowledgement(
                channel_turn_id=snap1.channel_turn_id,
                delivery_id=del1_id,
                lease_id=lease1,
                status=ChannelDeliveryStatus.DELIVERED,
                provider_message_id="prov-legacy-msg-1",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        assert acked1.status is ChannelDeliveryStatus.DELIVERED
        plan1 = await container.external_channels.get_delivery_plan(
            connection_id, del1_id, access_token=access_token
        )
        assert plan1.status is ChannelDeliveryStatus.DELIVERED
        assert plan1.parts[0].status is ChannelDeliveryPartStatus.DELIVERED

        # Case 2: Multipart plan (3 parts factory)
        container.external_channels.delivery_plan_factory = _ThreePartsPlanFactory()
        receipt2 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="legacy-multi-msg"),
            access_token=access_token,
        )
        snap2 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt2.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap2.delivery_id is not None
        del2_id = snap2.delivery_id

        # Calling legacy claim_delivery on multipart must raise 409
        lease2 = uuid4()
        with pytest.raises(ChannelDeliveryMultipartConflictError) as exc_info:
            await container.external_channels.claim_delivery(
                connection_id,
                del2_id,
                ChannelDeliveryClaimRequest(
                    channel_turn_id=snap2.channel_turn_id,
                    delivery_id=del2_id,
                    lease_id=lease2,
                    lease_seconds=30,
                ),
                access_token=access_token,
            )
        assert exc_info.value.http_status == 409

        # Zero side-effects: verify plan and parts remain pending
        plan2_after_claim = await container.external_channels.get_delivery_plan(
            connection_id, del2_id, access_token=access_token
        )
        assert plan2_after_claim.status is ChannelDeliveryStatus.PENDING
        assert all(p.status is ChannelDeliveryPartStatus.PENDING for p in plan2_after_claim.parts)

        # Claim Part 0 via part API
        p0_claim = await container.external_channels.claim_next_delivery_part(
            connection_id,
            del2_id,
            ChannelDeliveryPartClaimRequest(
                delivery_id=del2_id,
                part_id=None,
                lease_id=lease2,
                lease_seconds=30,
            ),
            access_token=access_token,
        )
        assert p0_claim is not None
        assert p0_claim.status is ChannelDeliveryPartStatus.SENDING

        # Calling legacy acknowledge_delivery on multipart must raise 409
        with pytest.raises(ChannelDeliveryMultipartConflictError) as exc_info2:
            await container.external_channels.acknowledge_delivery(
                connection_id,
                del2_id,
                ChannelDeliveryAcknowledgement(
                    channel_turn_id=snap2.channel_turn_id,
                    delivery_id=del2_id,
                    lease_id=lease2,
                    status=ChannelDeliveryStatus.DELIVERED,
                    provider_message_id="prov-legacy-fail",
                    acknowledged_at=datetime.now(UTC),
                ),
                access_token=access_token,
            )
        assert exc_info2.value.http_status == 409

        # Zero side-effects: Part 0 remains sending under lease2
        parts2 = await container.external_channels.list_delivery_parts(
            connection_id, del2_id, access_token=access_token
        )
        assert parts2[0].status is ChannelDeliveryPartStatus.SENDING
        assert parts2[0].lease_id == lease2
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_event_failure_transactional_rollback(tmp_path: Path) -> None:
    """Requirement 10: Part state, Parent state, and Event Store outbox are strictly
    in the same transaction. Failure in event store causes complete rollback.
    """
    db_path = tmp_path / "tx_rollback.db"
    storage = StorageConfig(database_path=db_path)
    database = Database(db_path, storage)
    await database.open()

    failing_event_store = _FailingEventStore(database)
    repo = SQLiteExternalChannelRepository(database, failing_event_store)
    try:
        now = datetime.now(UTC)
        connection_id = uuid4()
        session_id = uuid4()
        binding_id = uuid4()

        await repo.create_connection(
            _configuration(connection_id),
            access_token_hash="hash-123",
            created_at=now,
        )
        async with database.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO sessions(
                    session_id, character_id, state, conversation_state,
                    revision, next_sequence, created_at, updated_at
                ) VALUES (?, 'default', 'ready', 'idle', 0, 1, ?, ?)
                """,
                (str(session_id), now.isoformat(), now.isoformat()),
            )
        await repo.create_binding(
            binding_id=binding_id,
            connection_id=connection_id,
            conversation_key="chat-user-1",
            sender_key="sender-user-1",
            session_id=session_id,
            created_at=now,
        )

        _turn, delivery_id = await _create_plan_with_parts(
            repo, connection_id, session_id, binding_id, ("Part 0 text", "Part 1 text")
        )

        # 1. Rollback on claim_next_delivery_part
        failing_event_store.fail = True
        lease_1 = uuid4()
        with pytest.raises(RuntimeError, match="simulated EventStore write failure"):
            await repo.claim_next_delivery_part(
                ChannelDeliveryPartClaimRequest(
                    delivery_id=delivery_id,
                    part_id=None,
                    lease_id=lease_1,
                    lease_seconds=30,
                ),
                claimed_at=now,
            )

        # Verify DB was completely rolled back: part 0 still pending, attempt 0
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is not None
        assert plan.status is ChannelDeliveryStatus.PENDING
        assert plan.parts[0].status is ChannelDeliveryPartStatus.PENDING
        assert plan.parts[0].attempt == 0
        assert plan.parts[0].lease_id is None

        # 2. Rollback on acknowledge_delivery_part
        failing_event_store.fail = False
        claimed_res = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id,
                part_id=None,
                lease_id=lease_1,
                lease_seconds=30,
            ),
            claimed_at=now,
        )
        assert claimed_res is not None
        assert claimed_res.part is not None
        assert claimed_res.part.status is ChannelDeliveryPartStatus.SENDING

        failing_event_store.fail = True
        with pytest.raises(RuntimeError, match="simulated EventStore write failure"):
            await repo.acknowledge_delivery_part(
                ChannelDeliveryPartAcknowledgement(
                    delivery_id=delivery_id,
                    part_id=claimed_res.part.part_id,
                    lease_id=lease_1,
                    status=ChannelDeliveryPartStatus.DELIVERED,
                    provider_message_id="msg-ack-fail",
                    acknowledged_at=now,
                ),
                updated_at=now,
            )

        # Verify DB was rolled back: part 0 still sending under lease_1, delivered_at is None
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is not None
        assert plan.status is ChannelDeliveryStatus.SENDING
        assert plan.parts[0].status is ChannelDeliveryPartStatus.SENDING
        assert plan.parts[0].lease_id == lease_1
        assert plan.parts[0].delivered_at is None

        # 3. Rollback on cancel_remaining_delivery_parts
        with pytest.raises(RuntimeError, match="simulated EventStore write failure"):
            await repo.cancel_remaining_delivery_parts(
                delivery_id,
                ChannelDeliveryPartsCancelRequest(
                    reason="fail cancel",
                    requested_at=now,
                ),
            )

        # Verify DB was rolled back: cancel_requested_at is still None, part 1 still pending
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is not None
        assert plan.delivery.cancel_requested_at is None
        assert plan.parts[1].status is ChannelDeliveryPartStatus.PENDING
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_early_cursor_advance_before_delivery_completion(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 2: WeChat polling advances adapter cursor checkpoint immediately
    upon durable admission, without blocking on generation or delivery.
    """
    container = RuntimeContainer(runtime_settings)
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
    hold_execution = asyncio.Event()
    execute_started = asyncio.Event()
    await container.start()
    try:
        connection_id = uuid4()
        access_token = "g" * 43
        created = await container.external_channels.create_connection(
            _configuration(connection_id), access_token=access_token
        )
        credentials = WeixinCredentials(
            bot_token="ilink-token",
            bot_id="bot-owner",
            user_id="sender-user-1",
            base_url="https://ilinkai.weixin.qq.com/",
            gateway_access_token=access_token,
            pending_contexts={
                "msg-early-cursor": WeixinPendingContext(
                    context_token="ctx-1",
                    recipient_user_id="sender-user-1",
                )
            },
        )
        await store.set(f"weixin_ilink:{connection_id}", credentials.to_json())

        # Block send_text to hold delivery in progress
        original_send = transport.send_text

        async def _delayed_send(*args: Any, **kwargs: Any) -> str:
            execute_started.set()
            await hold_execution.wait()
            return await original_send(*args, **kwargs)

        monkeypatch.setattr(transport, "send_text", _delayed_send)

        await management.connection_configuration_changed(created.snapshot)

        # Push an inbound update batch
        await transport.updates.put(
            WeixinUpdates(
                cursor="cursor-checkpoint-999",
                messages=(
                    WeixinInboundText(
                        external_message_id="msg-early-cursor",
                        sender_user_id="sender-user-1",
                        recipient_bot_id="bot-owner",
                        text="你好，测试提前推进 Cursor！",
                        context_token="ctx-1",
                        received_at=datetime.now(UTC),
                    ),
                ),
            )
        )

        # Verify cursor advances to "cursor-checkpoint-999" quickly
        for _ in range(30):
            cur = await container.external_channel_repository.get_adapter_cursor(connection_id)
            if cur == "cursor-checkpoint-999":
                break
            await asyncio.sleep(0.05)

        assert (
            await container.external_channel_repository.get_adapter_cursor(connection_id)
            == "cursor-checkpoint-999"
        )

        # Stage 1: Verify Admission - cursor advanced and turn reached durable admission
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "msg-early-cursor"
        )
        assert turn is not None
        assert turn.status in (
            ChannelTurnStatus.ACCEPTED,
            ChannelTurnStatus.PROCESSING,
            ChannelTurnStatus.COMPLETED,
        )

        # Stage 2: Wait until delivery plan is created
        for _ in range(100):
            turn = await container.external_channel_repository.find_turn_by_external_message(
                connection_id, "msg-early-cursor"
            )
            if turn is not None and turn.delivery_id is not None:
                break
            await asyncio.sleep(0.1)

        assert turn is not None
        assert turn.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert plan is not None
        assert plan.status in (ChannelDeliveryStatus.PENDING, ChannelDeliveryStatus.SENDING)

        # Now unblock delivery and wait for completion
        hold_execution.set()
        for _ in range(100):
            plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
            if plan and plan.status is ChannelDeliveryStatus.DELIVERED:
                break
            await asyncio.sleep(0.1)

        assert plan is not None
        assert plan.status is ChannelDeliveryStatus.DELIVERED
    finally:
        hold_execution.set()
        await container.stop()


@pytest.mark.asyncio
async def test_new_message_tail_cancellation_on_same_binding(
    runtime_settings: Settings,
) -> None:
    """Requirement 6: Ingesting a new message on the same binding automatically cancels
    the uncompleted tail of any previous active delivery plan.
    """
    container = RuntimeContainer(runtime_settings)
    container.external_channels.delivery_plan_factory = _ThreePartsPlanFactory()
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        access_token = created.access_token

        # Message 1
        receipt1 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="msg-tail-cancel-1"),
            access_token=access_token,
        )
        snap1 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt1.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap1.delivery_id is not None
        del1_id = snap1.delivery_id

        # Deliver Part 0 of Message 1
        lease1 = uuid4()
        p0 = await container.external_channels.claim_next_delivery_part(
            connection_id,
            del1_id,
            ChannelDeliveryPartClaimRequest(
                delivery_id=del1_id,
                part_id=None,
                lease_id=lease1,
                lease_seconds=30,
            ),
            access_token=access_token,
        )
        assert p0 is not None
        await container.external_channels.acknowledge_delivery_part(
            connection_id,
            del1_id,
            ChannelDeliveryPartAcknowledgement(
                delivery_id=del1_id,
                part_id=p0.part_id,
                lease_id=lease1,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="p0-done",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )

        plan1_mid = await container.external_channels.get_delivery_plan(
            connection_id, del1_id, access_token=access_token
        )
        assert plan1_mid.status is ChannelDeliveryStatus.PENDING
        assert plan1_mid.delivered_part_count == 1

        # Now Message 2 arrives on the SAME binding!
        receipt2 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="msg-tail-cancel-2"),
            access_token=access_token,
        )

        # Plan 1 must have had its remaining parts (Part 1 and 2) cancelled
        plan1_final = await container.external_channels.get_delivery_plan(
            connection_id, del1_id, access_token=access_token
        )
        assert plan1_final.status is ChannelDeliveryStatus.CANCELLED
        assert plan1_final.delivered_part_count == 1
        assert plan1_final.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
        assert plan1_final.parts[1].status is ChannelDeliveryPartStatus.CANCELLED
        assert plan1_final.parts[2].status is ChannelDeliveryPartStatus.CANCELLED

        # Message 2 proceeds to be accepted and delivered
        snap2 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt2.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap2.delivery_id is not None
        plan2 = await container.external_channels.get_delivery_plan(
            connection_id, snap2.delivery_id, access_token=access_token
        )
        assert plan2.status is ChannelDeliveryStatus.PENDING
        assert len(plan2.parts) == 3
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_repeated_ack_does_not_duplicate_domain_events(runtime_settings: Settings) -> None:
    """Requirement 11: Repeated ACK on an already delivered part or single-part plan must not
    duplicate domain events in EventStore (SQLite events table) or EventPublisher.
    """
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        access_token = created.access_token

        # Subscribe to all events
        received_events: list[dict[str, object]] = []
        sub = container.event_hub.subscribe(lambda _: True)

        async def _collector() -> None:
            try:
                while True:
                    ev = await sub.receive()
                    received_events.append(ev)
            except asyncio.CancelledError:
                pass

        collector_task = asyncio.create_task(_collector())

        async def _get_db_event_count() -> int:
            async with container.database.transaction() as conn:
                cursor = await conn.execute("SELECT COUNT(*) AS c FROM events")
                row = await cursor.fetchone()
                assert row is not None
                return int(row["c"])

        # 1. Single-part delivery through legacy ACK
        receipt1 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="msg-single-ack"),
            access_token=access_token,
        )
        snap1 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt1.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap1.delivery_id is not None
        del1_id = snap1.delivery_id

        lease1 = uuid4()
        await container.external_channels.claim_delivery(
            connection_id,
            del1_id,
            ChannelDeliveryClaimRequest(
                channel_turn_id=snap1.channel_turn_id,
                delivery_id=del1_id,
                lease_id=lease1,
                lease_seconds=30,
            ),
            access_token=access_token,
        )

        # First ACK
        await container.external_channels.acknowledge_delivery(
            connection_id,
            del1_id,
            ChannelDeliveryAcknowledgement(
                channel_turn_id=snap1.channel_turn_id,
                delivery_id=del1_id,
                lease_id=lease1,
                status=ChannelDeliveryStatus.DELIVERED,
                provider_message_id="p-single-first",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        await asyncio.sleep(0.05)

        db_count_after_first_ack = await _get_db_event_count()
        bus_count_after_first_ack = len(received_events)

        # Verify delivered events exist
        delivered_events = [
            e
            for e in received_events
            if e.get("event_type")
            in ("channel.delivery_part_delivered", "channel.delivery_acknowledged")
        ]
        assert len(delivered_events) >= 1

        # Second ACK (Repeated ACK via legacy acknowledge_delivery)
        await container.external_channels.acknowledge_delivery(
            connection_id,
            del1_id,
            ChannelDeliveryAcknowledgement(
                channel_turn_id=snap1.channel_turn_id,
                delivery_id=del1_id,
                lease_id=lease1,
                status=ChannelDeliveryStatus.DELIVERED,
                provider_message_id="p-single-second",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        await asyncio.sleep(0.05)

        # Neither DB nor event bus count should increase!
        assert await _get_db_event_count() == db_count_after_first_ack
        assert len(received_events) == bus_count_after_first_ack

        # Third ACK via part-level API
        plan1 = await container.external_channels.get_delivery_plan(
            connection_id, del1_id, access_token=access_token
        )
        await container.external_channels.acknowledge_delivery_part(
            connection_id,
            del1_id,
            ChannelDeliveryPartAcknowledgement(
                delivery_id=del1_id,
                part_id=plan1.parts[0].part_id,
                lease_id=lease1,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="p-single-third",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        await asyncio.sleep(0.05)

        assert await _get_db_event_count() == db_count_after_first_ack
        assert len(received_events) == bus_count_after_first_ack

        # 2. Multipart plan repeated ACK
        container.external_channels.delivery_plan_factory = _ThreePartsPlanFactory()
        receipt2 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="msg-multi-ack"),
            access_token=access_token,
        )
        snap2 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt2.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap2.delivery_id is not None
        del2_id = snap2.delivery_id

        lease2 = uuid4()
        part0 = await container.external_channels.claim_next_delivery_part(
            connection_id,
            del2_id,
            ChannelDeliveryPartClaimRequest(
                delivery_id=del2_id,
                part_id=None,
                lease_id=lease2,
                lease_seconds=30,
            ),
            access_token=access_token,
        )
        assert part0 is not None

        # Part 0 first ACK
        await container.external_channels.acknowledge_delivery_part(
            connection_id,
            del2_id,
            ChannelDeliveryPartAcknowledgement(
                delivery_id=del2_id,
                part_id=part0.part_id,
                lease_id=lease2,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="p0-first",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        await asyncio.sleep(0.05)

        db_count_p0 = await _get_db_event_count()
        bus_count_p0 = len(received_events)

        # Part 0 duplicate ACK
        await container.external_channels.acknowledge_delivery_part(
            connection_id,
            del2_id,
            ChannelDeliveryPartAcknowledgement(
                delivery_id=del2_id,
                part_id=part0.part_id,
                lease_id=lease2,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="p0-dup",
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        await asyncio.sleep(0.05)

        assert await _get_db_event_count() == db_count_p0
        assert len(received_events) == bus_count_p0

        collector_task.cancel()
        await asyncio.gather(collector_task, return_exceptions=True)
        container.event_hub.unsubscribe(sub)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_part_acknowledgement_rejects_cancelled_status_and_preserves_state(
    runtime_settings: Settings,
) -> None:
    """Requirement P0-2: Part ACK must reject CANCELLED status at protocol/API schema level (422)
    and at repository level, preserving parent and child state without permanently
    blocking the plan.
    """
    app = create_app(runtime_settings)
    container = app.state.container
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        access_token = created.access_token

        receipt = await container.external_channels.ingest(
            _message(connection_id, external_message_id="part-ack-cancel-msg"),
            access_token=access_token,
        )
        snap = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap.delivery_id is not None
        delivery_id = snap.delivery_id

        # Claim part 0 so it enters SENDING status
        lease_id = uuid4()
        part0 = await container.external_channels.claim_next_delivery_part(
            connection_id,
            delivery_id,
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id,
                part_id=None,
                lease_id=lease_id,
                lease_seconds=60,
            ),
            access_token=access_token,
        )
        assert part0 is not None
        assert part0.status is ChannelDeliveryPartStatus.SENDING

        # 1. Pydantic schema validation rejects CANCELLED status
        with pytest.raises(ValidationError):
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=part0.part_id,
                lease_id=lease_id,
                status="cancelled",  # type: ignore[arg-type]
                acknowledged_at=datetime.now(UTC),
            )

        # 2. HTTP endpoint rejects CANCELLED status with HTTP 422
        headers = {"Authorization": f"Bearer {access_token}"}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                f"/v1/channel-connections/{connection_id}/deliveries/{delivery_id}/parts/ack",
                headers=headers,
                json={
                    "delivery_id": str(delivery_id),
                    "part_id": str(part0.part_id),
                    "lease_id": str(lease_id),
                    "status": "cancelled",
                    "acknowledged_at": datetime.now(UTC).isoformat(),
                },
            )
            assert resp.status_code == 422

        # 3. Verify state remains SENDING and is NOT corrupted
        plan = await container.external_channels.get_delivery_plan(
            connection_id, delivery_id, access_token=access_token
        )
        assert plan.status is ChannelDeliveryStatus.SENDING
        assert plan.parts[0].status is ChannelDeliveryPartStatus.SENDING

        # 4. Repository level directly rejects invalid status
        from dataclasses import dataclass, field

        @dataclass
        class _BypassedAck:
            delivery_id: UUID
            part_id: UUID
            lease_id: UUID
            status: Any
            provider_message_id: str | None = None
            error: Any = None
            acknowledged_at: datetime = field(default_factory=lambda: datetime.now(UTC))

        bypassed = _BypassedAck(
            delivery_id=delivery_id,
            part_id=part0.part_id,
            lease_id=lease_id,
            status=ChannelDeliveryPartStatus.CANCELLED,
        )
        with pytest.raises(
            ValueError, match="only delivered or failed part acknowledgements are supported"
        ):
            await container.external_channel_repository.acknowledge_delivery_part(
                bypassed,  # type: ignore[arg-type]
                updated_at=datetime.now(UTC),
            )

        # Re-verify state in repository remains SENDING
        db_plan = await container.external_channel_repository.get_delivery_plan(delivery_id)
        assert db_plan is not None
        assert db_plan.status is ChannelDeliveryStatus.SENDING
        assert db_plan.parts[0].status is ChannelDeliveryPartStatus.SENDING
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_legacy_whole_delivery_cancelled_delegates_to_cancel_without_error(
    runtime_settings: Settings,
) -> None:
    """Requirement P0-2: Legacy whole-delivery ACK with CANCELLED status must delegate
    to cancel_remaining_delivery_parts, succeed without requiring an error payload,
    and cleanly transition both parent plan and child parts to CANCELLED.
    """
    app = create_app(runtime_settings)
    container = app.state.container
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        access_token = created.access_token

        # Ingest turn 1
        receipt1 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="legacy-cancel-turn-1"),
            access_token=access_token,
        )
        snap1 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt1.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap1.delivery_id is not None
        del1_id = snap1.delivery_id

        # Claim legacy delivery
        lease1 = uuid4()
        claimed = await container.external_channels.claim_delivery(
            connection_id,
            del1_id,
            ChannelDeliveryClaimRequest(
                channel_turn_id=snap1.channel_turn_id,
                delivery_id=del1_id,
                lease_id=lease1,
                lease_seconds=30,
            ),
            access_token=access_token,
        )
        assert claimed.status is ChannelDeliveryStatus.SENDING

        # Legacy ACK with CANCELLED and error=None (no error provided)
        acked1 = await container.external_channels.acknowledge_delivery(
            connection_id,
            del1_id,
            ChannelDeliveryAcknowledgement(
                channel_turn_id=snap1.channel_turn_id,
                delivery_id=del1_id,
                lease_id=lease1,
                status=ChannelDeliveryStatus.CANCELLED,
                provider_message_id=None,
                error=None,
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        assert acked1.status is ChannelDeliveryStatus.CANCELLED

        # Verify plan and part 0 in repository are cancelled
        plan1 = await container.external_channels.get_delivery_plan(
            connection_id, del1_id, access_token=access_token
        )
        assert plan1.status is ChannelDeliveryStatus.CANCELLED
        assert plan1.parts[0].status is ChannelDeliveryPartStatus.CANCELLED

        # Test through HTTP route as well
        receipt2 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="legacy-cancel-turn-2"),
            access_token=access_token,
        )
        snap2 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt2.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap2.delivery_id is not None
        del2_id = snap2.delivery_id
        lease2 = uuid4()
        await container.external_channels.claim_delivery(
            connection_id,
            del2_id,
            ChannelDeliveryClaimRequest(
                channel_turn_id=snap2.channel_turn_id,
                delivery_id=del2_id,
                lease_id=lease2,
                lease_seconds=30,
            ),
            access_token=access_token,
        )

        headers = {"Authorization": f"Bearer {access_token}"}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                f"/v1/channel-connections/{connection_id}/deliveries/{del2_id}/ack",
                headers=headers,
                json={
                    "channel_turn_id": str(snap2.channel_turn_id),
                    "delivery_id": str(del2_id),
                    "lease_id": str(lease2),
                    "status": "cancelled",
                    "provider_message_id": None,
                    "error": None,
                    "acknowledged_at": datetime.now(UTC).isoformat(),
                },
            )
            assert resp.status_code == 200
            resp_body = resp.json()
            assert resp_body["status"] == "cancelled"

        plan2 = await container.external_channels.get_delivery_plan(
            connection_id, del2_id, access_token=access_token
        )
        assert plan2.status is ChannelDeliveryStatus.CANCELLED
        assert plan2.parts[0].status is ChannelDeliveryPartStatus.CANCELLED

        # Turn 3: Test wrong lease_id rejection
        receipt3 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="legacy-cancel-turn-3"),
            access_token=access_token,
        )
        snap3 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt3.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap3.delivery_id is not None
        del3_id = snap3.delivery_id
        lease3 = uuid4()
        await container.external_channels.claim_delivery(
            connection_id,
            del3_id,
            ChannelDeliveryClaimRequest(
                channel_turn_id=snap3.channel_turn_id,
                delivery_id=del3_id,
                lease_id=lease3,
                lease_seconds=30,
            ),
            access_token=access_token,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp_wrong_lease = await client.post(
                f"/v1/channel-connections/{connection_id}/deliveries/{del3_id}/ack",
                headers=headers,
                json={
                    "channel_turn_id": str(snap3.channel_turn_id),
                    "delivery_id": str(del3_id),
                    "lease_id": str(uuid4()),  # WRONG lease id!
                    "status": "cancelled",
                    "provider_message_id": None,
                    "error": None,
                    "acknowledged_at": datetime.now(UTC).isoformat(),
                },
            )
            assert resp_wrong_lease.status_code == 409

        # Turn 4: Test already delivered reply cannot be downgraded/cancelled
        receipt4 = await container.external_channels.ingest(
            _message(connection_id, external_message_id="legacy-cancel-turn-4"),
            access_token=access_token,
        )
        snap4 = await container.external_channels.wait_for_turn(
            connection_id,
            receipt4.channel_turn_id,
            access_token=access_token,
            wait_seconds=5,
        )
        assert snap4.delivery_id is not None
        del4_id = snap4.delivery_id
        lease4 = uuid4()
        await container.external_channels.claim_delivery(
            connection_id,
            del4_id,
            ChannelDeliveryClaimRequest(
                channel_turn_id=snap4.channel_turn_id,
                delivery_id=del4_id,
                lease_id=lease4,
                lease_seconds=30,
            ),
            access_token=access_token,
        )
        await container.external_channels.acknowledge_delivery(
            connection_id,
            del4_id,
            ChannelDeliveryAcknowledgement(
                channel_turn_id=snap4.channel_turn_id,
                delivery_id=del4_id,
                lease_id=lease4,
                status=ChannelDeliveryStatus.DELIVERED,
                provider_message_id="msg-4-delivered",
                error=None,
                acknowledged_at=datetime.now(UTC),
            ),
            access_token=access_token,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp_downgrade = await client.post(
                f"/v1/channel-connections/{connection_id}/deliveries/{del4_id}/ack",
                headers=headers,
                json={
                    "channel_turn_id": str(snap4.channel_turn_id),
                    "delivery_id": str(del4_id),
                    "lease_id": str(lease4),
                    "status": "cancelled",
                    "provider_message_id": None,
                    "error": None,
                    "acknowledged_at": datetime.now(UTC).isoformat(),
                },
            )
            assert resp_downgrade.status_code == 409
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_next_delivery_wakeup_at_avoids_busy_loop_on_blocked_subsequent_parts(
    runtime_settings: Settings,
) -> None:
    """Requirement P1-3: next_delivery_wakeup_at must only consider the active sending part's
    lease expiry or the lowest-ordinal pending part, and never wake up on blocked subsequent parts.
    """
    container = RuntimeContainer(runtime_settings)
    container.external_channels.delivery_plan_factory = _ThreePartsPlanFactory()
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        receipt = await container.external_channels.ingest(
            _message(connection_id, external_message_id="wakeup-busyloop-msg"),
            access_token=created.access_token,
        )
        snap = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert snap.delivery_id is not None
        delivery_id = snap.delivery_id
        plan = await container.external_channel_repository.get_delivery_plan(delivery_id)
        assert plan is not None
        assert len(plan.parts) == 3

        now = datetime.now(UTC)
        part0_lease_id = uuid4()
        part0_lease_expires = now + timedelta(seconds=60)
        part1_not_before = now - timedelta(seconds=10)  # in the past!
        part2_not_before = now - timedelta(seconds=5)  # in the past!

        # Case A: Part 0 is SENDING with unexpired lease (+60s).
        # Part 1 and 2 are PENDING with past not_before_at.
        async with container.database.transaction() as conn:
            await conn.execute(
                """
                UPDATE channel_delivery_parts
                SET status = 'sending',
                    lease_id = ?,
                    lease_expires_at = ?
                WHERE part_id = ?
                """,
                (str(part0_lease_id), part0_lease_expires.isoformat(), str(plan.parts[0].part_id)),
            )
            await conn.execute(
                """
                UPDATE channel_delivery_parts
                SET not_before_at = ?
                WHERE part_id = ?
                """,
                (part1_not_before.isoformat(), str(plan.parts[1].part_id)),
            )
            await conn.execute(
                """
                UPDATE channel_delivery_parts
                SET not_before_at = ?
                WHERE part_id = ?
                """,
                (part2_not_before.isoformat(), str(plan.parts[2].part_id)),
            )

        wakeup_a = await container.external_channel_repository.next_delivery_wakeup_at(
            connection_id=connection_id
        )
        assert wakeup_a is not None
        # MUST match Part 0's lease expiration (~now + 60s), NOT Part 1/2's past timestamps!
        assert wakeup_a >= now + timedelta(seconds=50)

        # Case B: No parts sending. Part 0 is PENDING (+30s).
        # Part 1 is PENDING (+5s). Part 2 is PENDING (+2s).
        part0_not_before = now + timedelta(seconds=30)
        part1_future_not_before = now + timedelta(seconds=5)
        part2_future_not_before = now + timedelta(seconds=2)
        async with container.database.transaction() as conn:
            await conn.execute(
                """
                UPDATE channel_delivery_parts
                SET status = 'pending',
                    lease_id = NULL,
                    lease_expires_at = NULL,
                    not_before_at = ?
                WHERE part_id = ?
                """,
                (part0_not_before.isoformat(), str(plan.parts[0].part_id)),
            )
            await conn.execute(
                """
                UPDATE channel_delivery_parts
                SET not_before_at = ?
                WHERE part_id = ?
                """,
                (part1_future_not_before.isoformat(), str(plan.parts[1].part_id)),
            )
            await conn.execute(
                """
                UPDATE channel_delivery_parts
                SET not_before_at = ?
                WHERE part_id = ?
                """,
                (part2_future_not_before.isoformat(), str(plan.parts[2].part_id)),
            )

        wakeup_b = await container.external_channel_repository.next_delivery_wakeup_at(
            connection_id=connection_id
        )
        assert wakeup_b is not None
        # Part 1 and 2 are blocked by Part 0 (lowest ordinal 0).
        # Wakeup MUST be Part 0's not_before_at (+30s)!
        assert wakeup_b >= now + timedelta(seconds=25)

        # Case C: Part 0 is DELIVERED. Part 1 is PENDING (+15s). Part 2 is PENDING (+5s).
        part1_target_not_before = now + timedelta(seconds=15)
        async with container.database.transaction() as conn:
            await conn.execute(
                """
                UPDATE channel_delivery_parts
                SET status = 'delivered',
                    delivered_at = ?
                WHERE part_id = ?
                """,
                (now.isoformat(), str(plan.parts[0].part_id)),
            )
            await conn.execute(
                """
                UPDATE channel_delivery_parts
                SET not_before_at = ?
                WHERE part_id = ?
                """,
                (part1_target_not_before.isoformat(), str(plan.parts[1].part_id)),
            )

        wakeup_c = await container.external_channel_repository.next_delivery_wakeup_at(
            connection_id=connection_id
        )
        assert wakeup_c is not None
        # Now Part 1 is lowest pending ordinal (1).
        # Wakeup MUST be Part 1's not_before_at (+15s), not Part 2's (+5s)!
        assert wakeup_c >= now + timedelta(seconds=10)
    finally:
        await container.stop()


class _FakeUtcClock:
    def __init__(self, initial_time: datetime) -> None:
        self._current = initial_time

    def __call__(self) -> datetime:
        return self._current

    def advance(self, seconds: float) -> None:
        self._current += timedelta(seconds=seconds)


class _SlowDeliveringExecutor(DeliveryPartExecutor):
    def __init__(self, clock: _FakeUtcClock, delay_s: float = 5.0) -> None:
        self._clock = clock
        self._delay_s = delay_s
        self.executed_parts: list[tuple[UUID, int]] = []

    async def execute_part(
        self,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
    ) -> DeliveryPartExecutionResult:
        self._clock.advance(self._delay_s)
        self.executed_parts.append((plan.delivery_id, part.ordinal))
        return DeliveryPartExecutionResult(
            outcome=DeliveryPartOutcome.DELIVERED,
            provider_message_id=f"prov-{part.provider_client_id}",
        )


class _SlowFailingExecutor(DeliveryPartExecutor):
    def __init__(self, clock: _FakeUtcClock, delay_s: float = 5.0) -> None:
        self._clock = clock
        self._delay_s = delay_s
        self.attempts: list[tuple[UUID, int, int]] = []

    async def execute_part(
        self,
        plan: ChannelDeliveryPlanRecord,
        part: ChannelDeliveryPartRecord,
    ) -> DeliveryPartExecutionResult:
        self._clock.advance(self._delay_s)
        self.attempts.append((plan.delivery_id, part.ordinal, part.attempt))
        return DeliveryPartExecutionResult(
            outcome=DeliveryPartOutcome.RETRYABLE_ERROR,
            error=StructuredError(
                code="slow_gateway_timeout",
                message="Upstream timed out after 5 seconds",
                retryable=True,
                component="external_channels",
            ),
        )


@pytest.mark.asyncio
async def test_scheduler_slow_successful_send_advances_clock_and_cadence_waits_from_completion(
    tmp_path: Path,
) -> None:
    (
        database,
        repo,
        _,
        _,
        connection_id,
        session_id,
        binding_id,
    ) = await _setup_test_environment(tmp_path)
    try:
        t0 = datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC)
        clock = _FakeUtcClock(t0)

        turn_id = uuid4()
        delivery_id = uuid4()
        now = t0

        turn = ChannelTurnRecord(
            channel_turn_id=turn_id,
            connection_id=connection_id,
            binding_id=binding_id,
            external_message_id=f"ext-{uuid4().hex[:8]}",
            content_sha256="fake-sha",
            account_key="bot-owner",
            conversation_key="chat-user-1",
            chat_type=ChannelChatType.DIRECT,
            conversation_label="Test Chat",
            sender_key="sender-user-1",
            sender_display_name="User",
            principal_scope="local",
            session_id=session_id,
            turn_id=uuid4(),
            generation_id=uuid4(),
            status=ChannelTurnStatus.ACCEPTED,
            reply_text=None,
            error=None,
            delivery_id=None,
            delivery_status=None,
            revision=0,
            accepted_at=now,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        await repo.create_turn(turn)

        draft_parts = (
            ChannelDeliveryPartDraft(
                ordinal=0,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(
                    kind=ChannelDeliveryPartKind.TEXT,
                    text="Part 1 with 2s cadence",
                ),
                required=True,
                delay_after_ms=2000,
                not_before_at=None,
            ),
            ChannelDeliveryPartDraft(
                ordinal=1,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(
                    kind=ChannelDeliveryPartKind.TEXT,
                    text="Part 2 following Part 1",
                ),
                required=True,
                delay_after_ms=0,
                not_before_at=None,
            ),
        )

        await repo.complete_turn(
            turn_id,
            reply_text="Part 1 with 2s cadence Part 2 following Part 1",
            delivery_id=delivery_id,
            completed_at=now,
            parts=draft_parts,
        )

        executor = _SlowDeliveringExecutor(clock, delay_s=5.0)
        scheduler = ChannelDeliveryScheduler(
            repository=repo,
            executor=executor,
            connection_id=connection_id,
            clock=clock,
        )

        # Step 1: Part 0 begins sending at T0, execution advances clock by 5s, finishes at T0+5s.
        assert await scheduler.step() is True
        assert clock() == t0 + timedelta(seconds=5)
        assert len(executor.executed_parts) == 1
        assert executor.executed_parts[0] == (delivery_id, 0)

        # Delivered timestamp must be actual completion time (T0+5s), NOT start time (T0)
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is not None
        assert plan.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
        assert plan.parts[0].delivered_at == t0 + timedelta(seconds=5)

        # Cadence delay (2000ms = 2s) MUST be scheduled from completion time: (T0+5s) + 2s = T0+7s
        assert plan.parts[1].status is ChannelDeliveryPartStatus.PENDING
        assert plan.parts[1].not_before_at == t0 + timedelta(seconds=7)

        # At actual completion time (T0+5s), Part 1 must NOT be claimable yet
        assert await scheduler.step() is False
        assert len(executor.executed_parts) == 1

        # Advance clock to T0+6s (still before T0+7s) -> still not claimable
        clock.advance(1.0)
        assert await scheduler.step() is False
        assert len(executor.executed_parts) == 1

        # Advance clock to T0+7s -> Part 1 is now claimable and delivered
        clock.advance(1.0)
        assert await scheduler.step() is True
        assert len(executor.executed_parts) == 2
        assert executor.executed_parts[1] == (delivery_id, 1)

        plan_terminal = await repo.get_delivery_plan(delivery_id)
        assert plan_terminal is not None
        assert plan_terminal.status is ChannelDeliveryStatus.DELIVERED
        assert plan_terminal.parts[1].status is ChannelDeliveryPartStatus.DELIVERED
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_slow_retryable_send_advances_clock_and_backoff_waits_from_completion(
    tmp_path: Path,
) -> None:
    (
        database,
        repo,
        _,
        _,
        connection_id,
        session_id,
        binding_id,
    ) = await _setup_test_environment(tmp_path)
    try:
        t0 = datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC)
        clock = _FakeUtcClock(t0)

        turn_id = uuid4()
        delivery_id = uuid4()
        now = t0

        turn = ChannelTurnRecord(
            channel_turn_id=turn_id,
            connection_id=connection_id,
            binding_id=binding_id,
            external_message_id=f"ext-{uuid4().hex[:8]}",
            content_sha256="fake-sha",
            account_key="bot-owner",
            conversation_key="chat-user-1",
            chat_type=ChannelChatType.DIRECT,
            conversation_label="Test Chat",
            sender_key="sender-user-1",
            sender_display_name="User",
            principal_scope="local",
            session_id=session_id,
            turn_id=uuid4(),
            generation_id=uuid4(),
            status=ChannelTurnStatus.ACCEPTED,
            reply_text=None,
            error=None,
            delivery_id=None,
            delivery_status=None,
            revision=0,
            accepted_at=now,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        await repo.create_turn(turn)

        draft_parts = (
            ChannelDeliveryPartDraft(
                ordinal=0,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(
                    kind=ChannelDeliveryPartKind.TEXT,
                    text="Part requiring retry",
                ),
                required=True,
                delay_after_ms=0,
                not_before_at=None,
            ),
        )

        await repo.complete_turn(
            turn_id,
            reply_text="Part requiring retry",
            delivery_id=delivery_id,
            completed_at=now,
            parts=draft_parts,
        )

        executor = _SlowFailingExecutor(clock, delay_s=5.0)
        scheduler = ChannelDeliveryScheduler(
            repository=repo,
            executor=executor,
            connection_id=connection_id,
            initial_backoff_seconds=2.0,
            max_attempts=3,
            clock=clock,
        )

        # Step 1: Send begins at T0, fails after 5s at T0+5s.
        assert await scheduler.step() is True
        assert clock() == t0 + timedelta(seconds=5)
        assert len(executor.attempts) == 1
        assert executor.attempts[0] == (delivery_id, 0, 1)

        # Backoff delay is 2.0s. It MUST be computed from completion time (T0+5s) + 2s = T0+7s!
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is not None
        part0 = plan.parts[0]
        assert part0.status is ChannelDeliveryPartStatus.PENDING
        assert part0.attempt == 1
        assert part0.not_before_at == t0 + timedelta(seconds=7)

        # At actual failure time (T0+5s), Part 0 must NOT retry immediately!
        assert await scheduler.step() is False
        assert len(executor.attempts) == 1

        # Advance to T0+6s -> still within backoff window
        clock.advance(1.0)
        assert await scheduler.step() is False
        assert len(executor.attempts) == 1

        # Advance to T0+7s -> backoff expired, attempt 2 executes
        clock.advance(1.0)
        assert await scheduler.step() is True
        assert len(executor.attempts) == 2
        assert executor.attempts[1] == (delivery_id, 0, 2)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_multiple_plans_fresh_timing_in_single_cycle(
    tmp_path: Path,
) -> None:
    (
        database,
        repo,
        _,
        _,
        connection_id,
        session_id,
        binding_id,
    ) = await _setup_test_environment(tmp_path)
    try:
        t0 = datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC)
        clock = _FakeUtcClock(t0)

        # Plan A: Ready immediately
        _, deliv_a = await _create_plan_with_parts(
            repo,
            connection_id,
            session_id,
            binding_id,
            ("Plan A text",),
            created_at=t0 - timedelta(seconds=1),
        )

        # Plan B: Scheduled with not_before_at = T0 + 3s
        turn_b_id = uuid4()
        deliv_b = uuid4()
        turn_b = ChannelTurnRecord(
            channel_turn_id=turn_b_id,
            connection_id=connection_id,
            binding_id=binding_id,
            external_message_id=f"ext-{uuid4().hex[:8]}",
            content_sha256="fake-sha-b",
            account_key="bot-owner",
            conversation_key="chat-user-1",
            chat_type=ChannelChatType.DIRECT,
            conversation_label="Test Chat",
            sender_key="sender-user-1",
            sender_display_name="User",
            principal_scope="local",
            session_id=session_id,
            turn_id=uuid4(),
            generation_id=uuid4(),
            status=ChannelTurnStatus.ACCEPTED,
            reply_text=None,
            error=None,
            delivery_id=None,
            delivery_status=None,
            revision=0,
            accepted_at=t0,
            created_at=t0,
            updated_at=t0,
            completed_at=None,
        )
        await repo.create_turn(turn_b)
        await repo.complete_turn(
            turn_b_id,
            reply_text="Plan B text",
            delivery_id=deliv_b,
            completed_at=t0,
            parts=(
                ChannelDeliveryPartDraft(
                    ordinal=0,
                    kind=ChannelDeliveryPartKind.TEXT,
                    payload=ChannelTextDeliveryPartPayload(
                        kind=ChannelDeliveryPartKind.TEXT,
                        text="Plan B text",
                    ),
                    required=True,
                    delay_after_ms=0,
                    not_before_at=t0 + timedelta(seconds=3),
                ),
            ),
        )

        # Plan A takes 5s to send. Plan B takes 1s to send.
        class _MultiPlanExecutor(DeliveryPartExecutor):
            def __init__(self, c: _FakeUtcClock) -> None:
                self.c = c
                self.history: list[tuple[UUID, datetime, datetime]] = []

            async def execute_part(
                self,
                plan: ChannelDeliveryPlanRecord,
                part: ChannelDeliveryPartRecord,
            ) -> DeliveryPartExecutionResult:
                start_time = self.c()
                dur = 5.0 if plan.delivery_id == deliv_a else 1.0
                self.c.advance(dur)
                end_time = self.c()
                self.history.append((plan.delivery_id, start_time, end_time))
                return DeliveryPartExecutionResult(
                    outcome=DeliveryPartOutcome.DELIVERED,
                    provider_message_id=f"msg-{part.provider_client_id}",
                )

        multi_executor = _MultiPlanExecutor(clock)
        scheduler = ChannelDeliveryScheduler(
            repository=repo,
            executor=multi_executor,
            connection_id=connection_id,
            lease_seconds=30,
            clock=clock,
        )

        # Single step() cycle evaluates both plans.
        # Plan A is claimed at T0, takes 5s, finishes at T0+5s.
        # When loop reaches Plan B, fresh time T0+5s is evaluated:
        # since T0+5s >= T0+3s (not_before_at), Plan B is claimed at T0+5s in the SAME cycle!
        progress = await scheduler.step()
        assert progress is True
        assert len(multi_executor.history) == 2

        # Verify execution timestamps
        assert multi_executor.history[0][0] == deliv_a
        assert multi_executor.history[0][1] == t0
        assert multi_executor.history[0][2] == t0 + timedelta(seconds=5)

        assert multi_executor.history[1][0] == deliv_b
        assert multi_executor.history[1][1] == t0 + timedelta(seconds=5)
        assert multi_executor.history[1][2] == t0 + timedelta(seconds=6)

        # Verify delivered timestamps in database
        plan_a_rec = await repo.get_delivery_plan(deliv_a)
        assert plan_a_rec is not None
        assert plan_a_rec.parts[0].delivered_at == t0 + timedelta(seconds=5)

        plan_b_rec = await repo.get_delivery_plan(deliv_b)
        assert plan_b_rec is not None
        assert plan_b_rec.parts[0].delivered_at == t0 + timedelta(seconds=6)
    finally:
        await database.close()
