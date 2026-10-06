"""Populated 40->41 migration and the receipt-only group cadence boundary."""

# pyright: reportPrivateUsage=false

import sqlite3
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartStatus,
)
from chatwaifu_runtime.external_channels.presentation import (
    InstantMessageDeliveryPlanFactory,
    messaging_presentation_policy,
)
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_external_channels import SQLiteExternalChannelRepository
from test_channel_group_migration import _snapshot
from test_channel_groups import NOW, _generation, _group, _open


async def test_populated_40_to_41_preserves_all_facts_and_migration_checksums(
    tmp_path: Path,
) -> None:
    path = tmp_path / "group-bubbles.db"
    database = await _open(path, through=40)
    try:
        original = await _group(database)
        admission = original.admission("111", "1")
        admitted = await original.repository.admit_group_turn(admission)
        assert await original.repository.begin_group_turn(admitted.lineage, updated_at=NOW)
        await _generation(original, admission)
        old_plan = await original.repository.create_group_plan(
            admitted.lineage, reply_text="reply", delivery_id=uuid4(), completed_at=NOW
        )
        columns, before = await _snapshot(database)
        checksums = [
            tuple(row) for row in await database.fetchall("SELECT * FROM schema_migrations")
        ]
    finally:
        await database.close()

    database = await _open(path, through=41)
    try:
        _, after = await _snapshot(database, columns)
        assert after == before
        assert [
            tuple(row)
            for row in await database.fetchall("SELECT * FROM schema_migrations WHERE version<=40")
        ] == checksums
        assert await database.fetchall("PRAGMA foreign_key_check") == []
        integrity = await database.fetchone("PRAGMA quick_check")
        assert integrity is not None and integrity[0] == "ok"
        deliveries = SQLiteExternalChannelRepository(database, EventStore(database))
        old = await deliveries.get_delivery_plan(old_plan.delivery_id)
        assert old is not None and old.part_count == 1

        group = await _group(database, group_id="501", existing=original)
        admission = group.admission("111", "2")
        admitted = await group.repository.admit_group_turn(admission)
        assert await group.repository.begin_group_turn(admitted.lineage, updated_at=NOW)
        text = "在呀。\n\n怎么啦？"
        await _generation(group, admission, text=text)
        parts = InstantMessageDeliveryPlanFactory().create_parts(
            text, policy=messaging_presentation_policy()
        )
        created = await group.repository.create_group_plan(
            admitted.lineage,
            reply_text=text,
            delivery_id=uuid4(),
            completed_at=NOW,
            parts=parts,
        )
        plan = await deliveries.get_delivery_plan(created.delivery_id)
        assert plan is not None and plan.part_count == 2
        with pytest.raises(sqlite3.IntegrityError, match="content is immutable"):
            await database.execute(
                "UPDATE channel_delivery_parts SET payload_json='{}' WHERE part_id=?",
                (str(plan.parts[1].part_id),),
            )
        with pytest.raises(sqlite3.IntegrityError, match="requires previous receipt"):
            await database.execute(
                "UPDATE channel_delivery_parts SET not_before_at=? WHERE part_id=?",
                (NOW.isoformat(), str(plan.parts[1].part_id)),
            )
        first = await deliveries.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(delivery_id=created.delivery_id, lease_id=uuid4()),
            claimed_at=NOW,
        )
        assert first is not None and first.part is not None and first.part.lease_id is not None
        transition = await deliveries.acknowledge_delivery_part(
            ChannelDeliveryPartAcknowledgement(
                delivery_id=created.delivery_id,
                part_id=first.part.part_id,
                lease_id=first.part.lease_id,
                status=ChannelDeliveryPartStatus.DELIVERED,
                provider_message_id="fixture-receipt",
                acknowledged_at=NOW,
            ),
            updated_at=NOW,
        )
        tail = transition.plan.parts[1]
        assert tail.not_before_at == NOW + timedelta(milliseconds=parts[0].delay_after_ms)
        with pytest.raises(sqlite3.IntegrityError, match="requires previous receipt"):
            await database.execute(
                "UPDATE channel_delivery_parts SET not_before_at=? WHERE part_id=?",
                ((NOW + timedelta(hours=1)).isoformat(), str(tail.part_id)),
            )
    finally:
        await database.close()

    database = await _open(path, through=41)
    try:
        deliveries = SQLiteExternalChannelRepository(database, EventStore(database))
        resumed = await deliveries.get_delivery_plan(created.delivery_id)
        assert resumed is not None and resumed.parts[1].not_before_at == tail.not_before_at
        assert resumed.parts[0].provider_message_id == "fixture-receipt"
        assert resumed.parts[1].provider_client_id == tail.provider_client_id
        second = await deliveries.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(delivery_id=created.delivery_id, lease_id=uuid4()),
            claimed_at=NOW + timedelta(seconds=10),
        )
        assert second is not None and second.part is not None and second.part.ordinal == 1
    finally:
        await database.close()
