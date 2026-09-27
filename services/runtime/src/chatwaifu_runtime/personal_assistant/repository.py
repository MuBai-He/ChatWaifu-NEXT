"""Owner-scoped calendar persistence port, independent of SQL implementations."""

from dataclasses import dataclass, field
from typing import Protocol

from chatwaifu_runtime.personal_assistant.google_calendar import Calendar, EventSync
from chatwaifu_runtime.personal_assistant.google_tasks import TaskList


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
    display_label: str | None = None


@dataclass(frozen=True, slots=True)
class CalendarSelection:
    calendar: Calendar
    selected: bool


@dataclass(frozen=True, slots=True)
class WriteDestination:
    kind: str
    provider: str
    account_id: str | None
    collection_id: str
    device_id: str | None


@dataclass(frozen=True, slots=True)
class TaskListSelection:
    tasklist: TaskList
    selected: bool


class AssistantRepository(Protocol):
    async def tasklists(self, owner: str, account_id: str) -> tuple[TaskListSelection, ...]: ...

    async def add_tasklist(self, owner: str, account_id: str, tasklist: TaskList) -> None: ...

    async def select_tasklist(
        self, owner: str, account_id: str, list_id: str, selected: bool
    ) -> None: ...
    async def destinations(self, owner: str) -> tuple[WriteDestination, ...]: ...

    async def set_destination(self, owner: str, destination: WriteDestination) -> None: ...

    async def calendars(self, owner: str, account_id: str) -> tuple[CalendarSelection, ...]: ...

    async def session_owner(self, session_id: str) -> str: ...

    async def accounts(self) -> tuple[AccountRecord, ...]: ...

    async def connect(self, owner: str, account_id: str, secret_ref: str) -> None: ...

    async def bump_account_revision(self, owner: str, account_id: str) -> None: ...

    async def set_account_label(self, owner: str, account_id: str, label: str) -> None: ...

    async def add_calendar(self, owner: str, account_id: str, calendar: Calendar) -> None: ...

    async def select(
        self, owner: str, account_id: str, calendar_id: str, selected: bool
    ) -> None: ...

    async def begin_sync(self, owner: str, account_id: str, calendar_id: str) -> SyncTicket: ...

    async def apply_sync(self, owner: str, ticket: SyncTicket, batch: EventSync) -> bool: ...

    async def revoke(self, owner: str, account_id: str) -> str: ...
