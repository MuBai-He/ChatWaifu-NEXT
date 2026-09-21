"""Single-use desktop OAuth coordination; HTTP transport admission is separate."""

import asyncio
import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

from chatwaifu_runtime.personal_assistant.accounts import (
    AccountStatus,
    GoogleAccountService,
    GoogleClient,
)
from chatwaifu_runtime.personal_assistant.google_calendar import READ_SCOPE, GoogleCalendarAdapter
from chatwaifu_runtime.personal_assistant.repository import (
    AssistantAccessError,
    AssistantRepository,
)


@dataclass(frozen=True, slots=True)
class AuthorizationStart:
    state: str
    authorization_url: str = field(repr=False)
    expires_in: int = 300


@dataclass(frozen=True, slots=True)
class _Pending:
    session_id: str
    redirect_uri: str
    verifier: str = field(repr=False)
    expires_at: float


class GoogleOAuthCoordinator:
    def __init__(
        self,
        repository: AssistantRepository,
        adapter: GoogleCalendarAdapter,
        accounts: GoogleAccountService,
        client: GoogleClient,
    ) -> None:
        self._repository = repository
        self._adapter = adapter
        self._accounts = accounts
        self._client = client
        self._pending: dict[str, _Pending] = {}
        self._active: dict[str, tuple[str, asyncio.Task[object]]] = {}

    def clear(self) -> None:
        self._pending.clear()
        for _, task in self._active.values():
            task.cancel()

    async def close(self) -> None:
        self.clear()
        if self._active:
            await asyncio.gather(
                *(task for _, task in self._active.values()), return_exceptions=True
            )

    def _expire(self) -> None:
        now = time.monotonic()
        self._pending = {
            key: value for key, value in self._pending.items() if value.expires_at > now
        }

    async def begin(self, session_id: str, redirect_uri: str) -> AuthorizationStart:
        await self._repository.session_owner(session_id)
        # Match the planned native listener exactly; no arbitrary callback target,
        # credentials, DNS aliases, path traversal, fragments or query strings.
        try:
            parsed = urlsplit(redirect_uri)
            port = parsed.port
        except ValueError:
            raise AssistantAccessError("invalid_oauth_callback") from None
        if (
            not port
            or not 1024 <= port <= 65535
            or redirect_uri != f"http://127.0.0.1:{port}/oauth/google"
        ):
            raise AssistantAccessError("invalid_oauth_callback")
        self._expire()
        if len(self._pending) + len(self._active) >= 16:
            raise AssistantAccessError("oauth_flow_limit")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(
            b"="
        )
        self._pending[state] = _Pending(session_id, redirect_uri, verifier, time.monotonic() + 300)
        return AuthorizationStart(
            state,
            "https://accounts.google.com/o/oauth2/v2/auth?"
            + urlencode(
                {
                    "client_id": self._client.client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": READ_SCOPE,
                    "access_type": "offline",
                    "prompt": "consent",
                    "state": state,
                    "code_challenge": challenge.decode(),
                    "code_challenge_method": "S256",
                }
            ),
        )

    async def cancel(self, session_id: str, state: str) -> None:
        await self._repository.session_owner(session_id)
        pending = self._pending.get(state)
        if pending is not None and pending.session_id == session_id:
            self._pending.pop(state)
        active = self._active.get(state)
        if active is not None and active[0] == session_id:
            active[1].cancel()
            await asyncio.gather(active[1], return_exceptions=True)

    async def complete(self, session_id: str, state: str, code: str) -> AccountStatus:
        await self._repository.session_owner(session_id)
        self._expire()
        pending = self._pending.get(state)
        if pending is None or pending.session_id != session_id:
            raise AssistantAccessError("oauth_flow_invalid_or_expired")
        if not code or len(code) > 4096:
            raise AssistantAccessError("invalid_authorization_code")
        # Consume before awaiting Google. A replay cannot race an exchange.
        # Unknown exchange results require a fresh user authorization, not retry.
        self._pending.pop(state)
        task = asyncio.current_task()
        assert task is not None
        self._active[state] = (session_id, task)
        try:
            tokens = await self._adapter.exchange_code(
                client_id=self._client.client_id,
                client_secret=self._client.client_secret,
                code=code,
                verifier=pending.verifier,
                redirect_uri=pending.redirect_uri,
            )
            return await self._accounts.connect_authorized(session_id, tokens)
        finally:
            self._active.pop(state, None)
