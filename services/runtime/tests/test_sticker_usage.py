"""Usage follows durable image-part facts, never parent success or callbacks."""
# pyright: reportPrivateUsage=false

import hashlib
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelImageDeliveryPartPayload,
    ChannelTextDeliveryPartPayload,
)
from chatwaifu_protocol.errors import StructuredError
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.external_channels.models import ChannelDeliveryPartDeferRequest
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_external_channels import SQLiteExternalChannelRepository
from chatwaifu_runtime.persistence.sqlite_sticker_library import SqliteStickerLibraryRepository
from chatwaifu_runtime.persistence.sqlite_sticker_usage import SQLiteStickerUsageRepository
from chatwaifu_runtime.sticker_library.models import StickerSaveCandidate
from chatwaifu_runtime.sticker_library.usage import StickerUsagePreset
from fastapi.testclient import TestClient
from test_sticker_repository import PNG_1X1, _init_db, _seed_source_chain

PRESET = "kitten_happy"
SHA = "760e4ac2b7c4955a045d507e1b0c1ae884cdf6b9753ef00bb7ac58a77b7d8714"
PRESETS = {PRESET: StickerUsagePreset(sha256=SHA, label="开心小猫")}


async def _plan(
    db: Database,
    *,
    scope: str = "local",
    character: str = "default",
    sticker_id: str = PRESET,
    sha256: str = SHA,
) -> tuple[UUID, UUID, UUID, UUID]:
    """Create authentic retained lineage and a text-plus-optional-image plan."""
    conn_id, gen_id = await _seed_source_chain(
        db, scope=scope, character_id=character, generation_status="accepted"
    )
    rows = await db.fetchall("SELECT * FROM channel_turns WHERE generation_id = ?", (gen_id,))
    row = rows[0]
    async with db.transaction() as conn:
        await conn.execute(
            "UPDATE sessions SET state = 'ready', conversation_state = 'idle' WHERE session_id = ?",
            (row["session_id"],),
        )
        await conn.execute(
            "INSERT INTO generations(generation_id, session_id, turn_id, state, backend_kind) "
            "VALUES (?, ?, ?, 'completed', 'demo')",
            (gen_id, row["session_id"], row["turn_id"]),
        )
    repo = SQLiteExternalChannelRepository(db)
    delivery_id = uuid4()
    await repo.complete_turn(
        UUID(row["channel_turn_id"]),
        reply_text="送你一只小猫",
        delivery_id=delivery_id,
        completed_at=datetime.now(UTC),
        parts=(
            ChannelDeliveryPartDraft(
                ordinal=0,
                kind=ChannelDeliveryPartKind.TEXT,
                payload=ChannelTextDeliveryPartPayload(text="送你一只小猫"),
            ),
            ChannelDeliveryPartDraft(
                ordinal=1,
                kind=ChannelDeliveryPartKind.IMAGE,
                required=False,
                payload=ChannelImageDeliveryPartPayload(
                    sticker_id=sticker_id, sha256=sha256, mime_type="image/png"
                ),
            ),
        ),
    )
    parts = await repo.list_delivery_parts(delivery_id)
    return delivery_id, parts[1].part_id, UUID(conn_id), UUID(row["session_id"])


async def _ack_next(
    repo: SQLiteExternalChannelRepository,
    delivery_id: UUID,
    *,
    failed: bool = False,
) -> ChannelDeliveryPartAcknowledgement:
    now = datetime.now(UTC)
    lease = uuid4()
    claim = await repo.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(delivery_id=delivery_id, lease_id=lease), claimed_at=now
    )
    assert claim is not None and claim.part is not None
    ack = ChannelDeliveryPartAcknowledgement(
        delivery_id=delivery_id,
        part_id=claim.part.part_id,
        lease_id=lease,
        status=ChannelDeliveryPartStatus.FAILED if failed else ChannelDeliveryPartStatus.DELIVERED,
        acknowledged_at=now,
        error=StructuredError(
            code="channel.send_failed",
            message="private provider error",
            retryable=False,
            component="test",
        )
        if failed
        else None,
    )
    await repo.acknowledge_delivery_part(ack, updated_at=now)
    return ack


@pytest.mark.asyncio
async def test_part_outcome_not_parent_and_duplicate_ack_restart(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        delivery, image, _, _ = await _plan(db)
        repo = SQLiteExternalChannelRepository(db)
        usage = SQLiteStickerUsageRepository(db)
        pending = await usage.history("local", "default", PRESETS)
        assert [(x.part_id, x.status, x.attempt) for x in pending.items] == [(image, "pending", 0)]
        await _ack_next(repo, delivery)
        ack = await _ack_next(repo, delivery, failed=True)
        plan = await repo.get_delivery_plan(delivery)
        assert plan is not None and plan.status is ChannelDeliveryStatus.DELIVERED
        with pytest.raises(ValueError, match="active sending lease"):
            await repo.acknowledge_delivery_part(ack, updated_at=datetime.now(UTC))
        failed = await usage.history("local", "default", PRESETS)
        assert len(failed.items) == 1 and failed.items[0].status == "failed"
        assert failed.items[0].delivered_at is None
        assert "private provider error" not in failed.model_dump_json()
        await db.close()
        await db.open()
        assert await usage.history("local", "default", PRESETS) == failed
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_retry_and_cancellation_race_keep_one_actual_success(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        delivery, image, _, _ = await _plan(db)
        repo = SQLiteExternalChannelRepository(db)
        usage = SQLiteStickerUsageRepository(db)
        await _ack_next(repo, delivery)
        now = datetime.now(UTC)
        first_lease = uuid4()
        await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(delivery_id=delivery, lease_id=first_lease),
            claimed_at=now,
        )
        assert (await usage.history("local", "default", PRESETS)).items[0].status == "sending"
        await repo.defer_delivery_part(
            ChannelDeliveryPartDeferRequest(delivery, image, first_lease, now), updated_at=now
        )
        second_lease = uuid4()
        await repo.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(delivery_id=delivery, lease_id=second_lease),
            claimed_at=now + timedelta(seconds=1),
        )
        await repo.cancel_remaining_delivery_parts(
            delivery,
            ChannelDeliveryPartsCancelRequest(reason="停一下", requested_at=now),
        )
        ack = ChannelDeliveryPartAcknowledgement(
            delivery_id=delivery,
            part_id=image,
            lease_id=second_lease,
            status=ChannelDeliveryPartStatus.DELIVERED,
            acknowledged_at=now,
        )
        for _ in range(2):
            await repo.acknowledge_delivery_part(ack, updated_at=now)
        result = await usage.history("local", "default", PRESETS)
        assert len(result.items) == 1
        assert result.items[0].status == "delivered" and result.items[0].attempt == 2
        assert result.items[0].delivered_at == now
        cancelled_delivery, _, _, _ = await _plan(db)
        await repo.cancel_remaining_delivery_parts(
            cancelled_delivery,
            ChannelDeliveryPartsCancelRequest(reason="停一下", requested_at=now),
        )
        statuses = [x.status for x in (await usage.history("local", "default", PRESETS)).items]
        assert sorted(statuses) == ["cancelled", "delivered"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_scope_hash_lineage_and_bounded_window(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        await _plan(db, scope="other")
        await _plan(db, character="other")
        await _plan(db, sha256="f" * 64)
        await _plan(db, sticker_id="missing")
        _, invalid, _, _ = await _plan(db)
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE channel_delivery_parts SET payload_json = '{}' WHERE part_id = ?",
                (str(invalid),),
            )
        _, valid, _, _ = await _plan(db)
        usage = SQLiteStickerUsageRepository(db)
        assert [x.part_id for x in (await usage.history("local", "default", PRESETS)).items] == [
            valid
        ]
        # A forged channel-to-binding identity must not escape the owner boundary.
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE channel_turns SET sender_key = 'forged' WHERE principal_scope = 'local' "
                "AND session_id IN (SELECT session_id FROM sessions WHERE character_id = 'default')"
            )
        assert (await usage.history("local", "default", PRESETS)).items == []
        for _ in range(51):
            await _plan(db)
        result = await usage.history("local", "default", PRESETS)
        assert len(result.items) == 50 and result.has_more and result.scan_limit == 200
        assert len((await usage.history("local", "default", PRESETS, limit=2)).items) == 2
        # Fill the bounded candidate window with unknown images; older matches stay outside it.
        for _ in range(201):
            await _plan(db, sticker_id="unknown")
        bounded = await usage.history("local", "default", PRESETS)
        assert bounded.items == [] and bounded.has_more
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_deleted_asset_and_removed_source_turn_do_not_resurface(tmp_path: Path) -> None:
    db = await _init_db(tmp_path)
    try:
        library = SqliteStickerLibraryRepository(db)
        await library.update_settings(
            "local", "default", learning_enabled=True, expected_revision=0
        )
        conn, gen = await _seed_source_chain(db, scope="local", character_id="default")
        saved = await library.save(
            "local",
            "default",
            StickerSaveCandidate(
                data=PNG_1X1,
                label="猫",
                description="开心的猫",
                expression="happy",
                source_connection_id=UUID(conn),
                generation_id=UUID(gen),
            ),
            expected_revision=1,
        )
        assert saved is not None
        await _plan(db, sticker_id=saved.sticker_id, sha256=hashlib.sha256(PNG_1X1).hexdigest())
        usage = SQLiteStickerUsageRepository(db)
        assert (await usage.history("local", "default", PRESETS)).items[0].origin == "learned"
        await library.delete("local", "default", saved.sticker_id)
        assert (await usage.history("local", "default", PRESETS)).items == []
        settings = await library.get_settings("local", "default")
        readded = await library.save(
            "local",
            "default",
            StickerSaveCandidate(
                data=PNG_1X1,
                label="重新学习的猫",
                description="开心的猫",
                expression="happy",
                source_connection_id=UUID(conn),
                generation_id=UUID(gen),
            ),
            expected_revision=settings.revision,
        )
        assert readded is not None and readded.sticker_id != saved.sticker_id
        assert (await usage.history("local", "default", PRESETS)).items == []
        _, _, _, session = await _plan(db)
        assert len((await usage.history("local", "default", PRESETS)).items) == 1
        # Experience reset's authoritative deletion invalidates retained delivery evidence.
        async with db.transaction() as conn:
            await conn.execute("DELETE FROM turns WHERE session_id = ?", (str(session),))
        await db.close()
        await db.open()
        assert (await usage.history("local", "default", PRESETS)).items == []
    finally:
        await db.close()


def test_usage_api_auth_scope_and_bounds(client: TestClient) -> None:
    route = "/v1/sticker-library/usage"
    assert client.get(route, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get(route + "?character_id=other").status_code == 400
    for limit in (0, 51):
        assert client.get(route + f"?limit={limit}").status_code == 422
    container = cast(RuntimeContainer, client.app.state.container)  # type: ignore[union-attr]
    assert client.portal is not None
    _, _, _, session = client.portal.call(partial(_plan, container.database))
    response = client.get(route)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["items"][0]["label"] == "开心小猫"
    assert response.json()["items"][0]["status"] == "pending"

    reset = client.post(f"/v1/sessions/{session}/reset", json={"confirm": True})
    assert reset.status_code == 200
    assert client.get(route).json()["items"] == []
