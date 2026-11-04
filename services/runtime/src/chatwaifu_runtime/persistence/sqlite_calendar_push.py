"""Durable watch leases and replay-safe hints, fenced against selected-account revisions."""

import aiosqlite

from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.personal_assistant.calendar_push import PushChannel, now_ms
from chatwaifu_runtime.personal_assistant.google_calendar import CalendarWatch


class SQLiteCalendarPushRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    @staticmethod
    def _channel(row: aiosqlite.Row) -> PushChannel:
        return PushChannel(
            channel_id=str(row["channel_id"]),
            owner=str(row["owner"]),
            account_id=str(row["account_id"]),
            calendar_id=str(row["calendar_id"]),
            account_revision=int(row["account_revision"]),
            calendar_revision=int(row["calendar_revision"]),
            token_digest=str(row["token_digest"]),
            callback_address=str(row["callback_address"]),
            state=str(row["state"]),
            expires_at=int(row["expires_at"]),
            created_at=int(row["created_at"]),
            resource_id=str(row["resource_id"]) if row["resource_id"] is not None else None,
            last_message=int(row["last_message"]),
            dirty_revision=int(row["dirty_revision"]),
            consumed_revision=int(row["consumed_revision"]),
            retry_at=int(row["retry_at"]),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        )

    async def channels(self, owner: str) -> list[PushChannel]:
        return [
            self._channel(row)
            for row in await self.db.fetchall(
                "SELECT * FROM calendar_push_channels WHERE owner=? AND "
                "(state!='retired' OR rowid IN (SELECT MAX(rowid) FROM calendar_push_channels "
                "WHERE owner=? AND state='retired' GROUP BY account_id,calendar_id)) "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1024",
                (owner, owner),
            )
        ]

    async def channel(self, identifier: str) -> PushChannel | None:
        row = await self.db.fetchone(
            "SELECT * FROM calendar_push_channels WHERE channel_id=?", (identifier,)
        )
        return self._channel(row) if row else None

    @staticmethod
    async def _authority(
        c: aiosqlite.Connection, owner: str, account: str, calendar: str
    ) -> tuple[int, int] | None:
        async with c.execute(
            "SELECT a.revision,c.revision FROM assistant_accounts a JOIN "
            "assistant_calendars c ON c.account_id=a.account_id"
            " WHERE a.account_id=? AND a.owner_scope=? AND a.status='connected' "
            "AND c.calendar_id=? AND c.selected=1",
            (account, owner, calendar),
        ) as cursor:
            row = await cursor.fetchone()
        return (int(row[0]), int(row[1])) if row else None

    async def current(self, channel: PushChannel) -> bool:
        async with self.db.transaction() as c:
            revisions = await self._authority(
                c, channel.owner, channel.account_id, channel.calendar_id
            )
        return (
            revisions == (channel.account_revision, channel.calendar_revision)
            and channel.expires_at > now_ms()
        )

    async def reserve(
        self,
        owner: str,
        account: str,
        calendar: str,
        identifier: str,
        digest: str,
        expires: int,
        address: str,
    ) -> PushChannel:
        async with self.db.transaction() as c:
            revisions = await self._authority(c, owner, account, calendar)
            if revisions is None:
                raise ValueError("calendar_push_scope_changed")
            channel = PushChannel(
                identifier,
                owner,
                account,
                calendar,
                revisions[0],
                revisions[1],
                digest,
                address,
                "preparing",
                expires,
                now_ms(),
            )
            await c.execute(
                "INSERT INTO calendar_push_channels(channel_id,owner,account_id,calendar_id,"
                "account_revision,calendar_revision,token_digest,callback_address,state,"
                "expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    owner,
                    account,
                    calendar,
                    *revisions,
                    digest,
                    address,
                    "preparing",
                    expires,
                    channel.created_at,
                ),
            )
        return channel

    async def activate(self, channel: PushChannel, receipt: CalendarWatch) -> bool:
        async with self.db.transaction() as c:
            revisions = await self._authority(
                c, channel.owner, channel.account_id, channel.calendar_id
            )
            if revisions != (channel.account_revision, channel.calendar_revision):
                return False
            cursor = await c.execute(
                "UPDATE calendar_push_channels SET state='active',resource_id=?,expires_at=?"
                " WHERE channel_id=? AND state IN ('preparing','active') AND "
                "(resource_id IS NULL OR resource_id=?)",
                (
                    receipt.resource_id,
                    min(channel.expires_at, receipt.expires_at or channel.expires_at),
                    channel.channel_id,
                    receipt.resource_id,
                ),
            )
            return cursor.rowcount == 1

    async def retire(self, channel: str, *, error: str | None = None, retry_at: int = 0) -> None:
        await self.db.execute(
            "UPDATE calendar_push_channels SET "
            "state='retired',error_code=?,retry_at=? WHERE channel_id=?",
            (error, retry_at, channel),
        )

    async def receive(self, channel: PushChannel, resource: str, number: int) -> bool:
        async with self.db.transaction() as c:
            revisions = await self._authority(
                c, channel.owner, channel.account_id, channel.calendar_id
            )
            if revisions != (channel.account_revision, channel.calendar_revision):
                return False
            async with c.execute(
                "SELECT state,resource_id,last_message,expires_at FROM "
                "calendar_push_channels WHERE channel_id=? AND token_digest=?",
                (channel.channel_id, channel.token_digest),
            ) as cursor:
                row = await cursor.fetchone()
            if (
                row is None
                or row[0] not in {"preparing", "active"}
                or row[3] <= now_ms()
                or (row[1] is not None and row[1] != resource)
            ):
                return False
            if number <= row[2]:
                return True
            # Initial sync can precede the HTTP reply. Its unguessable token proves
            # receipt of our request; only an authorized provider read follows.
            await c.execute(
                "UPDATE calendar_push_channels SET state='active',resource_id=?,l"
                "ast_message=?,dirty_revision=dirty_revision+1 WHERE channel_id=?",
                (resource, number, channel.channel_id),
            )
            return True

    async def consume(self, channel: str, revision: int) -> None:
        await self.db.execute(
            "UPDATE calendar_push_channels SET "
            "consumed_revision=max(consumed_revision,?) WHERE channel_id=? AND "
            "dirty_revision>=?",
            (revision, channel, revision),
        )
