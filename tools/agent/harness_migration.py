"""Consistent SQLite backup and auditable offline migration for the owner harness."""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_harness import SQLiteHarnessRepository


def inventory(connection: sqlite3.Connection) -> dict[str, object]:
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    counts = {
        table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        for table in (
            "knowledge_records",
            "memory_records",
            "memory_proposals",
            "harness_work_items",
            "harness_observations",
            "agent_tasks",
            "harness_action_claims",
        )
        if table in tables
    }
    digest = hashlib.sha256()
    if "knowledge_records" in tables:
        for row in connection.execute(
            "SELECT owner,record_id,payload FROM knowledge_records ORDER BY owner,record_id"
        ):
            digest.update(
                json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode()
            )
    return {
        "counts": counts,
        "knowledge_sha256": digest.hexdigest(),
        "integrity": connection.execute("PRAGMA quick_check").fetchone()[0],
    }


async def run(args: argparse.Namespace) -> None:
    database = args.database.resolve(strict=True)
    if args.backup.exists():
        raise ValueError("backup_destination_exists")
    if args.apply and not args.runtime_stopped:
        raise ValueError("stop_runtime_before_migration")
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    # The online backup API includes committed WAL pages without copying a live DB file.
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
        before = inventory(source)
        with sqlite3.connect(args.backup) as destination:
            source.backup(destination)
    args.backup.chmod(0o600)
    report = {
        "database": str(database),
        "backup": str(args.backup.resolve()),
        "before": before,
        "applied": False,
    }
    if args.apply:
        db = Database(database, StorageConfig())
        await db.open()
        try:
            await SQLiteHarnessRepository(db).import_confirmed()
        finally:
            await db.close()
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
            after = inventory(source)
        report.update({"after": after, "applied": True})
        if before["knowledge_sha256"] != after["knowledge_sha256"]:
            raise ValueError("original_knowledge_changed")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    args.report.chmod(0o600)
    print(
        json.dumps(
            {"backup": str(args.backup), "report": str(args.report), "applied": report["applied"]}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--runtime-stopped", action="store_true")
    asyncio.run(run(parser.parse_args()))
