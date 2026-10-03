# pyright: reportPrivateUsage=false
"""Pinned NapCat v4.18.28 image stream on actual bounded local WebSocket peers."""

import asyncio
import base64
import hashlib
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.external_channels.adapters.qq_napcat import client as client_module
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import (
    NapCatClient,
    NapCatError,
    NapCatRejected,
    NapCatUncertain,
    validate_image_file_ref,
)
from websockets.asyncio.server import ServerConnection, serve

FILE_REF = "admitted-owner-image.png"
TOKEN = "local-fixture-token"
CHUNK = 65536
MAX_BYTES = 5 * 1024 * 1024
Handler = Callable[[ServerConnection], Awaitable[None]]


@asynccontextmanager
async def connected(handler: Handler) -> AsyncGenerator[NapCatClient]:
    async with serve(handler, "127.0.0.1", 0) as server:
        client = NapCatClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", TOKEN)
        await client.open()
        try:
            yield client
        finally:
            await client.close()


async def request(socket: ServerConnection) -> JsonObject:
    return cast(JsonObject, json.loads(await socket.recv()))


def stream_packets(content: bytes, file_ref: str = FILE_REF) -> list[JsonObject]:
    packets: list[JsonObject] = [
        {
            "type": "stream",
            "data_type": "file_info",
            "file_name": file_ref,
            "file_size": len(content),
            "chunk_size": CHUNK,
            "width": 1,
            "height": 1,
        }
    ]
    for index, offset in enumerate(range(0, len(content), CHUNK)):
        chunk = content[offset : offset + CHUNK]
        encoded = base64.b64encode(chunk).decode("ascii")
        packets.append(
            {
                "type": "stream",
                "data_type": "file_chunk",
                "index": index,
                "data": encoded,
                "size": len(chunk),
                "base64_size": len(encoded),
                "progress": round((offset + len(chunk)) / len(content) * 100),
            }
        )
    packets.append(
        {
            "type": "response",
            "data_type": "file_complete",
            "total_bytes": len(content),
            "total_chunks": len(packets) - 1,
            "message": "Download completed",
        }
    )
    return packets


async def frame(socket: ServerConnection, received: JsonObject, packet: JsonObject) -> None:
    await socket.send(
        json.dumps(
            {
                "status": "ok",
                "retcode": 0,
                "stream": "stream-action",
                "echo": received["echo"],
                "data": packet,
            }
        )
    )


async def normal_reply(
    socket: ServerConnection,
    received: JsonObject,
    data: JsonObject | None = None,
) -> None:
    await socket.send(
        json.dumps(
            {
                "status": "ok",
                "retcode": 0,
                "stream": "normal-action",
                "echo": received["echo"],
                "data": data if data is not None else {"online": True},
            }
        )
    )


@pytest.mark.parametrize("file_ref", ["001122.JPG", "截图 1.png", "{abc-def}.jpeg", "a.gif"])
def test_provider_filenames_are_preserved(file_ref: str) -> None:
    assert validate_image_file_ref(file_ref) == file_ref


@pytest.mark.parametrize(
    "file_ref",
    [
        "",
        " ",
        " .png",
        "a.png ",
        ".",
        "..",
        "/tmp/a.png",
        "../a.png",
        "a/b.png",
        r"C:\a.png",
        r"a\b.png",
        "https://example.test/a.png",
        "file:a.png",
        "a\x00.png",
        "a\n.png",
        "a\x7f.png",
        "a" * 256,
        "图" * 86,
    ],
)
def test_reject_paths_schemes_controls_and_unbounded_filenames(file_ref: str) -> None:
    with pytest.raises(ValueError, match="provider filename"):
        validate_image_file_ref(file_ref)


@pytest.mark.asyncio
@pytest.mark.parametrize("max_bytes", [0, -1, MAX_BYTES + 1, True, 1.5])
async def test_invalid_byte_limit_sends_nothing(max_bytes: object) -> None:
    # Validation occurs even before a socket is opened.
    client = NapCatClient("ws://127.0.0.1:3001", TOKEN)
    with pytest.raises(ValueError, match="byte limit"):
        await client.download_image(FILE_REF, max_bytes=cast(int, max_bytes))


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1, CHUNK - 1, CHUNK, CHUNK + 1, MAX_BYTES])
async def test_exact_pinned_stream_and_interleaved_normal_rpc_and_event(size: int) -> None:
    content = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        assert socket.request is not None
        assert socket.request.headers["Authorization"] == f"Bearer {TOKEN}"
        observed.extend([await request(socket), await request(socket)])
        image = next(value for value in observed if value["action"] == "download_file_image_stream")
        ordinary = next(value for value in observed if value["action"] == "get_status")
        assert image["params"] == {"file": FILE_REF, "chunk_size": CHUNK}
        packets = stream_packets(content)
        await frame(socket, image, packets[0])
        await socket.send(json.dumps({"post_type": "message", "message_id": 4321}))
        await normal_reply(socket, ordinary)
        for packet in packets[1:]:
            await frame(socket, image, packet)
        await socket.wait_closed()

    async with connected(peer) as client:
        downloaded, status = await asyncio.wait_for(
            asyncio.gather(client.download_image(FILE_REF), client.call("get_status", {})),
            3,
        )
        assert downloaded == content
        assert hashlib.sha256(downloaded).digest() == hashlib.sha256(content).digest()
        assert status == {"online": True}
        assert await asyncio.wait_for(client.event(), 2) == {
            "post_type": "message",
            "message_id": 4321,
        }
        assert client._pending == {} and client._streams == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("display_name", ["different-original-name.JPG", ""])
async def test_display_filename_is_not_a_reference_or_path_authority(display_name: str) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        assert received["params"] == {"file": FILE_REF, "chunk_size": CHUNK}
        packets = stream_packets(b"authenticated-content")
        packets[0]["file_name"] = display_name
        for packet in packets:
            await frame(socket, received, packet)
        await socket.wait_closed()

    async with connected(peer) as client:
        assert await client.download_image(FILE_REF) == b"authenticated-content"
        assert not client._streams and not client._pending


def broken_packets(case: str) -> list[JsonObject]:
    packets = stream_packets(b"a")
    if case == "no_header":
        return packets[1:]
    if case == "duplicate_header":
        return [packets[0], *packets]
    if case == "empty_size":
        packets[0]["file_size"] = 0
    elif case == "oversize":
        packets[0]["file_size"] = MAX_BYTES + 1
    elif case == "string_size":
        packets[0]["file_size"] = "1"
    elif case == "bool_size":
        packets[0]["file_size"] = True
    elif case == "nonstring_filename":
        packets[0]["file_name"] = 123
    elif case == "different_chunk_size":
        packets[0]["chunk_size"] = 1
    elif case == "index_gap":
        packets[1]["index"] = 1
    elif case == "bool_index":
        packets[1]["index"] = False
    elif case == "empty_chunk":
        packets[1]["size"] = 0
    elif case == "wrong_decoded_size":
        packets[1]["size"] = 2
    elif case == "wrong_encoded_length":
        packets[1]["base64_size"] = 3
    elif case == "invalid_base64":
        packets[1]["data"] = "!!!!"
    elif case == "noncanonical_base64":
        packets[1]["data"] = "YR=="
    elif case == "wrong_chunk_count":
        packets[2]["total_chunks"] = 2
    elif case == "wrong_byte_count":
        packets[2]["total_bytes"] = 2
    elif case == "bool_byte_count":
        packets[2]["total_bytes"] = True
    elif case == "early_complete":
        return [packets[0], packets[2]]
    elif case == "reset":
        packets[1]["type"] = "reset"
    elif case == "duplicate_chunk":
        return [*packets[:2], packets[1], packets[2]]
    elif case == "overrun":
        packets[0]["file_size"] = 2
        packets[1]["data"] = "YWJj"
        packets[1]["size"] = 3
    return packets


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "no_header",
        "duplicate_header",
        "empty_size",
        "oversize",
        "string_size",
        "bool_size",
        "nonstring_filename",
        "different_chunk_size",
        "index_gap",
        "bool_index",
        "empty_chunk",
        "wrong_decoded_size",
        "wrong_encoded_length",
        "invalid_base64",
        "noncanonical_base64",
        "wrong_chunk_count",
        "wrong_byte_count",
        "bool_byte_count",
        "early_complete",
        "reset",
        "duplicate_chunk",
        "overrun",
    ],
)
async def test_malformed_stream_fails_and_keeps_normal_rpc_usable(case: str) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        for packet in broken_packets(case):
            await frame(socket, received, packet)
        await normal_reply(socket, await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError):
            await asyncio.wait_for(client.download_image(FILE_REF), 2)
        assert client._streams == {}
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}


@pytest.mark.asyncio
async def test_custom_byte_limit_is_checked_before_decoding() -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        await frame(socket, received, stream_packets(b"ab")[0])
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="size exceeds"):
            await asyncio.wait_for(client.download_image(FILE_REF, max_bytes=1), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_mode", ["stream-action", "normal-action"])
async def test_provider_rejection_is_sanitized_including_unsupported_stream(
    stream_mode: str,
) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        await socket.send(
            json.dumps(
                {
                    "status": "failed",
                    "retcode": 1200,
                    "echo": received["echo"],
                    "stream": stream_mode,
                    "data": {"type": "error", "data_type": "error"},
                    "message": "private url and token",
                    "wording": "private filename and owner",
                }
            )
        )
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatRejected) as failure:
            await asyncio.wait_for(client.download_image(FILE_REF), 2)
        assert str(failure.value) == "QQ rejected the image download"
        assert client._streams == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("ordinary_request", [False, True])
async def test_normal_response_and_stream_frame_cannot_be_confused(ordinary_request: bool) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        if ordinary_request:
            await frame(socket, received, stream_packets(b"a")[0])
        else:
            await normal_reply(socket, received)
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="stream"):
            operation = (
                client.call("get_status", {})
                if ordinary_request
                else client.download_image(FILE_REF)
            )
            await asyncio.wait_for(operation, 2)
        assert client._pending == {} and client._streams == {}


@pytest.mark.asyncio
async def test_cancellation_releases_capacity_and_discards_late_same_echo_frames() -> None:
    arrived = asyncio.Event()
    release = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        image = await request(socket)
        arrived.set()
        await release.wait()
        for packet in stream_packets(b"late"):
            await frame(socket, image, packet)
        await normal_reply(socket, await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        download = asyncio.create_task(client.download_image(FILE_REF))
        await asyncio.wait_for(arrived.wait(), 2)
        download.cancel()
        with pytest.raises(asyncio.CancelledError):
            await download
        assert client._streams == {}
        release.set()
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
        assert client._events.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("after_header", [False, True])
async def test_connection_loss_wakes_stream_consumer(after_header: bool) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        if after_header:
            await frame(socket, received, stream_packets(b"a")[0])
        await socket.close()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="connection lost"):
            await asyncio.wait_for(client.download_image(FILE_REF), 2)
        assert client._streams == {}


@pytest.mark.asyncio
async def test_download_deadline_sends_once_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    assert client_module._IMAGE_TIMEOUT_SECONDS == 20
    monkeypatch.setattr(client_module, "_IMAGE_TIMEOUT_SECONDS", 0.02)
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="did not complete"):
            await asyncio.wait_for(client.download_image(FILE_REF), 2)
        assert len(observed) == 1 and client._streams == {}


@pytest.mark.asyncio
async def test_shared_32_request_bound_and_single_stream_bound() -> None:
    arrived = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        for _ in range(32):
            await request(socket)
        arrived.set()
        await socket.wait_closed()

    async with connected(peer) as client:
        pending: list[asyncio.Task[JsonObject | bytes]] = [
            asyncio.create_task(client.call("get_status", {})) for _ in range(31)
        ]
        pending.append(asyncio.create_task(client.download_image(FILE_REF)))
        try:
            await asyncio.wait_for(arrived.wait(), 2)
            assert len(client._pending) + len(client._streams) == 32
            with pytest.raises(NapCatRejected, match="capacity"):
                await client.call("get_status", {})
            with pytest.raises(NapCatRejected, match="capacity"):
                await client.download_image(FILE_REF)
        finally:
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*pending, return_exceptions=True)
            assert all(isinstance(value, asyncio.CancelledError) for value in results)
        assert client._pending == {} and client._streams == {}


@pytest.mark.asyncio
async def test_queue_overflow_fails_stream_without_blocking_rpc_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    arrived = asyncio.Event()
    original = client_module._ImageStream.next_frame

    async def blocked_next(stream: client_module._ImageStream) -> JsonObject:
        await gate.wait()
        return await original(stream)

    monkeypatch.setattr(client_module._ImageStream, "next_frame", blocked_next)

    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        for _ in range(client_module._IMAGE_QUEUE_FRAMES + 1):
            await frame(socket, received, stream_packets(b"a")[0])
        arrived.set()
        await normal_reply(socket, await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        download = asyncio.create_task(client.download_image(FILE_REF))
        try:
            await asyncio.wait_for(arrived.wait(), 2)
            assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
            stream = next(iter(client._streams.values()))
            assert stream.frames.qsize() == client_module._IMAGE_QUEUE_FRAMES
            gate.set()
            with pytest.raises(NapCatError, match="capacity"):
                await asyncio.wait_for(download, 2)
        finally:
            gate.set()
            if not download.done():
                download.cancel()
            await asyncio.gather(download, return_exceptions=True)
        assert client._streams == {}


@pytest.mark.asyncio
async def test_oversized_stream_frame_is_rejected_before_queueing() -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        await frame(socket, received, {"type": "stream", "data": "a" * (91 * 1024)})
        await normal_reply(socket, await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="frame exceeds"):
            await asyncio.wait_for(client.download_image(FILE_REF), 2)
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
        assert client._streams == {}


@pytest.mark.asyncio
async def test_bound_account_preflight_precedes_image_download() -> None:
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        assert observed[0]["action"] == "get_login_info"
        await normal_reply(socket, observed[0], {"user_id": 10001})
        observed.append(await request(socket))
        assert observed[1]["action"] == "download_file_image_stream"
        for packet in stream_packets(b"account-bound"):
            await frame(socket, observed[1], packet)
        observed.append(await request(socket))
        assert observed[2]["action"] == "get_login_info"
        await normal_reply(socket, observed[2], {"user_id": 10001})
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        assert await asyncio.wait_for(client.download_image(FILE_REF), 2) == b"account-bound"
        assert len(observed) == 3
        assert client._streams == {} and client._pending == {}


@pytest.mark.asyncio
async def test_changed_account_rejects_without_requesting_any_image() -> None:
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await normal_reply(socket, observed[0], {"user_id": 99999})
        observed.append(await request(socket))
        await normal_reply(socket, observed[1])
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        with pytest.raises(NapCatRejected, match="account changed"):
            await asyncio.wait_for(client.download_image(FILE_REF), 2)
        assert client._streams == {} and client._pending == {}
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
        assert [value["action"] for value in observed] == ["get_login_info", "get_status"]


@pytest.mark.asyncio
async def test_cancel_during_account_preflight_leaves_no_image_rpc_or_stream() -> None:
    arrived = asyncio.Event()
    release = asyncio.Event()
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        arrived.set()
        await release.wait()
        await normal_reply(socket, observed[0], {"user_id": 10001})
        observed.append(await request(socket))
        await normal_reply(socket, observed[1])
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        download = asyncio.create_task(client.download_image(FILE_REF))
        await asyncio.wait_for(arrived.wait(), 2)
        assert client._streams == {} and len(client._pending) == 1
        download.cancel()
        with pytest.raises(asyncio.CancelledError):
            await download
        assert client._streams == {} and client._pending == {}
        release.set()
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
        assert [value["action"] for value in observed] == ["get_login_info", "get_status"]


@pytest.mark.asyncio
async def test_whole_download_deadline_includes_account_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_IMAGE_TIMEOUT_SECONDS", 0.02)
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        with pytest.raises(NapCatError, match="did not complete"):
            await asyncio.wait_for(client.download_image(FILE_REF), 2)
        assert [value["action"] for value in observed] == ["get_login_info"]
        assert client._streams == {} and client._pending == {}


@pytest.mark.asyncio
async def test_client_close_wakes_blocked_stream_without_leftover_queue_getters() -> None:
    arrived = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await request(socket)
        arrived.set()
        await socket.wait_closed()

    async with connected(peer) as client:
        download = asyncio.create_task(client.download_image(FILE_REF))
        await asyncio.wait_for(arrived.wait(), 2)
        await client.close()
        with pytest.raises(NapCatError, match="connection lost"):
            await asyncio.wait_for(download, 2)
        assert not any(task.get_name() == "qq-image-frame" for task in asyncio.all_tasks())
        assert client._streams == {}


@pytest.mark.asyncio
async def test_stream_response_to_send_remains_uncertain_and_is_never_retried() -> None:
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await frame(socket, observed[0], stream_packets(b"bad-send-response")[0])
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatUncertain, match="unexpected stream"):
            await asyncio.wait_for(client.send("10002", []), 2)
        assert len(observed) == 1 and observed[0]["action"] == "send_private_msg"
        assert client._pending == {}


@pytest.mark.asyncio
async def test_complete_stream_remains_valid_if_peer_closes_immediately_after_final() -> None:
    content = b"z" * (CHUNK + 1)

    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        for packet in stream_packets(content):
            await frame(socket, received, packet)
        await socket.close()

    async with connected(peer) as client:
        assert await asyncio.wait_for(client.download_image(FILE_REF), 2) == content
        assert client._streams == {}


@pytest.mark.asyncio
async def test_account_switch_during_stream_discards_valid_bytes_before_return() -> None:
    streaming = asyncio.Event()
    switch = asyncio.Event()
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await normal_reply(socket, observed[0], {"user_id": 10001})
        observed.append(await request(socket))
        packets = stream_packets(b"b" * (CHUNK + 1))
        await frame(socket, observed[1], packets[0])
        await frame(socket, observed[1], packets[1])
        streaming.set()
        await switch.wait()
        for packet in packets[2:]:
            await frame(socket, observed[1], packet)
        observed.append(await request(socket))
        await normal_reply(socket, observed[2], {"user_id": 99999})
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        download = asyncio.create_task(client.download_image(FILE_REF))
        await asyncio.wait_for(streaming.wait(), 2)
        switch.set()
        with pytest.raises(NapCatRejected, match="account changed during"):
            await asyncio.wait_for(download, 2)
        assert [value["action"] for value in observed] == [
            "get_login_info",
            "download_file_image_stream",
            "get_login_info",
        ]
        assert client._streams == {} and client._pending == {}


@pytest.mark.asyncio
async def test_cancellation_during_postflight_discards_bytes_and_late_account_response() -> None:
    arrived = asyncio.Event()
    release = asyncio.Event()
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await normal_reply(socket, observed[0], {"user_id": 10001})
        observed.append(await request(socket))
        for packet in stream_packets(b"bytes-never-returned"):
            await frame(socket, observed[1], packet)
        observed.append(await request(socket))
        arrived.set()
        await release.wait()
        await normal_reply(socket, observed[2], {"user_id": 10001})
        observed.append(await request(socket))
        await normal_reply(socket, observed[3])
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        download = asyncio.create_task(client.download_image(FILE_REF))
        await asyncio.wait_for(arrived.wait(), 2)
        assert len(client._streams) == 1 and len(client._pending) == 1
        download.cancel()
        with pytest.raises(asyncio.CancelledError):
            await download
        assert client._streams == {} and client._pending == {}
        release.set()
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
