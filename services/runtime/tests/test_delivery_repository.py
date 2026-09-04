# pyright: reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportReturnType=false, reportArgumentType=false, reportIndexIssue=false, reportGeneralTypeIssues=false, reportOptionalMemberAccess=false
"""Repository state machine tests for durable multipart delivery (Phase 17.1A)."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.external_channels.models import (
    ChannelTurnRecord,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    SQLiteExternalChannelRepository,
)


async def _setup_repo(
    tmp_path: Path,
) -> tuple[Database, SQLiteExternalChannelRepository, UUID, UUID, UUID]:
    db_path = tmp_path / "repo_test.db"
    storage = StorageConfig(database_path=db_path)
    database = Database(db_path, storage)
    await database.open()

    repo = SQLiteExternalChannelRepository(database)
    now = datetime.now(UTC)
    connection_id = uuid4()
    session_id = uuid4()
    binding_id = uuid4()

    await repo.create_connection(
        ChannelConnectionConfiguration(
            connection_id=connection_id,
            provider_id="weixin_ilink",
            name="我的微信",
            character_id="ayachi_nene",
            principal_scope="local",
            account_key="owner-acc",
            allowed_sender_keys=["owner-sender"],
        ),
        access_token_hash="hash123",
        created_at=now,
    )

    # Insert a dummy session
    async with database.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO sessions(
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'ayachi_nene', 'ready', 'idle', 0, 1, ?, ?)
            """,
            (str(session_id), now.isoformat(), now.isoformat()),
        )

    await repo.create_binding(
        binding_id=binding_id,
        connection_id=connection_id,
        conversation_key="c_key",
        sender_key="s_key",
        session_id=session_id,
        created_at=now,
    )

    return database, repo, connection_id, session_id, binding_id


async def _create_test_turn_with_parts(
    repo: SQLiteExternalChannelRepository,
    connection_id: UUID,
    session_id: UUID,
    binding_id: UUID,
    part_texts: tuple[str, ...],
) -> tuple[ChannelTurnRecord, UUID]:
    now = datetime.now(UTC)
    turn_id = uuid4()
    delivery_id = uuid4()

    turn = ChannelTurnRecord(
        channel_turn_id=turn_id,
        connection_id=connection_id,
        binding_id=binding_id,
        external_message_id=f"ext-{uuid4()}",
        content_sha256="sha256fake",
        account_key="owner-acc",
        conversation_key="c_key",
        chat_type=ChannelChatType.DIRECT,
        conversation_label="label",
        sender_key="s_key",
        sender_display_name="木白",
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

    parts = tuple(
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
        for idx, text in enumerate(part_texts)
    )

    full_reply = " ".join(part_texts)
    completed_turn = await repo.complete_turn(
        turn_id,
        reply_text=full_reply,
        delivery_id=delivery_id,
        completed_at=now,
        parts=parts,
    )
    return completed_turn, delivery_id


@pytest.mark.asyncio
async def test_part_strict_sequence(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Part 0 text", "Part 1 text")
        )
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is not None
        assert len(plan.parts) == 2

        # Trying to claim Part 1 specifically before Part 0 is delivered must return None
        part_1_id = plan.parts[1].part_id
        claim_p1 = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=part_1_id,
            lease_id=uuid4(),
            lease_seconds=30,
        )
        claimed_p1 = await repo.claim_next_delivery_part(claim_p1, claimed_at=datetime.now(UTC))
        assert claimed_p1 is None

        # General claim must yield Part 0
        lease_p0 = uuid4()
        claim_p0 = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=lease_p0,
            lease_seconds=30,
        )
        claimed_p0 = await repo.claim_next_delivery_part(claim_p0, claimed_at=datetime.now(UTC))
        assert claimed_p0 is not None
        assert claimed_p0.ordinal == 0
        assert claimed_p0.status == ChannelDeliveryPartStatus.SENDING

        # While Part 0 is sending, general claim returns None (mutual exclusion)
        claim_another = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=uuid4(),
            lease_seconds=30,
        )
        assert (
            await repo.claim_next_delivery_part(claim_another, claimed_at=datetime.now(UTC)) is None
        )

        # Deliver Part 0
        now = datetime.now(UTC)
        ack_p0 = ChannelDeliveryPartAcknowledgement(
            delivery_id=delivery_id,
            part_id=claimed_p0.part_id,
            lease_id=lease_p0,
            status=ChannelDeliveryPartStatus.DELIVERED,
            acknowledged_at=now,
            provider_message_id="prov-p0",
            error=None,
        )
        await repo.acknowledge_delivery_part(ack_p0, updated_at=now)

        # Now Part 1 can be claimed
        lease_p1 = uuid4()
        claim_p1_ok = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=lease_p1,
            lease_seconds=30,
        )
        claimed_p1_ok = await repo.claim_next_delivery_part(
            claim_p1_ok, claimed_at=datetime.now(UTC)
        )
        assert claimed_p1_ok is not None
        assert claimed_p1_ok.ordinal == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_invalid_lease_ack_rejected(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Part 0 text",)
        )
        lease_id = uuid4()
        now = datetime.now(UTC)
        claim = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=lease_id,
            lease_seconds=30,
        )
        claimed = await repo.claim_next_delivery_part(claim, claimed_at=now)
        assert claimed is not None

        # 1. Wrong lease_id must raise ValueError
        wrong_lease_ack = ChannelDeliveryPartAcknowledgement(
            delivery_id=delivery_id,
            part_id=claimed.part_id,
            lease_id=uuid4(),  # wrong lease
            status=ChannelDeliveryPartStatus.DELIVERED,
            acknowledged_at=now,
            provider_message_id="msg-1",
            error=None,
        )
        with pytest.raises(ValueError, match="lease_id mismatch"):
            await repo.acknowledge_delivery_part(wrong_lease_ack, updated_at=now)

        # 2. Expired lease must raise ValueError
        future_time = now + timedelta(seconds=60)
        expired_ack = ChannelDeliveryPartAcknowledgement(
            delivery_id=delivery_id,
            part_id=claimed.part_id,
            lease_id=lease_id,
            status=ChannelDeliveryPartStatus.DELIVERED,
            acknowledged_at=future_time,
            provider_message_id="msg-1",
            error=None,
        )
        with pytest.raises(ValueError, match="lease expired"):
            await repo.acknowledge_delivery_part(expired_ack, updated_at=future_time)

        # Ensure Part 0 is still sending, untouched
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is not None
        assert plan.parts[0].status == ChannelDeliveryPartStatus.SENDING
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_expired_lease_reclaimable_with_stable_provider_client_id(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Part 0 text",)
        )
        plan_before = await repo.get_delivery_plan(delivery_id)
        assert plan_before is not None
        expected_client_id = f"chatwaifu-{delivery_id.hex}-000"
        assert plan_before.parts[0].provider_client_id == expected_client_id

        # Claim Part 0 with 10s lease
        lease_1 = uuid4()
        t0 = datetime.now(UTC)
        claim_1 = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=lease_1,
            lease_seconds=10,
        )
        p0_attempt1 = await repo.claim_next_delivery_part(claim_1, claimed_at=t0)
        assert p0_attempt1 is not None
        assert p0_attempt1.attempt == 1
        assert p0_attempt1.provider_client_id == expected_client_id

        # Advance time by 20s and recover expired leases
        t1 = t0 + timedelta(seconds=20)
        recovered_count = await repo.recover_expired_delivery_part_leases(as_of=t1)
        assert recovered_count == 1

        plan_recovered = await repo.get_delivery_plan(delivery_id)
        assert plan_recovered is not None
        assert plan_recovered.parts[0].status == ChannelDeliveryPartStatus.PENDING
        assert plan_recovered.parts[0].lease_id is None

        # Reclaim Part 0
        lease_2 = uuid4()
        claim_2 = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=lease_2,
            lease_seconds=10,
        )
        p0_attempt2 = await repo.claim_next_delivery_part(claim_2, claimed_at=t1)
        assert p0_attempt2 is not None
        assert p0_attempt2.attempt == 2
        # Provider client ID must be 100% stable and unchanged
        assert p0_attempt2.provider_client_id == expected_client_id
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_idempotent_repeated_delivered_ack(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Single part text",)
        )
        lease_id = uuid4()
        now = datetime.now(UTC)
        claim = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=lease_id,
            lease_seconds=30,
        )
        claimed = await repo.claim_next_delivery_part(claim, claimed_at=now)
        assert claimed is not None

        ack = ChannelDeliveryPartAcknowledgement(
            delivery_id=delivery_id,
            part_id=claimed.part_id,
            lease_id=lease_id,
            status=ChannelDeliveryPartStatus.DELIVERED,
            acknowledged_at=now,
            provider_message_id="msg-id-123",
            error=None,
        )
        plan, part = await repo.acknowledge_delivery_part(ack, updated_at=now)
        assert plan.status == ChannelDeliveryStatus.DELIVERED
        assert part.status == ChannelDeliveryPartStatus.DELIVERED

        # Repeated ACK with same status must succeed idempotently
        plan2, part2 = await repo.acknowledge_delivery_part(
            ack, updated_at=now + timedelta(seconds=1)
        )
        assert plan2.status == ChannelDeliveryStatus.DELIVERED
        assert part2.status == ChannelDeliveryPartStatus.DELIVERED

        # But attempting to downgrade DELIVERED to FAILED must raise ValueError
        bad_downgrade = ChannelDeliveryPartAcknowledgement(
            delivery_id=delivery_id,
            part_id=claimed.part_id,
            lease_id=lease_id,
            status=ChannelDeliveryPartStatus.FAILED,
            acknowledged_at=now,
            provider_message_id="msg-id-123",
            error=StructuredError(
                code="send_failed", message="fail", retryable=False, component="external_channels"
            ),
        )
        with pytest.raises(ValueError, match="cannot be downgraded"):
            await repo.acknowledge_delivery_part(bad_downgrade, updated_at=now)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_plan_lifecycle_and_parent_status_derivation(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Part 0", "Part 1", "Part 2")
        )
        plan0 = await repo.get_delivery_plan(delivery_id)
        assert plan0 is not None
        assert plan0.status == ChannelDeliveryStatus.PENDING
        assert plan0.part_count == 3
        assert plan0.delivered_part_count == 0

        # Deliver Part 0
        now = datetime.now(UTC)
        l0 = uuid4()
        c0 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l0, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c0 is not None
        plan_after_c0 = await repo.get_delivery_plan(delivery_id)
        assert plan_after_c0 is not None
        assert plan_after_c0.status == ChannelDeliveryStatus.SENDING

        plan1, _ = await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c0.part_id,
                lease_id=l0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="msg-0",
                error=None,
            ),
            updated_at=now,
        )
        assert plan1.status == ChannelDeliveryStatus.PENDING
        assert plan1.delivered_part_count == 1

        # Deliver Part 1
        l1 = uuid4()
        c1 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l1, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c1 is not None and c1.ordinal == 1
        plan2, _ = await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c1.part_id,
                lease_id=l1,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="msg-1",
                error=None,
            ),
            updated_at=now,
        )
        assert plan2.status == ChannelDeliveryStatus.PENDING
        assert plan2.delivered_part_count == 2

        # Deliver Part 2
        l2 = uuid4()
        c2 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l2, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c2 is not None and c2.ordinal == 2
        plan3, _ = await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c2.part_id,
                lease_id=l2,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="msg-2",
                error=None,
            ),
            updated_at=now,
        )
        assert plan3.status == ChannelDeliveryStatus.DELIVERED
        assert plan3.delivered_part_count == 3
        assert plan3.delivered_at is not None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_intermediate_part_failed_parent_status(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Part 0 ok", "Part 1 fail", "Part 2")
        )
        now = datetime.now(UTC)
        l0 = uuid4()
        c0 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l0, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c0 is not None
        await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c0.part_id,
                lease_id=l0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="m0",
                error=None,
            ),
            updated_at=now,
        )

        # Part 1 claimed and failed
        l1 = uuid4()
        c1 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l1, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c1 is not None
        fail_err = StructuredError(
            code="send_network_error",
            message="WeChat iLink network timeout",
            retryable=True,
            component="external_channels",
        )
        plan_failed, part_failed = await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c1.part_id,
                lease_id=l1,
                status=ChannelDeliveryPartStatus.FAILED,
                acknowledged_at=now,
                provider_message_id=None,
                error=fail_err,
            ),
            updated_at=now,
        )
        assert plan_failed.status == ChannelDeliveryStatus.FAILED
        assert part_failed.status == ChannelDeliveryPartStatus.FAILED
        assert plan_failed.delivery.last_error is not None
        assert plan_failed.delivery.last_error.code == "send_network_error"

        # Subsequent claims must return None because delivery is failed
        assert (
            await repo.claim_next_delivery_part(
                ChannelDeliveryPartClaimRequest(
                    delivery_id=delivery_id, part_id=None, lease_id=uuid4(), lease_seconds=30
                ),
                claimed_at=now,
            )
            is None
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_tail_cancellation(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Part 0", "Part 1", "Part 2")
        )
        now = datetime.now(UTC)
        l0 = uuid4()
        c0 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l0, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c0 is not None
        await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c0.part_id,
                lease_id=l0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="m0",
                error=None,
            ),
            updated_at=now,
        )

        # Cancel remaining parts
        cancel_req = ChannelDeliveryPartsCancelRequest(
            reason="User interrupted turn",
            requested_at=now,
        )
        cancelled_plan = await repo.cancel_remaining_delivery_parts(delivery_id, cancel_req)
        assert cancelled_plan.status == ChannelDeliveryStatus.CANCELLED
        assert cancelled_plan.cancel_requested_at is not None

        # Part 0 remains DELIVERED
        assert cancelled_plan.parts[0].status == ChannelDeliveryPartStatus.DELIVERED
        # Part 1 and 2 are CANCELLED
        assert cancelled_plan.parts[1].status == ChannelDeliveryPartStatus.CANCELLED
        assert cancelled_plan.parts[2].status == ChannelDeliveryPartStatus.CANCELLED

        # Subsequent claims return None
        assert (
            await repo.claim_next_delivery_part(
                ChannelDeliveryPartClaimRequest(
                    delivery_id=delivery_id, part_id=None, lease_id=uuid4(), lease_seconds=30
                ),
                claimed_at=now,
            )
            is None
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_claims_single_winner(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Concurrent part",)
        )
        now = datetime.now(UTC)

        async def _attempt_claim(i: int):
            lease_id = uuid4()
            claim = ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id,
                part_id=None,
                lease_id=lease_id,
                lease_seconds=30,
            )
            return await repo.claim_next_delivery_part(claim, claimed_at=now)

        # 10 tasks race to claim
        results = await asyncio.gather(*[_attempt_claim(i) for i in range(10)])
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        assert winners[0].ordinal == 0
        assert winners[0].status == ChannelDeliveryPartStatus.SENDING
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_crash_recovery_does_not_resend_delivered_part(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Part 0 ok", "Part 1 ok")
        )
        now = datetime.now(UTC)

        # Deliver Part 0
        l0 = uuid4()
        c0 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l0, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c0 is not None
        await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c0.part_id,
                lease_id=l0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="m0",
                error=None,
            ),
            updated_at=now,
        )

        # Crash recovery run
        await repo.recover_expired_delivery_part_leases(as_of=now + timedelta(hours=1))

        # Re-query delivery plan
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is not None
        assert plan.parts[0].status == ChannelDeliveryPartStatus.DELIVERED

        # Next claim MUST be Part 1, NOT Part 0!
        c1 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=uuid4(), lease_seconds=30
            ),
            claimed_at=now + timedelta(hours=1),
        )
        assert c1 is not None
        assert c1.ordinal == 1
        assert c1.payload.text == "Part 1 ok"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_create_delivery_plan_validation_and_rollback(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        now = datetime.now(UTC)
        turn_id = uuid4()
        delivery_id = uuid4()

        turn = ChannelTurnRecord(
            channel_turn_id=turn_id,
            connection_id=conn_id,
            binding_id=bind_id,
            external_message_id="ext-validation",
            content_sha256="sha256fake",
            account_key="owner-acc",
            conversation_key="c_key",
            chat_type=ChannelChatType.DIRECT,
            conversation_label="label",
            sender_key="s_key",
            sender_display_name="木白",
            principal_scope="local",
            session_id=sess_id,
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

        # 1. Empty parts rejected
        with pytest.raises(ValueError, match="cannot be empty"):
            await repo.create_delivery_plan(
                turn_id,
                delivery_id=delivery_id,
                parts=(),
                created_at=now,
            )

        # 2. Non-continuous ordinals rejected
        bad_ordinals = (
            ChannelDeliveryPartDraft(
                ordinal=0,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(text="part 0"),
                required=True,
            ),
            ChannelDeliveryPartDraft(
                ordinal=2,  # Should be 1
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(text="part 2"),
                required=True,
            ),
        )
        with pytest.raises(ValueError, match="strictly continuous"):
            await repo.create_delivery_plan(
                turn_id,
                delivery_id=delivery_id,
                parts=bad_ordinals,
                created_at=now,
            )

        # Verify no orphan delivery created in database
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is None
        parts = await repo.list_delivery_parts(delivery_id)
        assert len(parts) == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_create_delivery_plan_three_parts_stable_client_ids(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        now = datetime.now(UTC)
        turn_id = uuid4()
        delivery_id = uuid4()

        turn = ChannelTurnRecord(
            channel_turn_id=turn_id,
            connection_id=conn_id,
            binding_id=bind_id,
            external_message_id="ext-3parts",
            content_sha256="sha256fake",
            account_key="owner-acc",
            conversation_key="c_key",
            chat_type=ChannelChatType.DIRECT,
            conversation_label="label",
            sender_key="s_key",
            sender_display_name="木白",
            principal_scope="local",
            session_id=sess_id,
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

        parts_draft = (
            ChannelDeliveryPartDraft(
                ordinal=0,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(text="Part zero"),
                required=True,
            ),
            ChannelDeliveryPartDraft(
                ordinal=1,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(text="Part one"),
                required=True,
            ),
            ChannelDeliveryPartDraft(
                ordinal=2,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(text="Part two"),
                required=True,
            ),
        )
        plan = await repo.create_delivery_plan(
            turn_id,
            delivery_id=delivery_id,
            parts=parts_draft,
            created_at=now,
        )
        assert plan.part_count == 3
        assert len(plan.parts) == 3
        assert plan.status == ChannelDeliveryStatus.PENDING

        expected_prefix = f"chatwaifu-{delivery_id.hex}-"
        assert plan.parts[0].provider_client_id == f"{expected_prefix}000"
        assert plan.parts[1].provider_client_id == f"{expected_prefix}001"
        assert plan.parts[2].provider_client_id == f"{expected_prefix}002"

        # Unique provider client IDs
        client_ids = {p.provider_client_id for p in plan.parts}
        assert len(client_ids) == 3
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_sending_part_with_cancel_race(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Part 0 sending", "Part 1 tail")
        )
        now = datetime.now(UTC)

        # Claim Part 0 (status: sending)
        lease_id = uuid4()
        part_0 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id,
                part_id=None,
                lease_id=lease_id,
                lease_seconds=60,
            ),
            claimed_at=now,
        )
        assert part_0 is not None
        assert part_0.status == ChannelDeliveryPartStatus.SENDING

        # Cancel remaining while Part 0 is in-flight
        cancel_req = ChannelDeliveryPartsCancelRequest(
            reason="User cancelled before tail",
            requested_at=now,
        )
        plan_after_cancel = await repo.cancel_remaining_delivery_parts(delivery_id, cancel_req)
        # Part 0 is still sending (lease preserved)
        assert plan_after_cancel.parts[0].status == ChannelDeliveryPartStatus.SENDING
        # Part 1 is cancelled
        assert plan_after_cancel.parts[1].status == ChannelDeliveryPartStatus.CANCELLED
        # Plan records cancel_requested_at
        assert plan_after_cancel.cancel_requested_at is not None

        # ACK for in-flight Part 0 arrives
        updated_plan, updated_p0 = await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=part_0.part_id,
                lease_id=lease_id,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="prov-p0",
                acknowledged_at=now,
            ),
            updated_at=now,
        )
        assert updated_p0.status == ChannelDeliveryPartStatus.DELIVERED
        # With cancel requested and remaining parts cancelled,
        # parent plan status resolves to CANCELLED
        assert updated_plan.status == ChannelDeliveryStatus.CANCELLED
        assert updated_plan.delivered_part_count == 1

        # No further parts can be claimed
        assert (
            await repo.claim_next_delivery_part(
                ChannelDeliveryPartClaimRequest(
                    delivery_id=delivery_id,
                    part_id=None,
                    lease_id=uuid4(),
                    lease_seconds=60,
                ),
                claimed_at=now,
            )
            is None
        )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_crash_before_ack_reclaims_with_identical_client_id(tmp_path: Path) -> None:
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Crash test part",)
        )
        now = datetime.now(UTC)

        # 1. Claim Part 0
        lease_1 = uuid4()
        c1 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id,
                part_id=None,
                lease_id=lease_1,
                lease_seconds=30,
            ),
            claimed_at=now,
        )
        assert c1 is not None
        initial_client_id = c1.provider_client_id
        assert c1.attempt == 1

        # 2. Simulate crash: lease expires after 30s
        after_crash = now + timedelta(seconds=60)
        await repo.recover_expired_delivery_part_leases(as_of=after_crash)

        # 3. Re-claim Part 0
        lease_2 = uuid4()
        c2 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id,
                part_id=None,
                lease_id=lease_2,
                lease_seconds=30,
            ),
            claimed_at=after_crash,
        )
        assert c2 is not None
        assert c2.part_id == c1.part_id
        assert c2.attempt == 2
        # Client ID MUST BE IDENTICAL to enable upstream deduplication
        assert c2.provider_client_id == initial_client_id
    finally:
        await database.close()
