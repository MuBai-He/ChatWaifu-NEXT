"""Create a current-schema database with committed state left only in its WAL."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT / "packages" / "protocol-python" / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "runtime" / "src"))

from tools.windows import recover_runtime_database as recovery  # noqa: E402


def main() -> None:
    database = Path(sys.argv[1])
    connection = sqlite3.connect(database)
    recovery._create_current_schema(connection)  # pyright: ignore[reportPrivateUsage]
    mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).casefold() != "wal":
        raise RuntimeError(f"SQLite refused WAL mode: {mode}")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute(
        """
        INSERT INTO model_role_configs(
            role, provider, model, base_url, timeout_seconds, context_window,
            enabled, updated_at
        ) VALUES (
            'chat', 'openai_compatible', 'wal-only-model', 'http://127.0.0.1',
            30, 4096, 1, '2026-09-01T08:00:00+00:00'
        )
        """
    )
    connection.commit()
    os._exit(0)


if __name__ == "__main__":
    main()
