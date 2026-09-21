"""Google wire failure boundaries; these do not establish real account acceptance."""

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from chatwaifu_runtime.personal_assistant.google_calendar import (
    GoogleCalendarAdapter,
    GoogleCalendarError,
)


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
