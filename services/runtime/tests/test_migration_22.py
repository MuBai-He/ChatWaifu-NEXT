"""Tests for Migration 22: durable multipart delivery foundation."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartKind,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
)
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.migrations import MIGRATIONS
from chatwaifu_runtime.persistence.sqlite_external_channels import (
    SQLiteExternalChannelRepository,
)


@pytest.mark.asyncio
async def test_migration_22_empty_database(tmp_path: Path) -> None:
    path = tmp_path / "empty-migration-22.db"
    storage = StorageConfig(database_path=path)
    database = Database(path, storage)
    await database.open()
    try:
        # Check schema of channel_deliveries
        delivery_cols = {
            row[1]: row for row in await database.fetchall("PRAGMA table_info(channel_deliveries)")
        }
        assert "plan_version" in delivery_cols
        assert "cancel_requested_at" in delivery_cols

        # Check schema of channel_delivery_parts
        part_cols = {
            row[1]: row
            for row in await database.fetchall("PRAGMA table_info(channel_delivery_parts)")
        }
        assert "part_id" in part_cols
        assert "delivery_id" in part_cols
        assert "ordinal" in part_cols
        assert "kind" in part_cols
        assert "payload_json" in part_cols
        assert "required" in part_cols
        assert "status" in part_cols
        assert "delay_after_ms" in part_cols
        assert "not_before_at" in part_cols
        assert "attempt" in part_cols
        assert "lease_id" in part_cols
        assert "lease_expires_at" in part_cols
        assert "provider_client_id" in part_cols
        assert "provider_message_id" in part_cols
        assert "last_error_json" in part_cols
        assert "created_at" in part_cols
        assert "updated_at" in part_cols
        assert "delivered_at" in part_cols

        # Check indexes
        index_names = {
            row[1] for row in await database.fetchall("PRAGMA index_list(channel_delivery_parts)")
        }
        assert "channel_delivery_parts_delivery_idx" in index_names
        assert "channel_delivery_parts_claim_idx" in index_names
        assert "channel_delivery_parts_lease_idx" in index_names
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_migration_22_non_empty_database_backfill(tmp_path: Path) -> None:
    path = tmp_path / "non-empty-v21.db"
    storage = StorageConfig(database_path=path)

    # 1. Initialize with migrations up to v21
    legacy_db = Database(
        path,
        storage,
        migrations=tuple(item for item in MIGRATIONS if item[0] <= 21),
    )
    await legacy_db.open()
    await legacy_db.close()

    # 2. Seed v21 data: connections, bindings, turns, deliveries with various statuses
    now = datetime.now(UTC)
    conn_id = str(uuid4())
    binding_id = str(uuid4())
    session_id = str(uuid4())
    turn_1_id = str(uuid4())
    del_1_id = str(uuid4())

    turn_2_id = str(uuid4())
    del_2_id = str(uuid4())

    turn_3_id = str(uuid4())
    del_3_id = str(uuid4())

    turn_4_id = str(uuid4())
    del_4_id = str(uuid4())

    lease_id = str(uuid4())
    lease_expires = (now + timedelta(seconds=60)).isoformat()

    with sqlite3.connect(path) as connection:
        # Create channel connection
        connection.execute(
            """
            INSERT INTO channel_connections(
                connection_id, provider_id, name, character_id, principal_scope,
                status, access_token_hash, created_at, updated_at
            ) VALUES (?, 'weixin_ilink', '我的微信', 'ayachi_nene', 'local', 'ready', 'hash', ?, ?)
            """,
            (conn_id, now.isoformat(), now.isoformat()),
        )
        # Create session
        connection.execute(
            """
            INSERT INTO sessions(
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'ayachi_nene', 'ready', 'idle', 0, 1, ?, ?)
            """,
            (session_id, now.isoformat(), now.isoformat()),
        )
        # Create channel binding
        connection.execute(
            """
            INSERT INTO channel_bindings(
                binding_id, connection_id, conversation_key, sender_key,
                session_id, created_at, updated_at
            ) VALUES (?, ?, 'c1', 'u1', ?, ?, ?)
            """,
            (binding_id, conn_id, session_id, now.isoformat(), now.isoformat()),
        )

        # Turn 1: delivered delivery
        connection.execute(
            """
            INSERT INTO channel_turns(
                channel_turn_id, connection_id, binding_id, external_message_id,
                content_sha256, account_key, conversation_key, chat_type,
                conversation_label, sender_key, sender_display_name, principal_scope,
                session_id, turn_id, generation_id, status, reply_text,
                delivery_id, revision, accepted_at, created_at, updated_at, completed_at
            ) VALUES (
                ?, ?, ?, 'm1', 'sha1', 'acc1', 'c1', 'direct', 'label', 'u1', 'name1', 'local',
                ?, ?, ?, 'completed', 'reply 1', ?, 1, ?, ?, ?, ?
            )
            """,
            (
                turn_1_id,
                conn_id,
                binding_id,
                session_id,
                str(uuid4()),
                str(uuid4()),
                del_1_id,
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO channel_deliveries(
                delivery_id, channel_turn_id, connection_id, status, attempt,
                provider_message_id, created_at, updated_at, delivered_at
            ) VALUES (?, ?, ?, 'delivered', 1, 'prov-msg-1', ?, ?, ?)
            """,
            (
                del_1_id,
                turn_1_id,
                conn_id,
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )

        # Turn 2: failed delivery
        err_json = json.dumps({"code": "send_failed", "message": "error msg"})
        connection.execute(
            """
            INSERT INTO channel_turns(
                channel_turn_id, connection_id, binding_id, external_message_id,
                content_sha256, account_key, conversation_key, chat_type,
                conversation_label, sender_key, sender_display_name, principal_scope,
                session_id, turn_id, generation_id, status, reply_text,
                error_json, delivery_id, revision, accepted_at, created_at, updated_at, completed_at
            ) VALUES (
                ?, ?, ?, 'm2', 'sha2', 'acc1', 'c1', 'direct', 'label', 'u1', 'name1', 'local',
                ?, ?, ?, 'failed', 'reply 2', ?, ?, 1, ?, ?, ?, ?
            )
            """,
            (
                turn_2_id,
                conn_id,
                binding_id,
                session_id,
                str(uuid4()),
                str(uuid4()),
                err_json,
                del_2_id,
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO channel_deliveries(
                delivery_id, channel_turn_id, connection_id, status, attempt,
                last_error_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'failed', 2, ?, ?, ?)
            """,
            (del_2_id, turn_2_id, conn_id, err_json, now.isoformat(), now.isoformat()),
        )

        # Turn 3: sending delivery with active lease
        connection.execute(
            """
            INSERT INTO channel_turns(
                channel_turn_id, connection_id, binding_id, external_message_id,
                content_sha256, account_key, conversation_key, chat_type,
                conversation_label, sender_key, sender_display_name, principal_scope,
                session_id, turn_id, generation_id, status, reply_text,
                delivery_id, revision, accepted_at, created_at, updated_at, completed_at
            ) VALUES (
                ?, ?, ?, 'm3', 'sha3', 'acc1', 'c1', 'direct', 'label', 'u1', 'name1', 'local',
                ?, ?, ?, 'completed', 'reply 3', ?, 1, ?, ?, ?, ?
            )
            """,
            (
                turn_3_id,
                conn_id,
                binding_id,
                session_id,
                str(uuid4()),
                str(uuid4()),
                del_3_id,
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO channel_deliveries(
                delivery_id, channel_turn_id, connection_id, status, attempt,
                lease_id, lease_expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'sending', 1, ?, ?, ?, ?)
            """,
            (
                del_3_id,
                turn_3_id,
                conn_id,
                lease_id,
                lease_expires,
                now.isoformat(),
                now.isoformat(),
            ),
        )

        # Turn 4: cancelled delivery
        connection.execute(
            """
            INSERT INTO channel_turns(
                channel_turn_id, connection_id, binding_id, external_message_id,
                content_sha256, account_key, conversation_key, chat_type,
                conversation_label, sender_key, sender_display_name, principal_scope,
                session_id, turn_id, generation_id, status, reply_text,
                delivery_id, revision, accepted_at, created_at, updated_at, completed_at
            ) VALUES (
                ?, ?, ?, 'm4', 'sha4', 'acc1', 'c1', 'direct', 'label', 'u1', 'name1', 'local',
                ?, ?, ?, 'cancelled', 'reply 4', ?, 1, ?, ?, ?, ?
            )
            """,
            (
                turn_4_id,
                conn_id,
                binding_id,
                session_id,
                str(uuid4()),
                str(uuid4()),
                del_4_id,
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO channel_deliveries(
                delivery_id, channel_turn_id, connection_id, status, attempt,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'cancelled', 1, ?, ?)
            """,
            (del_4_id, turn_4_id, conn_id, now.isoformat(), now.isoformat()),
        )

    # 3. Migrate to v22
    migrated_db = Database(path, storage)
    await migrated_db.open()
    try:
        parts = await migrated_db.fetchall(
            "SELECT * FROM channel_delivery_parts ORDER BY delivery_id"
        )
        assert len(parts) == 4

        # Verify delivered Part 1
        part_1 = next(p for p in parts if str(p["delivery_id"]) == del_1_id)
        assert part_1["ordinal"] == 0
        assert part_1["kind"] == "text"
        p1_payload = json.loads(str(part_1["payload_json"]))
        assert p1_payload["text"] == "reply 1"
        assert p1_payload.get("schema_version") == "1.0"
        assert part_1["status"] == "delivered"
        assert part_1["provider_message_id"] == "prov-msg-1"
        assert part_1["provider_client_id"] == f"chatwaifu-{del_1_id.replace('-', '')}-000"
        assert part_1["delivered_at"] is not None

        # Verify failed Part 2
        part_2 = next(p for p in parts if str(p["delivery_id"]) == del_2_id)
        assert part_2["ordinal"] == 0
        assert part_2["status"] == "failed"
        assert part_2["last_error_json"] == err_json

        # Verify sending Part 3
        part_3 = next(p for p in parts if str(p["delivery_id"]) == del_3_id)
        assert part_3["ordinal"] == 0
        assert part_3["status"] == "sending"
        assert str(part_3["lease_id"]) == lease_id
        assert part_3["lease_expires_at"] == lease_expires

        # Verify cancelled Part 4
        part_4 = next(p for p in parts if str(p["delivery_id"]) == del_4_id)
        assert part_4["ordinal"] == 0
        assert part_4["status"] == "cancelled"

        # Check channel_deliveries plan_version
        deliveries = await migrated_db.fetchall(
            "SELECT plan_version, cancel_requested_at FROM channel_deliveries"
        )
        for d in deliveries:
            assert d["plan_version"] == 1
            assert d["cancel_requested_at"] is None

        # 4. Repository can query migrated records
        repo = SQLiteExternalChannelRepository(migrated_db)
        plan_1 = await repo.get_delivery_plan_by_turn(UUID(turn_1_id))
        assert plan_1 is not None
        assert plan_1.status == ChannelDeliveryStatus.DELIVERED
        assert plan_1.part_count == 1
        assert plan_1.delivered_part_count == 1
        assert len(plan_1.parts) == 1
        assert plan_1.parts[0].status == ChannelDeliveryPartStatus.DELIVERED
        assert plan_1.parts[0].kind == ChannelDeliveryPartKind.TEXT
        assert plan_1.parts[0].payload.text == "reply 1"
    finally:
        await migrated_db.close()


@pytest.mark.asyncio
async def test_migration_22_checksum_and_idempotency(tmp_path: Path) -> None:
    path = tmp_path / "idempotent-v22.db"
    storage = StorageConfig(database_path=path)

    # 1. Open database and apply all migrations including v22
    db = Database(path, storage)
    await db.open()
    rows = await db.fetchall(
        "SELECT version, checksum, applied_at FROM schema_migrations WHERE version = 22"
    )
    assert len(rows) == 1
    v22_row = rows[0]
    assert v22_row["version"] == 22
    checksum = v22_row["checksum"]
    assert checksum and len(checksum) > 0
    await db.close()

    # 2. Re-open database; verify migrations are idempotent and checksum is verified
    db_reopened = Database(path, storage)
    await db_reopened.open()
    rows_reopened = await db_reopened.fetchall(
        "SELECT version, checksum, applied_at FROM schema_migrations WHERE version = 22"
    )
    assert len(rows_reopened) == 1
    assert rows_reopened[0]["checksum"] == checksum
    await db_reopened.close()
