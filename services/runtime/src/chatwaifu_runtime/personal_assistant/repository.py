"""Owner-scoped calendar persistence port, independent of SQL implementations."""

from dataclasses import dataclass, field
from typing import Protocol

from chatwaifu_runtime.personal_assistant.google_calendar import Calendar, EventSync


class AssistantAccessError(ValueError):
    """The current owner or account cannot access this operation."""


@dataclass(frozen=True, slots=True)
class SyncTicket:
    account_id: str
    calendar_id: str
    account_revision: int
    calendar_revision: int
    sync_token: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class AccountRecord:
    account_id: str
    status: str
    secret_ref: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CalendarSelection:
    calendar: Calendar
    selected: bool


class AssistantRepository(Protocol):
    async def calendars(self, owner: str, account_id: str) -> tuple[CalendarSelection, ...]: ...

    async def session_owner(self, session_id: str) -> str: ...

    async def accounts(self) -> tuple[AccountRecord, ...]: ...

    async def connect(self, owner: str, account_id: str, secret_ref: str) -> None: ...

    async def add_calendar(self, owner: str, account_id: str, calendar: Calendar) -> None: ...

    async def select(
        self, owner: str, account_id: str, calendar_id: str, selected: bool
    ) -> None: ...

    async def begin_sync(self, owner: str, account_id: str, calendar_id: str) -> SyncTicket: ...

    async def apply_sync(self, owner: str, ticket: SyncTicket, batch: EventSync) -> bool: ...

    async def revoke(self, owner: str, account_id: str) -> str: ...
