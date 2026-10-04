# pyright: reportPrivateUsage=false
"""Exercise the OneBot boundary with actual local WebSocket peers."""

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import ChannelConnectionConfiguration, ChannelPairingStartRequest
from chatwaifu_runtime.characters.service import CharacterService
from chatwaifu_runtime.eventing.hub import EventHub
from chatwaifu_runtime.eventing.publisher import EventPublisher
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import (
    NapCatClient,
    NapCatError,
    NapCatRejected,
    NapCatUncertain,
    validate_endpoint,
)
from chatwaifu_runtime.external_channels.adapters.qq_napcat.management import NapCatManagement
from chatwaifu_runtime.external_channels.credentials import ChannelCredentialStore
from chatwaifu_runtime.external_channels.models import ChannelDeliveryPlanRecord
from chatwaifu_runtime.external_channels.ports import ExternalChannelRepository
from chatwaifu_runtime.external_channels.service import ExternalChannelService
from pydantic import SecretStr
from websockets.asyncio.server import ServerConnection, serve

TOKEN = "test-token-never-a-real-secret"
Handler = Callable[[ServerConnection], Awaitable[None]]


@asynccontextmanager
async def connected(handler: Handler) -> AsyncGenerator[NapCatClient]:
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = NapCatClient(f"ws://127.0.0.1:{port}/onebot", TOKEN)
        await client.open()
        try:
            yield client
        finally:
            await client.close()


async def request(socket: ServerConnection) -> JsonObject:
    return cast(JsonObject, json.loads(await socket.recv()))


async def reply(socket: ServerConnection, received: JsonObject, data: JsonObject) -> None:
    await socket.send(
        json.dumps(
            {
                "status": "ok",
                "retcode": 0,
                "echo": received["echo"],
                "data": data,
            }
        )
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "ws://127.0.0.1:3001",
        "ws://localhost:3001/ws",
        "ws://[::1]:3001",
        "wss://qq.example.test/onebot",
    ],
)
def test_supported_endpoints(endpoint: str) -> None:
    assert validate_endpoint(endpoint) == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:3001",
        "ws://remote.example.test:3001",
        "ws://",
        "ws://token@localhost:3001",
        "wss://qq.example.test/?access_token=secret",
        "ws://localhost:3001/#secret",
    ],
)
def test_reject_unsafe_or_nonwebsocket_endpoint(endpoint: str) -> None:
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)


@pytest.mark.asyncio
async def test_header_auth_interleaved_events_and_out_of_order_echo_responses() -> None:
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        assert socket.request is not None
        assert socket.request.headers["Authorization"] == f"Bearer {TOKEN}"
        observed.extend([await request(socket), await request(socket)])
        assert observed[0]["echo"] != observed[1]["echo"]
        await socket.send("malformed json")
        await socket.send("[]")
        await socket.send(json.dumps({"post_type": "message", "message_id": 4001}))
        await reply(socket, observed[1], {"action": observed[1]["action"]})
        await reply(socket, observed[0], {"action": observed[0]["action"]})
        await socket.wait_closed()

    async with connected(peer) as client:
        results = await asyncio.wait_for(
            asyncio.gather(
                client.call("get_login_info", {}),
                client.call("get_status", {}),
            ),
            timeout=2,
        )
        assert results == [{"action": "get_login_info"}, {"action": "get_status"}]
        assert await asyncio.wait_for(client.event(), timeout=2) == {
            "post_type": "message",
            "message_id": 4001,
        }
        assert client._pending == {}


@pytest.mark.asyncio
async def test_send_returns_provider_id_and_structured_message_segments() -> None:
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await reply(socket, observed[0], {"message_id": -12345})
        await socket.wait_closed()

    segments: list[JsonObject] = [{"type": "text", "data": {"text": "你好"}}]
    async with connected(peer) as client:
        assert await client.send("10002", segments) == "-12345"
    assert observed[0]["action"] == "send_private_msg"
    assert observed[0]["params"] == {"user_id": 10002, "message": segments}


@pytest.mark.asyncio
async def test_explicit_rejection_is_sanitized_and_pending_removed() -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        await socket.send(
            json.dumps(
                {
                    "status": "failed",
                    "retcode": 1200,
                    "echo": received["echo"],
                    "message": "private user text and token must not be exposed",
                }
            )
        )
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatRejected) as failure:
            await client.send("10002", [{"type": "text", "data": {"text": "hello"}}])
        assert "private user text" not in str(failure.value)
        assert "token" not in str(failure.value)
        assert client._pending == {}


@pytest.mark.asyncio
async def test_closed_peer_fails_inflight_call_and_event_consumer() -> None:
    async def peer(socket: ServerConnection) -> None:
        await request(socket)
        await socket.close()

    async with connected(peer) as client:
        with pytest.raises(NapCatUncertain):
            await asyncio.wait_for(client.call("get_status", {}), timeout=2)
        with pytest.raises(NapCatError, match="closed"):
            await asyncio.wait_for(client.event(), timeout=2)
        assert client._pending == {}


@pytest.mark.asyncio
async def test_cancellation_releases_pending_capacity_and_reader_survives_late_reply() -> None:
    received_first = asyncio.Event()
    release_late_reply = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        first = await request(socket)
        received_first.set()
        await release_late_reply.wait()
        await reply(socket, first, {"message_id": 1})
        second = await request(socket)
        await reply(socket, second, {"status": "online"})
        await socket.wait_closed()

    async with connected(peer) as client:
        pending = asyncio.create_task(client.call("get_login_info", {}))
        await asyncio.wait_for(received_first.wait(), timeout=2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert client._pending == {}
        release_late_reply.set()
        assert await asyncio.wait_for(client.call("get_status", {}), timeout=2) == {
            "status": "online",
        }


@pytest.mark.asyncio
async def test_pending_calls_are_bounded_and_can_be_cancelled() -> None:
    all_received = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        for _ in range(32):
            await request(socket)
        all_received.set()
        await socket.wait_closed()

    async with connected(peer) as client:
        pending = [asyncio.create_task(client.call("get_status", {})) for _ in range(32)]
        try:
            await asyncio.wait_for(all_received.wait(), timeout=2)
            with pytest.raises(NapCatRejected, match="capacity"):
                await client.call("get_status", {})
            assert len(client._pending) == 32
        finally:
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*pending, return_exceptions=True)
            assert all(isinstance(value, asyncio.CancelledError) for value in results)
        assert client._pending == {}


@pytest.mark.asyncio
async def test_request_deadline_is_uncertain_without_retry() -> None:
    received: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        received.append(await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatUncertain, match="unknown"):
            await asyncio.wait_for(client.send("10002", []), timeout=12)
        assert len(received) == 1
        assert client._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_id", [None, "", " ", "not-an-id", "unknown", True, 1.2, {"bad": "id"}]
)
async def test_missing_or_invalid_send_id_is_uncertain(message_id: object) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        await reply(socket, received, cast(JsonObject, {"message_id": message_id}))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatUncertain, match="identifier"):
            await client.send("10002", [])


@pytest.mark.asyncio
async def test_pairing_ignores_unicode_non_code_messages_before_valid_owner_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chinese or emoji private messages must not abort the pending pairing loop."""

    class PairingClient(NapCatClient):
        def __init__(self) -> None:
            super().__init__("ws://127.0.0.1:3001", TOKEN)
            self.incoming: asyncio.Queue[JsonObject] = asyncio.Queue()
            self.requested: asyncio.Queue[None] = asyncio.Queue()

        async def open(self) -> None:
            pass

        async def close(self) -> None:
            pass

        async def call(self, action: str, params: JsonObject) -> JsonObject:
            assert action == "get_login_info"
            return {"user_id": 10001}

        async def event(self) -> JsonObject:
            self.requested.put_nowait(None)
            return await self.incoming.get()

    client = PairingClient()
    gateway = MagicMock()
    gateway.list_connections = AsyncMock(return_value=())
    credentials = MagicMock()
    credentials.available = AsyncMock(return_value=True)
    credentials.delete = AsyncMock()
    enrolled: list[ChannelConnectionConfiguration] = []
    supervised: list[UUID] = []

    async def terminal(_plan: ChannelDeliveryPlanRecord) -> None:
        pass

    manager = NapCatManagement(
        cast(ExternalChannelService, gateway),
        cast(ExternalChannelRepository, MagicMock()),
        cast(ChannelCredentialStore, credentials),
        cast(CharacterService, MagicMock()),
        cast(EventPublisher, MagicMock()),
        cast(EventHub, MagicMock()),
        tmp_path,
        terminal,
        client_factory=lambda _endpoint, _token: client,
    )

    async def enroll(
        _request: ChannelPairingStartRequest,
        configuration: ChannelConnectionConfiguration,
        _access_token: str,
        pairing_id: UUID,
    ) -> None:
        enrolled.append(configuration)
        await manager._update_pairing(pairing_id, status="confirmed", pairing_code=None)

    monkeypatch.setattr(manager, "_enroll", enroll)
    monkeypatch.setattr(manager, "_start_connection", supervised.append)
    pairing = await manager.begin_pairing(
        ChannelPairingStartRequest(
            endpoint="ws://127.0.0.1:3001",
            access_token=SecretStr(TOKEN),
            character_id="ayachi_nene",
        )
    )
    task = manager._pair_tasks[pairing.pairing_id]
    try:
        await asyncio.wait_for(client.requested.get(), timeout=2)
        for message_id, text in enumerate(["你好", "🦊", "CW2 INCORRECT"], start=90001):
            client.incoming.put_nowait(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "self_id": 10001,
                    "user_id": 10003,
                    "message_id": message_id,
                    "message": [{"type": "text", "data": {"text": text}}],
                }
            )
            # A second event request proves the previous message was safely ignored.
            await asyncio.wait_for(client.requested.get(), timeout=2)
            assert (await manager.pairing(pairing.pairing_id)).status == "pending"
            assert not enrolled
        client.incoming.put_nowait(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 10001,
                "user_id": 10002,
                "message_id": 90004,
                "message": [{"type": "text", "data": {"text": f"CW2 {pairing.pairing_code}"}}],
            }
        )
        await asyncio.wait_for(task, timeout=2)
        assert (await manager.pairing(pairing.pairing_id)).status == "confirmed"
        assert len(enrolled) == 1
        assert enrolled[0].allowed_sender_keys == ["10002"]
        assert supervised == [enrolled[0].connection_id]
    finally:
        await manager.stop()
