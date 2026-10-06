"""A populated 41->42 upgrade preserves facts and rejects unscoped image tails."""
# pyright: reportPrivateUsage=false

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from test_channel_group_migration import _snapshot
from test_channel_groups import NOW, _generation, _group, _open


async def test_populated_41_to_42_preserves_facts_and_rejects_arbitrary_group_media(
    tmp_path: Path,
) -> None:
    path = tmp_path / "group-stickers.db"
    database = await _open(path, through=41)
    try:
        group = await _group(database)
        admission = group.admission("111", "1")
        admitted = await group.repository.admit_group_turn(admission)
        assert await group.repository.begin_group_turn(admitted.lineage, updated_at=NOW)
        await _generation(group, admission)
        created = await group.repository.create_group_plan(
            admitted.lineage,
            reply_text="reply",
            delivery_id=uuid4(),
            completed_at=NOW,
        )
        columns, before = await _snapshot(database)
        checksums = [
            tuple(row) for row in await database.fetchall("SELECT * FROM schema_migrations")
        ]
    finally:
        await database.close()
    database = await _open(path, through=42)
    try:
        assert (await _snapshot(database, columns))[1] == before
        assert [
            tuple(row)
            for row in await database.fetchall("SELECT * FROM schema_migrations WHERE version<=41")
        ] == checksums
        assert await database.fetchall("PRAGMA foreign_key_check") == []
        check = await database.fetchone("PRAGMA quick_check")
        assert check is not None and check[0] == "ok"
        image = {
            "schema_version": "1.0",
            "kind": "image",
            "sticker_id": "learned_" + "a" * 32,
            "sha256": "b" * 64,
            "mime_type": "image/png",
        }
        for kind, required, payload in [
            ("image", 0, image),
            ("image", 1, image),
            ("audio", 1, {"kind": "audio"}),
            ("text", 1, {"kind": "text", "text": ""}),
        ]:
            with pytest.raises(sqlite3.IntegrityError, match="group delivery requires"):
                await database.execute(
                    "INSERT INTO channel_delivery_parts(part_id,delivery_id,ordinal,kind,"
                    "payload_json,required,status,delay_after_ms,attempt,provider_client_id,"
                    "created_at,updated_at) "
                    "VALUES(?,?,1,?,?,?,'pending',0,0,?,?,?)",
                    (
                        str(uuid4()),
                        str(created.delivery_id),
                        kind,
                        json.dumps(payload),
                        required,
                        uuid4().hex,
                        NOW.isoformat(),
                        NOW.isoformat(),
                    ),
                )
        with pytest.raises(sqlite3.IntegrityError, match="content is immutable"):
            await database.execute(
                "UPDATE channel_delivery_parts SET kind='image' WHERE delivery_id=?",
                (str(created.delivery_id),),
            )
    finally:
        await database.close()
