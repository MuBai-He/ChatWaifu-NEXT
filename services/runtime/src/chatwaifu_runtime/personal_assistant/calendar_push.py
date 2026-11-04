"""Authenticated Calendar change hints; business facts still come from provider readback."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Protocol
from uuid import uuid4

from chatwaifu_runtime.personal_assistant.google_calendar import CalendarWatch

PUSH_PATH = "/v1/personal-assistant/google/calendar/notifications"
LEASE_MS = 86_400_000
RENEW_MS = 3_600_000


def now_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass(frozen=True)
class PushChannel:
    channel_id: str
    owner: str
    account_id: str
    calendar_id: str
    account_revision: int
    calendar_revision: int
    token_digest: str = field(repr=False)
    callback_address: str
    state: str
    expires_at: int
    created_at: int
    resource_id: str | None = None
    last_message: int = 0
    dirty_revision: int = 0
    consumed_revision: int = 0
    retry_at: int = 0
    error_code: str | None = None


class CalendarWatchAccounts(Protocol):
    async def watch_calendar(
        self,
        session_id: str,
        account_id: str,
        calendar_id: str,
        channel_id: str,
        channel_token: str,
        address: str,
    ) -> CalendarWatch: ...
    async def stop_calendar_watch(
        self, session_id: str, account_id: str, channel_id: str, resource_id: str
    ) -> None: ...


class CalendarPushRepository(Protocol):
    async def channels(self, owner: str) -> list[PushChannel]: ...
    async def channel(self, identifier: str) -> PushChannel | None: ...
    async def reserve(
        self,
        owner: str,
        account: str,
        calendar: str,
        identifier: str,
        digest: str,
        expires: int,
        address: str,
    ) -> PushChannel: ...
    async def activate(self, channel: PushChannel, receipt: CalendarWatch) -> bool: ...
    async def retire(
        self, channel: str, *, error: str | None = None, retry_at: int = 0
    ) -> None: ...
    async def receive(self, channel: PushChannel, resource: str, number: int) -> bool: ...
    async def consume(self, channel: str, revision: int) -> None: ...
    async def current(self, channel: PushChannel) -> bool: ...


class CalendarPushService:
    def __init__(
        self,
        accounts: CalendarWatchAccounts,
        repository: CalendarPushRepository,
        origin: str | None,
        on_change: Callable[[], None],
    ) -> None:
        self.accounts, self.repository = accounts, repository
        self.address = origin.rstrip("/") + PUSH_PATH if origin else None
        self.on_change = on_change
        self._closed = False
        self.last_error_code: str | None = None
        self._maintenance: asyncio.Task[None] | None = None
        self._requested: tuple[str, frozenset[tuple[str, str]]] | None = None

    def observe(self, session: str, scopes: set[tuple[str, str]]) -> asyncio.Task[None] | None:
        if self._closed:
            return
        self._requested = (session, frozenset(scopes))
        if self._maintenance is None or self._maintenance.done():
            self._maintenance = asyncio.create_task(
                self._maintain(), name="calendar-push-maintenance"
            )
        return self._maintenance

    async def _retire(self, session: str, channel: PushChannel) -> None:
        # Fence callbacks before waiting for a remote stop, including revoked accounts.
        await self.repository.retire(channel.channel_id)
        if channel.resource_id:
            try:
                await self.accounts.stop_calendar_watch(
                    session, channel.account_id, channel.channel_id, channel.resource_id
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # Remote lease expires; local hints are already disabled.

    async def _maintain(self) -> None:
        requested = self._requested
        if requested is None:
            return
        session, targets = requested
        try:
            rows = await self.repository.channels("local")
            for channel in rows:
                if channel.state != "retired" and (
                    (channel.account_id, channel.calendar_id) not in targets
                    or not self.address
                    or channel.callback_address != self.address
                    or not await self.repository.current(channel)
                ):
                    await self._retire(session, channel)
            if self.address is None:
                return
            rows = await self.repository.channels("local")
            count = 0
            for account, calendar in sorted(targets)[:128]:
                latest = next(
                    (
                        row
                        for row in rows
                        if (row.account_id, row.calendar_id) == (account, calendar)
                    ),
                    None,
                )
                if (
                    latest
                    and latest.callback_address == self.address
                    and await self.repository.current(latest)
                ):
                    if latest.state == "active" and latest.expires_at > now_ms() + RENEW_MS:
                        continue
                    if latest.state == "preparing" and now_ms() - latest.created_at < 60_000:
                        continue
                if (
                    latest
                    and latest.callback_address == self.address
                    and latest.retry_at > now_ms()
                ):
                    continue
                token = secrets.token_urlsafe(32)
                identifier = str(uuid4())
                try:
                    channel = await self.repository.reserve(
                        "local",
                        account,
                        calendar,
                        identifier,
                        hashlib.sha256(token.encode()).hexdigest(),
                        now_ms() + LEASE_MS,
                        self.address,
                    )
                except ValueError:
                    continue  # Target was unselected or revoked after scope discovery.
                try:
                    receipt = await self.accounts.watch_calendar(
                        session, account, calendar, identifier, token, self.address
                    )
                    if not await self.repository.activate(channel, receipt):
                        await self._retire(
                            session, replace(channel, resource_id=receipt.resource_id)
                        )
                        continue
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    actual = await self.repository.channel(identifier)
                    # A valid initial callback proves acceptance even if the reply was lost.
                    if actual is None or actual.state != "active":
                        await self.repository.retire(
                            identifier, error=type(error).__name__[:64], retry_at=now_ms() + 900_000
                        )
                else:
                    self.on_change()
                    if latest and latest.state != "retired":
                        await self._retire(session, latest)
                count += 1
                if count >= 8:
                    break  # Remaining scopes continue at deterministic reconciliation.
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.last_error_code = type(error).__name__[:64]
            return
        finally:
            if not self._closed and self._requested != requested:
                self.on_change()  # Reconcile a scope update received during registration.

    async def receive(
        self, identifier: str, token: str, resource: str, number: str, state: str
    ) -> bool:
        if (
            self._closed
            or self.address is None
            or not identifier
            or len(identifier) > 64
            or not token
            or len(token) > 256
            or not resource
            or len(resource) > 1024
            or state not in {"sync", "exists", "not_exists"}
            or not number.isascii()
            or not number.isdigit()
            or len(number) > 19
            or not 0 < int(number) <= 2**63 - 1
        ):
            return False
        channel = await self.repository.channel(identifier)
        if (
            channel is None
            or channel.callback_address != self.address
            or not hmac.compare_digest(
                channel.token_digest, hashlib.sha256(token.encode()).hexdigest()
            )
        ):
            return False
        accepted = await self.repository.receive(channel, resource, int(number))
        if accepted and int(number) > channel.last_message:
            self.on_change()
        return accepted

    async def dirty(self) -> list[PushChannel]:
        return [
            channel
            for channel in await self.repository.channels("local")
            if channel.state == "active"
            and channel.dirty_revision > channel.consumed_revision
            and await self.repository.current(channel)
        ]

    async def close(self) -> None:
        self._closed = True
        if self._maintenance is not None:
            self._maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await self._maintenance
            self._maintenance = None
