# pyright: reportPrivateUsage=false
"""Migration 39 -> 40 preserves every old column, including legacy group receipts."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartKind,
    ChannelTextDeliveryPartPayload,
)
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.event_store import EventStore
from chatwaifu_runtime.persistence.sqlite_channel_groups import SQLiteChannelGroupRepository
from chatwaifu_runtime.persistence.sqlite_channel_proactive import SQLiteChannelProactiveRepository
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    SQLiteExternalChannelRepository,
    _binding_record,
)
from chatwaifu_runtime.sessions.service import SessionService
from test_channel_groups import _open
from test_channel_proactive_repository import NOW, _completed, _enabled, _input, _owner, _reserved


async def _snapshot(
    database: Database,
    columns: dict[str, tuple[str, ...]] | None = None,
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[tuple[object, ...], ...]]]:
    if columns is None:
        tables = await database.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        columns = {}
        for table in tables:
            name = str(table["name"])
            assert name.replace("_", "").isalnum()
            columns[name] = tuple(
                str(column["name"])
                for column in await database.fetchall(f'PRAGMA table_info("{name}")')
            )
    facts: dict[str, tuple[tuple[object, ...], ...]] = {}
    for name, fields in columns.items():
        select = ",".join(f'"{field}"' for field in fields)
        condition = " WHERE version <= 39" if name == "schema_migrations" else ""
        rows = await database.fetchall(f'SELECT {select} FROM "{name}"{condition}')
        facts[name] = tuple(sorted((tuple(row) for row in rows), key=repr))
    return columns, facts


async def test_migrated_legacy_group_binding_allows_fresh_private_without_proactive_reuse(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-private39.db"
    old = await _open(path, through=39)
    try:
        deliveries, old_proactive, connection_id, session_id, old_binding_id = await _owner(
            old, policy=_enabled()
        )
        source = await _input(deliveries, connection_id, session_id, old_binding_id)
        intent = await _reserved(old_proactive, connection_id)
        group = await deliveries.create_turn(
            replace(
                source,
                channel_turn_id=uuid4(),
                external_message_id="90001",
                chat_type=ChannelChatType.GROUP,
                conversation_key="group:500",
                turn_id=uuid4(),
                generation_id=uuid4(),
            )
        )
        columns, facts = await _snapshot(old)
    finally:
        await old.close()
    db = await _open(path)
    try:
        deliveries = SQLiteExternalChannelRepository(db, EventStore(db))
        proactive = SQLiteChannelProactiveRepository(db, EventStore(db), deliveries=deliveries)
        assert await _snapshot(db, columns) == (columns, facts)
        retained_row = await db.fetchone(
            "SELECT * FROM channel_bindings WHERE binding_id=?", (str(old_binding_id),)
        )
        assert retained_row is not None and _binding_record(retained_row).legacy_group_provenance
        assert await deliveries.find_binding(connection_id, "direct:owner") is None
        assert (await proactive.get_context(connection_id, as_of=NOW)).binding is None
        sessions = SessionService(db, EventStore(db), EventHub())
        private = await sessions.create_session("character")
        fresh = await deliveries.create_binding(
            binding_id=uuid4(),
            connection_id=connection_id,
            conversation_key="direct:owner",
            sender_key="owner",
            session_id=private.session_id,
            created_at=datetime.now(UTC),
        )
        assert fresh.session_id != session_id and not fresh.legacy_group_provenance
        assert await deliveries.find_binding(connection_id, "direct:owner") == fresh
        context = await proactive.get_context(connection_id, as_of=NOW)
        assert context.binding == fresh and context.last_owner_turn is None
        authorization = await proactive.authorize_intent(
            intent.request_id, as_of=NOW + timedelta(minutes=2)
        )
        assert not authorization.allowed
        assert authorization.reason == "owner_binding_changed"
        assert await proactive.get_intent(intent.request_id) == intent
        assert await deliveries.get_turn(group.channel_turn_id) is not None
        # Historical bindings still block desktop proactive even when quarantined.
        assert await proactive.is_channel_session(session_id)
        assert await db.fetchall("PRAGMA foreign_key_check") == []
        assert (await db.fetchone("PRAGMA integrity_check"))[0] == "ok"  # type: ignore[index]
    finally:
        await db.close()


async def test_populated39_to40_preserves_all_old_columns_ledger_and_receipts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "populated39.db"
    db = await _open(path, through=39)
    try:
        deliveries, proactive, connection_id, session_id, binding_id = await _owner(
            db, policy=_enabled()
        )
        legacy_group_id = None
        confirmed_client = None
        journal: dict[str, str | None] = {"unknown-provider-client": None}
        anchor = await _input(deliveries, connection_id, session_id, binding_id)
        intent = await _reserved(proactive, connection_id)
        assert (
            await proactive.claim_generation(
                intent.request_id, expected_revision=0, claimed_at=NOW + timedelta(minutes=2)
            )
            is not None
        )
        await _completed(db, intent)
        outbound = await proactive.create_outbound_text_plan(
            intent.request_id, reply_text="local test reply", created_at=NOW + timedelta(minutes=2)
        )
        assert outbound.plan.outbound_intent_id == intent.request_id
        for ordinal, status in enumerate(
            ("pending", "sending", "delivered", "failed", "cancelled")
        ):
            turn = await _input(deliveries, connection_id, session_id, binding_id)
            if status == "delivered":
                # Old GROUP facts have no trusted route lineage; retain them without granting it.
                turn = await deliveries.create_turn(
                    replace(
                        turn,
                        channel_turn_id=uuid4(),
                        external_message_id="-700",
                        conversation_key="group:700",
                        chat_type=ChannelChatType.GROUP,
                        turn_id=uuid4(),
                        generation_id=uuid4(),
                    )
                )
                legacy_group_id = turn.channel_turn_id
            result = await deliveries.create_delivery_plan(
                turn.channel_turn_id,
                delivery_id=uuid4(),
                parts=[
                    ChannelDeliveryPartDraft(
                        ordinal=0,
                        kind=ChannelDeliveryPartKind.TEXT,
                        payload=ChannelTextDeliveryPartPayload(text=f"old fact {ordinal}"),
                    )
                ],
                created_at=NOW,
            )
            part = result.plan.parts[0]
            receipt = "-701" if status == "delivered" else None
            lease = str(uuid4()) if status == "sending" else None
            await db.execute(
                "UPDATE channel_deliveries SET status=?,attempt=2,provider_message_id=?,"
                "delivered_at=?,lease_id=?,lease_expires_at=?,plan_version=2 WHERE delivery_id=?",
                (
                    status,
                    receipt,
                    NOW.isoformat() if receipt else None,
                    lease,
                    (NOW + timedelta(minutes=1)).isoformat() if lease else None,
                    str(result.plan.delivery_id),
                ),
            )
            await db.execute(
                "UPDATE channel_delivery_parts SET status=?,attempt=2,provider_message_id=?,"
                "delivered_at=?,lease_id=?,lease_expires_at=? WHERE part_id=?",
                (
                    status,
                    receipt,
                    NOW.isoformat() if receipt else None,
                    lease,
                    (NOW + timedelta(minutes=1)).isoformat() if lease else None,
                    str(part.part_id),
                ),
            )
            journal[part.provider_client_id] = receipt
            if receipt:
                confirmed_client = part.provider_client_id
        assert legacy_group_id is not None and confirmed_client is not None
        await db.execute(
            "INSERT INTO channel_turn_burst_members VALUES(?,?,?,?,?,?)",
            (
                str(uuid4()),
                str(legacy_group_id),
                str(anchor.channel_turn_id),
                0,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        await deliveries.set_adapter_cursor(
            connection_id, cursor=json.dumps(journal), updated_at=NOW
        )
        columns, before = await _snapshot(db)
        old_named = {
            (row["type"], row["name"])
            for row in await db.fetchall(
                "SELECT type,name FROM sqlite_master "
                "WHERE type IN ('index','trigger') AND sql IS NOT NULL"
            )
        }
        # Ensure every rebuilt table is populated, rather than proving only an empty migration.
        for name in (
            "channel_bindings",
            "channel_turns",
            "channel_turn_burst_members",
            "channel_proactive_policies",
            "channel_proactive_episodes",
            "channel_outbound_intents",
            "channel_deliveries",
            "channel_delivery_parts",
        ):
            assert before[name], name
    finally:
        await db.close()

    for _ in range(2):
        db = await _open(path)
        try:
            _, after = await _snapshot(db, columns)
            assert after == before
            assert (await db.fetchone("SELECT max(version) FROM schema_migrations"))[0] == 40  # type: ignore[index]
            assert (await db.fetchone("PRAGMA foreign_keys"))[0] == 1  # type: ignore[index]
            assert (await db.fetchone("PRAGMA integrity_check"))[0] == "ok"  # type: ignore[index]
            assert await db.fetchall("PRAGMA foreign_key_check") == []
            new_named = {
                (row["type"], row["name"])
                for row in await db.fetchall(
                    "SELECT type,name FROM sqlite_master "
                    "WHERE type IN ('index','trigger') AND sql IS NOT NULL"
                )
            }
            assert old_named <= new_named
            for name in (
                "channel_participant_links",
                "channel_group_audience_observations",
                "channel_group_routes",
                "channel_group_route_versions",
                "channel_group_route_members",
                "channel_group_route_heads",
            ):
                assert await db.fetchall(f"SELECT * FROM {name}") == []
            repository = SQLiteChannelGroupRepository(db)
            assert await repository.get_group_turn(legacy_group_id) is None
            assert await repository.find_group_turn(connection_id, "700", "-700") is None
            delivery_repository = SQLiteExternalChannelRepository(db, EventStore(db))
            assert await delivery_repository.get_adapter_cursor(connection_id) == json.dumps(
                journal
            )
            # A known legacy receipt remains confirmable after migration, without a send.
            reconciled = await delivery_repository.reconcile_known_delivery_part_receipt(
                connection_id, confirmed_client, "-701", observed_at=NOW
            )
            assert not reconciled.applied and reconciled.part is not None
            assert reconciled.part.attempt == 2 and reconciled.part.provider_message_id == "-701"
            with pytest.raises(ValueError, match="conflicts"):
                await delivery_repository.reconcile_known_delivery_part_receipt(
                    connection_id, confirmed_client, "-702", observed_at=NOW
                )
            assert (await _snapshot(db, columns))[1] == before
        finally:
            await db.close()
