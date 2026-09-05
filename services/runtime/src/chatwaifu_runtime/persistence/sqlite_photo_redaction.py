from datetime import UTC, datetime
from uuid import UUID

import aiosqlite

from chatwaifu_runtime.photo_memory.models import PhotoGenerationReference


async def redact_scope_photos(
    connection: aiosqlite.Connection, scope: str, character_id: str
) -> tuple[PhotoGenerationReference, ...]:
    now = datetime.now(UTC).isoformat()

    cursor = await connection.execute(
        """
        WITH RECURSIVE descendants AS (
            SELECT r.generation_id, r.session_id
            FROM photo_references r
            JOIN photo_assets a ON a.photo_id = r.photo_id
            WHERE a.principal_scope = ? AND a.character_id = ?

            UNION

            SELECT d.derived_generation_id, g.session_id
            FROM descendants p
            JOIN conversation_history_dependencies d ON d.source_generation_id = p.generation_id
            JOIN generations g ON g.generation_id = d.derived_generation_id
        )
        SELECT DISTINCT generation_id, session_id FROM descendants
        """,
        (scope, character_id),
    )
    rows = await cursor.fetchall()
    await cursor.close()

    refs: list[PhotoGenerationReference] = []
    for row in rows:
        gen_id = row["generation_id"]
        sess_id = row["session_id"]
        refs.append(PhotoGenerationReference(session_id=UUID(sess_id), generation_id=UUID(gen_id)))
        await connection.execute(
            """
            INSERT INTO photo_context_redactions (
                generation_id, session_id, principal_scope, character_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(generation_id) DO NOTHING
            """,
            (gen_id, sess_id, scope, character_id, now),
        )

    await connection.execute(
        "DELETE FROM photo_assets WHERE principal_scope = ? AND character_id = ?",
        (scope, character_id),
    )

    await connection.execute(
        """
        UPDATE photo_memory_settings
        SET revision = revision + 1, updated_at = ?
        WHERE principal_scope = ? AND character_id = ?
        """,
        (now, scope, character_id),
    )

    return tuple(refs)
