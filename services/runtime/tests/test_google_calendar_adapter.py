"""Google wire failure boundaries; these do not establish real account acceptance."""

import asyncio
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest
from chatwaifu_runtime.personal_assistant.google_calendar import (
    GoogleCalendarAdapter,
    GoogleCalendarError,
)
from chatwaifu_runtime.personal_assistant.google_tasks import GoogleTasksAdapter


@pytest.mark.parametrize("kind", ["oauth", "tasks"])
async def test_explicit_proxy_routes_google_requests_without_ambient_proxy(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets: list[bytes] = []

    async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
            targets.append(headers.split(b"\r\n", 1)[0])
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    async with await asyncio.start_server(proxy, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        calendar = GoogleCalendarAdapter(proxy_url=url)
        tasks = GoogleTasksAdapter(proxy_url=url)
        try:
            with pytest.raises(GoogleCalendarError, match="transport_error"):
                if kind == "oauth":
                    await calendar.exchange_code(
                        client_id="synthetic-client",
                        client_secret=None,
                        code="synthetic-code",
                        verifier="synthetic-verifier",
                        redirect_uri="http://127.0.0.1:12345/oauth/google",
                    )
                else:
                    await tasks.lists("synthetic-token")
            host = "oauth2.googleapis.com" if kind == "oauth" else "tasks.googleapis.com"
            assert targets == [f"CONNECT {host}:443 HTTP/1.1".encode()]
        finally:
            await calendar.close()
            await tasks.close()


async def test_expired_delta_discards_partial_pages_and_returns_full_snapshot() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "discard", "status": "cancelled"}],
                    "nextPageToken": "page2",
                },
            )
        if len(requests) == 2:
            return httpx.Response(410)
        return httpx.Response(
            200,
            json={
                "items": [{"id": "keep", "status": "cancelled"}],
                "nextSyncToken": "fresh",
            },
        )

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    try:
        result = await adapter.sync_events("access", "a/b@example.com", "expired")
        assert result.replace_snapshot and result.sync_token == "fresh"
        assert [event.id for event in result.events] == ["keep"]
        assert requests[0].url.params["syncToken"] == "expired"
        assert requests[1].url.params["syncToken"] == "expired"
        assert requests[1].url.params["pageToken"] == "page2"
        assert "syncToken" not in requests[2].url.params
        assert "pageToken" not in requests[2].url.params
        assert b"a%2Fb%40example.com" in requests[0].url.raw_path
        assert all("timeMin" not in r.url.params for r in requests)
    finally:
        await adapter.close()


@pytest.mark.parametrize(
    "last_page",
    [
        {"nextPageToken": "same"},
        {"nextPageToken": "other", "nextSyncToken": "premature"},
        {"items": []},
    ],
)
async def test_malformed_cursor_sequence_never_returns_a_batch(
    last_page: dict[str, object],
) -> None:
    count = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, json={"nextPageToken": "same"} if count == 1 else last_page)

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(GoogleCalendarError):
            await adapter.sync_events("access", "primary", None)
        assert count == 2
    finally:
        await adapter.close()


async def test_cancellation_during_second_page_propagates() -> None:
    waiting = asyncio.Event()
    count = 0

    async def handle(_: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(200, json={"nextPageToken": "next"})
        waiting.set()
        await asyncio.Future[None]()
        raise AssertionError("unreachable")

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    task = asyncio.create_task(adapter.sync_events("access", "primary", "old"))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await adapter.close()


async def test_token_refresh_preserves_omitted_token_and_revocation_uses_body() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/revoke":
            return httpx.Response(200)
        return httpx.Response(
            200,
            json={
                "access_token": "secret-access",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    try:
        tokens = await adapter.refresh(
            client_id="client",
            client_secret="secret-client",
            refresh_token="secret-refresh",
        )
        assert tokens.refresh_token is None
        assert "secret-access" not in repr(tokens)
        await adapter.revoke("secret-refresh")
        assert all(not r.url.query for r in requests)
        assert parse_qs(requests[-1].content.decode()) == {"token": ["secret-refresh"]}
    finally:
        await adapter.close()


@pytest.mark.parametrize("status", [302, 401, 429, 500])
async def test_errors_are_redacted_and_redirects_not_followed(status: int) -> None:
    count = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(
            status, headers={"Location": "https://example.com/secret"}, text="secret-provider-body"
        )

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(GoogleCalendarError) as caught:
            await adapter.calendars("secret-access")
        assert "secret" not in str(caught.value)
        assert caught.value.retryable == (status in (429, 500))
        assert count == 1
    finally:
        await adapter.close()


async def test_recurring_master_all_day_and_deleted_exception_are_preserved() -> None:
    adapter = GoogleCalendarAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "nextSyncToken": "next",
                    "items": [
                        {
                            "id": "master",
                            "status": "confirmed",
                            "start": {"date": "2026-09-22"},
                            "end": {"date": "2026-09-23"},
                            "recurrence": ["RRULE:FREQ=DAILY"],
                        },
                        {
                            "id": "exception",
                            "status": "cancelled",
                            "recurringEventId": "master",
                            "originalStartTime": {"date": "2026-09-24"},
                        },
                    ],
                },
            )
        )
    )
    try:
        result = await adapter.sync_events("access", "primary", None)
        assert result.events[0].recurrence == ("RRULE:FREQ=DAILY",)
        assert result.events[1].recurring_event_id == "master"
        assert result.events[1].original_start is not None
    finally:
        await adapter.close()


async def test_revoked_refresh_token_requires_reauthorization_without_echoing_body() -> None:
    adapter = GoogleCalendarAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={"error": "invalid_grant", "error_description": "secret-refresh"},
            )
        )
    )
    try:
        with pytest.raises(GoogleCalendarError) as caught:
            await adapter.refresh(client_id="client", client_secret=None, refresh_token="secret")
        assert caught.value.code == "authorization_expired"
        assert not caught.value.retryable
        assert "secret" not in str(caught.value)
    finally:
        await adapter.close()


async def test_failed_full_resync_returns_no_partial_result_and_stops_retrying() -> None:
    count = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(410)

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(GoogleCalendarError, match="sync_expired"):
            await adapter.sync_events("access", "primary", "expired")
        assert count == 2
    finally:
        await adapter.close()


async def test_window_query_expands_recurring_events_without_sync_cursor() -> None:
    from datetime import UTC, datetime

    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": [{"id": "deleted", "status": "cancelled"}]})

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    try:
        start, end = datetime(2026, 9, 22, tzinfo=UTC), datetime(2026, 9, 29, tzinfo=UTC)
        assert await adapter.events_between("access", "calendar", start, end) == ()
        assert requests[0].url.params["singleEvents"] == "true"
        assert requests[0].url.params["timeMin"] == start.isoformat()
        assert "syncToken" not in requests[0].url.params
        with pytest.raises(GoogleCalendarError, match="invalid_query_window"):
            await adapter.events_between(
                "access", "calendar", start, datetime(2027, 1, 1, tzinfo=UTC)
            )
        assert len(requests) == 1
    finally:
        await adapter.close()


async def test_calendar_write_uses_client_id_and_etag_and_rejects_stale_edit() -> None:
    requests: list[httpx.Request] = []
    event = {
        "id": "abcd1234",
        "status": "confirmed",
        "summary": "会议",
        "etag": '"v1"',
        "start": {"dateTime": "2026-09-28T10:00:00+08:00"},
        "end": {"dateTime": "2026-09-28T11:00:00+08:00"},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(412, text="secret provider body")
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json=event)

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    try:
        item = await adapter.create_event(
            "token",
            "me@example.com",
            "abcd1234",
            {
                "summary": "会议",
                "start": event["start"],
                "end": event["end"],
            },
        )
        assert item.id == "abcd1234" and item.etag == '"v1"'
        assert b"me%40example.com" in requests[0].url.raw_path
        assert b'"id":"abcd1234"' in requests[0].content
        with pytest.raises(GoogleCalendarError, match="item_changed_refresh_before_editing"):
            await adapter.update_event(
                "token", "me@example.com", item.id, {"summary": "新标题"}, '"v1"'
            )
        assert requests[1].headers["If-Match"] == '"v1"'
        await adapter.delete_event("token", "me@example.com", item.id, '"v1"')
        assert requests[2].headers["If-Match"] == '"v1"'
    finally:
        await adapter.close()


async def test_google_tasks_pages_date_only_and_stale_write() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/deleted"):
            return httpx.Response(200, json={"id": "deleted", "deleted": True})
        if request.method == "PATCH":
            return httpx.Response(412)
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": "task",
                    "title": "买牛奶",
                    "due": "2026-09-29T00:00:00.000Z",
                    "etag": '"t1"',
                },
            )
        if request.url.params.get("pageToken") == "next":
            return httpx.Response(200, json={"items": [{"id": "second", "title": "后续"}]})
        return httpx.Response(
            200, json={"items": [{"id": "first", "title": "已有"}], "nextPageToken": "next"}
        )

    adapter = GoogleTasksAdapter(transport=httpx.MockTransport(handle))
    try:
        items = await adapter.tasks("token", "a/b")
        assert [item.id for item in items] == ["first", "second"]
        created = await adapter.create("token", "a/b", "买牛奶", None, date(2026, 9, 29))
        assert created.due == date(2026, 9, 29)
        assert b"a%2Fb" in requests[0].url.raw_path
        assert b"2026-09-29T00:00:00.000Z" in requests[2].content
        with pytest.raises(GoogleCalendarError, match="item_changed_refresh_before_editing"):
            await adapter.update("token", "a/b", "task", {"title": "更新"}, '"t1"')
        assert requests[3].headers["If-Match"] == '"t1"'
        await adapter.delete("token", "a/b", "task", '"t1"')
        with pytest.raises(GoogleCalendarError, match="item_not_found"):
            await adapter.get("token", "a/b", "deleted")
    finally:
        await adapter.close()


@pytest.mark.parametrize("quote_escapes", [0, 1, 2, 3])
async def test_single_instance_write_is_conditional_and_does_not_change_its_sibling(
    quote_escapes: int,
) -> None:
    from copy import deepcopy
    from typing import cast
    from unittest.mock import AsyncMock

    from chatwaifu_runtime.personal_assistant.accounts import (
        GoogleAccountService,
        GoogleClient,
        SecretStore,
    )
    from chatwaifu_runtime.personal_assistant.repository import (
        AssistantAccessError,
        AssistantRepository,
    )

    store = {
        identifier: {
            "id": identifier,
            "status": "confirmed",
            "summary": "周会",
            "etag": '"v1"',
            "recurringEventId": "series",
            "originalStartTime": {
                "dateTime": "2026-11-03T09:00:00+08:00"
                if identifier == "one"
                else "2026-11-10T09:00:00+08:00"
            },
            "start": {
                "dateTime": "2026-11-03T09:00:00+08:00"
                if identifier == "one"
                else "2026-11-10T09:00:00+08:00"
            },
            "end": {
                "dateTime": "2026-11-03T10:00:00+08:00"
                if identifier == "one"
                else "2026-11-10T10:00:00+08:00"
            },
        }
        for identifier in ["one", "two"]
    }
    sibling = deepcopy(store["two"])
    writes: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        import json

        identifier = request.url.path.rsplit("/", 1)[-1]
        if request.method == "PATCH":
            assert request.headers["If-Match"] == '"v1"'
            writes.append(identifier)
            store[identifier].update(json.loads(request.content))
            store[identifier]["etag"] = '"v2"'
        return httpx.Response(200, json=store[identifier])

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    repository = AsyncMock(spec=AssistantRepository)
    repository.session_owner.return_value = "local"
    service = GoogleAccountService(
        cast(AssistantRepository, repository),
        cast(SecretStore, AsyncMock()),
        adapter,
        GoogleClient("synthetic-client"),
    )
    service._writable_calendar = AsyncMock()  # pyright: ignore[reportPrivateUsage]
    service._access = AsyncMock(return_value="synthetic-access")  # pyright: ignore[reportPrivateUsage]
    try:
        updated = await service.update_event(
            "session",
            "account",
            "calendar",
            "one",
            '"v1"'.replace('"', "\\" * quote_escapes + '"'),
            {
                "summary": "单次改期",
                "start": {"dateTime": "2026-11-03T11:00:00+08:00"},
                "end": {"dateTime": "2026-11-03T12:00:00+08:00"},
            },
        )
        assert updated.id == "one" and updated.original_start is not None
        assert store["two"] == sibling and writes == ["one"]
        with pytest.raises(AssistantAccessError, match="calendar_version_changed"):
            await service.update_event(
                "session",
                "account",
                "calendar",
                "one",
                '"v1"'.replace('"', "\\" * quote_escapes + '"'),
                {"summary": "过时修改"},
            )
        assert writes == ["one"]
    finally:
        await adapter.close()


async def test_all_day_creation_roundtrips_exclusive_dates_without_inventing_a_clock() -> None:
    from typing import cast
    from unittest.mock import AsyncMock

    from chatwaifu_runtime.personal_assistant.accounts import (
        GoogleAccountService,
        GoogleClient,
        SecretStore,
    )
    from chatwaifu_runtime.personal_assistant.repository import AssistantRepository

    body: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        import json

        if request.method == "POST":
            body.update(json.loads(request.content))
        return httpx.Response(200, json={**body, "status": "confirmed", "etag": "v1"})

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    repo = AsyncMock(spec=AssistantRepository)
    repo.session_owner.return_value = "local"
    service = GoogleAccountService(
        cast(AssistantRepository, repo),
        cast(SecretStore, AsyncMock()),
        adapter,
        GoogleClient("synthetic-client"),
    )
    service._writable_calendar = AsyncMock()  # pyright: ignore[reportPrivateUsage]
    service._access = AsyncMock(return_value="synthetic-access")  # pyright: ignore[reportPrivateUsage]
    try:
        event = await service.create_event(
            "session",
            "account",
            "calendar",
            "synthetic",
            "休假",
            date(2026, 11, 3),
            date(2026, 11, 5),
        )
        assert body["start"] == {"date": "2026-11-03"} and body["end"] == {"date": "2026-11-05"}
        assert (
            event.start is not None
            and event.start.day == date(2026, 11, 3)
            and event.start.timestamp is None
        )
    finally:
        await adapter.close()


async def test_calendar_watch_uses_scoped_opaque_channel_and_stops_exact_resource() -> None:
    import json

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic-access"
        if request.url.path.endswith("/events/watch"):
            body = json.loads(request.content)
            assert body == {
                "id": "opaque-channel",
                "token": "synthetic-channel-token",
                "type": "web_hook",
                "address": "https://calendar.example/notify",
                "params": {"ttl": "86400"},
            }
            return httpx.Response(
                200,
                json={
                    "id": "opaque-channel",
                    "resourceId": "opaque-resource",
                    "expiration": "2000000000000",
                },
            )
        assert request.url.path.endswith("/channels/stop")
        assert json.loads(request.content) == {
            "id": "opaque-channel",
            "resourceId": "opaque-resource",
        }
        return httpx.Response(204)

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handler))
    try:
        result = await adapter.watch_events(
            "synthetic-access",
            "selected/calendar@example",
            "opaque-channel",
            "synthetic-channel-token",
            "https://calendar.example/notify",
        )
        assert (
            result.channel_id == "opaque-channel"
            and result.resource_id == "opaque-resource"
            and result.expires_at == 2000000000000
        )
        assert "selected%2Fcalendar" in str(requests[0].url)
        await adapter.stop_watch("synthetic-access", result.channel_id, result.resource_id)
        assert len(requests) == 2
    finally:
        await adapter.close()
