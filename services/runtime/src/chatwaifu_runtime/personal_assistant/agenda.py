"""Account-owned agenda writes; provider items remain authoritative at their source."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError

from chatwaifu_runtime.personal_assistant.accounts import GoogleAccountService
from chatwaifu_runtime.personal_assistant.repository import (
    AssistantAccessError,
    AssistantRepository,
    WriteDestination,
)
from chatwaifu_runtime.personal_assistant.tasks import AppleOperation, TaskService


class AgendaService:
    def __init__(
        self,
        repository: AssistantRepository,
        accounts: GoogleAccountService | None,
        tasks: TaskService | None,
    ) -> None:
        self._repository = repository
        self._accounts = accounts
        self._tasks = tasks

    async def destinations(self, session_id: str) -> tuple[WriteDestination, ...]:
        owner = await self._repository.session_owner(session_id)
        return await self._repository.destinations(owner)

    async def set_destination(self, session_id: str, destination: WriteDestination) -> None:
        owner = await self._repository.session_owner(session_id)
        await self._validate(session_id, destination)
        await self._repository.set_destination(owner, destination)

    async def _validate(self, session_id: str, destination: WriteDestination) -> None:
        if destination.kind not in {"calendar", "reminder"}:
            raise AssistantAccessError("invalid_destination")
        if destination.provider == "google":
            service = self._accounts
            if service is None or not destination.account_id or destination.device_id:
                raise AssistantAccessError("google_account_unavailable")
            status = await service.status(session_id)
            account = next((a for a in status if a.account_id == destination.account_id), None)
            if destination.kind == "calendar":
                calendars = await service.calendars(session_id, destination.account_id)
                if (
                    not account
                    or not account.calendar_write
                    or not any(
                        item.calendar.id == destination.collection_id
                        and item.selected
                        and item.calendar.access_role in {"owner", "writer"}
                        for item in calendars
                    )
                ):
                    raise AssistantAccessError("calendar_not_selected_or_writable")
            elif (
                not account
                or not account.tasks_write
                or not any(
                    item.tasklist.id == destination.collection_id and item.selected
                    for item in await service.tasklists(session_id, destination.account_id)
                )
            ):
                raise AssistantAccessError("tasklist_not_found_or_writable")
            return
        if destination.provider == "apple":
            service = self._tasks
            if service is None or destination.account_id or not destination.device_id:
                raise AssistantAccessError("apple_device_unavailable")
            await service.owner(session_id)
            for device in await service.repository.devices():
                if device["device_id"] != destination.device_id:
                    continue
                if any(
                    source.get("id") == destination.collection_id
                    and source.get("resource") == destination.kind
                    and source.get("writable") is True
                    for source in device["sources"]
                ):
                    return
            raise AssistantAccessError("apple_source_not_selected_or_writable")
        raise AssistantAccessError("invalid_destination")

    async def create(
        self,
        session_id: str,
        kind: Literal["calendar", "reminder"],
        request_id: UUID,
        title: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        due_date: date | None = None,
        due_at: datetime | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        if not title.strip() or len(title) > 200:
            raise AssistantAccessError("invalid_title")
        destinations = await self.destinations(session_id)
        destination = next((item for item in destinations if item.kind == kind), None)
        if destination is None:
            raise AssistantAccessError("write_destination_required")
        await self._validate(session_id, destination)
        if destination.provider == "google":
            assert self._accounts is not None and destination.account_id is not None
            if kind == "calendar":
                if (
                    start is None
                    or end is None
                    or start.utcoffset() is None
                    or end.utcoffset() is None
                    or end <= start
                    or end - start > timedelta(days=31)
                ):
                    raise AssistantAccessError("invalid_event_window")
                event = await self._accounts.create_event(
                    session_id,
                    destination.account_id,
                    destination.collection_id,
                    request_id.hex,
                    title,
                    start,
                    end,
                )
                return {"state": "saved", "provider": "google", "item": asdict(event)}
            if due_at is not None:
                raise AssistantAccessError("google_tasks_date_only_choose_apple_or_schedule_alarm")
            task = await self._accounts.create_task(
                session_id,
                destination.account_id,
                destination.collection_id,
                title,
                notes,
                due_date,
            )
            return {"state": "saved", "provider": "google", "item": asdict(task)}
        assert self._tasks is not None and destination.device_id is not None
        if kind == "calendar":
            if (
                start is None
                or end is None
                or start.utcoffset() is None
                or end.utcoffset() is None
                or end <= start
                or end - start > timedelta(days=31)
            ):
                raise AssistantAccessError("invalid_event_window")
            payload = dict(
                request_id=request_id,
                device_id=UUID(destination.device_id),
                resource="calendar",
                action="create",
                calendar_id=destination.collection_id,
                title=title,
                start=start,
                end=end,
            )
        else:
            if due_date is not None and due_at is None:
                raise AssistantAccessError("apple_reminder_requires_time_or_no_due_date")
            payload = dict(
                request_id=request_id,
                device_id=UUID(destination.device_id),
                resource="reminder",
                action="create",
                calendar_id=destination.collection_id,
                title=title,
                start=due_at,
            )
        try:
            operation = AppleOperation.model_validate(payload)
        except ValidationError:
            raise AssistantAccessError("invalid_apple_operation") from None
        try:
            receipt = await self._tasks.repository.enqueue(operation, datetime.now(UTC).timestamp())
        except ValueError as error:
            raise AssistantAccessError(str(error)) from None
        return {"state": "queued", "provider": "apple", "receipt": receipt}
