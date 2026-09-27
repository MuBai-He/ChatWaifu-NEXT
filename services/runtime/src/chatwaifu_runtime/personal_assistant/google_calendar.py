"""Google Calendar boundary; no persistence, account permissions or UI policy here.

Callers must authorize the owner and selected calendar before calling this adapter.
Sync returns a complete batch, never partial pages. A repository must atomically
apply that batch and its cursor, guarded against concurrent account revocation.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal, cast
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

READ_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 50_000
MAX_PAGES = 200
MAX_BATCH_BYTES = 16 * 1024 * 1024


class GoogleCalendarError(RuntimeError):
    """Stable code only: provider bodies, URLs and request secrets are never echoed."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_in: int
    scopes: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class Calendar:
    id: str
    title: str
    timezone: str | None
    access_role: str
    primary: bool = False


@dataclass(frozen=True, slots=True)
class EventTime:
    day: date | None
    timestamp: datetime | None
    timezone: str | None


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    id: str
    status: str
    title: str | None
    start: EventTime | None
    end: EventTime | None
    etag: str | None
    recurrence: tuple[str, ...]
    recurring_event_id: str | None
    original_start: EventTime | None
    event_type: str = "default"


@dataclass(frozen=True, slots=True)
class EventSync:
    events: tuple[CalendarEvent, ...]
    sync_token: str = field(repr=False)
    replace_snapshot: bool


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _Tokens(_WireModel):
    access_token: str = Field(min_length=1)
    refresh_token: str | None = Field(default=None, min_length=1)
    token_type: str
    expires_in: int = Field(gt=0)
    scope: str | None = None


class _Calendar(_WireModel):
    id: str = Field(min_length=1)
    summary: str = ""
    timeZone: str | None = None
    accessRole: str
    primary: bool = False


class _Time(_WireModel):
    date: str | None = None
    dateTime: str | None = None
    timeZone: str | None = None

    def domain(self) -> EventTime:
        if (self.date is None) == (self.dateTime is None):
            raise GoogleCalendarError("invalid_response")
        try:
            day = date.fromisoformat(self.date) if self.date is not None else None
            timestamp = datetime.fromisoformat(self.dateTime) if self.dateTime else None
        except ValueError:
            raise GoogleCalendarError("invalid_response") from None
        # A provider-local datetime can use its accompanying IANA time zone.
        if timestamp is not None and timestamp.tzinfo is None and not self.timeZone:
            raise GoogleCalendarError("invalid_response")
        return EventTime(day, timestamp, self.timeZone)


class _Event(_WireModel):
    id: str = Field(min_length=1)
    status: Literal["confirmed", "tentative", "cancelled"]
    summary: str | None = None
    start: _Time | None = None
    end: _Time | None = None
    etag: str | None = None
    recurrence: list[str] = Field(default_factory=list)
    recurringEventId: str | None = None
    originalStartTime: _Time | None = None
    eventType: str = "default"

    def domain(self) -> CalendarEvent:
        # Deleted entries can contain only id + status; retain these tombstones.
        if self.status != "cancelled" and (self.start is None or self.end is None):
            raise GoogleCalendarError("invalid_response")
        return CalendarEvent(
            self.id,
            self.status,
            self.summary,
            self.start.domain() if self.start else None,
            self.end.domain() if self.end else None,
            self.etag,
            tuple(self.recurrence),
            self.recurringEventId,
            self.originalStartTime.domain() if self.originalStartTime else None,
            self.eventType,
        )


class _Page(_WireModel):
    items: list[dict[str, object]] = Field(default_factory=lambda: [])
    nextPageToken: str | None = Field(default=None, min_length=1)
    nextSyncToken: str | None = Field(default=None, min_length=1)


class GoogleCalendarAdapter:
    """Own one bounded HTTP client; the application owns start/close lifecycle."""

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

    async def exchange_code(
        self,
        *,
        client_id: str,
        client_secret: str | None,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> OAuthTokens:
        data = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        }
        if client_secret:
            data["client_secret"] = client_secret
        return await self._tokens(data)

    async def refresh(
        self,
        *,
        client_id: str,
        client_secret: str | None,
        refresh_token: str,
    ) -> OAuthTokens:
        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        }
        if client_secret:
            data["client_secret"] = client_secret
        # An omitted refresh token means preserve the stored token, not erase it.
        return await self._tokens(data)

    async def _tokens(self, data: dict[str, str]) -> OAuthTokens:
        raw = await self._request("POST", TOKEN_URL, data=data)
        try:
            tokens = _Tokens.model_validate(raw)
        except ValidationError:
            raise GoogleCalendarError("invalid_response") from None
        if tokens.token_type.lower() != "bearer":
            raise GoogleCalendarError("invalid_response")
        return OAuthTokens(
            tokens.access_token,
            tokens.refresh_token,
            tokens.expires_in,
            tuple(tokens.scope.split()) if tokens.scope is not None else None,
        )

    async def revoke(self, token: str) -> None:
        # Never use a query parameter: request URLs routinely enter access logs.
        await self._request("POST", REVOKE_URL, data={"token": token}, empty=True)

    async def calendars(self, access_token: str) -> tuple[Calendar, ...]:
        items, _ = await self._pages(
            f"{API}/users/me/calendarList",
            access_token,
            {"maxResults": "250", "showDeleted": "false"},
        )
        try:
            entries = [_Calendar.model_validate(item) for item in items]
        except ValidationError:
            raise GoogleCalendarError("invalid_response") from None
        return tuple(
            Calendar(c.id, c.summary, c.timeZone, c.accessRole, c.primary) for c in entries
        )

    async def events_between(
        self, access_token: str, calendar_id: str, start: datetime, end: datetime
    ) -> tuple[CalendarEvent, ...]:
        if (
            not calendar_id
            or len(calendar_id) > 2048
            or start.utcoffset() is None
            or end.utcoffset() is None
            or not timedelta(0) < end - start <= timedelta(days=31)
        ):
            raise GoogleCalendarError("invalid_query_window")
        # Google expands recurrence with its own timezone/DST and exception rules.
        # This independent bounded query never changes the incremental sync cursor.
        items, _ = await self._pages(
            f"{API}/calendars/{quote(calendar_id, safe='')}/events",
            access_token,
            {
                "singleEvents": "true",
                "showDeleted": "false",
                "orderBy": "startTime",
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "maxResults": "250",
            },
            max_items=1000,
        )
        try:
            return tuple(
                event
                for item in items
                if (event := _Event.model_validate(item).domain()).status != "cancelled"
            )
        except ValidationError:
            raise GoogleCalendarError("invalid_response") from None

    async def create_event(
        self, access_token: str, calendar_id: str, event_id: str, payload: dict[str, object]
    ) -> CalendarEvent:
        url = self._event_url(calendar_id)
        raw = await self._request(
            "POST", url, token=access_token, json_body={"id": event_id, **payload}
        )
        return self._event(raw)

    async def get_event(self, access_token: str, calendar_id: str, event_id: str) -> CalendarEvent:
        raw = await self._request(
            "GET",
            f"{self._event_url(calendar_id)}/{quote(event_id, safe='')}",
            token=access_token,
        )
        return self._event(raw)

    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        payload: dict[str, object],
        etag: str,
    ) -> CalendarEvent:
        raw = await self._request(
            "PATCH",
            f"{self._event_url(calendar_id)}/{quote(event_id, safe='')}",
            token=access_token,
            json_body=payload,
            if_match=etag,
        )
        return self._event(raw)

    async def delete_event(
        self, access_token: str, calendar_id: str, event_id: str, etag: str
    ) -> None:
        await self._request(
            "DELETE",
            f"{self._event_url(calendar_id)}/{quote(event_id, safe='')}",
            token=access_token,
            if_match=etag,
            empty=True,
        )

    @staticmethod
    def _event_url(calendar_id: str) -> str:
        if not calendar_id or len(calendar_id) > 2048:
            raise GoogleCalendarError("invalid_calendar")
        return f"{API}/calendars/{quote(calendar_id, safe='')}/events"

    @staticmethod
    def _event(raw: dict[str, object]) -> CalendarEvent:
        try:
            return _Event.model_validate(raw).domain()
        except ValidationError:
            raise GoogleCalendarError("invalid_response") from None

    async def sync_events(
        self,
        access_token: str,
        calendar_id: str,
        sync_token: str | None,
    ) -> EventSync:
        if not calendar_id or len(calendar_id) > 2048:
            raise GoogleCalendarError("invalid_calendar")
        url = f"{API}/calendars/{quote(calendar_id, safe='')}/events"
        # Preserve recurring masters, exceptions and tombstones. Do not expand an
        # unbounded recurrence or mix time-window filters with a sync cursor.
        params = {"maxResults": "2500", "singleEvents": "false", "showDeleted": "true"}
        if sync_token:
            params["syncToken"] = sync_token
        replace = sync_token is None
        try:
            items, token = await self._pages(url, access_token, params)
        except GoogleCalendarError as error:
            if error.code != "sync_expired" or not sync_token:
                raise
            # Discard all partial delta pages. Attempt a fresh full snapshot once;
            # failure leaves the repository's existing snapshot untouched/stale.
            params.pop("syncToken")
            items, token = await self._pages(url, access_token, params)
            replace = True
        if not token:
            raise GoogleCalendarError("invalid_response")
        try:
            events = tuple(_Event.model_validate(item).domain() for item in items)
        except ValidationError:
            raise GoogleCalendarError("invalid_response") from None
        return EventSync(events, token, replace)

    async def _pages(
        self,
        url: str,
        token: str,
        params: dict[str, str],
        *,
        max_items: int = MAX_ITEMS,
    ) -> tuple[list[dict[str, object]], str | None]:
        items: list[dict[str, object]] = []
        seen: set[str] = set()
        query = dict(params)
        batch_bytes = 0
        try:
            async with asyncio.timeout(120):
                for _ in range(MAX_PAGES):
                    raw = await self._request("GET", url, token=token, params=query)
                    batch_bytes += len(json.dumps(raw).encode("utf-8"))
                    if batch_bytes > MAX_BATCH_BYTES:
                        raise GoogleCalendarError("sync_limit_exceeded")
                    try:
                        page = _Page.model_validate(raw)
                    except ValidationError:
                        raise GoogleCalendarError("invalid_response") from None
                    items.extend(page.items)
                    if len(items) > max_items:
                        raise GoogleCalendarError("sync_limit_exceeded")
                    if page.nextPageToken is None:
                        return items, page.nextSyncToken
                    if page.nextSyncToken is not None or page.nextPageToken in seen:
                        raise GoogleCalendarError("invalid_pagination")
                    seen.add(page.nextPageToken)
                    query["pageToken"] = page.nextPageToken
        except TimeoutError:
            raise GoogleCalendarError("transport_error", retryable=True) from None
        raise GoogleCalendarError("sync_limit_exceeded")

    async def _request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        data: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        empty: bool = False,
        json_body: dict[str, object] | None = None,
        if_match: str | None = None,
    ) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if if_match is not None:
            if not if_match or len(if_match) > 256 or "\r" in if_match or "\n" in if_match:
                raise GoogleCalendarError("invalid_etag")
            headers["If-Match"] = if_match
        try:
            async with asyncio.timeout(20):
                async with self._client.stream(
                    method,
                    url,
                    data=data,
                    json=json_body,
                    params=params,
                    headers=headers,
                ) as response:
                    status = response.status_code
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise GoogleCalendarError("response_too_large")
                    if status not in (200, 201, 204):
                        code = {
                            400: "invalid_request",
                            401: "authorization_expired",
                            403: "access_denied",
                            404: "item_not_found",
                            409: "item_already_exists",
                            410: "sync_expired",
                            412: "item_changed_refresh_before_editing",
                            429: "rate_limited",
                        }.get(status, "provider_unavailable" if status >= 500 else "provider_error")
                        if status == 400 and url in (TOKEN_URL, REVOKE_URL):
                            try:
                                failure: object = json.loads(content)
                            except (ValueError, UnicodeError):
                                failure = None
                            if isinstance(failure, dict):
                                reason = cast(dict[str, object], failure).get("error")
                                if url == REVOKE_URL and reason == "invalid_token":
                                    code = "token_already_revoked"
                                elif reason == "invalid_grant":
                                    code = "authorization_expired"
                                elif reason == "invalid_client":
                                    code = "client_configuration_invalid"
                        raise GoogleCalendarError(code, retryable=status == 429 or status >= 500)
                    if empty:
                        return {}
                    raw: object = json.loads(content)
                    if not isinstance(raw, dict):
                        raise GoogleCalendarError("invalid_response")
                    return cast(dict[str, object], raw)
        except (httpx.HTTPError, TimeoutError):
            raise GoogleCalendarError("transport_error", retryable=True) from None
        except (ValueError, UnicodeError):
            raise GoogleCalendarError("invalid_response") from None
