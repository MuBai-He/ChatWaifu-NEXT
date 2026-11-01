"""Shared SQLite photo record decoding."""

import json
from datetime import datetime
from uuid import UUID

import aiosqlite
from chatwaifu_protocol.photo_memory import SavedPhoto


def saved_photo_from_row(row: aiosqlite.Row) -> SavedPhoto:
    keys = row.keys()
    return SavedPhoto(
        user_annotations=json.loads(row["user_annotations_json"])
        if "user_annotations_json" in row.keys()
        else [],
        photo_id=UUID(row["photo_id"]),
        sha256=row["sha256"],
        mime_type=row["mime_type"],
        byte_size=row["byte_size"],
        width=row["width"],
        height=row["height"],
        title=row["title"],
        description=row["description"],
        confidence=row["confidence"],
        keywords=json.loads(row["keywords"]),
        caption=row["caption"],
        received_at=datetime.fromisoformat(row["received_at"]),
        saved_at=datetime.fromisoformat(row["saved_at"]),
        source_connection_id=UUID(row["source_connection_id"]),
        source_session_id=UUID(row["source_session_id"]),
        source_turn_id=UUID(row["source_turn_id"]),
        source_generation_id=UUID(row["source_generation_id"]),
        captured_at=row["captured_at"] if "captured_at" in keys else None,
        captured_at_offset=row["captured_at_offset"] if "captured_at_offset" in keys else None,
        original_width=row["original_width"] if "original_width" in keys else None,
        original_height=row["original_height"] if "original_height" in keys else None,
        original_mime_type=row["original_mime_type"] if "original_mime_type" in keys else None,
    )
