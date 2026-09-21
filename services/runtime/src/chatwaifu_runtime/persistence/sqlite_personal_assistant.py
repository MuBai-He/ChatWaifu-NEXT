"""Atomic Google cache batches with account and selection revision fencing."""

import json
from dataclasses import asdict
from datetime import UTC, datetime

import aiosqlite

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.personal_assistant.google_calendar import Calendar, EventSync
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError, SyncTicket


class SQLiteAssistantRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _owner(owner: str) -> None:
        if owner != "local":
            raise AssistantAccessError("personal_account_requires_owner")

    async def _account(self, connection: aiosqlite.Connection, account_id: str) -> aiosqlite.Row:
        async with connection.execute(
            "SELECT * FROM assistant_accounts WHERE account_id=? AND owner_scope='local'",
            (account_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or row["status"] != "connected":
            raise AssistantAccessError("account_not_connected")
        return row

    async def connect(self, owner: str, account_id: str, secret_ref: str) -> None:
        self._owner(owner)
        if not account_id or not secret_ref:
            raise ValueError("account_id_and_secret_reference_required")
        # Reauthorization uses a new opaque account ID. Never resurrect a revoked
        # row and make an older in-flight request valid again.
        await self._database.execute(
            "INSERT INTO assistant_accounts(account_id, owner_scope, status, secret_ref) "
            "VALUES (?, 'local', 'connected', ?)",
            (account_id, secret_ref),
        )

    async def add_calendar(self, owner: str, account_id: str, calendar: Calendar) -> None:
        self._owner(owner)
        async with self._database.transaction() as connection:
            await self._account(connection, account_id)
            # Discovery never implicitly selects calendars. Refreshing discovery
            # metadata invalidates old sync tickets without deleting good data.
            await connection.execute(
                "INSERT INTO assistant_calendars "
                "(account_id,calendar_id,title,timezone,access_role) VALUES (?,?,?,?,?) "
                "ON CONFLICT(account_id,calendar_id) DO UPDATE SET "
                "title=excluded.title,timezone=excluded.timezone,access_role=excluded.access_role,"
                "revision=assistant_calendars.revision+1",
                (account_id, calendar.id, calendar.title, calendar.timezone, calendar.access_role),
            )

    async def select(self, owner: str, account_id: str, calendar_id: str, selected: bool) -> None:
        self._owner(owner)
        async with self._database.transaction() as connection:
            await self._account(connection, account_id)
            async with connection.execute(
                "UPDATE assistant_calendars SET selected=?,revision=revision+1,"
                "sync_token=NULL,synced_at=NULL WHERE account_id=? AND calendar_id=?",
                (int(selected), account_id, calendar_id),
            ) as cursor:
                if cursor.rowcount != 1:
                    raise AssistantAccessError("calendar_not_found")
            await connection.execute(
                "DELETE FROM assistant_events WHERE account_id=? AND calendar_id=?",
                (account_id, calendar_id),
            )

    async def begin_sync(self, owner: str, account_id: str, calendar_id: str) -> SyncTicket:
        self._owner(owner)
        async with self._database.transaction() as connection:
            account = await self._account(connection, account_id)
            async with connection.execute(
                "SELECT revision,sync_token FROM assistant_calendars "
                "WHERE account_id=? AND calendar_id=? AND selected=1",
                (account_id, calendar_id),
            ) as cursor:
                calendar = await cursor.fetchone()
            if calendar is None:
                raise AssistantAccessError("calendar_not_selected")
            return SyncTicket(
                account_id,
                calendar_id,
                account["revision"],
                calendar["revision"],
                calendar["sync_token"],
            )

    async def apply_sync(self, owner: str, ticket: SyncTicket, batch: EventSync) -> bool:
        self._owner(owner)
        if not batch.sync_token:
            raise ValueError("complete_sync_token_required")
        if ticket.sync_token is None and not batch.replace_snapshot:
            raise ValueError("initial_sync_requires_full_snapshot")
        async with self._database.transaction() as connection:
            # Claim the exact pre-network revision in the same transaction as
            # event writes. A competing sync, deselection or revocation wins once.
            async with connection.execute(
                "UPDATE assistant_calendars SET revision=revision+1,sync_token=?,synced_at=? "
                "WHERE account_id=? AND calendar_id=? AND selected=1 AND revision=? "
                "AND sync_token IS ? AND EXISTS (SELECT 1 FROM assistant_accounts a "
                "WHERE a.account_id=assistant_calendars.account_id AND a.owner_scope='local' "
                "AND a.status='connected' AND a.revision=?)",
                (
                    batch.sync_token,
                    datetime.now(UTC).isoformat(),
                    ticket.account_id,
                    ticket.calendar_id,
                    ticket.calendar_revision,
                    ticket.sync_token,
                    ticket.account_revision,
                ),
            ) as cursor:
                if cursor.rowcount != 1:
                    return False
            if batch.replace_snapshot:
                await connection.execute(
                    "DELETE FROM assistant_events WHERE account_id=? AND calendar_id=?",
                    (ticket.account_id, ticket.calendar_id),
                )
            # Keep cancelled recurring exceptions as tombstones for future query
            # expansion. A tombstone is not an active event in presentation.
            for event in batch.events:
                await connection.execute(
                    "INSERT INTO assistant_events VALUES (?,?,?,?) "
                    "ON CONFLICT(account_id,calendar_id,event_id) DO UPDATE SET "
                    "payload_json=excluded.payload_json",
                    (
                        ticket.account_id,
                        ticket.calendar_id,
                        event.id,
                        json.dumps(asdict(event), default=lambda value: value.isoformat()),
                    ),
                )
        return True

    async def revoke(self, owner: str, account_id: str) -> str:
        self._owner(owner)
        async with self._database.transaction() as connection:
            # Idempotent local disconnect; retain the reference so cleanup can
            # retry after a crash between the database commit and secret deletion.
            async with connection.execute(
                "SELECT secret_ref FROM assistant_accounts "
                "WHERE account_id=? AND owner_scope='local'",
                (account_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise AssistantAccessError("account_not_found")
            await connection.execute(
                "UPDATE assistant_accounts SET status='revoked',revision=revision+1 "
                "WHERE account_id=?",
                (account_id,),
            )
            await connection.execute(
                "DELETE FROM assistant_calendars WHERE account_id=?", (account_id,)
            )
            return str(row["secret_ref"])
