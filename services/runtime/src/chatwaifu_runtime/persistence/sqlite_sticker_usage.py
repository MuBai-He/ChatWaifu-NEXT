"""Bounded read projection of the authoritative image-part delivery state.

No counters, callbacks, backfills or new database state: replay and missed event
notifications cannot create additional usage. Reads share a SQLite snapshot with
asset and source checks, including removal of source turns by experience reset.
"""

from collections.abc import Mapping

from chatwaifu_protocol.channels import ChannelImageDeliveryPartPayload
from chatwaifu_protocol.sticker_library import StickerUsageHistory, StickerUsageRecord
from pydantic import ValidationError

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.sticker_library.usage import StickerUsagePreset

SCAN_LIMIT = 200


class SQLiteStickerUsageRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def history(
        self,
        scope: str,
        character_id: str,
        presets: Mapping[str, StickerUsagePreset],
        *,
        limit: int = 50,
    ) -> StickerUsageHistory:
        if not 1 <= limit <= 50:
            raise ValueError("history limit must be between 1 and 50")
        async with self._database.transaction() as connection:
            cursor = await connection.execute(
                """
                SELECT p.part_id, p.payload_json, p.status, p.attempt,
                       p.created_at, p.updated_at, p.delivered_at
                FROM channel_turns t
                JOIN channel_deliveries d ON d.channel_turn_id = t.channel_turn_id
                    AND d.connection_id = t.connection_id AND t.delivery_id = d.delivery_id
                JOIN channel_delivery_parts p ON p.delivery_id = d.delivery_id
                JOIN sessions s ON s.session_id = t.session_id
                JOIN turns u ON u.turn_id = t.turn_id AND u.session_id = s.session_id
                JOIN generations g ON g.generation_id = t.generation_id
                    AND g.turn_id = u.turn_id AND g.session_id = s.session_id
                JOIN channel_bindings b ON b.binding_id = t.binding_id
                    AND b.connection_id = t.connection_id AND b.session_id = t.session_id
                    AND b.sender_key = t.sender_key AND b.conversation_key = t.conversation_key
                WHERE t.principal_scope = ? AND s.character_id = ?
                    AND t.chat_type = 'direct' AND u.role = 'user' AND p.kind = 'image'
                ORDER BY p.created_at DESC, p.part_id DESC
                LIMIT ?
                """,
                (scope, character_id, SCAN_LIMIT + 1),
            )
            rows = list(await cursor.fetchall())
            await cursor.close()
            # Library capacity is bounded at 100. Never fetch BLOBs for history.
            cursor = await connection.execute(
                """SELECT sticker_id, sha256, label FROM learned_stickers
                   WHERE principal_scope = ? AND character_id = ? LIMIT 100""",
                (scope, character_id),
            )
            learned = {str(r["sticker_id"]): r for r in await cursor.fetchall()}
            await cursor.close()

            items: list[StickerUsageRecord] = []
            more = len(rows) > SCAN_LIMIT
            for row in rows[:SCAN_LIMIT]:
                try:
                    payload = ChannelImageDeliveryPartPayload.model_validate_json(
                        row["payload_json"]
                    )
                    if payload.sticker_id.startswith("learned_"):
                        asset = learned.get(payload.sticker_id)
                        if asset is None or asset["sha256"] != payload.sha256:
                            continue
                        label = str(asset["label"])
                        kind = "learned"
                    else:
                        preset = presets.get(payload.sticker_id)
                        if preset is None or preset.sha256 != payload.sha256:
                            continue
                        label = preset.label
                        kind = "preset"
                    record = StickerUsageRecord(
                        part_id=row["part_id"],
                        sticker_id=payload.sticker_id,
                        label=label,
                        origin=kind,
                        status=row["status"],
                        attempt=row["attempt"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        delivered_at=row["delivered_at"],
                    )
                except (ValidationError, ValueError, TypeError):
                    # Unknown/corrupted historical payloads are not usage evidence.
                    continue
                if len(items) == limit:
                    more = True
                    break
                items.append(record)
        return StickerUsageHistory(items=items, has_more=more)
