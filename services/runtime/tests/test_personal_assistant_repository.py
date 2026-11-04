"""Real SQLite boundaries for calendar synchronization and account revocation."""

import asyncio
import base64
import hashlib
import json
import sqlite3
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.persistence.async_secret_store import AsyncSecretStore
from chatwaifu_runtime.persistence.atomic_secret_store import AtomicSecretStore
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_personal_assistant import SQLiteAssistantRepository
from chatwaifu_runtime.personal_assistant.accounts import GoogleAccountService, GoogleClient
from chatwaifu_runtime.personal_assistant.agenda import AgendaService
from chatwaifu_runtime.personal_assistant.google_calendar import (
    READ_SCOPE,
    WRITE_SCOPE,
    Calendar,
    CalendarEvent,
    EventSync,
    GoogleCalendarAdapter,
    OAuthTokens,
)
from chatwaifu_runtime.personal_assistant.google_tasks import TASKS_SCOPE, TaskList
from chatwaifu_runtime.personal_assistant.oauth import GoogleOAuthCoordinator
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError, WriteDestination


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[tuple[Database, SQLiteAssistantRepository]]:
    path = tmp_path / "assistant.db"
    db = Database(path, StorageConfig(database_path=path))
    await db.open()
    repo = SQLiteAssistantRepository(db)
    await repo.connect("local", "account", "secret-reference-only")
    await repo.add_calendar("local", "account", Calendar("calendar", "Existing", "UTC", "owner"))
    await repo.select("local", "account", "calendar", True)
    try:
        yield db, repo
    finally:
        await db.close()


def batch(token: str, *ids: str, replace: bool = True) -> EventSync:
    return EventSync(
        tuple(CalendarEvent(id, "cancelled", None, None, None, None, (), None, None) for id in ids),
        token,
        replace,
    )


async def test_competing_batches_only_commit_once(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    db, repo = store
    ticket = await repo.begin_sync("local", "account", "calendar")
    results = await asyncio.gather(
        repo.apply_sync("local", ticket, batch("first", "a")),
        repo.apply_sync("local", ticket, batch("second", "b")),
    )
    assert results.count(True) == results.count(False) == 1
    assert len(await db.fetchall("SELECT * FROM assistant_events")) == 1
    winner = await repo.begin_sync("local", "account", "calendar")
    assert winner.sync_token in {"first", "second"}


async def test_write_destination_is_owner_private_and_cleared_on_account_revoke(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    db, repo = store
    destination = WriteDestination("calendar", "google", "account", "calendar", None)
    with pytest.raises(AssistantAccessError):
        await repo.set_destination("guest", destination)
    await repo.set_destination("local", destination)
    await db.close()
    await db.open()
    assert await repo.destinations("local") == (destination,)
    await repo.revoke("local", "account")
    assert await repo.destinations("local") == ()


async def test_tasklist_selection_and_default_are_cleared_together(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    _, repo = store
    await repo.add_tasklist("local", "account", TaskList("list", "私人待办"))
    assert not (await repo.tasklists("local", "account"))[0].selected
    await repo.select_tasklist("local", "account", "list", True)
    await repo.set_destination(
        "local", WriteDestination("reminder", "google", "account", "list", None)
    )
    await repo.select_tasklist("local", "account", "list", False)
    assert await repo.destinations("local") == ()
    assert not (await repo.tasklists("local", "account"))[0].selected


async def test_old_readonly_google_account_can_still_read_but_cannot_write(
    store: tuple[Database, SQLiteAssistantRepository],
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime, timedelta

    db, repo = store
    await add_session(db, "owner", "local")
    secrets = AtomicSecretStore(tmp_path / "legacy-secret.json")
    secrets.set(
        "secret-reference-only", json.dumps({"refresh_token": "old", "scopes": [READ_SCOPE]})
    )
    methods: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path == "/token":
            return httpx.Response(
                200, json={"access_token": "access", "expires_in": 3600, "token_type": "Bearer"}
            )
        return httpx.Response(200, json={"items": []})

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    service = GoogleAccountService(repo, AsyncSecretStore(secrets), adapter, GoogleClient("client"))
    try:
        status = await service.status("owner")
        assert status[0].account_id == "account" and not status[0].calendar_write
        start = datetime(2026, 9, 28, tzinfo=UTC)
        assert (
            await service.query("owner", "account", "calendar", start, start + timedelta(days=1))
            == ()
        )
        with pytest.raises(AssistantAccessError, match="google_write_consent_required"):
            await service.create_event(
                "owner",
                "account",
                "calendar",
                "abcd1234",
                "新事件",
                start,
                start + timedelta(hours=1),
            )
        assert "POST" in methods  # OAuth refresh only, no Calendar event write.
        assert "GET" in methods
        assert methods.count("POST") == 2
    finally:
        await adapter.close()


async def test_agenda_refuses_local_fallback_without_a_default(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    from uuid import uuid4

    db, repo = store
    await add_session(db, "owner", "local")
    agenda = AgendaService(repo, None, None)
    with pytest.raises(AssistantAccessError, match="write_destination_required"):
        await agenda.create("owner", "reminder", uuid4(), "买牛奶")


@pytest.mark.parametrize("mutation", ["revoke", "deselect", "reselect"])
async def test_old_batch_cannot_resurrect_cache(
    store: tuple[Database, SQLiteAssistantRepository],
    mutation: str,
) -> None:
    db, repo = store
    ticket = await repo.begin_sync("local", "account", "calendar")
    if mutation == "revoke":
        assert await repo.revoke("local", "account") == "secret-reference-only"
        assert await repo.revoke("local", "account") == "secret-reference-only"
    else:
        await repo.select("local", "account", "calendar", False)
        if mutation == "reselect":
            await repo.select("local", "account", "calendar", True)
    assert not await repo.apply_sync("local", ticket, batch("late", "forbidden"))
    assert not await db.fetchall("SELECT * FROM assistant_events")


async def test_insertion_failure_rolls_back_cursor_and_full_snapshot_deletion(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    db, repo = store
    ticket = await repo.begin_sync("local", "account", "calendar")
    assert await repo.apply_sync("local", ticket, batch("old", "existing"))
    ticket = await repo.begin_sync("local", "account", "calendar")
    await db.execute(
        "CREATE TRIGGER fail_event BEFORE INSERT ON assistant_events "
        "WHEN NEW.event_id='fail' BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await repo.apply_sync("local", ticket, batch("new", "partial", "fail"))
    assert (await repo.begin_sync("local", "account", "calendar")).sync_token == "old"
    rows = await db.fetchall("SELECT event_id FROM assistant_events")
    assert [row[0] for row in rows] == ["existing"]


async def test_restart_keeps_cursor_and_revocation_fence(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    db, repo = store
    ticket = await repo.begin_sync("local", "account", "calendar")
    assert await repo.apply_sync("local", ticket, batch("saved", "event"))
    await db.close()
    await db.open()
    assert (await repo.begin_sync("local", "account", "calendar")).sync_token == "saved"
    await repo.revoke("local", "account")
    await db.close()
    await db.open()
    with pytest.raises(AssistantAccessError):
        await repo.begin_sync("local", "account", "calendar")
    assert not await repo.apply_sync("local", ticket, batch("late", "event"))


async def test_discovery_does_not_select_and_foreign_scope_cannot_access(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    _, repo = store
    await repo.add_calendar("local", "account", Calendar("other", "Other", "UTC", "reader"))
    with pytest.raises(AssistantAccessError, match="calendar_not_selected"):
        await repo.begin_sync("local", "account", "other")
    with pytest.raises(AssistantAccessError, match="requires_owner"):
        await repo.begin_sync("guest", "account", "calendar")
    with pytest.raises(AssistantAccessError, match="requires_owner"):
        await repo.revoke("shared:room", "account")


async def add_session(db: Database, session_id: str, scope: str) -> None:
    await db.execute(
        "INSERT INTO sessions(session_id,character_id,state,conversation_state,created_at,"
        "updated_at,user_scope) VALUES (?,'character','idle','idle','now','now',?)",
        (session_id, scope),
    )


async def test_account_service_rejects_guest_and_retries_remote_revoke_after_restart(
    store: tuple[Database, SQLiteAssistantRepository],
    tmp_path: Path,
) -> None:
    db, repo = store
    await add_session(db, "owner", "local")
    await add_session(db, "guest", "guest")
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls == 1 else httpx.Response(200)

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    secrets = AtomicSecretStore(tmp_path / "assistant-secrets.json")
    service = GoogleAccountService(repo, AsyncSecretStore(secrets), adapter, GoogleClient("client"))
    tokens = OAuthTokens("access", "refresh", 3600, (READ_SCOPE,))
    try:
        with pytest.raises(AssistantAccessError):
            await service.connect_authorized("guest", tokens)
        with pytest.raises(AssistantAccessError):
            await service.status("missing-session")
        account = await service.connect_authorized("owner", tokens)
        reference = f"google:{account.account_id}"
        assert not await service.disconnect("owner", account.account_id)
        assert secrets.get(reference) is not None
        assert any(
            a.status == "revoked" and a.account_id == account.account_id
            for a in await service.status("owner")
        )
        # A new service instance recovers cleanup from durable references.
        restarted = GoogleAccountService(
            repo, AsyncSecretStore(secrets), adapter, GoogleClient("client")
        )
        secrets.set("orphan-before-db-commit", "unreferenced")
        await restarted.reconcile()
        assert secrets.get(reference) is None
        assert secrets.get("orphan-before-db-commit") is None
        assert calls == 2
    finally:
        await adapter.close()


async def test_disconnect_while_refreshing_keeps_rotated_token_for_cleanup(
    store: tuple[Database, SQLiteAssistantRepository],
    tmp_path: Path,
) -> None:
    db, repo = store
    await add_session(db, "owner", "local")
    refreshing = asyncio.Event()
    release = asyncio.Event()
    revoked_bodies: list[bytes] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            refreshing.set()
            await release.wait()
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "rotated",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        if request.url.path == "/revoke":
            revoked_bodies.append(request.content)
            return httpx.Response(200)
        raise AssertionError("revoked account must not query Google")

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    secrets = AtomicSecretStore(tmp_path / "assistant-secrets.json")
    service = GoogleAccountService(repo, AsyncSecretStore(secrets), adapter, GoogleClient("client"))
    account = await service.connect_authorized(
        "owner", OAuthTokens("a", "old", 3600, (READ_SCOPE,))
    )
    task = asyncio.create_task(service.discover("owner", account.account_id))
    try:
        await asyncio.wait_for(refreshing.wait(), 1)
        # The same durable step that disconnect performs before waiting for its lock.
        await repo.revoke("local", account.account_id)
        release.set()
        with pytest.raises(AssistantAccessError, match="account_not_connected"):
            await task
        assert await service.disconnect("owner", account.account_id)
        assert revoked_bodies == [b"token=rotated"]
        assert secrets.get(f"google:{account.account_id}") is None
    finally:
        release.set()
        task.cancel()
        await adapter.close()


async def test_already_revoked_provider_token_completes_cleanup(
    store: tuple[Database, SQLiteAssistantRepository],
    tmp_path: Path,
) -> None:
    db, repo = store
    await add_session(db, "owner", "local")
    adapter = GoogleCalendarAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={"error": "invalid_token"},
            )
        )
    )
    secrets = AtomicSecretStore(tmp_path / "assistant-secrets.json")
    service = GoogleAccountService(repo, AsyncSecretStore(secrets), adapter, GoogleClient("client"))
    try:
        account = await service.connect_authorized(
            "owner", OAuthTokens("a", "old", 3600, (READ_SCOPE,))
        )
        assert await service.disconnect("owner", account.account_id)
        assert secrets.get(f"google:{account.account_id}") is None
    finally:
        await adapter.close()


async def test_cancelled_secret_write_finishes_before_returning(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingStore(AtomicSecretStore):
        def set(self, name: str, value: str | None) -> None:
            started.set()
            if not release.wait(5):
                raise TimeoutError("test did not release secret write")
            super().set(name, value)

    raw = BlockingStore(tmp_path / "secrets.json")
    task = asyncio.create_task(AsyncSecretStore(raw).set("token", "rotated"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert raw.get("token") == "rotated"
    finally:
        release.set()


async def test_oauth_session_binding_pkce_and_replay(
    store: tuple[Database, SQLiteAssistantRepository],
    tmp_path: Path,
) -> None:
    db, repo = store
    await add_session(db, "owner1", "local")
    await add_session(db, "owner2", "local")
    exchanges: list[dict[str, list[str]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        exchanges.append(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": " ".join((READ_SCOPE, WRITE_SCOPE, TASKS_SCOPE)),
            },
        )

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    client = GoogleClient("desktop-client")
    service = GoogleAccountService(
        repo, AsyncSecretStore(AtomicSecretStore(tmp_path / "tokens")), adapter, client
    )
    oauth = GoogleOAuthCoordinator(repo, adapter, service, client)
    try:
        with pytest.raises(AssistantAccessError, match="invalid_oauth_callback"):
            await oauth.begin("owner1", "http://evil.example/oauth/google")
        flow = await oauth.begin("owner1", "http://127.0.0.1:55555/oauth/google")
        with pytest.raises(AssistantAccessError, match="oauth_flow_invalid"):
            await oauth.complete("owner2", flow.state, "code")
        assert not exchanges
        result = await oauth.complete("owner1", flow.state, "code")
        assert result.status == "connected"
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(exchanges[0]["code_verifier"][0].encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        query = parse_qs(urlsplit(flow.authorization_url).query)
        assert set(query["scope"][0].split()) == {READ_SCOPE, WRITE_SCOPE, TASKS_SCOPE}
        assert query["code_challenge"] == [challenge]
        assert query["code_challenge_method"] == ["S256"]
        with pytest.raises(AssistantAccessError, match="oauth_flow_invalid"):
            await oauth.complete("owner1", flow.state, "code")
        assert len(exchanges) == 1
    finally:
        await oauth.close()
        await adapter.close()


@pytest.mark.parametrize("same_account", [True, False])
async def test_google_scope_upgrade_requires_same_primary_account(
    store: tuple[Database, SQLiteAssistantRepository],
    tmp_path: Path,
    same_account: bool,
) -> None:
    db, repo = store
    await add_session(db, "owner", "local")
    secrets = AtomicSecretStore(tmp_path / "upgrade.json")
    secrets.set(
        "secret-reference-only", json.dumps({"refresh_token": "old", "scopes": [READ_SCOPE]})
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            if b"grant_type=refresh_token" in request.content:
                return httpx.Response(
                    200,
                    json={"access_token": "old-access", "token_type": "Bearer", "expires_in": 3600},
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": " ".join((READ_SCOPE, WRITE_SCOPE, TASKS_SCOPE)),
                },
            )
        assert request.url.path == "/calendar/v3/users/me/calendarList"
        identity = "owner@example.com"
        if not same_account and request.headers["Authorization"] == "Bearer new-access":
            identity = "different@example.com"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": identity, "summary": identity, "accessRole": "owner", "primary": True}
                ]
            },
        )

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    service = GoogleAccountService(repo, AsyncSecretStore(secrets), adapter, GoogleClient("client"))
    oauth = GoogleOAuthCoordinator(repo, adapter, service, GoogleClient("client"))
    try:
        flow = await oauth.begin("owner", "http://127.0.0.1:55555/oauth/google", "account")
        if same_account:
            upgraded = await oauth.complete("owner", flow.state, "code")
            assert upgraded.account_id == "account"
            assert (await service.status("owner"))[0].tasks_write
            assert (
                json.loads(secrets.get("secret-reference-only") or "{}")["refresh_token"]
                == "new-refresh"
            )
        else:
            with pytest.raises(AssistantAccessError, match="google_upgrade_account_mismatch"):
                await oauth.complete("owner", flow.state, "code")
            assert not (await service.status("owner"))[0].calendar_write
            assert (
                json.loads(secrets.get("secret-reference-only") or "{}")["refresh_token"] == "old"
            )
        assert len(await repo.accounts()) == 1
        assert (await repo.calendars("local", "account"))[0].selected
    finally:
        await oauth.close()
        await adapter.close()


async def test_oauth_cancel_stops_inflight_exchange(
    store: tuple[Database, SQLiteAssistantRepository],
    tmp_path: Path,
) -> None:
    db, repo = store
    await add_session(db, "owner", "local")
    entered = asyncio.Event()

    async def handle(_: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Future[None]()
        raise AssertionError("cancelled exchange continued")

    adapter = GoogleCalendarAdapter(transport=httpx.MockTransport(handle))
    client = GoogleClient("desktop-client")
    service = GoogleAccountService(
        repo, AsyncSecretStore(AtomicSecretStore(tmp_path / "tokens")), adapter, client
    )
    oauth = GoogleOAuthCoordinator(repo, adapter, service, client)
    flow = await oauth.begin("owner", "http://127.0.0.1:55555/oauth/google")
    task = asyncio.create_task(oauth.complete("owner", flow.state, "code"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await oauth.cancel("owner", flow.state)
        assert task.cancelled()
        assert len(await repo.accounts()) == 1  # only the fixture's existing account
    finally:
        await oauth.close()
        await adapter.close()


async def test_calendar_selection_is_explicit_and_owner_only(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    _, repo = store
    await repo.add_calendar("local", "account", Calendar("new", "New", "UTC", "reader"))
    items = await repo.calendars("local", "account")
    assert {item.calendar.id: item.selected for item in items} == {"calendar": True, "new": False}
    with pytest.raises(AssistantAccessError):
        await repo.calendars("shared", "account")
    await repo.revoke("local", "account")
    with pytest.raises(AssistantAccessError):
        await repo.calendars("local", "account")


async def test_query_discards_results_after_deselection(
    store: tuple[Database, SQLiteAssistantRepository],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    _, repo = store
    monkeypatch.setattr(repo, "session_owner", AsyncMock(return_value="local"))
    adapter = GoogleCalendarAdapter()
    service = GoogleAccountService(
        repo,
        AsyncSecretStore(AtomicSecretStore(tmp_path / "secrets")),
        adapter,
        GoogleClient("fixture", None),
    )
    monkeypatch.setattr(service, "_access", AsyncMock(return_value="access"))

    async def late(*args: object) -> tuple[CalendarEvent, ...]:
        await repo.select("local", "account", "calendar", False)
        return ()

    monkeypatch.setattr(adapter, "events_between", late)
    try:
        with pytest.raises(AssistantAccessError, match="calendar_not_selected"):
            await service.query(
                "session",
                "account",
                "calendar",
                datetime(2026, 9, 22, tzinfo=UTC),
                datetime(2026, 9, 29, tzinfo=UTC),
            )
    finally:
        await adapter.close()


@pytest.mark.parametrize("lost_reply", [False, True])
async def test_calendar_push_early_sync_replay_restart_and_selection_fence(
    store: tuple[Database, SQLiteAssistantRepository],
    lost_reply: bool,
) -> None:
    from chatwaifu_runtime.persistence.sqlite_calendar_push import SQLiteCalendarPushRepository
    from chatwaifu_runtime.personal_assistant.calendar_push import CalendarPushService, now_ms
    from chatwaifu_runtime.personal_assistant.google_calendar import CalendarWatch

    db, accounts_repo = store
    repository = SQLiteCalendarPushRepository(db)
    notices: list[int] = []
    requests: list[tuple[str, str]] = []
    stops: list[str] = []
    push: CalendarPushService | None = None

    class Accounts:
        async def watch_calendar(
            self,
            session_id: str,
            account_id: str,
            calendar_id: str,
            channel_id: str,
            channel_token: str,
            address: str,
        ) -> CalendarWatch:
            requests.append((channel_id, channel_token))
            assert push is not None
            assert await push.receive(channel_id, channel_token, "resource", "1", "sync")
            if lost_reply:
                raise TimeoutError()
            return CalendarWatch(channel_id, "resource", now_ms() + 86400000)

        async def stop_calendar_watch(
            self, session_id: str, account_id: str, channel_id: str, resource_id: str
        ) -> None:
            stops.append(channel_id)

    adapter = Accounts()
    push = CalendarPushService(
        adapter, repository, "https://calendar.example", lambda: notices.append(1)
    )
    try:
        pending = push.observe("owner", {("account", "calendar")})
        assert pending is not None
        await pending
        assert len(requests) == 1
        identifier, token = requests[0]
        row = await repository.channel(identifier)
        assert row is not None and row.state == "active" and row.resource_id == "resource"
        assert row.token_digest != token
        original_dirty = row.dirty_revision
        notice_count = len(notices)
        assert await push.receive(identifier, token, "resource", "1", "exists")
        assert len(notices) == notice_count
        assert not await push.receive(identifier, "wrong-token", "resource", "2", "exists")
        assert not await push.receive(identifier, token, "other-resource", "2", "exists")
        assert await push.receive(identifier, token, "resource", "3", "exists")
        assert await push.receive(identifier, token, "resource", "2", "exists")
        updated = await repository.channel(identifier)
        assert updated is not None and updated.dirty_revision == original_dirty + 1
        await repository.consume(identifier, original_dirty)
        assert (await push.dirty())[0].consumed_revision == original_dirty
        await push.close()
        push = CalendarPushService(
            adapter, repository, "https://calendar.example", lambda: notices.append(1)
        )
        assert await push.receive(identifier, token, "resource", "4", "exists")
        pending = push.observe("owner", {("account", "calendar")})
        assert pending is not None
        await pending
        assert len(requests) == 1  # A lost watch response cannot create another fresh lease.
        await accounts_repo.select("local", "account", "calendar", False)
        assert not await push.receive(identifier, token, "resource", "5", "exists")
        assert await push.dirty() == []
        pending = push.observe("owner", set())
        assert pending is not None
        await pending
        retired = await repository.channel(identifier)
        assert retired is not None and retired.state == "retired" and stops == [identifier]
    finally:
        await push.close()


async def test_calendar_push_rebinds_callback_origin_and_retires_old_token(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    from chatwaifu_runtime.persistence.sqlite_calendar_push import SQLiteCalendarPushRepository
    from chatwaifu_runtime.personal_assistant.calendar_push import (
        PUSH_PATH,
        CalendarPushService,
        now_ms,
    )
    from chatwaifu_runtime.personal_assistant.google_calendar import CalendarWatch

    db, _accounts = store
    repo = SQLiteCalendarPushRepository(db)
    old = await repo.reserve(
        "local",
        "account",
        "calendar",
        "old-channel",
        hashlib.sha256(b"old-secret").hexdigest(),
        now_ms() + 86400000,
        "https://old.example" + PUSH_PATH,
    )
    assert await repo.activate(
        old, CalendarWatch("old-channel", "old-resource", now_ms() + 86400000)
    )
    created: list[tuple[str, str]] = []
    stopped: list[str] = []

    class Accounts:
        async def watch_calendar(
            self,
            session_id: str,
            account_id: str,
            calendar_id: str,
            channel_id: str,
            channel_token: str,
            address: str,
        ) -> CalendarWatch:
            assert address == "https://new.example" + PUSH_PATH
            created.append((channel_id, channel_token))
            return CalendarWatch(channel_id, "new-resource", now_ms() + 86400000)

        async def stop_calendar_watch(
            self, session_id: str, account_id: str, channel_id: str, resource_id: str
        ) -> None:
            stopped.append(channel_id)

    service = CalendarPushService(Accounts(), repo, "https://new.example/", lambda: None)
    try:
        assert not await service.receive("old-channel", "old-secret", "old-resource", "1", "sync")
        pending = service.observe("owner", {("account", "calendar")})
        assert pending is not None
        await pending
        assert len(created) == 1 and stopped == ["old-channel"]
        identifier, token = created[0]
        assert await service.receive(identifier, token, "new-resource", "1", "sync")
        await db.execute(
            "UPDATE calendar_push_channels SET expires_at=? WHERE channel_id=?",
            (now_ms() - 1, identifier),
        )
        assert not await service.receive(identifier, token, "new-resource", "2", "exists")
    finally:
        await service.close()


async def test_calendar_active_lease_survives_more_than_one_page_of_retired_history(
    store: tuple[Database, SQLiteAssistantRepository],
) -> None:
    import hashlib

    from chatwaifu_runtime.persistence.sqlite_calendar_push import SQLiteCalendarPushRepository
    from chatwaifu_runtime.personal_assistant.calendar_push import now_ms
    from chatwaifu_runtime.personal_assistant.google_calendar import CalendarWatch

    db, _accounts = store
    leases = SQLiteCalendarPushRepository(db)
    channel = await leases.reserve(
        "local",
        "account",
        "calendar",
        "active",
        hashlib.sha256(b"token").hexdigest(),
        now_ms() + 86400000,
        "https://example.test/notifications",
    )
    assert await leases.activate(channel, CalendarWatch("active", "resource", now_ms() + 86400000))
    async with db.transaction() as connection:
        await connection.executemany(
            "INSERT INTO calendar_push_channels(channel_id,owner,account_id,calendar_id,"
            "account_revision,calendar_revision,token_digest,callback_address,"
            "state,expires_at,created_at) "
            "VALUES(?,'local',?,'unused',1,1,'digest','https://example.test','retired',?,?)",
            [
                (f"old-{i}", "account", channel.expires_at, channel.created_at + i + 1)
                for i in range(1100)
            ],
        )
    current = await leases.channels("local")
    assert len(current) == 2
    assert any(c.channel_id == "active" and c.state == "active" for c in current)
