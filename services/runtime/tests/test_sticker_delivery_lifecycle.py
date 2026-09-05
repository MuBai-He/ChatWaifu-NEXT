# pyright: reportPrivateUsage=false
"""Repository lifecycle tests for sticker (image) delivery parts.

Tests the state machine behavior of multi-part delivery plans containing
required text parts followed by optional image (sticker) parts.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelImageDeliveryPartPayload,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_runtime.external_channels.models import ChannelTurnRecord
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    SQLiteExternalChannelRepository,
)
from test_delivery_repository import (
    _create_test_turn_with_parts,
    _setup_repo,
)

_VALID_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _make_text_draft(
    ordinal: int,
    text: str,
    *,
    required: bool = True,
    delay_after_ms: int = 0,
) -> ChannelDeliveryPartDraft:
    return ChannelDeliveryPartDraft(
        ordinal=ordinal,
        kind=ChannelDeliveryPartKind.TEXT,
        payload=ChannelTextDeliveryPartPayload(
            kind=ChannelDeliveryPartKind.TEXT,
            text=text,
        ),
        required=required,
        delay_after_ms=delay_after_ms,
        not_before_at=None,
    )


def _make_image_draft(
    ordinal: int,
    *,
    sticker_id: str = "sticker-ayachi-01",
    sha256: str = _VALID_SHA256,
    mime_type: str = "image/png",
    required: bool = False,
    delay_after_ms: int = 0,
) -> ChannelDeliveryPartDraft:
    return ChannelDeliveryPartDraft(
        ordinal=ordinal,
        kind=ChannelDeliveryPartKind.IMAGE,
        payload=ChannelImageDeliveryPartPayload(
            kind=ChannelDeliveryPartKind.IMAGE,
            sticker_id=sticker_id,
            sha256=sha256,
            mime_type=mime_type,
        ),
        required=required,
        delay_after_ms=delay_after_ms,
        not_before_at=None,
    )


async def _create_test_turn_with_custom_parts(
    repo: SQLiteExternalChannelRepository,
    connection_id: UUID,
    session_id: UUID,
    binding_id: UUID,
    parts: Sequence[ChannelDeliveryPartDraft],
    *,
    reply_text: str = "Test turn reply",
) -> tuple[ChannelTurnRecord, UUID]:
    """Dedicated turn helper adapted from _create_test_turn_with_parts for arbitrary part drafts."""
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

    completed_result = await repo.complete_turn(
        turn_id,
        reply_text=reply_text,
        delivery_id=delivery_id,
        completed_at=now,
        parts=parts,
    )
    return completed_result.turn, delivery_id


async def _create_test_turn_with_image_plan(
    repo: SQLiteExternalChannelRepository,
    connection_id: UUID,
    session_id: UUID,
    binding_id: UUID,
    *,
    text: str = "Required bubble text",
    sticker_id: str = "sticker-ayachi-01",
    sha256: str = _VALID_SHA256,
    mime_type: str = "image/png",
    delay_after_ms: int = 0,
) -> tuple[ChannelTurnRecord, UUID]:
    """Helper to create a standard turn with required text (Part 0) and optional image (Part 1)."""
    parts = (
        _make_text_draft(0, text, required=True, delay_after_ms=delay_after_ms),
        _make_image_draft(
            1,
            sticker_id=sticker_id,
            sha256=sha256,
            mime_type=mime_type,
            required=False,
            delay_after_ms=0,
        ),
    )
    return await _create_test_turn_with_custom_parts(
        repo,
        connection_id,
        session_id,
        binding_id,
        parts=parts,
        reply_text=text,
    )


@pytest.mark.asyncio
async def test_required_text_then_optional_image_pending_parent_not_delivered(
    tmp_path: Path,
) -> None:
    """When Part 0 (required text) is delivered and Part 1 (optional image) is pending,

    the parent delivery plan status must remain PENDING and NOT be marked DELIVERED.
    """
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        # Establish standard text turn via existing helper to verify environment baseline
        _, baseline_delivery_id = await _create_test_turn_with_parts(
            repo, conn_id, sess_id, bind_id, ("Baseline text",)
        )
        baseline_plan = await repo.get_delivery_plan(baseline_delivery_id)
        assert baseline_plan is not None
        assert baseline_plan.part_count == 1

        # Create turn with required text + optional image
        _, delivery_id = await _create_test_turn_with_image_plan(
            repo,
            conn_id,
            sess_id,
            bind_id,
            text="Text message before optional sticker",
            sticker_id="ayachi_wink",
        )
        plan = await repo.get_delivery_plan(delivery_id)
        assert plan is not None
        assert plan.status == ChannelDeliveryStatus.PENDING
        assert plan.part_count == 2
        assert plan.delivered_part_count == 0
        assert plan.parts[0].required is True
        assert plan.parts[0].kind == ChannelDeliveryPartKind.TEXT
        assert plan.parts[1].required is False
        assert plan.parts[1].kind == ChannelDeliveryPartKind.IMAGE

        # Strict sequence: Part 1 cannot be claimed before Part 0 is delivered
        early_claim_p1 = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=plan.parts[1].part_id,
            lease_id=uuid4(),
            lease_seconds=30,
        )
        assert (
            await repo.claim_next_delivery_part(early_claim_p1, claimed_at=datetime.now(UTC))
            is None
        )

        # Claim Part 0
        now = datetime.now(UTC)
        lease_p0 = uuid4()
        claim_p0 = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=lease_p0,
            lease_seconds=30,
        )
        c0_result = await repo.claim_next_delivery_part(claim_p0, claimed_at=now)
        assert c0_result is not None
        assert c0_result.part is not None
        assert c0_result.part.ordinal == 0
        assert c0_result.part.status == ChannelDeliveryPartStatus.SENDING
        assert c0_result.plan.status == ChannelDeliveryStatus.SENDING

        # Acknowledge Part 0 as DELIVERED
        ack_p0 = ChannelDeliveryPartAcknowledgement(
            delivery_id=delivery_id,
            part_id=c0_result.part.part_id,
            lease_id=lease_p0,
            status=ChannelDeliveryPartStatus.DELIVERED,
            acknowledged_at=now,
            provider_message_id="prov-msg-text-0",
            error=None,
        )
        ack_res = await repo.acknowledge_delivery_part(ack_p0, updated_at=now)

        # Invariant check: Part 0 is DELIVERED, but Part 1 is PENDING.
        # Parent plan status MUST remain PENDING because an active pending part remains.
        assert ack_res.part is not None
        assert ack_res.part.status == ChannelDeliveryPartStatus.DELIVERED
        assert ack_res.plan.status == ChannelDeliveryStatus.PENDING
        assert ack_res.plan.delivered_part_count == 1
        assert ack_res.plan.delivered_at is None
        assert ack_res.plan.parts[0].status == ChannelDeliveryPartStatus.DELIVERED
        assert ack_res.plan.parts[1].status == ChannelDeliveryPartStatus.PENDING

        # Part 1 can now be claimed
        lease_p1 = uuid4()
        claim_p1 = ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id,
            part_id=None,
            lease_id=lease_p1,
            lease_seconds=30,
        )
        c1_result = await repo.claim_next_delivery_part(claim_p1, claimed_at=now)
        assert c1_result is not None
        assert c1_result.part is not None
        assert c1_result.part.ordinal == 1
        assert c1_result.part.kind == ChannelDeliveryPartKind.IMAGE
        assert c1_result.part.status == ChannelDeliveryPartStatus.SENDING
        assert c1_result.plan.status == ChannelDeliveryStatus.SENDING
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_delivered_optional_image_parent_delivered(tmp_path: Path) -> None:
    """When Part 0 (required text) and Part 1 (optional image) both deliver,

    the parent delivery plan resolves to DELIVERED with delivered_part_count == 2.
    """
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_image_plan(
            repo, conn_id, sess_id, bind_id, text="Hello sticker world"
        )
        now = datetime.now(UTC)

        # Claim and deliver Part 0
        lease_0 = uuid4()
        c0 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=lease_0, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c0 is not None and c0.part is not None
        await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c0.part.part_id,
                lease_id=lease_0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="msg-p0-delivered",
            ),
            updated_at=now,
        )

        # Claim Part 1
        lease_1 = uuid4()
        c1 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=lease_1, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c1 is not None and c1.part is not None
        assert c1.part.ordinal == 1
        assert c1.part.kind == ChannelDeliveryPartKind.IMAGE

        # Acknowledge Part 1 as DELIVERED
        ack_1 = ChannelDeliveryPartAcknowledgement(
            delivery_id=delivery_id,
            part_id=c1.part.part_id,
            lease_id=lease_1,
            status=ChannelDeliveryPartStatus.DELIVERED,
            acknowledged_at=now,
            provider_message_id="msg-p1-img-delivered",
        )
        result = await repo.acknowledge_delivery_part(ack_1, updated_at=now)

        # Both delivered -> parent plan status is DELIVERED
        assert result.plan.status == ChannelDeliveryStatus.DELIVERED
        assert result.plan.delivered_part_count == 2
        assert result.plan.delivered_at is not None
        assert result.part is not None
        assert result.part.status == ChannelDeliveryPartStatus.DELIVERED
        assert result.part.provider_message_id == "msg-p1-img-delivered"

        # Plan is terminal: subsequent claims return None
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
async def test_fatal_optional_image_parent_delivered(tmp_path: Path) -> None:
    """When Part 0 (required text) delivers but Part 1 (optional image) encounters a fatal failure,

    because the failed part is optional and all required parts are delivered,
    the parent delivery plan resolves to DELIVERED.
    """
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        _, delivery_id = await _create_test_turn_with_image_plan(
            repo, conn_id, sess_id, bind_id, text="Text before fatal image"
        )
        now = datetime.now(UTC)

        # Deliver Part 0
        lease_0 = uuid4()
        c0 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=lease_0, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c0 is not None and c0.part is not None
        await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c0.part.part_id,
                lease_id=lease_0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="msg-p0-ok",
            ),
            updated_at=now,
        )

        # Claim Part 1
        lease_1 = uuid4()
        c1 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=lease_1, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c1 is not None and c1.part is not None
        assert c1.part.ordinal == 1

        # Acknowledge Part 1 as FAILED with a fatal error
        fatal_error = StructuredError(
            code="sticker_send_failed",
            message="Provider rejected sticker media asset",
            retryable=False,
            component="external_channels",
        )
        ack_fail = ChannelDeliveryPartAcknowledgement(
            delivery_id=delivery_id,
            part_id=c1.part.part_id,
            lease_id=lease_1,
            status=ChannelDeliveryPartStatus.FAILED,
            acknowledged_at=now,
            provider_message_id=None,
            error=fatal_error,
        )
        result = await repo.acknowledge_delivery_part(ack_fail, updated_at=now)

        # Part 1 is FAILED
        assert result.part is not None
        assert result.part.status == ChannelDeliveryPartStatus.FAILED
        assert result.part.last_error is not None
        assert result.part.last_error.code == "sticker_send_failed"

        # Parent plan status must still resolve to DELIVERED because only an optional part failed
        assert result.plan.status == ChannelDeliveryStatus.DELIVERED
        assert result.plan.delivered_part_count == 1
        assert result.plan.delivered_at is not None

        # Subsequent claims return None since plan reached terminal DELIVERED
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
async def test_restart_preserves_frozen_payload_and_client_id(tmp_path: Path) -> None:
    """Service restart / crash recovery must preserve the frozen image payload

    and keep provider_client_id strictly stable across reclaims.
    """
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        sticker_id = "sticker-ayachi-special"
        sha256 = _VALID_SHA256
        mime_type = "image/png"

        _, delivery_id = await _create_test_turn_with_image_plan(
            repo,
            conn_id,
            sess_id,
            bind_id,
            text="Preserve test",
            sticker_id=sticker_id,
            sha256=sha256,
            mime_type=mime_type,
        )

        plan_before = await repo.get_delivery_plan(delivery_id)
        assert plan_before is not None
        expected_client_id_p1 = f"chatwaifu-{delivery_id.hex}-001"
        part_1_before = plan_before.parts[1]
        assert part_1_before.provider_client_id == expected_client_id_p1
        assert isinstance(part_1_before.payload, ChannelImageDeliveryPartPayload)
        assert part_1_before.payload.sticker_id == sticker_id
        assert part_1_before.payload.sha256 == sha256
        assert part_1_before.payload.mime_type == mime_type

        # Deliver Part 0
        t0 = datetime.now(UTC)
        l0 = uuid4()
        c0 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l0, lease_seconds=30
            ),
            claimed_at=t0,
        )
        assert c0 is not None and c0.part is not None
        await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c0.part.part_id,
                lease_id=l0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=t0,
                provider_message_id="msg-p0",
            ),
            updated_at=t0,
        )

        # Claim Part 1 with a short 10s lease
        l1 = uuid4()
        c1 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l1, lease_seconds=10
            ),
            claimed_at=t0,
        )
        assert c1 is not None and c1.part is not None
        assert c1.part.attempt == 1
        assert c1.part.provider_client_id == expected_client_id_p1
        assert isinstance(c1.part.payload, ChannelImageDeliveryPartPayload)
        assert c1.part.payload.sticker_id == sticker_id

        # Advance time by 20s and simulate restart with a fresh repository instance
        t1 = t0 + timedelta(seconds=20)
        repo_restarted = SQLiteExternalChannelRepository(database)
        recovered = await repo_restarted.recover_expired_delivery_part_leases(as_of=t1)
        assert getattr(recovered, "recovered_count", recovered) == 1

        # Check plan state via restarted repository instance
        plan_recovered = await repo_restarted.get_delivery_plan(delivery_id)
        assert plan_recovered is not None
        p1_recovered = plan_recovered.parts[1]
        assert p1_recovered.status == ChannelDeliveryPartStatus.PENDING
        assert p1_recovered.lease_id is None
        assert p1_recovered.provider_client_id == expected_client_id_p1
        assert isinstance(p1_recovered.payload, ChannelImageDeliveryPartPayload)
        assert p1_recovered.payload.sticker_id == sticker_id
        assert p1_recovered.payload.sha256 == sha256
        assert p1_recovered.payload.mime_type == mime_type

        # Re-claim Part 1 after restart
        l2 = uuid4()
        c2 = await repo_restarted.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l2, lease_seconds=30
            ),
            claimed_at=t1,
        )
        assert c2 is not None and c2.part is not None
        assert c2.part.attempt == 2
        # Client ID must be identical across attempts/restarts to guarantee upstream deduplication
        assert c2.part.provider_client_id == expected_client_id_p1
        # Payload remains unchanged
        assert isinstance(c2.part.payload, ChannelImageDeliveryPartPayload)
        assert c2.part.payload.sticker_id == sticker_id
        assert c2.part.payload.sha256 == sha256
        assert c2.part.payload.mime_type == mime_type
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_cancel_pending_optional_tail(tmp_path: Path) -> None:
    """Cancelling remaining parts when the optional image tail is pending or sending

    must transition the tail to CANCELLED and the parent plan to CANCELLED.
    """
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        now = datetime.now(UTC)

        # Case A: Optional image tail is PENDING
        _, delivery_id = await _create_test_turn_with_image_plan(
            repo, conn_id, sess_id, bind_id, text="Part 0 ok, tail cancelled"
        )
        l0 = uuid4()
        c0 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id, part_id=None, lease_id=l0, lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c0 is not None and c0.part is not None
        await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id,
                part_id=c0.part.part_id,
                lease_id=l0,
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="msg-p0",
            ),
            updated_at=now,
        )

        cancel_req = ChannelDeliveryPartsCancelRequest(
            reason="User interrupted before sticker sent",
            requested_at=now,
        )
        cancelled_plan = await repo.cancel_remaining_delivery_parts(delivery_id, cancel_req)
        assert cancelled_plan.plan.status == ChannelDeliveryStatus.CANCELLED
        assert cancelled_plan.plan.cancel_requested_at is not None
        assert cancelled_plan.plan.parts[0].status == ChannelDeliveryPartStatus.DELIVERED
        assert cancelled_plan.plan.parts[1].status == ChannelDeliveryPartStatus.CANCELLED
        assert cancelled_plan.plan.parts[1].last_error is not None
        assert cancelled_plan.plan.parts[1].last_error.code == "delivery_cancelled"

        # Subsequent claims must return None
        assert (
            await repo.claim_next_delivery_part(
                ChannelDeliveryPartClaimRequest(
                    delivery_id=delivery_id, part_id=None, lease_id=uuid4(), lease_seconds=30
                ),
                claimed_at=now,
            )
            is None
        )

        # Case B: In-flight optional image tail cancelled with lease ID
        _, delivery_id_2 = await _create_test_turn_with_image_plan(
            repo, conn_id, sess_id, bind_id, text="In-flight tail cancel"
        )
        c0_2 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id_2, part_id=None, lease_id=uuid4(), lease_seconds=30
            ),
            claimed_at=now,
        )
        assert c0_2 is not None and c0_2.part is not None
        await repo.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=delivery_id_2,
                part_id=c0_2.part.part_id,
                lease_id=c0_2.part.lease_id or uuid4(),
                status=ChannelDeliveryPartStatus.DELIVERED,
                acknowledged_at=now,
                provider_message_id="msg-p0-2",
            ),
            updated_at=now,
        )

        # Claim Part 1 (image is now sending)
        lease_img = uuid4()
        c1_2 = await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(
                delivery_id=delivery_id_2, part_id=None, lease_id=lease_img, lease_seconds=60
            ),
            claimed_at=now,
        )
        assert c1_2 is not None and c1_2.part is not None
        assert c1_2.part.status == ChannelDeliveryPartStatus.SENDING

        # Cancel remaining including active in-flight lease
        cancel_inflight = await repo.cancel_remaining_delivery_parts(
            delivery_id_2,
            ChannelDeliveryPartsCancelRequest(
                reason="User cancelled while sticker in-flight",
                requested_at=now,
            ),
            cancel_sending_lease_id=lease_img,
        )
        assert cancel_inflight.plan.status == ChannelDeliveryStatus.CANCELLED
        assert cancel_inflight.plan.parts[1].status == ChannelDeliveryPartStatus.CANCELLED
        assert cancel_inflight.plan.parts[1].lease_id is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_reject_optional_middle_required_image_only_image(tmp_path: Path) -> None:
    """Repository validation must reject invalid part combinations:

    1. Optional middle parts
    2. Image parts in the middle
    3. Required image parts
    4. Delivery plan with only an image part (no preceding text)
    5. Optional text tail
    """
    database, repo, conn_id, sess_id, bind_id = await _setup_repo(tmp_path)
    try:
        now = datetime.now(UTC)

        async def _create_base_turn(turn_id: UUID) -> ChannelTurnRecord:
            turn = ChannelTurnRecord(
                channel_turn_id=turn_id,
                connection_id=conn_id,
                binding_id=bind_id,
                external_message_id=f"ext-{turn_id}",
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
            return await repo.create_turn(turn)

        # 1A. Reject optional text in the middle
        turn_1 = await _create_base_turn(uuid4())
        deliv_1 = uuid4()
        optional_middle_text = (
            _make_text_draft(0, "Part 0", required=True),
            _make_text_draft(1, "Part 1 optional middle", required=False),
            _make_text_draft(2, "Part 2", required=True),
        )
        with pytest.raises(ValueError, match="middle delivery parts must be required"):
            await repo.create_delivery_plan(
                turn_1.channel_turn_id,
                delivery_id=deliv_1,
                parts=optional_middle_text,
                created_at=now,
            )
        assert await repo.get_delivery_plan(deliv_1) is None

        # 1B. Reject image in the middle
        turn_2 = await _create_base_turn(uuid4())
        deliv_2 = uuid4()
        image_in_middle = (
            _make_text_draft(0, "Part 0", required=True),
            _make_image_draft(1, required=False),
            _make_text_draft(2, "Part 2", required=True),
        )
        with pytest.raises(ValueError, match="middle delivery parts must be text parts"):
            await repo.create_delivery_plan(
                turn_2.channel_turn_id,
                delivery_id=deliv_2,
                parts=image_in_middle,
                created_at=now,
            )
        assert await repo.get_delivery_plan(deliv_2) is None

        # 2. Reject required image
        turn_3 = await _create_base_turn(uuid4())
        deliv_3 = uuid4()
        required_image_parts = (
            _make_text_draft(0, "Part 0", required=True),
            _make_image_draft(1, required=True),
        )
        with pytest.raises(ValueError, match="image delivery part must be optional"):
            await repo.create_delivery_plan(
                turn_3.channel_turn_id,
                delivery_id=deliv_3,
                parts=required_image_parts,
                created_at=now,
            )
        assert await repo.get_delivery_plan(deliv_3) is None

        # 3. Reject only image (no preceding text)
        turn_4 = await _create_base_turn(uuid4())
        deliv_4 = uuid4()
        only_image_parts = (_make_image_draft(0, required=False),)
        with pytest.raises(ValueError, match="image delivery part must follow required text parts"):
            await repo.create_delivery_plan(
                turn_4.channel_turn_id,
                delivery_id=deliv_4,
                parts=only_image_parts,
                created_at=now,
            )
        assert await repo.get_delivery_plan(deliv_4) is None

        # 4. Reject optional text tail (all text parts must be required)
        turn_5 = await _create_base_turn(uuid4())
        deliv_5 = uuid4()
        optional_text_tail = (
            _make_text_draft(0, "Part 0", required=True),
            _make_text_draft(1, "Part 1 optional tail", required=False),
        )
        with pytest.raises(ValueError, match="text delivery parts must be required"):
            await repo.create_delivery_plan(
                turn_5.channel_turn_id,
                delivery_id=deliv_5,
                parts=optional_text_tail,
                created_at=now,
            )
        assert await repo.get_delivery_plan(deliv_5) is None
    finally:
        await database.close()
