"""SQLite-backed companion preferences; Web never persists them directly."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast

from chatwaifu_runtime.companion.models import CompanionSettings, CompanionSettingsUpdate
from chatwaifu_runtime.persistence.database import Database


class CompanionSettingsService:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._cached = CompanionSettings()

    async def start(self) -> None:
        row = await self._database.fetchone(
            "SELECT * FROM companion_settings WHERE singleton_id = 1"
        )
        if row is not None:
            self._cached = _from_row(dict(row))

    def get(self) -> CompanionSettings:
        return self._cached

    async def update(self, update: CompanionSettingsUpdate) -> CompanionSettings:
        value = update.validated()
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                UPDATE companion_settings SET
                    wake_phrase_enabled = ?, wake_phrases_json = ?,
                    quiet_hours_enabled = ?, quiet_start = ?, quiet_end = ?,
                    proactive_enabled = ?, proactive_idle_minutes = ?,
                    proactive_cooldown_minutes = ?, proactive_daily_budget = ?,
                    resource_sleep_enabled = ?, resource_idle_minutes = ?,
                    updated_at = ?
                WHERE singleton_id = 1
                """,
                (
                    int(value.wake_phrase_enabled),
                    json.dumps(value.wake_phrases, ensure_ascii=False),
                    int(value.quiet_hours_enabled),
                    value.quiet_start,
                    value.quiet_end,
                    int(value.proactive_enabled),
                    value.proactive_idle_minutes,
                    value.proactive_cooldown_minutes,
                    value.proactive_daily_budget,
                    int(value.resource_sleep_enabled),
                    value.resource_idle_minutes,
                    value.updated_at.isoformat(),
                ),
            )
        self._cached = value
        return value


def _from_row(row: dict[str, object]) -> CompanionSettings:
    parsed_phrases = cast(object, json.loads(str(row["wake_phrases_json"])))
    if not isinstance(parsed_phrases, list):
        raise ValueError("stored wake phrases are invalid")
    raw_phrases = cast(list[object], parsed_phrases)
    phrases: list[str] = []
    for item in raw_phrases:
        if not isinstance(item, str):
            raise ValueError("stored wake phrases are invalid")
        phrases.append(item)
    updated_at = datetime.fromisoformat(str(row["updated_at"]))
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return CompanionSettings(
        wake_phrase_enabled=bool(row["wake_phrase_enabled"]),
        wake_phrases=tuple(phrases),
        quiet_hours_enabled=bool(row["quiet_hours_enabled"]),
        quiet_start=str(row["quiet_start"]),
        quiet_end=str(row["quiet_end"]),
        proactive_enabled=bool(row["proactive_enabled"]),
        proactive_idle_minutes=int(str(row["proactive_idle_minutes"])),
        proactive_cooldown_minutes=int(str(row["proactive_cooldown_minutes"])),
        proactive_daily_budget=int(str(row["proactive_daily_budget"])),
        resource_sleep_enabled=bool(row["resource_sleep_enabled"]),
        resource_idle_minutes=int(str(row["resource_idle_minutes"])),
        updated_at=updated_at,
    )
