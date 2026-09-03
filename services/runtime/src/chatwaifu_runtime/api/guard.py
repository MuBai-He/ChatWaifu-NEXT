"""Local client security perimeter and authentication guard."""

import asyncio
import hmac
import json
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Final, Literal

from starlette.types import ASGIApp, Receive, Scope, Send

from chatwaifu_runtime.config.settings import Settings

type TicketPurpose = Literal["events", "audio", "admin_events"]


@dataclass(frozen=True, slots=True)
class WebSocketTicketClaims:
    expiry: float
    purpose: TicketPurpose
    origin: str | None = None
    session_id: str | None = None


LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {"127.0.0.1", "localhost", "::1", "[::1]", "testclient", "testserver"}
)
DESKTOP_ORIGINS: Final[frozenset[str]] = frozenset(
    {
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    }
)
EXEMPT_HTTP_PATHS: Final[frozenset[str]] = frozenset(
    {"/v1/runtime/health", "/docs", "/openapi.json", "/redoc"}
)
CHANNEL_EXEMPT_RE: Final[re.Pattern[str]] = re.compile(
    r"^/v1/channel-connections/[^/]+/(messages(/[^/]+(/interrupt)?)?|deliveries/[^/]+/(claim|ack))$"
)


class WebSocketTicketStore:
    """In-memory thread-safe store for short-lived single-use WebSocket tickets with claims."""

    def __init__(self) -> None:
        self._tickets: dict[str, WebSocketTicketClaims] = {}
        self._lock = asyncio.Lock()

    async def create_ticket(
        self,
        purpose: TicketPurpose = "events",
        origin: str | None = None,
        session_id: str | None = None,
        ttl_seconds: float = 30.0,
    ) -> str:
        async with self._lock:
            now = time.monotonic()
            self._evict_expired(now)
            ticket = secrets.token_urlsafe(32)
            normalized_origin = origin.strip().lower() if origin and origin.strip() else None
            normalized_session_id = (
                session_id.strip() if session_id and session_id.strip() else None
            )
            self._tickets[ticket] = WebSocketTicketClaims(
                expiry=now + ttl_seconds,
                purpose=purpose,
                origin=normalized_origin,
                session_id=normalized_session_id,
            )
            return ticket

    async def consume_ticket(
        self,
        ticket: str | None,
        expected_purpose: TicketPurpose | None = None,
        origin: str | None = None,
        session_id: str | None = None,
    ) -> WebSocketTicketClaims | None:
        if not ticket:
            return None
        async with self._lock:
            now = time.monotonic()
            self._evict_expired(now)
            claims = self._tickets.pop(ticket, None)
            if claims is None or claims.expiry < now:
                return None
            if expected_purpose is not None:
                if claims.purpose == "admin_events":
                    if expected_purpose not in ("events", "admin_events"):
                        return None
                elif claims.purpose != expected_purpose:
                    return None
            if claims.origin is not None:
                if not origin:
                    return None
                if claims.origin != origin.strip().lower():
                    return None
            normalized_session_id = (
                session_id.strip() if session_id and session_id.strip() else None
            )
            if claims.purpose in ("events", "audio"):
                if not claims.session_id:
                    return None
                if not normalized_session_id or claims.session_id != normalized_session_id:
                    return None
            elif claims.session_id is not None:
                if claims.session_id != normalized_session_id:
                    return None
            return claims

    def clear(self) -> None:
        self._tickets.clear()

    def _evict_expired(self, now: float) -> None:
        expired = [t for t, claims in self._tickets.items() if claims.expiry < now]
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

    def _cors_headers(self, origin_header: str | None) -> list[tuple[bytes, bytes]]:
        if not origin_header or not self._is_origin_allowed(origin_header):
            return []
        origin_val = origin_header.strip().encode("latin-1")
        return [
            (b"access-control-allow-origin", origin_val),
            (b"vary", b"Origin"),
        ]

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
            path = scope.get("path", "")
            expected_purpose: TicketPurpose | None = None
            if path.endswith("/events"):
                expected_purpose = "events"
            elif path.endswith("/audio/stream"):
                expected_purpose = "audio"

            query_string = scope.get("query_string", b"").decode("latin-1", errors="ignore")
            query_pairs = urllib.parse.parse_qsl(query_string, keep_blank_values=True)
            param_counts: dict[str, int] = {}
            for key, _ in query_pairs:
                param_counts[key] = param_counts.get(key, 0) + 1
            duplicate_params = [key for key, count in param_counts.items() if count > 1]
            if duplicate_params:
                await send(
                    {
                        "type": "websocket.close",
                        "code": 1008,
                        "reason": f"duplicate query parameter: {duplicate_params[0]}",
                    }
                )
                return

            query_params = dict(query_pairs)
            ticket = query_params.get("ticket")
            ws_session_id = query_params.get("session_id")
            bearer_token = self._extract_bearer_token(auth_header)

            authenticated = False
            if ticket:
                claims = await self.ticket_store.consume_ticket(
                    ticket,
                    expected_purpose=expected_purpose,
                    origin=origin_header,
                    session_id=ws_session_id,
                )
                if claims is not None:
                    scope.setdefault("state", {})["chatwaifu_ws_ticket"] = claims
                    authenticated = True
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
                    headers=[
                        (b"www-authenticate", b"Bearer"),
                        *self._cors_headers(origin_header),
                    ],
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
