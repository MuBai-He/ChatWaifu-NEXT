"""Local client security perimeter and authentication guard."""

import asyncio
import hmac
import json
import re
import secrets
import time
import urllib.parse
from typing import Final

from starlette.types import ASGIApp, Receive, Scope, Send

from chatwaifu_runtime.config.settings import Settings

LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {"127.0.0.1", "localhost", "::1", "[::1]", "testclient", "testserver"}
)
DESKTOP_ORIGINS: Final[frozenset[str]] = frozenset(
    {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}
)
EXEMPT_HTTP_PATHS: Final[frozenset[str]] = frozenset(
    {"/v1/runtime/health", "/docs", "/openapi.json", "/redoc"}
)
CHANNEL_EXEMPT_RE: Final[re.Pattern[str]] = re.compile(
    r"^/v1/channel-connections/[^/]+/(messages(/[^/]+(/interrupt)?)?|deliveries/[^/]+/(claim|ack))$"
)


class WebSocketTicketStore:
    """In-memory thread-safe store for short-lived single-use WebSocket tickets."""

    def __init__(self) -> None:
        self._tickets: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def create_ticket(self, ttl_seconds: float = 30.0) -> str:
        async with self._lock:
            now = time.monotonic()
            self._evict_expired(now)
            ticket = secrets.token_urlsafe(32)
            self._tickets[ticket] = now + ttl_seconds
            return ticket

    async def consume_ticket(self, ticket: str | None) -> bool:
        if not ticket:
            return False
        async with self._lock:
            now = time.monotonic()
            self._evict_expired(now)
            expiry = self._tickets.pop(ticket, None)
            if expiry is not None and expiry >= now:
                return True
            return False

    def clear(self) -> None:
        self._tickets.clear()

    def _evict_expired(self, now: float) -> None:
        expired = [t for t, exp in self._tickets.items() if exp < now]
        for t in expired:
            self._tickets.pop(t, None)


class LocalClientGuardMiddleware:
    """Enforce Host allowlist, Origin allowlist, and local capability token."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        capability_token: str,
        ticket_store: WebSocketTicketStore,
    ) -> None:
        self.app = app
        self.settings = settings
        self.capability_token = capability_token
        self.ticket_store = ticket_store
        self._allowed_hosts = self._compute_allowed_hosts()
        self._allowed_origins = self._compute_allowed_origins()

    def _compute_allowed_hosts(self) -> set[str]:
        hosts = set(LOOPBACK_HOSTS)
        if self.settings.runtime.host:
            hosts.add(self.settings.runtime.host.lower())
        for host in self.settings.security.allowed_hosts:
            hosts.add(host.lower())
        return hosts

    def _compute_allowed_origins(self) -> set[str]:
        origins = set(DESKTOP_ORIGINS)
        if self.settings.runtime.web_origin:
            origins.add(self.settings.runtime.web_origin.lower())
        for origin in self.settings.security.allowed_origins:
            origins.add(origin.lower())
        return origins

    def _is_host_allowed(self, host_header: str | None) -> bool:
        if not host_header:
            return False
        raw = host_header.strip()
        if raw.startswith("["):
            end_idx = raw.find("]")
            if end_idx != -1:
                hostname = raw[: end_idx + 1].lower()
            else:
                hostname = raw.split(":", 1)[0].lower()
        else:
            hostname = raw.split(":", 1)[0].lower()
        if hostname in self._allowed_hosts:
            return True
        if hostname.strip("[]") in self._allowed_hosts:
            return True
        return False

    def _is_origin_allowed(self, origin_header: str | None) -> bool:
        if origin_header is None:
            # Permitted for native desktop host, curl, test client, non-browser IPC
            return True
        raw = origin_header.strip()
        if not raw:
            return True
        if raw.lower() == "null":
            # Untrusted / sandboxed browser origin - reject
            return False
        raw_lower = raw.lower()
        if raw_lower in self._allowed_origins:
            return True
        try:
            parsed = urllib.parse.urlparse(raw_lower)
            if parsed.scheme in ("http", "https", "tauri"):
                host = parsed.hostname
                if host and (
                    host in self._allowed_hosts
                    or host in LOOPBACK_HOSTS
                    or host == "tauri.localhost"
                ):
                    return True
        except Exception:
            return False
        return False

    def _is_token_valid(self, token: str | None) -> bool:
        if not token:
            return False
        candidate = token.strip()
        if self.capability_token and hmac.compare_digest(candidate, self.capability_token):
            return True
        admin_token = self.settings.security.admin_token
        if admin_token and hmac.compare_digest(candidate, admin_token.get_secret_value()):
            return True
        return False

    @staticmethod
    def _extract_bearer_token(auth_header: str | None) -> str | None:
        if not auth_header:
            return None
        scheme, sep, token = auth_header.partition(" ")
        if sep == " " and scheme.lower() == "bearer" and token.strip():
            return token.strip()
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        host_header = self._get_header(headers, b"host")
        origin_header = self._get_header(headers, b"origin")
        auth_header = self._get_header(headers, b"authorization")

        # 1. Host header validation (DNS rebinding protection)
        if not self._is_host_allowed(host_header):
            if scope["type"] == "websocket":
                await send(
                    {"type": "websocket.close", "code": 1008, "reason": "Forbidden: invalid host"}
                )
                return
            await self._send_json(send, 403, {"detail": "Forbidden: invalid host"})
            return

        # 2. Origin header validation (Cross-Origin protection)
        if not self._is_origin_allowed(origin_header):
            if scope["type"] == "websocket":
                await send(
                    {
                        "type": "websocket.close",
                        "code": 1008,
                        "reason": "Forbidden: untrusted origin",
                    }
                )
                return
            await self._send_json(send, 403, {"detail": "Forbidden: untrusted origin"})
            return

        # 3. CORS Preflight OPTIONS pass-through
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # 4. Authentication Check
        if not self.settings.security.auth_enabled:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            query_string = scope.get("query_string", b"").decode("latin-1", errors="ignore")
            query_params = urllib.parse.parse_qs(query_string)
            ticket = query_params.get("ticket", [None])[0]
            bearer_token = self._extract_bearer_token(auth_header)

            authenticated = False
            if ticket:
                authenticated = await self.ticket_store.consume_ticket(ticket)
            elif bearer_token:
                authenticated = self._is_token_valid(bearer_token)

            if not authenticated:
                await send(
                    {
                        "type": "websocket.close",
                        "code": 1008,
                        "reason": "Unauthorized: invalid or missing ticket",
                    }
                )
                return
            await self.app(scope, receive, send)
            return

        if scope["type"] == "http":
            path = scope.get("path", "")
            method = scope.get("method", "GET")

            if path in EXEMPT_HTTP_PATHS:
                await self.app(scope, receive, send)
                return

            if method == "GET" and path.startswith("/v1/audio/") and path.endswith(".wav"):
                await self.app(scope, receive, send)
                return

            if CHANNEL_EXEMPT_RE.match(path):
                await self.app(scope, receive, send)
                return

            bearer_token = self._extract_bearer_token(auth_header)
            if not self._is_token_valid(bearer_token):
                await self._send_json(
                    send,
                    401,
                    {"detail": "Unauthorized: invalid or missing token"},
                    headers=[(b"www-authenticate", b"Bearer")],
                )
                return

            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _get_header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
        target = name.lower()
        for k, v in headers:
            if k.lower() == target:
                return v.decode("latin-1", errors="ignore")
        return None

    @staticmethod
    async def _send_json(
        send: Send,
        status: int,
        body: dict[str, object],
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        payload = json.dumps(body).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("latin-1")),
            *(headers or []),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": response_headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": payload,
            }
        )
