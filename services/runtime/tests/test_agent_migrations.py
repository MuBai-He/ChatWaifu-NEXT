"""Upgrade a populated pre-Agent delivery ledger without replaying or losing work."""

import sqlite3
from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.migrations import MIGRATIONS


@pytest.mark.asyncio
async def test_agent_upgrade_preserves_legacy_receipts_leases_and_guards(tmp_path: Path) -> None:
    path = tmp_path / "legacy-deliveries.db"
    storage = StorageConfig(database_path=path)
    legacy = Database(path, storage, migrations=tuple(item for item in MIGRATIONS if item[0] <= 44))
    await legacy.open()
    await legacy.close()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
            INSERT INTO sessions(session_id,character_id,state,conversation_state,
                created_at,updated_at)
            VALUES ('session','default','active','idle','2026-01-01','2026-01-01');
            INSERT INTO channel_connections(connection_id,provider_id,name,character_id,
                principal_scope,access_token_hash,created_at,updated_at)
            VALUES ('connection','qq','synthetic','default','local','synthetic-hash',
                '2026-01-01','2026-01-01');
            INSERT INTO channel_bindings(binding_id,connection_id,conversation_key,sender_key,
                session_id,created_at,updated_at)
            VALUES ('binding','connection','direct:synthetic','synthetic','session',
                '2026-01-01','2026-01-01');
        """)
        for status in ("pending", "sending", "delivered", "failed", "cancelled"):
            connection.execute(
                """INSERT INTO channel_turns(channel_turn_id,connection_id,binding_id,
                    external_message_id,content_sha256,conversation_key,sender_key,
                    principal_scope,session_id,turn_id,generation_id,status,accepted_at,
                    created_at,updated_at)
                VALUES (?, 'connection','binding',?,'synthetic','direct:synthetic','synthetic',
                    'local','session',?,?,'completed','2026-01-01','2026-01-01','2026-01-01')""",
                (status, status, f"turn-{status}", f"generation-{status}"),
            )
            receipt = "2026-01-01T00:00:01Z" if status == "delivered" else None
            connection.execute(
                """INSERT INTO channel_deliveries(delivery_id,channel_turn_id,connection_id,
                    binding_id,status,attempt,provider_message_id,created_at,updated_at,
                    delivered_at,lease_id,lease_expires_at)
                VALUES (?,?,'connection','binding',?,2,?,'2026-01-01','2026-01-01',?,?,?)""",
                (status, status, status, f"receipt-{status}", receipt, "lease", "2026-01-02"),
            )
            connection.execute(
                """INSERT INTO channel_delivery_parts(part_id,delivery_id,ordinal,kind,payload_json,
                    status,delay_after_ms,attempt,lease_id,lease_expires_at,provider_client_id,
                    provider_message_id,created_at,updated_at,delivered_at)
                VALUES (?,?,0,'text','{"kind":"text","text":"synthetic"}',?,1357,2,'lease',
                    '2026-01-02',?,?,'2026-01-01','2026-01-01',?)""",
                (status, status, status, f"client-{status}", f"receipt-{status}", receipt),
            )
        tables = ("channel_deliveries", "channel_delivery_parts", "companion_settings")
        columns = {
            table: ",".join(
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            )
            for table in tables
        }
        rows = {
            table: list(connection.execute(f"SELECT {columns[table]} FROM {table} ORDER BY 1"))
            for table in tables
        }
        guards = set(
            connection.execute(
                "SELECT type,name FROM sqlite_master WHERE tbl_name IN "
                "('channel_deliveries','channel_delivery_parts') AND type IN ('trigger','index')"
            )
        )
        ledger = list(connection.execute("SELECT * FROM schema_migrations ORDER BY version"))
        assert list(connection.execute("PRAGMA foreign_key_check")) == []

    upgraded = Database(path, storage)
    await upgraded.open()
    await upgraded.close()
    # A second startup must validate the same migration checksums and leave receipts settled.
    await upgraded.open()
    await upgraded.close()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for table in tables:
            assert (
                list(connection.execute(f"SELECT {columns[table]} FROM {table} ORDER BY 1"))
                == rows[table]
            )
        assert (
            list(
                connection.execute(
                    "SELECT * FROM schema_migrations WHERE version<=44 ORDER BY version"
                )
            )
            == ledger
        )
        assert guards <= set(connection.execute("SELECT type,name FROM sqlite_master"))
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT count(*) FROM agent_task_deliveries").fetchone() == (0,)
        assert connection.execute(
            "SELECT proactive_decision_mode FROM companion_settings"
        ).fetchone() == ("legacy",)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO channel_deliveries(delivery_id,connection_id,status,
                    created_at,updated_at)
                VALUES ('no-source','connection','pending','2026-01-01','2026-01-01')"""
            )
