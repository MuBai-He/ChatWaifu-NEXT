"""Bounded, ephemeral owner-chat typing; never participates in delivery ACKs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol, TypeVar
from uuid import UUID

from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import WeixinCredentials

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class WeixinTypingTransport(Protocol):
    async def get_typing_ticket(
        self, credentials: WeixinCredentials, *, recipient_user_id: str, context_token: str
    ) -> str | None: ...

    async def send_typing(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        typing_ticket: str,
        active: bool,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class WeixinTypingTarget:
    external_message_id: str
    session_id: UUID
    turn_id: UUID
    generation_id: UUID
    credentials: WeixinCredentials = field(repr=False)
    recipient_user_id: str = field(repr=False)
    context_token: str = field(repr=False)


@dataclass(slots=True)
class _Session:
    target: WeixinTypingTarget
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    refresh: asyncio.Event = field(default_factory=asyncio.Event)


class _Superseded(Exception):
    pass


class WeixinTypingController:
    """One worker and one replaceable target per owner connection, with no queued history.

    Ticket acquisition and status requests are cancellable and bounded. Old OFF is
    attempted before new ON; terminal events only clear their own message target.
    Durable state is checked before every refresh, covering missed event notifications.
    """

    def __init__(
        self,
        transport: WeixinTypingTransport,
        is_active: Callable[[str], Awaitable[bool]],
        *,
        refresh_seconds: float = 5,
        request_timeout: float = 2,
        lifetime_seconds: float = 120,
    ) -> None:
        self._transport = transport
        self._is_active = is_active
        self._refresh_seconds = refresh_seconds
        self._request_timeout = request_timeout
        self._lifetime_seconds = lifetime_seconds
        self._desired: _Session | None = None
        self._current: _Session | None = None
        self._wake = asyncio.Event()
        self._closed = False
        self._task = asyncio.create_task(self._run(), name="weixin-typing")

    def start(self, target: WeixinTypingTarget) -> None:
        if self._closed:
            return
        if self._desired and self._desired.target.external_message_id == target.external_message_id:
            return
        if self._current:
            self._current.cancelled.set()
        self._desired = _Session(target)
        self._wake.set()

    def stop(self, external_message_id: str) -> None:
        if self._desired and self._desired.target.external_message_id == external_message_id:
            self._desired.cancelled.set()
            self._desired = None
        if self._current and self._current.target.external_message_id == external_message_id:
            self._current.cancelled.set()
        self._wake.set()

    def reset(self) -> None:
        if self._desired:
            self._desired.cancelled.set()
        self._desired = None
        if self._current:
            self._current.cancelled.set()
        self._wake.set()

    def refresh(self) -> None:
        if self._current:
            self._current.refresh.set()

    async def close(self) -> None:
        self._closed = True
        self._desired = None
        if self._current:
            self._current.cancelled.set()
        self._wake.set()
        await self._task

    async def _run(self) -> None:
        while not self._closed:
            self._wake.clear()
            session = self._desired
            if session is None:
                await self._wake.wait()
                continue
            self._current = session
            try:
                await self._show(session)
            except _Superseded:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log(session.target, "unavailable")
            finally:
                if self._desired is session:
                    self._desired = None
                self._current = None

    async def _interruptible(self, session: _Session, operation: Awaitable[_T]) -> _T:
        work = asyncio.ensure_future(operation)
        cancelled = asyncio.create_task(session.cancelled.wait())
        try:
            done, _ = await asyncio.wait(
                (work, cancelled),
                timeout=self._request_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if session.cancelled.is_set():
                raise _Superseded
            if work not in done:
                raise TimeoutError
            return work.result()
        finally:
            work.cancel()
            cancelled.cancel()
            await asyncio.gather(work, cancelled, return_exceptions=True)

    async def _show(self, session: _Session) -> None:
        target = session.target
        ticket: str | None = None
        attempted = False
        deadline = monotonic() + self._lifetime_seconds
        try:
            if not await self._interruptible(session, self._is_active(target.external_message_id)):
                return
            ticket = await self._interruptible(
                session,
                self._transport.get_typing_ticket(
                    target.credentials,
                    recipient_user_id=target.recipient_user_id,
                    context_token=target.context_token,
                ),
            )
            if not ticket:
                return
            while monotonic() < deadline:
                session.refresh.clear()
                if not await self._interruptible(
                    session, self._is_active(target.external_message_id)
                ):
                    break
                attempted = True
                await self._interruptible(
                    session,
                    self._transport.send_typing(
                        target.credentials,
                        recipient_user_id=target.recipient_user_id,
                        typing_ticket=ticket,
                        active=True,
                    ),
                )
                self._log(target, "on")
                # A timer renews ephemeral provider state; events interrupt the wait immediately.
                refresh = asyncio.create_task(session.refresh.wait())
                cancelled = asyncio.create_task(session.cancelled.wait())
                try:
                    remaining = max(0, deadline - monotonic())
                    done, _ = await asyncio.wait(
                        (refresh, cancelled),
                        timeout=min(self._refresh_seconds, remaining),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if session.cancelled.is_set():
                        raise _Superseded
                    # Timer resolution may wake us before monotonic reaches the deadline.
                    # A lifetime-limited timeout must end typing, not renew it once more.
                    if not done and remaining <= self._refresh_seconds:
                        break
                finally:
                    refresh.cancel()
                    cancelled.cancel()
                    await asyncio.gather(refresh, cancelled, return_exceptions=True)
        finally:
            if attempted and ticket:
                try:
                    async with asyncio.timeout(self._request_timeout):
                        await self._transport.send_typing(
                            target.credentials,
                            recipient_user_id=target.recipient_user_id,
                            typing_ticket=ticket,
                            active=False,
                        )
                    self._log(target, "off")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._log(target, "off_failed")

    @staticmethod
    def _log(target: WeixinTypingTarget, stage: str) -> None:
        logger.info(
            json.dumps(
                {
                    "event": "weixin.typing",
                    "stage": stage,
                    "observed_at": datetime.now(UTC).isoformat(),
                    "session_id": str(target.session_id),
                    "turn_id": str(target.turn_id),
                    "generation_id": str(target.generation_id),
                }
            )
        )
