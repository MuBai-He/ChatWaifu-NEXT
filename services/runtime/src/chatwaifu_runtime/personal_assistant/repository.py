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


class AssistantRepository(Protocol):
    async def connect(self, owner: str, account_id: str, secret_ref: str) -> None: ...

    async def add_calendar(self, owner: str, account_id: str, calendar: Calendar) -> None: ...

    async def select(
        self, owner: str, account_id: str, calendar_id: str, selected: bool
    ) -> None: ...

    async def begin_sync(self, owner: str, account_id: str, calendar_id: str) -> SyncTicket: ...

    async def apply_sync(self, owner: str, ticket: SyncTicket, batch: EventSync) -> bool: ...

    async def revoke(self, owner: str, account_id: str) -> str: ...
