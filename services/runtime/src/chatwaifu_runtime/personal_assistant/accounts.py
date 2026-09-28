"""Server-owned Google credential lifecycle and session-derived owner authorization.

The native OAuth coordinator must validate PKCE/state/expiry and protected handoff
before passing tokens to connect_authorized. No API should expose that method raw.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chatwaifu_runtime.personal_assistant.google_calendar import (
    READ_SCOPE,
    WRITE_SCOPE,
    Calendar,
    CalendarEvent,
    GoogleCalendarAdapter,
    GoogleCalendarError,
    OAuthTokens,
)
from chatwaifu_runtime.personal_assistant.google_tasks import (
    TASKS_SCOPE,
    GoogleTask,
    GoogleTasksAdapter,
)
from chatwaifu_runtime.personal_assistant.repository import (
    AccountRecord,
    AssistantAccessError,
    AssistantRepository,
    CalendarSelection,
    TaskListSelection,
)


class SecretStore(Protocol):
    async def get(self, name: str) -> str | None: ...
    async def set(self, name: str, value: str | None) -> None: ...
    async def prune(self, retained_names: set[str]) -> None: ...


@dataclass(frozen=True, slots=True)
class GoogleClient:
    client_id: str
    client_secret: str | None = field(default=None, repr=False)


class _Credential(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    refresh_token: str = Field(min_length=1, repr=False)
    scopes: list[str]


@dataclass(frozen=True, slots=True)
class AccountStatus:
    account_id: str
    status: str
    calendar_write: bool = False
    tasks_write: bool = False
    display_label: str | None = None


class GoogleAccountService:
    """One instance per Runtime, with a dedicated personal-assistant secret store.

    Access tokens are short-lived locals only. Operations serialize within this
    subsystem, never hold database locks during I/O, and do not retry writes.
    """

    def __init__(
        self,
        repository: AssistantRepository,
        secrets: SecretStore,
        adapter: GoogleCalendarAdapter,
        client: GoogleClient,
        tasks_adapter: GoogleTasksAdapter | None = None,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._adapter = adapter
        self._client = client
        self._tasks = tasks_adapter
        self._lock = asyncio.Lock()

    async def connect_authorized(self, session_id: str, tokens: OAuthTokens) -> AccountStatus:
        owner = await self._repository.session_owner(session_id)
        if not tokens.refresh_token or not tokens.scopes or READ_SCOPE not in tokens.scopes:
            raise AssistantAccessError("calendar_offline_read_consent_required")
        async with self._lock:
            account_id = str(uuid4())
            reference = f"google:{account_id}"
            credential = _Credential(refresh_token=tokens.refresh_token, scopes=list(tokens.scopes))
            await self._secrets.set(reference, credential.model_dump_json())
            # Do not delete on CancelledError: SQLite may already have committed.
            # Startup reconciliation prunes only references with no durable row.
            await self._repository.connect(owner, account_id, reference)
            return AccountStatus(account_id, "connected")

    async def primary_identity(self, session_id: str, account_id: str) -> str:
        await self._repository.session_owner(session_id)
        async with self._lock:
            access = await self._access(account_id)
            calendars = await self._adapter.calendars(access)
            primary = next((item.id for item in calendars if item.primary), None)
            if not primary:
                raise AssistantAccessError("google_primary_calendar_unavailable")
            return primary

    async def upgrade_authorized(
        self, session_id: str, account_id: str, expected_primary: str, tokens: OAuthTokens
    ) -> AccountStatus:
        owner = await self._repository.session_owner(session_id)
        if (
            not tokens.refresh_token
            or not tokens.scopes
            or not {READ_SCOPE, WRITE_SCOPE, TASKS_SCOPE}.issubset(tokens.scopes)
        ):
            raise AssistantAccessError("google_write_consent_required")
        async with self._lock:
            old = await self._connected(account_id)
            new_calendars = await self._adapter.calendars(tokens.access_token)
            new_primary = next((item.id for item in new_calendars if item.primary), None)
            if not new_primary or expected_primary != new_primary:
                raise AssistantAccessError("google_upgrade_account_mismatch")
            await self._connected(account_id)
            await self._secrets.set(
                old.secret_ref,
                _Credential(
                    refresh_token=tokens.refresh_token,
                    scopes=list(tokens.scopes),
                ).model_dump_json(),
            )
            await self._repository.bump_account_revision(owner, account_id)
            primary = next(item for item in new_calendars if item.primary)
            label = (primary.title or primary.id)[:200]
            await self._repository.set_account_label(owner, account_id, label)
            return AccountStatus(account_id, "connected", True, True, label)

    async def status(self, session_id: str) -> tuple[AccountStatus, ...]:
        await self._repository.session_owner(session_id)
        result: list[AccountStatus] = []
        for account in await self._repository.accounts():
            if account.status != "connected":
                result.append(
                    AccountStatus(
                        account.account_id, account.status, display_label=account.display_label
                    )
                )
                continue
            try:
                credential = await self._credential(account.secret_ref)
            except AssistantAccessError:
                result.append(
                    AccountStatus(
                        account.account_id, account.status, display_label=account.display_label
                    )
                )
                continue
            result.append(
                AccountStatus(
                    account.account_id,
                    account.status,
                    WRITE_SCOPE in credential.scopes,
                    TASKS_SCOPE in credential.scopes,
                    account.display_label,
                )
            )
        return tuple(result)

    async def create_event(
        self,
        session_id: str,
        account_id: str,
        calendar_id: str,
        event_id: str,
        title: str,
        start: datetime,
        end: datetime,
    ) -> CalendarEvent:
        if (
            not title.strip()
            or len(title) > 200
            or start.utcoffset() is None
            or end.utcoffset() is None
            or end <= start
        ):
            raise AssistantAccessError("invalid_event")
        owner = await self._repository.session_owner(session_id)
        async with self._lock:
            await self._writable_calendar(owner, account_id, calendar_id)
            token = await self._access(account_id, WRITE_SCOPE)
            return await self._adapter.create_event(
                token,
                calendar_id,
                event_id,
                {
                    "summary": title.strip(),
                    "start": {"dateTime": start.isoformat()},
                    "end": {"dateTime": end.isoformat()},
                },
            )

    async def update_event(
        self,
        session_id: str,
        account_id: str,
        calendar_id: str,
        event_id: str,
        etag: str,
        changes: dict[str, object],
    ) -> CalendarEvent:
        owner = await self._repository.session_owner(session_id)
        async with self._lock:
            await self._writable_calendar(owner, account_id, calendar_id)
            token = await self._access(account_id, WRITE_SCOPE)
            current = await self._adapter.get_event(token, calendar_id, event_id)
            if current.recurrence or current.recurring_event_id or current.event_type != "default":
                raise AssistantAccessError("complex_event_edit_not_supported")
            return await self._adapter.update_event(token, calendar_id, event_id, changes, etag)

    async def delete_event(
        self, session_id: str, account_id: str, calendar_id: str, event_id: str, etag: str
    ) -> None:
        owner = await self._repository.session_owner(session_id)
        async with self._lock:
            await self._writable_calendar(owner, account_id, calendar_id)
            token = await self._access(account_id, WRITE_SCOPE)
            current = await self._adapter.get_event(token, calendar_id, event_id)
            if current.recurrence or current.recurring_event_id or current.event_type != "default":
                raise AssistantAccessError("complex_event_edit_not_supported")
            await self._adapter.delete_event(token, calendar_id, event_id, etag)

    async def _writable_calendar(self, owner: str, account_id: str, calendar_id: str) -> None:
        selection = await self._repository.calendars(owner, account_id)
        if not any(
            item.calendar.id == calendar_id
            and item.selected
            and item.calendar.access_role in {"owner", "writer"}
            for item in selection
        ):
            raise AssistantAccessError("calendar_not_selected_or_writable")

    async def tasklists(
        self, session_id: str, account_id: str, *, discover: bool = False
    ) -> tuple[TaskListSelection, ...]:
        owner = await self._repository.session_owner(session_id)
        if discover:
            async with self._lock:
                token = await self._access(account_id, TASKS_SCOPE)
                for tasklist in await self._task_adapter().lists(token):
                    await self._repository.add_tasklist(owner, account_id, tasklist)
        return await self._repository.tasklists(owner, account_id)

    async def select_tasklist(
        self, session_id: str, account_id: str, list_id: str, selected: bool
    ) -> None:
        owner = await self._repository.session_owner(session_id)
        await self._repository.select_tasklist(owner, account_id, list_id, selected)

    async def tasks(self, session_id: str, account_id: str, list_id: str) -> tuple[GoogleTask, ...]:
        owner = await self._repository.session_owner(session_id)
        async with self._lock:
            await self._selected_tasklist(owner, account_id, list_id)
            token = await self._access(account_id, TASKS_SCOPE)
            return await self._task_adapter().tasks(token, list_id)

    async def create_task(
        self,
        session_id: str,
        account_id: str,
        list_id: str,
        title: str,
        notes: str | None,
        due: date | None,
    ) -> GoogleTask:
        owner = await self._repository.session_owner(session_id)
        if not title.strip() or len(title) > 200 or (notes is not None and len(notes) > 8192):
            raise AssistantAccessError("invalid_task")
        async with self._lock:
            await self._selected_tasklist(owner, account_id, list_id)
            token = await self._access(account_id, TASKS_SCOPE)
            return await self._task_adapter().create(token, list_id, title.strip(), notes, due)

    async def update_task(
        self,
        session_id: str,
        account_id: str,
        list_id: str,
        task_id: str,
        etag: str,
        changes: dict[str, object],
    ) -> GoogleTask:
        owner = await self._repository.session_owner(session_id)
        async with self._lock:
            await self._selected_tasklist(owner, account_id, list_id)
            token = await self._access(account_id, TASKS_SCOPE)
            return await self._task_adapter().update(token, list_id, task_id, changes, etag)

    async def delete_task(
        self, session_id: str, account_id: str, list_id: str, task_id: str, etag: str
    ) -> None:
        owner = await self._repository.session_owner(session_id)
        async with self._lock:
            await self._selected_tasklist(owner, account_id, list_id)
            token = await self._access(account_id, TASKS_SCOPE)
            await self._task_adapter().delete(token, list_id, task_id, etag)

    def _task_adapter(self) -> GoogleTasksAdapter:
        if self._tasks is None:
            raise AssistantAccessError("google_tasks_not_configured")
        return self._tasks

    async def _selected_tasklist(self, owner: str, account_id: str, list_id: str) -> None:
        if not any(
            item.tasklist.id == list_id and item.selected
            for item in await self._repository.tasklists(owner, account_id)
        ):
            raise AssistantAccessError("tasklist_not_selected")

    async def discover(self, session_id: str, account_id: str) -> tuple[Calendar, ...]:
        owner = await self._repository.session_owner(session_id)
        async with self._lock:
            access = await self._access(account_id)
            calendars = await self._adapter.calendars(access)
            for calendar in calendars:
                await self._repository.add_calendar(owner, account_id, calendar)
                if calendar.primary:
                    await self._repository.set_account_label(
                        owner, account_id, (calendar.title or calendar.id)[:200]
                    )
            return calendars

    async def calendars(self, session_id: str, account_id: str) -> tuple[CalendarSelection, ...]:
        owner = await self._repository.session_owner(session_id)
        return await self._repository.calendars(owner, account_id)

    async def query(
        self,
        session_id: str,
        account_id: str,
        calendar_id: str,
        start: datetime,
        end: datetime,
    ) -> tuple[CalendarEvent, ...]:
        owner = await self._repository.session_owner(session_id)
        async with self._lock:
            ticket = await self._repository.begin_sync(owner, account_id, calendar_id)
            access = await self._access(account_id)
            events = await self._adapter.events_between(access, calendar_id, start, end)
            # Deselection/revocation can race the network read; discard late data.
            current = await self._repository.begin_sync(owner, account_id, calendar_id)
            if current != ticket:
                raise AssistantAccessError("calendar_changed_during_query")
            await self._repository.session_owner(session_id)
            return events

    async def select(
        self, session_id: str, account_id: str, calendar_id: str, selected: bool
    ) -> None:
        owner = await self._repository.session_owner(session_id)
        await self._repository.select(owner, account_id, calendar_id, selected)

    async def sync(self, session_id: str, account_id: str, calendar_id: str) -> bool:
        owner = await self._repository.session_owner(session_id)
        async with self._lock:
            ticket = await self._repository.begin_sync(owner, account_id, calendar_id)
            access = await self._access(account_id)
            batch = await self._adapter.sync_events(access, calendar_id, ticket.sync_token)
            return await self._repository.apply_sync(owner, ticket, batch)

    async def disconnect(self, session_id: str, account_id: str) -> bool:
        owner = await self._repository.session_owner(session_id)
        # Revoke locally before waiting for an in-flight sync. Its late batch is
        # fenced immediately even when Google is unreachable.
        reference = await self._repository.revoke(owner, account_id)
        async with self._lock:
            return await self._cleanup(reference)

    async def reconcile(self) -> None:
        """Startup/retry maintenance; caller must invoke after database.open()."""
        async with self._lock:
            accounts = await self._repository.accounts()
            await self._secrets.prune({a.secret_ref for a in accounts})
            for account in accounts:
                if account.status == "revoked":
                    await self._cleanup(account.secret_ref)

    async def _connected(self, account_id: str) -> AccountRecord:
        for account in await self._repository.accounts():
            if account.account_id == account_id and account.status == "connected":
                return account
        raise AssistantAccessError("account_not_connected")

    async def _credential(self, reference: str) -> _Credential:
        raw = await self._secrets.get(reference)
        if raw is None:
            raise AssistantAccessError("account_credential_missing")
        try:
            return _Credential.model_validate_json(raw)
        except ValidationError:
            raise AssistantAccessError("account_credential_invalid") from None

    async def _access(self, account_id: str, required_scope: str = READ_SCOPE) -> str:
        account = await self._connected(account_id)
        credential = await self._credential(account.secret_ref)
        try:
            tokens = await self._adapter.refresh(
                client_id=self._client.client_id,
                client_secret=self._client.client_secret,
                refresh_token=credential.refresh_token,
            )
        except GoogleCalendarError as error:
            if error.code == "authorization_expired":
                await self._repository.revoke("local", account_id)
                await self._secrets.set(account.secret_ref, None)
            raise
        # Persist a rotated token before subsequent account checks, including when local
        # disconnect has won: its cleanup still needs the newest credential.
        scopes = tokens.scopes if tokens.scopes is not None else tuple(credential.scopes)
        await self._secrets.set(
            account.secret_ref,
            json.dumps(
                {
                    "refresh_token": tokens.refresh_token or credential.refresh_token,
                    "scopes": list(scopes),
                }
            ),
        )
        await self._connected(account_id)
        if READ_SCOPE not in scopes:
            await self._repository.revoke("local", account_id)
            raise AssistantAccessError("calendar_read_consent_lost")
        if required_scope not in scopes:
            raise AssistantAccessError("google_write_consent_required")
        return tokens.access_token

    async def _cleanup(self, reference: str) -> bool:
        if await self._secrets.get(reference) is None:
            return True
        credential = await self._credential(reference)
        try:
            await self._adapter.revoke(credential.refresh_token)
        except GoogleCalendarError as error:
            if error.code == "token_already_revoked":
                await self._secrets.set(reference, None)
                return True
            # Remote revocation remains pending. The local account is already
            # disabled. Keep the credential only for a later revocation attempt.
            return False
        await self._secrets.set(reference, None)
        return True
