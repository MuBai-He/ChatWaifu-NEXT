"""Owner-private durable reminders and device operations; no provider or SQLite dependency."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from chatwaifu_runtime.personal_assistant.repository import AssistantRepository


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    device_id: UUID
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["reminder", "alarm"] = "reminder"
    due_at: datetime
    timezone: str = "Asia/Shanghai"
    repeat: Literal["none", "daily", "weekdays"] = "none"

    @model_validator(mode="after")
    def valid_time(self) -> TaskInput:
        if self.due_at.utcoffset() is None:
            raise ValueError("timezone_required")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError("unknown_timezone") from None
        if not self.title.strip():
            raise ValueError("title_required")
        if (
            self.repeat == "weekdays"
            and self.due_at.astimezone(ZoneInfo(self.timezone)).weekday() >= 5
        ):
            following = next_due(self.due_at, self.timezone, self.repeat, self.due_at)
            assert following is not None
            self.due_at = following
        self.title = self.title.strip()
        return self


class AppleOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    device_id: UUID
    resource: Literal["calendar", "reminder"]
    action: Literal["list", "create", "update", "complete", "delete"]
    calendar_id: str = Field(min_length=1, max_length=512)
    item_id: str | None = Field(default=None, max_length=1024)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start: datetime | None = None
    end: datetime | None = None
    expected_modified: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_operation(self) -> AppleOperation:
        for value in (self.start, self.end):
            if value is not None and value.utcoffset() is None:
                raise ValueError("timezone_required")
        if self.action in ("update", "delete", "complete") and (
            not self.item_id or self.expected_modified is None
        ):
            raise ValueError("item_id_and_expected_modified_required")
        if self.action == "complete" and self.resource != "reminder":
            raise ValueError("only_reminders_can_be_completed")
        if self.action in ("create", "update") and not self.title:
            raise ValueError("title_required")
        if self.resource == "calendar" and self.action in ("list", "create", "update"):
            if (
                not self.start
                or not self.end
                or not timedelta(0) < self.end - self.start <= timedelta(days=31)
            ):
                raise ValueError("calendar_requires_bounded_time_range")
        return self


def next_due(due: datetime, timezone: str, repeat: str, after: datetime) -> datetime | None:
    """Keep wall clock time across DST; gaps move forward, folds fire once (fold=0)."""
    if repeat == "none":
        return None
    zone = ZoneInfo(timezone)
    local = due.astimezone(zone)
    day = max(local.date() + timedelta(days=1), after.astimezone(zone).date())
    for _ in range(8):
        candidate = datetime.combine(day, local.time().replace(tzinfo=None), zone).replace(fold=0)
        candidate = candidate.astimezone(UTC).astimezone(zone)
        if candidate > after and (repeat != "weekdays" or day.weekday() < 5):
            return candidate.astimezone(UTC)
        day += timedelta(days=1)
    raise ValueError("cannot_compute_next_occurrence")


class TaskRepository(Protocol):
    async def pair(self, name: str) -> dict[str, Any]: ...
    async def devices(self) -> list[dict[str, Any]]: ...
    async def revoke(self, device_id: str) -> None: ...
    async def create_task(self, task: TaskInput, now: float) -> dict[str, Any]: ...
    async def tasks(self) -> list[dict[str, Any]]: ...
    async def revise_task(self, task: TaskInput, expected_revision: int, now: float) -> None: ...
    async def change_task(self, task_id: str, action: str, now: float) -> None: ...
    async def tick(self, now: float) -> None: ...
    async def poll(
        self,
        device_id: str,
        secret: str,
        sources: list[dict[str, Any]],
        now: float,
        *,
        deliver: bool = True,
        source_revision: int | None = None,
    ) -> dict[str, Any]: ...
    async def acknowledge(
        self,
        device_id: str,
        secret: str,
        item_id: str,
        action: str,
        result: dict[str, Any],
        now: float,
    ) -> None: ...
    async def enqueue(self, operation: AppleOperation, now: float) -> dict[str, Any]: ...
    async def operations(self) -> list[dict[str, Any]]: ...
    async def history(self) -> list[dict[str, Any]]: ...
    async def dismiss_history(self, delivery_id: str) -> None: ...


class TaskService:
    def __init__(self, owners: AssistantRepository, repository: TaskRepository):
        self.owners = owners
        self.repository = repository
        self._worker: asyncio.Task[None] | None = None
        self.last_error: str | None = None

    async def owner(self, session_id: str) -> None:
        await self.owners.session_owner(session_id)

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="assistant-scheduler")

    async def _run(self) -> None:
        while True:
            try:
                await self.repository.tick(datetime.now(UTC).timestamp())
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                self.last_error = "scheduler_storage_unavailable"
            await asyncio.sleep(2)

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
