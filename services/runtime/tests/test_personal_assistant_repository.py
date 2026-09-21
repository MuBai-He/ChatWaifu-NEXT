"""Real SQLite boundaries for calendar synchronization and account revocation."""

import asyncio
import base64
import hashlib
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
from chatwaifu_runtime.personal_assistant.google_calendar import (
    READ_SCOPE,
    Calendar,
    CalendarEvent,
    EventSync,
    GoogleCalendarAdapter,
    OAuthTokens,
)
from chatwaifu_runtime.personal_assistant.oauth import GoogleOAuthCoordinator
from chatwaifu_runtime.personal_assistant.repository import AssistantAccessError


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
                "scope": READ_SCOPE,
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
        assert query["code_challenge"] == [challenge]
        assert query["code_challenge_method"] == ["S256"]
        with pytest.raises(AssistantAccessError, match="oauth_flow_invalid"):
            await oauth.complete("owner1", flow.state, "code")
        assert len(exchanges) == 1
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
