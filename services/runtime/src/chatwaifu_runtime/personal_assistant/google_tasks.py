"""Google Tasks API boundary. A due date has no time-of-day in this API."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from typing import Literal, cast
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chatwaifu_runtime.personal_assistant.google_calendar import GoogleCalendarError

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
API = "https://tasks.googleapis.com/tasks/v1"
MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TaskList:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class GoogleTask:
    id: str
    title: str
    notes: str | None
    due: date | None
    status: Literal["needsAction", "completed"]
    etag: str | None


class _Wire(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _List(_Wire):
    id: str = Field(min_length=1)
    title: str = ""


class _Task(_Wire):
    id: str = Field(min_length=1)
    title: str = ""
    notes: str | None = None
    due: str | None = None
    status: Literal["needsAction", "completed"] = "needsAction"
    etag: str | None = None
    deleted: bool = False

    def domain(self) -> GoogleTask:
        try:
            due = date.fromisoformat(self.due[:10]) if self.due else None
        except ValueError:
            raise GoogleCalendarError("invalid_response") from None
        return GoogleTask(self.id, self.title, self.notes, due, self.status, self.etag)


class _Page(_Wire):
    items: list[dict[str, object]] = Field(default_factory=lambda: list[dict[str, object]]())
    nextPageToken: str | None = None


class GoogleTasksAdapter:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=15,
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _url(list_id: str, task_id: str | None = None) -> str:
        if (
            not list_id
            or len(list_id) > 2048
            or (task_id is not None and (not task_id or len(task_id) > 2048))
        ):
            raise GoogleCalendarError("invalid_task_reference")
        base = f"{API}/lists/{quote(list_id, safe='')}/tasks"
        return f"{base}/{quote(task_id, safe='')}" if task_id else base

    async def lists(self, token: str) -> tuple[TaskList, ...]:
        items = await self._pages(f"{API}/users/@me/lists", token, {"maxResults": "100"})
        try:
            return tuple(
                TaskList(item.id, item.title)
                for raw in items
                if (item := _List.model_validate(raw))
            )
        except ValidationError:
            raise GoogleCalendarError("invalid_response") from None

    async def tasks(self, token: str, list_id: str) -> tuple[GoogleTask, ...]:
        items = await self._pages(
            self._url(list_id),
            token,
            {
                "maxResults": "100",
                "showCompleted": "true",
                "showHidden": "false",
                "showDeleted": "false",
            },
        )
        try:
            return tuple(
                task.domain() for raw in items if not (task := _Task.model_validate(raw)).deleted
            )
        except ValidationError:
            raise GoogleCalendarError("invalid_response") from None

    async def create(
        self, token: str, list_id: str, title: str, notes: str | None, due: date | None
    ) -> GoogleTask:
        body: dict[str, object] = {"title": title}
        if notes is not None:
            body["notes"] = notes
        if due is not None:
            body["due"] = f"{due.isoformat()}T00:00:00.000Z"
        return self._task(await self._request("POST", self._url(list_id), token, body=body))

    async def update(
        self, token: str, list_id: str, task_id: str, changes: dict[str, object], etag: str
    ) -> GoogleTask:
        return self._task(
            await self._request(
                "PATCH", self._url(list_id, task_id), token, body=changes, etag=etag
            )
        )

    async def delete(self, token: str, list_id: str, task_id: str, etag: str) -> None:
        await self._request("DELETE", self._url(list_id, task_id), token, etag=etag)

    @staticmethod
    def _task(raw: dict[str, object]) -> GoogleTask:
        try:
            return _Task.model_validate(raw).domain()
        except ValidationError:
            raise GoogleCalendarError("invalid_response") from None

    async def _pages(self, url: str, token: str, params: dict[str, str]) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        seen: set[str] = set()
        query = dict(params)
        for _ in range(20):
            raw = await self._request("GET", url, token, params=query)
            try:
                page = _Page.model_validate(raw)
            except ValidationError:
                raise GoogleCalendarError("invalid_response") from None
            items.extend(page.items)
            if len(items) > 1000:
                raise GoogleCalendarError("task_limit_exceeded")
            if page.nextPageToken is None:
                return items
            if page.nextPageToken in seen:
                raise GoogleCalendarError("invalid_pagination")
            seen.add(page.nextPageToken)
            query["pageToken"] = page.nextPageToken
        raise GoogleCalendarError("task_limit_exceeded")

    async def _request(
        self,
        method: str,
        url: str,
        token: str,
        *,
        body: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
        etag: str | None = None,
    ) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {token}"}
        if etag is not None:
            if not etag or len(etag) > 256 or "\r" in etag or "\n" in etag:
                raise GoogleCalendarError("invalid_etag")
            headers["If-Match"] = etag
        try:
            async with asyncio.timeout(20):
                async with self._client.stream(
                    method, url, headers=headers, json=body, params=params
                ) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise GoogleCalendarError("response_too_large")
                    status = response.status_code
                    if status not in (200, 201, 204):
                        code = {
                            400: "invalid_request",
                            401: "authorization_expired",
                            403: "access_denied",
                            404: "item_not_found",
                            409: "item_already_exists",
                            412: "item_changed_refresh_before_editing",
                            429: "rate_limited",
                        }.get(status, "provider_unavailable" if status >= 500 else "provider_error")
                        raise GoogleCalendarError(code, retryable=status == 429 or status >= 500)
                    if status == 204 or not content:
                        return {}
                    raw: object = json.loads(content)
                    if not isinstance(raw, dict):
                        raise GoogleCalendarError("invalid_response")
                    return cast(dict[str, object], raw)
        except (httpx.HTTPError, TimeoutError):
            raise GoogleCalendarError("transport_error", retryable=True) from None
        except (ValueError, UnicodeError):
            raise GoogleCalendarError("invalid_response") from None
