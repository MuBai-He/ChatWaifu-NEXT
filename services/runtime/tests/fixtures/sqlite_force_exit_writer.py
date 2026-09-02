"""Child process used to exercise abrupt SQLite WAL recovery on Windows."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "runtime" / "src"))

from chatwaifu_runtime.config.settings import StorageConfig  # noqa: E402
from chatwaifu_runtime.persistence.database import Database  # noqa: E402

SESSION_ID = "00000000-0000-0000-0000-000000000001"
TURN_ID = "00000000-0000-0000-0000-000000000002"
GENERATION_ID = "00000000-0000-0000-0000-000000000003"
SEGMENT_ID = "00000000-0000-0000-0000-000000000004"
STREAM_ID = "00000000-0000-0000-0000-000000000005"


async def main(database_path: Path, warmup_transactions: int) -> None:
    database = Database(
        database_path,
        StorageConfig(
            database_path=database_path,
            synchronous="full",
            wal_autocheckpoint_pages=8,
        ),
    )
    await database.open()
    now = datetime.now(UTC).isoformat()
    async with database.transaction() as connection:
        await connection.execute(
            """
            INSERT OR IGNORE INTO sessions(
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'default', 'ready', 'idle', 0, 1, ?, ?)
            """,
            (SESSION_ID, now, now),
        )
        await connection.execute(
            """
            INSERT OR IGNORE INTO turns(
                turn_id, session_id, role, committed_text, committed_at, created_at
            ) VALUES (?, ?, 'assistant', '', ?, ?)
            """,
            (TURN_ID, SESSION_ID, now, now),
        )
        await connection.execute(
            """
            INSERT OR IGNORE INTO generations(
                generation_id, session_id, turn_id, state, backend_kind,
                started_at, output_text, spoken_text, audio_stream_id
            ) VALUES (?, ?, ?, 'streaming', 'cascade', ?, '', '', ?)
            """,
            (GENERATION_ID, SESSION_ID, TURN_ID, now, STREAM_ID),
        )
        await connection.execute(
            """
            INSERT OR IGNORE INTO playback_segments(
                segment_id, stream_id, session_id, generation_id, segment_index,
                text, duration_ms, duration_finalized, state, queued_at
            ) VALUES (?, ?, ?, ?, 0, '', 0, 1, 'queued', ?)
            """,
            (SEGMENT_ID, STREAM_ID, SESSION_ID, GENERATION_ID, now),
        )

    payload = "x" * 8192
    for index in range(warmup_transactions + 1):
        event_id = str(uuid4())
        command_id = str(uuid4())
        occurred_at = datetime.now(UTC).isoformat()
        envelope = json.dumps(
            {
                "event_id": event_id,
                "event_type": "test.force_exit",
                "payload": payload,
            },
            separators=(",", ":"),
        )
        async with database.transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE sessions
                SET next_sequence = next_sequence + 1, updated_at = ?
                WHERE session_id = ?
                RETURNING next_sequence - 1
                """,
                (occurred_at, SESSION_ID),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise RuntimeError("force-exit fixture session disappeared")
            await connection.execute(
                """
                INSERT INTO events(
                    event_id, session_id, sequence, event_type, schema_version,
                    occurred_at, source, payload_json, envelope_json
                ) VALUES (?, ?, ?, 'test.force_exit', '1.0', ?, 'test', ?, ?)
                """,
                (event_id, SESSION_ID, int(row[0]), occurred_at, envelope, envelope),
            )
            await connection.execute(
                """
                INSERT INTO outbox(event_id, envelope_json, created_at)
                VALUES (?, ?, ?)
                """,
                (event_id, envelope, occurred_at),
            )
            await connection.execute(
                """
                INSERT INTO playback_ack_commands(command_id, segment_id, phase, received_at)
                VALUES (?, ?, 'progress', ?)
                """,
                (command_id, SEGMENT_ID, occurred_at),
            )
            if index == warmup_transactions:
                print("READY_TO_TERMINATE", flush=True)

    raise RuntimeError("parent did not terminate the crash fixture")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), int(sys.argv[2])))
