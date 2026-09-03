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
    _credential_reference,
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
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    SQLiteExternalChannelRepository,
)


class _ThreePartsPlanFactory:
    def create_parts(self, reply_text: str) -> tuple[ChannelDeliveryPartDraft, ...]:
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
) -> tuple[ChannelTurnRecord, UUID]:
    now = datetime.now(UTC)
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
    completed_turn = res.turn if hasattr(res, "turn") else res
    return completed_turn, delivery_id


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
                    delivery_id=delivery_id,
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
        await store.set(_credential_reference(connection_id), credentials.to_json())

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

        # At this moment, delivery is STILL held
        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, "msg-early-cursor"
        )
        assert turn is not None
        assert turn.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(turn.delivery_id)
        assert plan is not None
        assert plan.status in (ChannelDeliveryStatus.PENDING, ChannelDeliveryStatus.SENDING)

        # Now unblock delivery and wait for completion
        hold_execution.set()
        for _ in range(30):
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
