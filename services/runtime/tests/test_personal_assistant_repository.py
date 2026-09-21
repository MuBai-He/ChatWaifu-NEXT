"""Real SQLite boundaries for calendar synchronization and account revocation."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_personal_assistant import SQLiteAssistantRepository
from chatwaifu_runtime.personal_assistant.google_calendar import Calendar, CalendarEvent, EventSync
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[tuple[Database, SQLiteAssistantRepository]]:
    path = tmp_path / "assistant.db"
    db = Database(path, StorageConfig(database_path=path))
    await db.open()
    repo = SQLiteAssistantRepository(db)
    await repo.connect("local", "account", "secret-reference-only")
    await repo.add_calendar("local", "account", Calendar("calendar", "Existing", "UTC", "owner"))
    await repo.select("local", "account", "calendar", True)
    try:
        yield db, repo
    finally:
        await db.close()


def batch(token: str, *ids: str, replace: bool = True) -> EventSync:
    return EventSync(
        tuple(CalendarEvent(id, "cancelled", None, None, None, None, (), None, None) for id in ids),
        token,
        replace,
    )


async def test_competing_batches_only_commit_once(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    db, repo = store
    ticket = await repo.begin_sync("local", "account", "calendar")
    results = await asyncio.gather(
        repo.apply_sync("local", ticket, batch("first", "a")),
        repo.apply_sync("local", ticket, batch("second", "b")),
    )
    assert results.count(True) == results.count(False) == 1
    assert len(await db.fetchall("SELECT * FROM assistant_events")) == 1
    winner = await repo.begin_sync("local", "account", "calendar")
    assert winner.sync_token in {"first", "second"}


@pytest.mark.parametrize("mutation", ["revoke", "deselect", "reselect"])
async def test_old_batch_cannot_resurrect_cache(
    store: tuple[Database, SQLiteAssistantRepository],
    mutation: str,
) -> None:
    db, repo = store
    ticket = await repo.begin_sync("local", "account", "calendar")
    if mutation == "revoke":
        assert await repo.revoke("local", "account") == "secret-reference-only"
        assert await repo.revoke("local", "account") == "secret-reference-only"
    else:
        await repo.select("local", "account", "calendar", False)
        if mutation == "reselect":
            await repo.select("local", "account", "calendar", True)
    assert not await repo.apply_sync("local", ticket, batch("late", "forbidden"))
    assert not await db.fetchall("SELECT * FROM assistant_events")


async def test_insertion_failure_rolls_back_cursor_and_full_snapshot_deletion(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    db, repo = store
    ticket = await repo.begin_sync("local", "account", "calendar")
    assert await repo.apply_sync("local", ticket, batch("old", "existing"))
    ticket = await repo.begin_sync("local", "account", "calendar")
    await db.execute(
        "CREATE TRIGGER fail_event BEFORE INSERT ON assistant_events "
        "WHEN NEW.event_id='fail' BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await repo.apply_sync("local", ticket, batch("new", "partial", "fail"))
    assert (await repo.begin_sync("local", "account", "calendar")).sync_token == "old"
    rows = await db.fetchall("SELECT event_id FROM assistant_events")
    assert [row[0] for row in rows] == ["existing"]


async def test_restart_keeps_cursor_and_revocation_fence(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    db, repo = store
    ticket = await repo.begin_sync("local", "account", "calendar")
    assert await repo.apply_sync("local", ticket, batch("saved", "event"))
    await db.close()
    await db.open()
    assert (await repo.begin_sync("local", "account", "calendar")).sync_token == "saved"
    await repo.revoke("local", "account")
    await db.close()
    await db.open()
    with pytest.raises(AssistantAccessError):
        await repo.begin_sync("local", "account", "calendar")
    assert not await repo.apply_sync("local", ticket, batch("late", "event"))


async def test_discovery_does_not_select_and_foreign_scope_cannot_access(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    _, repo = store
    await repo.add_calendar("local", "account", Calendar("other", "Other", "UTC", "reader"))
    with pytest.raises(AssistantAccessError, match="calendar_not_selected"):
        await repo.begin_sync("local", "account", "other")
    with pytest.raises(AssistantAccessError, match="requires_owner"):
        await repo.begin_sync("guest", "account", "calendar")
    with pytest.raises(AssistantAccessError, match="requires_owner"):
        await repo.revoke("shared:room", "account")
