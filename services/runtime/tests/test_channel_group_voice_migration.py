"""Populated 42→43 preservation and actual SQLite voice admission guards."""

# pyright: reportPrivateUsage=false

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from chatwaifu_protocol.channels import ChannelAudioDeliveryPartPayload
from chatwaifu_runtime.external_channels.service import ChannelPolicyError
from test_channel_group_migration import _snapshot
from test_channel_groups import NOW, _generation, _group, _open


async def test_populated_42_to_43_preserves_old_rows_and_defaults_voice_off(tmp_path: Path) -> None:
    path = tmp_path / "group-voice.db"
    db = await _open(path, through=42)
    try:
        group = await _group(db)
        admission = group.admission("111", "1")
        admitted = await group.repository.admit_group_turn(admission)
        assert await group.repository.begin_group_turn(admitted.lineage, updated_at=NOW)
        await _generation(group, admission)
        await group.repository.create_group_plan(
            admitted.lineage, reply_text="reply", delivery_id=uuid4(), completed_at=NOW
        )
        columns, before = await _snapshot(db)
        migrations = [tuple(r) for r in await db.fetchall("SELECT * FROM schema_migrations")]
    finally:
        await db.close()
    db = await _open(path, through=43)
    try:
        assert (await _snapshot(db, columns))[1] == before
        assert [
            tuple(r) for r in await db.fetchall("SELECT * FROM schema_migrations WHERE version<=42")
        ] == migrations
        for table in ("channel_group_routes", "channel_group_route_versions"):
            rows = await db.fetchall(f"SELECT allow_requested_voice FROM {table}")
            assert rows and all(r[0] == 0 for r in rows)
        assert await db.fetchall("PRAGMA foreign_key_check") == []
        result = await db.fetchone("PRAGMA quick_check")
        assert result is not None and result[0] == "ok"
    finally:
        await db.close()


async def test_sql_audio_is_bounded_and_requires_opt_in_and_running_canonical_reply(
    tmp_path: Path,
) -> None:
    db = await _open(tmp_path / "voice-guards.db", through=43)
    try:
        group = await _group(db)
        payload = ChannelAudioDeliveryPartPayload(
            asset_id=uuid4(), sha256="a" * 64, duration_ms=500, text="good morning"
        )
        denied = group.admission("111", "1", text="用语音说早上好")
        admitted = await group.repository.admit_group_turn(denied)
        assert await group.repository.begin_group_turn(admitted.lineage, updated_at=NOW)
        await _generation(group, denied)
        with pytest.raises(ChannelPolicyError):
            await group.repository.create_group_voice_plan(
                admitted.lineage, payload=payload, delivery_id=uuid4(), created_at=NOW
            )
        changed = await group.repository.update_route(
            group.route.route_id,
            expected_revision=group.route.revision,
            enabled=True,
            observation_id=group.route.observation_id,
            members=group.route.members,
            scene_id=group.route.scene_id,
            updated_at=NOW,
            allow_requested_voice=True,
        )
        assert changed.route is not None
        group.route = changed.route
        await group.repository.release_group_active(
            group.route.route_id, admitted.turn.channel_turn_id, updated_at=NOW
        )
        request = group.admission("111", "2", text="用语音说早上好")
        admitted = await group.repository.admit_group_turn(request)
        assert await group.repository.begin_group_turn(admitted.lineage, updated_at=NOW)
        await _generation(group, request)
        await db.execute(
            "UPDATE turns SET committed_text=? WHERE turn_id=?",
            (request.message.text, str(request.turn_id)),
        )
        # A completed text generation cannot be retrofitted into a voice action.
        with pytest.raises(ChannelPolicyError):
            await group.repository.create_group_voice_plan(
                admitted.lineage, payload=payload, delivery_id=uuid4(), created_at=NOW
            )
        await db.execute(
            "UPDATE generations SET state='running',completed_at=NULL,output_text=NULL "
            "WHERE generation_id=?",
            (str(request.generation_id),),
        )
        created = await group.repository.create_group_voice_plan(
            admitted.lineage, payload=payload, delivery_id=uuid4(), created_at=NOW
        )
        with pytest.raises(sqlite3.IntegrityError, match="content is immutable"):
            await db.execute(
                "UPDATE channel_delivery_parts SET required=0 WHERE delivery_id=?",
                (str(created.delivery_id),),
            )
        # Exercise the insert trigger directly in this isolated test DB; no send is involved.
        await db.execute(
            "DELETE FROM channel_delivery_parts WHERE delivery_id=?", (str(created.delivery_id),)
        )
        valid = payload.model_dump(mode="json")
        invalid = [
            {k: v for k, v in valid.items() if k != "asset_id"},
            {**valid, "sha256": "x" * 64},
            {**valid, "duration_ms": 120001},
            {**valid, "mime_type": "audio/mpeg"},
            {**valid, "text": "different reply"},
        ]
        for raw in invalid:
            with pytest.raises(sqlite3.IntegrityError, match="authorized bounded reply parts"):
                await db.execute(
                    "INSERT INTO channel_delivery_parts(part_id,delivery_id,ordinal,kind,"
                    "payload_json,required,status,delay_after_ms,attempt,provider_client_id,"
                    "created_at,updated_at) "
                    "VALUES(?,?,0,'audio',?,1,'pending',0,0,?,?,?)",
                    (
                        str(uuid4()),
                        str(created.delivery_id),
                        json.dumps(raw),
                        uuid4().hex,
                        NOW.isoformat(),
                        NOW.isoformat(),
                    ),
                )
    finally:
        await db.close()
