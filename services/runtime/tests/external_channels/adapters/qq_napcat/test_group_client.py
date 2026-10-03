"""Actual loopback OneBot peers exercise group RPC and admission fences."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

import pytest
from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_runtime.external_channels.adapters.qq_napcat import client as client_module
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import (
    NapCatClient,
    NapCatError,
    NapCatRejected,
    NapCatUncertain,
)
from chatwaifu_runtime.external_channels.adapters.qq_napcat.groups import normalize_group_notice
from websockets.asyncio.server import ServerConnection, serve

ACCOUNT = "10001"
GROUP = "20001"
Handler = Callable[[ServerConnection], Awaitable[None]]


@asynccontextmanager
async def connected(peer: Handler) -> AsyncGenerator[NapCatClient]:
    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = NapCatClient(f"ws://127.0.0.1:{port}/onebot", "fixture-token")
        await client.open()
        client.bind_account(ACCOUNT)
        try:
            yield client
        finally:
            await client.close()


async def request(socket: ServerConnection) -> JsonObject:
    return cast(JsonObject, json.loads(await socket.recv()))


async def reply(socket: ServerConnection, received: JsonObject, data: JsonValue) -> None:
    await socket.send(
        json.dumps({"status": "ok", "retcode": 0, "echo": received["echo"], "data": data})
    )


def members(count: int = 2) -> list[JsonValue]:
    return [
        {"group_id": GROUP, "user_id": ACCOUNT},
        *[
            {"group_id": GROUP, "user_id": str(10002 + index), "nickname": "同名"}
            for index in range(count)
        ],
    ]


def membership_notice() -> JsonObject:
    return {
        "post_type": "notice",
        "notice_type": "group_increase",
        "sub_type": "approve",
        "self_id": 10001,
        "group_id": 20001,
        "user_id": 10004,
        "operator_id": 0,
    }


@pytest.mark.parametrize("count", [2, 32])
async def test_group_member_array_excludes_self_and_has_no_freshness_claim(count: int) -> None:
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        assert socket.request is not None
        assert socket.request.headers["Authorization"] == "Bearer fixture-token"
        for _ in range(3):
            received = await request(socket)
            actions.append(str(received["action"]))
            if received["action"] == "get_group_member_list":
                assert received["params"] == {"group_id": GROUP, "no_cache": True}
                await reply(socket, received, members(count))
            else:
                assert received["action"] == "get_login_info"
                await reply(socket, received, {"user_id": 10001})
        await socket.wait_closed()

    async with connected(peer) as client:
        audience = await asyncio.wait_for(client.get_group_member_list(GROUP), 2)
        assert audience.account_key == ACCOUNT and audience.group_id == GROUP
        assert audience.member_ids == tuple(str(10002 + i) for i in range(count))
        assert ACCOUNT not in audience.member_ids and not hasattr(audience, "fresh")
        assert client._pending == {}
    assert actions == ["get_login_info", "get_group_member_list", "get_login_info"]


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        "array",
        True,
        [None],
        [True],
        [],
        members(1),
        members(33),
        [{"group_id": GROUP, "user_id": "10002"}, {"group_id": GROUP, "user_id": "10003"}],
        [*members(), {"group_id": GROUP, "user_id": "10002"}],
        [*members(), {"group_id": "20002", "user_id": "10004"}],
        [*members(), {"group_id": GROUP, "user_id": True}],
        [*members(), {"group_id": GROUP, "user_id": 0}],
        [*members(), {"group_id": GROUP, "user_id": "010004"}],
    ],
)
async def test_group_member_malformed_duplicate_foreign_or_large_array_is_rejected(
    data: JsonValue,
) -> None:
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        await reply(socket, login, {"user_id": ACCOUNT})
        listing = await request(socket)
        actions.append(str(listing["action"]))
        await reply(socket, listing, data)
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError):
            await asyncio.wait_for(client.get_group_member_list(GROUP), 2)
        assert client._pending == {}
    assert actions == ["get_login_info", "get_group_member_list"]


async def test_array_and_dict_rpc_keep_echo_correlation_and_interleaved_events() -> None:
    async def peer(socket: ServerConnection) -> None:
        first, second = await request(socket), await request(socket)
        await socket.send(json.dumps({"post_type": "message", "message_id": 90001}))
        notice = membership_notice()
        notice["nickname"] = "未经授权昵称"
        await socket.send(json.dumps(notice))
        for received in (second, first):
            await reply(
                socket,
                received,
                members() if received["action"] == "get_group_member_list" else {"online": True},
            )
        await socket.wait_closed()

    async with connected(peer) as client:
        array, status = await asyncio.wait_for(
            asyncio.gather(
                client.call_array("get_group_member_list", {"group_id": GROUP}),
                client.call("get_status", {}),
            ),
            2,
        )
        assert len(array) == 3 and status == {"online": True}
        assert (await asyncio.wait_for(client.event(), 2))["post_type"] == "message"
        observed = await asyncio.wait_for(client.event(), 2)
        assert "nickname" not in observed
        assert normalize_group_notice(observed, account=ACCOUNT, group_id=GROUP) is not None


async def test_legacy_dict_call_keeps_array_fallback_compatibility() -> None:
    async def peer(socket: ServerConnection) -> None:
        await reply(socket, await request(socket), members())
        await socket.wait_closed()

    async with connected(peer) as client:
        assert await client.call("get_group_member_list", {}) == {}


async def test_array_pending_capacity_is_shared_with_dict_calls() -> None:
    arrived = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        for _ in range(32):
            await request(socket)
        arrived.set()
        await socket.wait_closed()

    async with connected(peer) as client:
        tasks: list[asyncio.Task[object]] = [
            asyncio.create_task(client.call_array("get_group_member_list", {})) for _ in range(16)
        ]
        tasks.extend(asyncio.create_task(client.call("get_status", {})) for _ in range(16))
        try:
            await asyncio.wait_for(arrived.wait(), 2)
            assert len(client._pending) == 32
            with pytest.raises(NapCatRejected, match="capacity"):
                await client.call_array("get_group_member_list", {})
        finally:
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(value, asyncio.CancelledError) for value in results)
        assert client._pending == {}


async def test_cancelled_member_preflight_does_not_lookup_and_survives_late_reply() -> None:
    arrived, released = asyncio.Event(), asyncio.Event()
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        arrived.set()
        await released.wait()
        await reply(socket, login, {"user_id": ACCOUNT})
        status = await request(socket)
        actions.append(str(status["action"]))
        await reply(socket, status, {"online": True})
        await socket.wait_closed()

    async with connected(peer) as client:
        task = asyncio.create_task(client.get_group_member_list(GROUP))
        await asyncio.wait_for(arrived.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._pending == {}
        released.set()
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
    assert actions == ["get_login_info", "get_status"]


@pytest.mark.parametrize("close", [False, True])
async def test_array_deadline_or_disconnect_is_uncertain_and_never_retried(
    close: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_RPC_TIMEOUT_SECONDS", 0.05)
    actions: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        actions.append(await request(socket))
        if close:
            await socket.close()
        else:
            await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatUncertain):
            await asyncio.wait_for(client.call_array("get_group_member_list", {}), 2)
        assert client._pending == {}
    assert len(actions) == 1


async def test_member_observation_has_one_total_deadline_and_cleans_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_GROUP_TIMEOUT_SECONDS", 0.2)
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        await reply(socket, login, {"user_id": ACCOUNT})
        listing = await request(socket)
        actions.append(str(listing["action"]))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="observation"):
            await asyncio.wait_for(client.get_group_member_list(GROUP), 2)
        assert client._pending == {}
    assert actions == ["get_login_info", "get_group_member_list"]


@pytest.mark.parametrize("phase", ["preflight", "postflight"])
async def test_member_observation_rejects_provider_account_switch(phase: str) -> None:
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login_count = 0
        async for raw in socket:
            received = cast(JsonObject, json.loads(raw))
            actions.append(str(received["action"]))
            if received["action"] == "get_login_info":
                login_count += 1
                switched = phase == "preflight" or login_count == 2
                await reply(socket, received, {"user_id": "10009" if switched else ACCOUNT})
            else:
                await reply(socket, received, members())

    async with connected(peer) as client:
        with pytest.raises(NapCatRejected, match="account"):
            await asyncio.wait_for(client.get_group_member_list(GROUP), 2)
        assert client._pending == {}
    assert actions == (
        ["get_login_info"]
        if phase == "preflight"
        else ["get_login_info", "get_group_member_list", "get_login_info"]
    )


@pytest.mark.parametrize("phase", ["lookup", "postflight"])
async def test_member_observation_rebind_during_lookup_or_postflight_is_rejected(
    phase: str,
) -> None:
    arrived, released = asyncio.Event(), asyncio.Event()
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        async for raw in socket:
            received = cast(JsonObject, json.loads(raw))
            actions.append(str(received["action"]))
            if (phase == "lookup" and len(actions) == 2) or (
                phase == "postflight" and len(actions) == 3
            ):
                arrived.set()
                await released.wait()
            await reply(
                socket,
                received,
                members()
                if received["action"] == "get_group_member_list"
                else {"user_id": ACCOUNT},
            )

    async with connected(peer) as client:
        task = asyncio.create_task(client.get_group_member_list(GROUP))
        try:
            await asyncio.wait_for(arrived.wait(), 2)
            client.bind_account(ACCOUNT)
            released.set()
            with pytest.raises(NapCatRejected, match="account"):
                await asyncio.wait_for(task, 2)
        finally:
            released.set()
            await asyncio.gather(task, return_exceptions=True)
        assert client._pending == {}
    assert len(actions) == (2 if phase == "lookup" else 3)


@pytest.mark.parametrize("operation", ["members", "group", "private"])
@pytest.mark.parametrize("rebind", ["same", "different", "aba"])
async def test_account_rebind_during_preflight_prevents_lookup_or_any_send(
    operation: str,
    rebind: str,
) -> None:
    arrived, released = asyncio.Event(), asyncio.Event()
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        arrived.set()
        await released.wait()
        await reply(socket, login, {"user_id": ACCOUNT})
        await socket.wait_closed()

    async def permitted() -> bool:
        return True

    async with connected(peer) as client:
        segments: list[JsonObject] = [{"type": "text", "data": {"text": "你好"}}]
        if operation == "members":
            pending = client.get_group_member_list(GROUP)
        elif operation == "group":
            pending = client.send_group(GROUP, segments, before_send=permitted)
        else:
            pending = client.send("10002", segments, before_send=permitted)
        task = asyncio.create_task(pending)
        try:
            await asyncio.wait_for(arrived.wait(), 2)
            client.bind_account(ACCOUNT if rebind == "same" else "10009")
            if rebind == "aba":
                client.bind_account(ACCOUNT)
            released.set()
            with pytest.raises(NapCatRejected, match="account"):
                await asyncio.wait_for(task, 2)
        finally:
            released.set()
            await asyncio.gather(task, return_exceptions=True)
        assert client._pending == {}
    assert actions == ["get_login_info"]


@pytest.mark.parametrize("stop", ["rights", "rebind", "cancel"])
async def test_group_send_last_guard_fences_changed_rights_and_cancellation(stop: str) -> None:
    checking, released = asyncio.Event(), asyncio.Event()
    rights = True
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        await reply(socket, login, {"user_id": ACCOUNT})
        await socket.wait_closed()

    async def before_send() -> bool:
        checking.set()
        await released.wait()
        return rights

    async with connected(peer) as client:
        task = asyncio.create_task(
            client.send_group(
                GROUP,
                [{"type": "text", "data": {"text": "不能发送"}}],
                before_send=before_send,
            )
        )
        try:
            await asyncio.wait_for(checking.wait(), 2)
            if stop == "rights":
                rights = False
            elif stop == "rebind":
                client.bind_account(ACCOUNT)
            else:
                task.cancel()
            released.set()
            with pytest.raises(asyncio.CancelledError if stop == "cancel" else NapCatRejected):
                await asyncio.wait_for(task, 2)
        finally:
            released.set()
            await asyncio.gather(task, return_exceptions=True)
        assert client._pending == {}
    assert actions == ["get_login_info"]


async def test_group_send_captures_text_and_fixed_target_before_preflight() -> None:
    observed: list[JsonObject] = []
    segments: list[JsonObject] = [{"type": "text", "data": {"text": "原始文字"}}]

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        observed.append(login)
        await reply(socket, login, {"user_id": ACCOUNT})
        sending = await request(socket)
        observed.append(sending)
        await reply(socket, sending, {"message_id": -123456})
        await socket.wait_closed()

    async def before_send() -> bool:
        segments[0]["data"] = {"text": "异步替换", "group_id": "20002"}
        return True

    async with connected(peer) as client:
        assert (
            await asyncio.wait_for(client.send_group(GROUP, segments, before_send=before_send), 2)
            == "-123456"
        )
    assert observed[-1]["action"] == "send_group_msg"
    assert observed[-1]["params"] == {
        "group_id": GROUP,
        "message": [{"type": "text", "data": {"text": "原始文字"}}],
    }


async def test_pending_notice_fences_group_and_keeps_private_send() -> None:
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        async for raw in socket:
            received = cast(JsonObject, json.loads(raw))
            actions.append(str(received["action"]))
            if received["action"] == "get_status":
                # Ordered notice + RPC response makes queue admission deterministic.
                await socket.send(json.dumps(membership_notice()))
                await reply(socket, received, {"online": True})
            elif received["action"] == "get_login_info":
                await reply(socket, received, {"user_id": ACCOUNT})
            else:
                assert received["action"] == "send_private_msg"
                await reply(socket, received, {"message_id": 123456})

    async def old_route() -> bool:
        return True

    async def disabled_route() -> bool:
        return False

    segments: list[JsonObject] = [{"type": "text", "data": {"text": "你好"}}]
    async with connected(peer) as client:
        await client.call("get_status", {})
        assert client._pending_group_notices == 1
        with pytest.raises(NapCatRejected, match="membership"):
            await client.send_group(GROUP, segments, before_send=old_route)
        with pytest.raises(NapCatRejected, match="membership"):
            await client.get_group_member_list(GROUP)
        assert actions == ["get_status"]
        assert await client.send("10002", segments) == "123456"
        assert client._pending_group_notices == 1
        assert (await client.event())["post_type"] == "notice"
        assert client._pending_group_notices == 0
        with pytest.raises(NapCatRejected, match="cancelled"):
            await client.send_group(GROUP, segments, before_send=disabled_route)
    assert actions == ["get_status", "get_login_info", "send_private_msg", "get_login_info"]


async def test_pending_membership_notice_does_not_block_private_image_stream() -> None:
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        async for raw in socket:
            received = cast(JsonObject, json.loads(raw))
            actions.append(str(received["action"]))
            if received["action"] == "get_status":
                await socket.send(json.dumps(membership_notice()))
                await reply(socket, received, {"online": True})
            elif received["action"] == "get_login_info":
                await reply(socket, received, {"user_id": ACCOUNT})
            else:
                assert received["action"] == "download_file_image_stream"
                packets: list[JsonObject] = [
                    {
                        "type": "stream",
                        "data_type": "file_info",
                        "file_size": 1,
                        "file_name": "private.png",
                        "chunk_size": 65536,
                    },
                    {
                        "type": "stream",
                        "data_type": "file_chunk",
                        "index": 0,
                        "size": 1,
                        "data": "eA==",
                        "base64_size": 4,
                    },
                    {
                        "type": "response",
                        "data_type": "file_complete",
                        "total_chunks": 1,
                        "total_bytes": 1,
                    },
                ]
                for data in packets:
                    await socket.send(
                        json.dumps(
                            {
                                "status": "ok",
                                "retcode": 0,
                                "echo": received["echo"],
                                "data": data,
                                "stream": "stream-action",
                            }
                        )
                    )

    async with connected(peer) as client:
        await client.call("get_status", {})
        assert await asyncio.wait_for(client.download_image("private.png"), 2) == b"x"
        assert client._pending_group_notices == 1
        assert client._streams == {} and client._pending == {}
    assert actions == [
        "get_status",
        "get_login_info",
        "download_file_image_stream",
        "get_login_info",
    ]


@pytest.mark.parametrize("phase", ["preflight", "guard", "guard_consumed"])
async def test_notice_during_preflight_or_guard_fences_prepared_group_send(phase: str) -> None:
    checking, trigger_notice, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        if phase == "preflight":
            await socket.send(json.dumps(membership_notice()))
        await reply(socket, login, {"user_id": ACCOUNT})
        if phase != "preflight":
            await trigger_notice.wait()
            await socket.send(json.dumps(membership_notice()))
            barrier = await request(socket)
            actions.append(str(barrier["action"]))
            await reply(socket, barrier, {"online": True})
        await socket.wait_closed()

    async def before_send() -> bool:
        checking.set()
        await released.wait()
        return True

    async with connected(peer) as client:
        task = asyncio.create_task(
            client.send_group(
                GROUP,
                [{"type": "text", "data": {"text": "旧工作"}}],
                before_send=before_send,
            )
        )
        try:
            if phase != "preflight":
                await asyncio.wait_for(checking.wait(), 2)
                trigger_notice.set()
                await asyncio.wait_for(client.call("get_status", {}), 2)
                assert client._pending_group_notices == 1
                if phase == "guard_consumed":
                    await client.event()
                    assert client._pending_group_notices == 0
                released.set()
            with pytest.raises(NapCatRejected, match="membership"):
                await asyncio.wait_for(task, 2)
            assert client._group_observation_epoch == 1
        finally:
            trigger_notice.set()
            released.set()
            await asyncio.gather(task, return_exceptions=True)
    assert "send_group_msg" not in actions
    assert actions == (
        ["get_login_info"] if phase == "preflight" else ["get_login_info", "get_status"]
    )


@pytest.mark.parametrize("phase", ["lookup", "postflight"])
async def test_notice_during_member_observation_rejects_even_valid_cached_rows(phase: str) -> None:
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        async for raw in socket:
            received = cast(JsonObject, json.loads(raw))
            actions.append(str(received["action"]))
            if (phase == "lookup" and len(actions) == 2) or (
                phase == "postflight" and len(actions) == 3
            ):
                await socket.send(json.dumps(membership_notice()))
            await reply(
                socket,
                received,
                members()
                if received["action"] == "get_group_member_list"
                else {"user_id": ACCOUNT},
            )

    async with connected(peer) as client:
        with pytest.raises(NapCatRejected, match="membership"):
            await asyncio.wait_for(client.get_group_member_list(GROUP), 2)
        assert client._pending == {} and client._pending_group_notices == 1
    assert len(actions) == (2 if phase == "lookup" else 3)


@pytest.mark.parametrize("kind", ["record", "image", "reply", "at"])
async def test_group_send_rejects_nontext_before_any_rpc(kind: str) -> None:
    calls: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        async for raw in socket:
            calls.append(cast(JsonObject, json.loads(raw)))

    async def before_send() -> bool:
        raise AssertionError("Invalid payload must not reach authorization")

    async with connected(peer) as client:
        with pytest.raises(ValueError):
            await client.send_group(
                GROUP, [{"type": kind, "data": {"text": "你好"}}], before_send=before_send
            )
    assert calls == []


@pytest.mark.parametrize("result", ["deadline", "disconnect", "rejected", "missing_id", "cancel"])
async def test_group_send_uncertainty_rejection_or_cancellation_never_retries(
    result: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_RPC_TIMEOUT_SECONDS", 0.2)
    sent, release_late = asyncio.Event(), asyncio.Event()
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        await reply(socket, login, {"user_id": ACCOUNT})
        sending = await request(socket)
        actions.append(str(sending["action"]))
        sent.set()
        if result == "disconnect":
            await socket.close()
        elif result == "rejected":
            await socket.send(
                json.dumps(
                    {
                        "status": "failed",
                        "retcode": 1200,
                        "echo": sending["echo"],
                        "message": "private provider detail",
                    }
                )
            )
        elif result == "missing_id":
            await reply(socket, sending, {})
        elif result == "cancel":
            await release_late.wait()
            await reply(socket, sending, {"message_id": 123456})
            status = await request(socket)
            actions.append(str(status["action"]))
            await reply(socket, status, {"online": True})
        await socket.wait_closed()

    async def before_send() -> bool:
        return True

    async with connected(peer) as client:
        task = asyncio.create_task(
            client.send_group(
                GROUP,
                [{"type": "text", "data": {"text": "一次发送"}}],
                before_send=before_send,
            )
        )
        try:
            await asyncio.wait_for(sent.wait(), 2)
            if result == "cancel":
                task.cancel()
            expected = (
                asyncio.CancelledError
                if result == "cancel"
                else NapCatRejected
                if result == "rejected"
                else NapCatUncertain
            )
            with pytest.raises(expected) as failure:
                await asyncio.wait_for(task, 2)
            assert "private provider detail" not in str(failure.value)
            assert client._pending == {}
            if result == "cancel":
                release_late.set()
                assert await client.call("get_status", {}) == {"online": True}
        finally:
            release_late.set()
            await asyncio.gather(task, return_exceptions=True)
    assert actions.count("send_group_msg") == 1
    assert actions[:2] == ["get_login_info", "send_group_msg"]


@pytest.mark.parametrize("flood", ["messages", "notices", "mixed"])
async def test_membership_event_queue_overflow_closes_connection(flood: str) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        await reply(socket, received, {"online": True})
        for index in range(64):
            event = (
                membership_notice()
                if flood == "notices" or (flood == "mixed" and index % 2)
                else {"post_type": "message", "message_id": index}
            )
            await socket.send(json.dumps(event))
        await socket.send(json.dumps(membership_notice()))
        await socket.wait_closed()

    async with connected(peer) as client:
        assert await client.call("get_status", {}) == {"online": True}
        assert client._reader is not None
        await asyncio.wait_for(client._reader, 2)
        assert client._events.qsize() == 64
        assert 0 <= client._pending_group_notices <= 64
        with pytest.raises(NapCatRejected, match="unavailable"):
            await client.call("get_status", {})
        for _ in range(64):
            await client.event()
        assert client._pending_group_notices == 0
        with pytest.raises(NapCatError, match="closed"):
            await asyncio.wait_for(client.event(), 2)


@pytest.mark.parametrize("key", ["self_id", "group_id", "user_id", "sub_type"])
async def test_invalid_supported_membership_notice_fails_closed(key: str) -> None:
    async def peer(socket: ServerConnection) -> None:
        await reply(socket, await request(socket), {"online": True})
        notice = membership_notice()
        notice[key] = "unknown"
        await socket.send(json.dumps(notice))
        await socket.wait_closed()

    async with connected(peer) as client:
        assert await client.call("get_status", {}) == {"online": True}
        assert client._reader is not None
        await asyncio.wait_for(client._reader, 2)
        with pytest.raises(NapCatError, match="closed"):
            await client.event()
