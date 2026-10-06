"""Merge stopped/snapshotted QQ stage data into a new primary database copy.

This operation never replaces either input. Configuration remains primary-owned;
conversation identities and provenance are copied without remapping. The caller
must separately fence live adapters, migrate the encrypted credential, and install
the verified output while both Runtime processes are stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PRIMARY_CONFIG = frozenset(
    {
        "schema_migrations",
        "model_role_configs",
        "companion_settings",
        "realtime_configurations",
        "tts_cloud_configs",
        "tts_provider_configs",
        "photo_memory_settings",
        "sticker_library_settings",
    }
)
IMPORT_TABLES = frozenset(
    {
        "participants",
        "conversation_scenes",
        "sessions",
        "turns",
        "generations",
        "events",
        "outbox",
        "memory_items",
        "memory_records",
        "memory_sources",
        "memory_proposals",
        "memory_embeddings",
        "memory_scope_resets",
        "character_states",
        "relationship_states",
        "conversation_history_dependencies",
        "skill_runs",
        "skill_tool_calls",
        "permission_requests",
        "permission_grants",
        "channel_connections",
        "channel_adapter_checkpoints",
        "channel_bindings",
        "channel_turns",
        "channel_turn_burst_members",
        "channel_deliveries",
        "channel_delivery_parts",
        "channel_participant_links",
        "channel_group_audience_observations",
        "channel_group_routes",
        "channel_group_route_versions",
        "channel_group_route_members",
        "channel_group_route_heads",
        "channel_proactive_policies",
        "channel_proactive_episodes",
        "channel_outbound_intents",
    }
)


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def ordinary_tables(db: sqlite3.Connection) -> list[str]:
    return sorted(
        str(row[1])
        for row in db.execute("PRAGMA table_list")
        if row[0] == "main" and row[2] == "table" and not row[1].startswith("sqlite_")
    )


def catalog(db: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return list(
        db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    )


def records(
    db: sqlite3.Connection, table: str
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    info = list(db.execute(f"PRAGMA table_info({quoted(table)})"))
    columns = [str(row[1]) for row in info]
    primary_key = [str(row[1]) for row in sorted(info, key=lambda row: row[5]) if row[5]]
    rows = [
        dict(zip(columns, row, strict=True)) for row in db.execute(f"SELECT * FROM {quoted(table)}")
    ]
    return columns, primary_key, rows


def assert_integrity(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("database integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("database foreign keys are invalid")


def assert_idle(db: sqlite3.Connection) -> None:
    checks = (
        ("generations", "state", ("completed", "failed", "cancelled", "timed_out")),
        ("channel_turns", "status", ("completed", "failed", "cancelled", "timed_out")),
        ("channel_deliveries", "status", ("delivered", "failed", "cancelled")),
        ("channel_delivery_parts", "status", ("delivered", "failed", "cancelled")),
        ("skill_runs", "state", ("succeeded", "failed", "cancelled", "timed_out")),
        ("channel_outbound_intents", "status", ("settled",)),
    )
    for table, column, terminal in checks:
        placeholders = ",".join("?" for _ in terminal)
        query = (
            f"SELECT count(*) FROM {quoted(table)} WHERE {quoted(column)} NOT IN ({placeholders})"
        )
        if db.execute(query, terminal).fetchone()[0]:
            raise ValueError(f"nonterminal work in {table}; do not cut over")
    if db.execute("SELECT count(*) FROM outbox WHERE published_at IS NULL").fetchone()[0]:
        raise ValueError("unpublished events; do not cut over")
    if db.execute(
        "SELECT count(*) FROM channel_group_route_heads "
        "WHERE active_channel_turn_id IS NOT NULL OR pending_channel_turn_id IS NOT NULL"
    ).fetchone()[0]:
        raise ValueError("active group work; do not cut over")


def _choose_state(table: str, primary: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    if primary["user_scope"] != "local":
        raise ValueError(f"duplicate nonowner state in {table}")
    latest = max((primary, incoming), key=lambda row: datetime.fromisoformat(row["updated_at"]))
    selected = dict(latest)
    selected["revision"] = max(primary["revision"], incoming["revision"]) + 1
    if table == "relationship_states":
        # Input sessions are disjoint, so the existing interaction counters are additive.
        # Affect/relationship scores remain the latest actual snapshot, never averaged.
        selected["interaction_count"] = primary["interaction_count"] + incoming["interaction_count"]
    return selected


def merge_database(primary: Path, qq: Path, output: Path) -> dict[str, Any]:
    if (
        output.exists()
        or output.is_symlink()
        or output.resolve() in {primary.resolve(), qq.resolve()}
    ):
        raise ValueError("output must be a new file distinct from both inputs")
    os.umask(0o077)
    source = sqlite3.connect(f"{qq.resolve().as_uri()}?mode=ro", uri=True)
    original = sqlite3.connect(f"{primary.resolve().as_uri()}?mode=ro", uri=True)
    target: sqlite3.Connection | None = None
    try:
        for db in (source, original):
            assert_integrity(db)
            assert_idle(db)
        ledger_query = "SELECT version,checksum FROM schema_migrations ORDER BY version"
        ledger = original.execute(ledger_query).fetchall()
        if (
            len(ledger) != 40
            or ledger[-1][0] != 40
            or ledger != source.execute(ledger_query).fetchall()
        ):
            raise ValueError("requires identical, fully migrated SQLite 40 databases")
        expected_catalog = catalog(original)
        if expected_catalog != catalog(source):
            raise ValueError("input schemas differ")
        source_sessions = {row[0] for row in source.execute("SELECT session_id FROM sessions")}
        primary_sessions = {row[0] for row in original.execute("SELECT session_id FROM sessions")}
        if source_sessions & primary_sessions:
            raise ValueError("input session identities overlap")
        providers = source.execute(
            "SELECT DISTINCT provider_id FROM channel_connections"
        ).fetchall()
        if (
            providers != [("qq_napcat",)]
            or original.execute(
                "SELECT count(*) FROM channel_connections WHERE provider_id='qq_napcat'"
            ).fetchone()[0]
        ):
            raise ValueError("requires a QQ-only source and a primary without QQ")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = sqlite3.connect(output)
        output.chmod(0o600)
        original.backup(target)
        target.execute("PRAGMA foreign_keys=ON")
        target.execute("BEGIN IMMEDIATE")
        target.execute("PRAGMA defer_foreign_keys=ON")
        # This guard authorizes NEW sends against the current group revision. Historical
        # delivered rows legitimately reference older revisions; restore the exact guard
        # inside this offline transaction before verifying or committing anything.
        guard = target.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='channel_group_delivery_guard'"
        ).fetchone()[0]
        target.execute("DROP TRIGGER channel_group_delivery_guard")
        imported: dict[str, int] = {}
        preserved_config: dict[str, int] = {}
        state_merges: dict[str, int] = {}
        for table in ordinary_tables(source):
            columns, pk, incoming_rows = records(source, table)
            if table in PRIMARY_CONFIG:
                preserved_config[table] = len(incoming_rows)
                continue
            if not incoming_rows:
                continue
            if table not in IMPORT_TABLES or not pk:
                raise ValueError(f"unexpected QQ source data in {table}")
            _, _, primary_rows = records(original, table)
            existing = {tuple(row[name] for name in pk): row for row in primary_rows}
            inserted = 0
            for row in incoming_rows:
                key = tuple(row[name] for name in pk)
                prior = existing.get(key)
                if prior is not None:
                    if table in {"character_states", "relationship_states"}:
                        selected = _choose_state(table, prior, row)
                        assignments = ",".join(
                            f"{quoted(name)}=?" for name in columns if name not in pk
                        )
                        where = " AND ".join(f"{quoted(name)}=?" for name in pk)
                        target.execute(
                            f"UPDATE {quoted(table)} SET {assignments} WHERE {where}",
                            [selected[name] for name in columns if name not in pk] + list(key),
                        )
                        state_merges[table] = state_merges.get(table, 0) + 1
                    elif table == "participants" and key == ("local",):
                        if any(
                            prior[name] != row[name] for name in columns if name != "created_at"
                        ):
                            raise ValueError("owner participant identity differs")
                    elif prior != row:
                        raise ValueError(f"conflicting identity in {table}")
                    continue
                placeholders = ",".join("?" for _ in columns)
                target.execute(
                    f"INSERT INTO {quoted(table)} ({','.join(map(quoted, columns))}) "
                    f"VALUES ({placeholders})",
                    [row[name] for name in columns],
                )
                inserted += 1
            imported[table] = inserted
        target.execute(guard)
        if catalog(target) != expected_catalog:
            raise ValueError("schema/trigger preservation failed")
        assert_integrity(target)
        assert_idle(target)
        # Verify EVERY retained primary and imported source row, including identity,
        # provenance, payload, fences, old receipts, and private/shared namespaces.
        for table in ordinary_tables(original):
            columns, pk, primary_rows = records(original, table)
            _, _, incoming_rows = records(source, table)
            _, _, output_rows = records(target, table)
            actual = {tuple(row[name] for name in pk): row for row in output_rows}
            expected = {tuple(row[name] for name in pk): row for row in primary_rows}
            if table not in PRIMARY_CONFIG:
                for row in incoming_rows:
                    key = tuple(row[name] for name in pk)
                    if key not in expected:
                        expected[key] = row
                    elif table in {"character_states", "relationship_states"}:
                        expected[key] = _choose_state(table, expected[key], row)
            if expected != actual:
                raise ValueError(f"row preservation failed in {table}")
        active = list(
            target.execute(
                "SELECT memory_id,text,search_terms FROM memory_records WHERE state='active'"
            )
        )
        indexed = list(target.execute("SELECT memory_id,text,search_terms FROM memory_records_fts"))
        if Counter(active) != Counter(indexed):
            raise ValueError("memory FTS projection differs from authoritative records")
        target.execute(
            "INSERT INTO memory_records_fts(memory_records_fts) VALUES ('integrity-check')"
        )
        target.commit()
        return {
            "schema_version": 40,
            "imported_rows": imported,
            "primary_configuration_preserved": preserved_config,
            "owner_state_snapshots_reconciled": state_merges,
            "all_business_rows_verified": True,
            "schema_and_triggers_preserved": True,
            "foreign_keys_ok": True,
            "integrity_ok": True,
            "memory_fts_verified": True,
            "session_scopes_unchanged": True,
        }
    except BaseException:
        if target is not None:
            target.close()
            target = None
        output.unlink(missing_ok=True)
        raise
    finally:
        if target is not None:
            target.close()
        original.close()
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--qq", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(merge_database(args.primary, args.qq, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
