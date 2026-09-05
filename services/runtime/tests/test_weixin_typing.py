"""Typing cleanup, supersession, timeout and durable-state race checks."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import WeixinCredentials
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.typing import (
    WeixinTypingController,
    WeixinTypingTarget,
)


def _target(message_id: str = "first") -> WeixinTypingTarget:
    return WeixinTypingTarget(
        external_message_id=message_id,
        session_id=uuid4(),
        turn_id=uuid4(),
        generation_id=uuid4(),
        credentials=WeixinCredentials(
            "bot-secret", "bot", "owner", "https://api.weixin.qq.com/", "gateway-secret"
        ),
        recipient_user_id="owner",
        context_token=message_id,
    )


class _Transport:
    def __init__(self) -> None:
        self.calls: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.hang_ticket = False
        self.hang_start = False
        self.missing_ticket = False

    async def get_typing_ticket(
        self, credentials: WeixinCredentials, *, recipient_user_id: str, context_token: str
    ) -> str | None:
        self.entered.set()
        if self.hang_ticket:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()
        return None if self.missing_ticket else context_token

    async def send_typing(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        typing_ticket: str,
        active: bool,
    ) -> None:
        await self.calls.put((typing_ticket, active))
        if active and self.hang_start:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()


async def _active(message_id: str) -> bool:
    return True


async def _next(transport: _Transport) -> tuple[str, bool]:
    return await asyncio.wait_for(transport.calls.get(), 1)


@pytest.mark.asyncio
async def test_typing_refresh_then_terminal_stop() -> None:
    transport = _Transport()
    controller = WeixinTypingController(transport, _active)
    try:
        controller.start(_target())
        assert await _next(transport) == ("first", True)
        controller.refresh()
        assert await _next(transport) == ("first", True)
        controller.stop("first")
        assert await _next(transport) == ("first", False)
    finally:
        await controller.close()
    assert transport.calls.empty()


@pytest.mark.asyncio
async def test_replacement_serializes_old_off_and_ignores_stale_terminal() -> None:
    transport = _Transport()
    controller = WeixinTypingController(transport, _active)
    try:
        controller.start(_target())
        assert await _next(transport) == ("first", True)
        controller.start(_target("second"))
        controller.stop("first")
        assert await _next(transport) == ("first", False)
        assert await _next(transport) == ("second", True)
    finally:
        await controller.close()
    assert await _next(transport) == ("second", False)
    assert transport.calls.empty()


@pytest.mark.asyncio
async def test_stop_during_ticket_acquisition_never_sends_stale_on() -> None:
    transport = _Transport()
    transport.hang_ticket = True
    controller = WeixinTypingController(transport, _active)
    controller.start(_target())
    await asyncio.wait_for(transport.entered.wait(), 1)
    controller.stop("first")
    await asyncio.wait_for(transport.cancelled.wait(), 1)
    await controller.close()
    assert transport.calls.empty()


@pytest.mark.asyncio
async def test_stop_during_inflight_on_still_attempts_off() -> None:
    transport = _Transport()
    transport.hang_start = True
    controller = WeixinTypingController(transport, _active)
    controller.start(_target())
    assert await _next(transport) == ("first", True)
    await controller.close()
    assert transport.cancelled.is_set()
    assert await _next(transport) == ("first", False)


@pytest.mark.asyncio
async def test_ticket_timeout_is_bounded_and_next_turn_can_type() -> None:
    transport = _Transport()
    transport.hang_ticket = True
    controller = WeixinTypingController(transport, _active, request_timeout=0.02)
    try:
        controller.start(_target())
        await asyncio.wait_for(transport.cancelled.wait(), 1)
        transport.hang_ticket = False
        controller.start(_target("next"))
        assert await _next(transport) == ("next", True)
    finally:
        await controller.close()
    assert await _next(transport) == ("next", False)


@pytest.mark.asyncio
async def test_durable_completion_before_ticket_returns_prevents_late_start() -> None:
    transport = _Transport()
    reads = 0

    async def active(message_id: str) -> bool:
        nonlocal reads
        reads += 1
        return reads == 1

    controller = WeixinTypingController(transport, active)
    controller.start(_target())
    await asyncio.wait_for(transport.entered.wait(), 1)
    await controller.close()
    assert transport.calls.empty()


@pytest.mark.asyncio
async def test_missing_ticket_and_disabled_target_make_no_typing_calls() -> None:
    transport = _Transport()
    transport.missing_ticket = True
    controller = WeixinTypingController(transport, _active)
    controller.start(_target())
    await asyncio.wait_for(transport.entered.wait(), 1)
    await controller.close()
    controller.start(replace(_target(), external_message_id="after-close"))
    assert transport.calls.empty()


@pytest.mark.asyncio
async def test_durable_terminal_without_event_is_cleared_on_refresh() -> None:
    transport = _Transport()
    active_now = True

    async def active(message_id: str) -> bool:
        return active_now

    controller = WeixinTypingController(transport, active)
    try:
        controller.start(_target())
        assert await _next(transport) == ("first", True)
        active_now = False
        controller.refresh()
        assert await _next(transport) == ("first", False)
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_typing_lifetime_is_bounded_and_disconnect_reset_clears_it() -> None:
    transport = _Transport()
    controller = WeixinTypingController(transport, _active, lifetime_seconds=0.02)
    try:
        controller.start(_target())
        assert await _next(transport) == ("first", True)
        assert await _next(transport) == ("first", False)
        controller.start(_target("after-reset"))
        assert await _next(transport) == ("after-reset", True)
        controller.reset()
        assert await _next(transport) == ("after-reset", False)
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_typing_logs_and_target_repr_do_not_disclose_private_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        "INFO", logger="chatwaifu_runtime.external_channels.adapters.weixin_ilink.typing"
    )
    transport = _Transport()
    controller = WeixinTypingController(transport, _active)
    target = _target("private-context")
    controller.start(target)
    assert await _next(transport) == ("private-context", True)
    await controller.close()
    assert await _next(transport) == ("private-context", False)
    for secret in ("bot-secret", "gateway-secret", "owner", "private-context"):
        assert secret not in caplog.text
    assert "bot-secret" not in repr(target)


@pytest.mark.asyncio
async def test_lifetime_timeout_does_not_renew_when_clock_lags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate a coarse clock still reporting a time before the deadline when
    # the event-loop timeout fires. Do not patch the event loop's own clock.
    monkeypatch.setattr(
        "chatwaifu_runtime.external_channels.adapters.weixin_ilink.typing.monotonic",
        lambda: 100.0,
    )
    transport = _Transport()
    controller = WeixinTypingController(transport, _active, lifetime_seconds=0.02)
    try:
        controller.start(_target())
        assert await _next(transport) == ("first", True)
        assert await _next(transport) == ("first", False)
    finally:
        await controller.close()
