"""Bounded OneBot 11 WebSocket transport; no Runtime domain or SQL dependencies."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

from chatwaifu_protocol.base import JsonObject, JsonValue
from websockets.asyncio.client import ClientConnection, connect

_SOCKET_LOGGER = logging.getLogger("qq.websocket")
_SOCKET_LOGGER.disabled = (
    True  # WebSocket debug frames contain credentials and private message content.
)


class NapCatError(RuntimeError):
    """Sanitized error; raw provider messages can contain tokens or user content."""


class NapCatRejected(NapCatError):
    """The provider explicitly rejected an operation."""


class NapCatUncertain(NapCatError):
    """An API request may have executed; never retry a send automatically."""


def validate_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"ws", "wss"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use a ws/wss endpoint without credentials, query, or fragment")
    if parsed.scheme == "ws" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote connections require wss; use a local tunnel for plain ws")
    return endpoint


class NapCatClient:
    def __init__(self, endpoint: str, token: str) -> None:
        self._endpoint = validate_endpoint(endpoint)
        self._token = token
        self._socket: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[JsonObject]] = {}
        self._events: asyncio.Queue[JsonObject | None] = asyncio.Queue(maxsize=64)
        self._account: str | None = None

    def bind_account(self, account: str) -> None:
        self._account = account

    async def open(self) -> None:
        self._socket = await connect(
            self._endpoint,
            additional_headers={"Authorization": f"Bearer {self._token}"},
            open_timeout=8,
            close_timeout=2,
            max_size=1_048_576,
            max_queue=16,
            ping_interval=20,
            ping_timeout=20,
            logger=_SOCKET_LOGGER,
        )
        self._reader = asyncio.create_task(self._read(), name="qq-onebot-reader")

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._socket:
            await self._socket.close()
        if self._reader:
            await asyncio.gather(self._reader, return_exceptions=True)
        self._socket = None
        self._fail_pending()

    def _fail_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(NapCatUncertain("QQ connection lost during request"))

    async def _read(self) -> None:
        try:
            assert self._socket is not None
            async for raw in self._socket:
                try:
                    payload: object = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                event = cast(JsonObject, payload)
                echo = event.get("echo")
                if isinstance(echo, str) and echo in self._pending:
                    future = self._pending[echo]
                    if not future.done():
                        future.set_result(event)
                elif event.get("post_type") == "message":
                    # Overflow closes the connection instead of dropping an admitted event silently.
                    self._events.put_nowait(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._fail_pending()
            if not self._events.full():
                self._events.put_nowait(None)
            elif self._socket:
                await self._socket.close()

    async def event(self) -> JsonObject:
        if self._reader and self._reader.done() and self._events.empty():
            raise NapCatError("QQ connection closed")
        value = await self._events.get()
        if value is None:
            raise NapCatError("QQ connection closed")
        return value

    async def call(self, action: str, params: JsonObject) -> JsonObject:
        if self._socket is None or (self._reader and self._reader.done()):
            raise NapCatRejected("QQ connection is unavailable before request")
        if len(self._pending) >= 32:
            raise NapCatRejected("QQ request capacity exceeded")
        echo = uuid4().hex
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        try:
            async with asyncio.timeout(10):
                await self._socket.send(
                    json.dumps({"action": action, "params": params, "echo": echo})
                )
                response = await future
            if response.get("status") != "ok" or response.get("retcode") != 0:
                raise NapCatRejected("QQ rejected the requested operation")
            data = response.get("data")
            return cast(JsonObject, data) if isinstance(data, dict) else {}
        except asyncio.CancelledError:
            raise
        except NapCatError:
            raise
        except Exception as error:
            raise NapCatUncertain("QQ request result is unknown") from error
        finally:
            self._pending.pop(echo, None)
            if not future.done():
                future.cancel()

    async def send(
        self,
        recipient: str,
        segments: list[JsonObject],
        *,
        before_send: Callable[[], Awaitable[bool]] | None = None,
    ) -> str:
        if self._account is not None:
            login = await self.call("get_login_info", {})
            if str(login.get("user_id")) != self._account:
                raise NapCatRejected("QQ account changed before send")
        if before_send is not None and not await before_send():
            raise NapCatRejected("QQ delivery cancelled before send")
        response = await self.call(
            "send_private_msg",
            {"user_id": int(recipient), "message": cast(list[JsonValue], segments)},
        )
        message_id = response.get("message_id")
        if type(message_id) not in {str, int} or not re.fullmatch(
            r"-?[0-9]{1,20}", str(message_id)
        ):
            raise NapCatUncertain("QQ returned no stable message identifier")
        return str(message_id)
