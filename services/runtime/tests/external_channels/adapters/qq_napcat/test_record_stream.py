# pyright: reportPrivateUsage=false
"""Pinned record streams use real local WebSockets; no QQ or audio provider calls."""

import asyncio
import base64
import io
import json
import wave
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.external_channels.adapters.qq_napcat import client as client_module
from chatwaifu_runtime.external_channels.adapters.qq_napcat.audio import decode_record_wav
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import (
    NapCatClient,
    NapCatError,
    NapCatRejected,
)
from websockets.asyncio.server import ServerConnection, serve

FILE_REF = "admitted-owner-record.silk"
CHUNK = 65536
MAX_BYTES = 5 * 1024 * 1024
TOKEN = "local-record-fixture-token"
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


def chunk_packet(content: bytes, index: int) -> JsonObject:
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "type": "stream",
        "data_type": "file_chunk",
        "index": index,
        "data": encoded,
        "size": len(content),
        "base64_size": len(encoded),
        "progress": 100,
    }


def packets(
    content: bytes, *, source_size: int = 37, name: str = "converted.wav"
) -> list[JsonObject]:
    result: list[JsonObject] = [
        {
            "type": "stream",
            "data_type": "file_info",
            "file_name": name,
            "file_size": source_size,
            "chunk_size": CHUNK,
            "out_format": "wav",
        }
    ]
    for index, offset in enumerate(range(0, len(content), CHUNK)):
        result.append(chunk_packet(content[offset : offset + CHUNK], index))
    result.append(
        {
            "type": "response",
            "data_type": "file_complete",
            "total_chunks": len(result) - 1,
            "total_bytes": len(content),
            "message": "Download completed",
        }
    )
    return result


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


async def ordinary(
    socket: ServerConnection, received: JsonObject, data: JsonObject | None = None
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


@pytest.mark.parametrize("size", [1, CHUNK - 1, CHUNK, CHUNK + 1, MAX_BYTES])
@pytest.mark.asyncio
async def test_converted_bytes_ignore_original_header_size_with_interleaved_rpc_and_event(
    size: int,
) -> None:
    content = b"w" * size

    async def peer(socket: ServerConnection) -> None:
        assert socket.request is not None
        assert socket.request.headers["Authorization"] == f"Bearer {TOKEN}"
        received = [await request(socket), await request(socket)]
        record = next(
            value for value in received if value["action"] == "download_file_record_stream"
        )
        normal = next(value for value in received if value["action"] == "get_status")
        assert record["params"] == {"file": FILE_REF, "chunk_size": CHUNK, "out_format": "wav"}
        stream = packets(content)
        await frame(socket, record, stream[0])
        await socket.send(json.dumps({"post_type": "message", "message_id": 9123}))
        await ordinary(socket, normal)
        for packet in stream[1:]:
            await frame(socket, record, packet)
        await socket.wait_closed()

    async with connected(peer) as client:
        downloaded, status = await asyncio.wait_for(
            asyncio.gather(client.download_record(FILE_REF), client.call("get_status", {})),
            3,
        )
        assert downloaded == content and status == {"online": True}
        assert await asyncio.wait_for(client.event(), 2) == {
            "post_type": "message",
            "message_id": 9123,
        }
        assert client._streams == {} and client._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "original.amr", "another-name.wav"])
async def test_filename_is_only_bounded_display_metadata(name: str) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        for packet in packets(b"wav-bytes", name=name):
            await frame(socket, received, packet)
        await socket.wait_closed()

    async with connected(peer) as client:
        assert await asyncio.wait_for(client.download_record(FILE_REF), 2) == b"wav-bytes"


@pytest.mark.asyncio
async def test_pinned_file_stream_short_reads_preserve_complete_pcm16_wav() -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x01" * 48000)
    content = buffer.getvalue()
    fragments = [content[:1024], content[1024 : 1024 + CHUNK], content[1024 + CHUNK :]]

    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        standard = packets(content)
        await frame(socket, received, standard[0])
        for index, fragment in enumerate(fragments):
            await frame(socket, received, chunk_packet(fragment, index))
        complete = standard[-1]
        complete["total_chunks"] = len(fragments)
        await frame(socket, received, complete)
        await socket.wait_closed()

    async with connected(peer) as client:
        result = await asyncio.wait_for(client.download_record(FILE_REF), 2)
        assert result == content
        assert len(decode_record_wav(result).audio) == 96000
        assert client._streams == {} and client._pending == {}


@pytest.mark.asyncio
async def test_fragment_budget_rejects_excess_even_with_valid_byte_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "_RECORD_MAX_CHUNKS", 2)

    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        await frame(socket, received, packets(b"abc")[0])
        for index, value in enumerate((b"a", b"b", b"c")):
            await frame(socket, received, chunk_packet(value, index))
        await ordinary(socket, await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError):
            await asyncio.wait_for(client.download_record(FILE_REF), 2)
        assert client._streams == {}
        assert await client.call("get_status", {}) == {"online": True}


def malformed(case: str) -> list[JsonObject]:
    result = packets(b"a")
    if case == "chunk_before_header":
        return result[1:]
    if case == "duplicate_header":
        return [result[0], *result]
    if case == "zero_source":
        result[0]["file_size"] = 0
    elif case == "negative_source":
        result[0]["file_size"] = -1
    elif case == "oversize_source":
        result[0]["file_size"] = MAX_BYTES + 1
    elif case == "string_source":
        result[0]["file_size"] = "1"
    elif case == "bool_source":
        result[0]["file_size"] = True
    elif case == "nonstring_name":
        result[0]["file_name"] = None
    elif case == "unbounded_name":
        result[0]["file_name"] = "a" * 1025
    elif case == "wrong_format":
        result[0]["out_format"] = "mp3"
    elif case == "missing_format":
        result[0].pop("out_format")
    elif case == "wrong_chunk_size":
        result[0]["chunk_size"] = CHUNK - 1
    elif case == "wrong_index":
        result[1]["index"] = 1
    elif case == "bool_index":
        result[1]["index"] = False
    elif case == "empty_chunk":
        result[1]["size"] = 0
    elif case == "oversize_chunk":
        result[1]["size"] = CHUNK + 1
    elif case == "string_chunk_size":
        result[1]["size"] = "1"
    elif case == "wrong_base64_size":
        result[1]["base64_size"] = 3
    elif case == "wrong_data_length":
        result[1]["data"] = "YQ==YQ=="
    elif case == "invalid_base64":
        result[1]["data"] = "!!!!"
    elif case == "noncanonical_base64":
        result[1]["data"] = "YR=="
    elif case == "wrong_decoded_size":
        result[1]["data"] = "YWI="
    elif case == "wrong_count":
        result[2]["total_chunks"] = 2
    elif case == "wrong_total":
        result[2]["total_bytes"] = 2
    elif case == "bool_total":
        result[2]["total_bytes"] = True
    elif case == "duplicate_chunk":
        return [*result[:2], result[1], result[2]]
    elif case == "empty_complete":
        return [
            result[0],
            {
                "type": "response",
                "data_type": "file_complete",
                "total_chunks": 0,
                "total_bytes": 0,
            },
        ]
    elif case == "reset":
        result[1]["type"] = "reset"
    elif case == "truncated_complete":
        return [result[0], result[2]]
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "chunk_before_header",
        "duplicate_header",
        "zero_source",
        "negative_source",
        "oversize_source",
        "string_source",
        "bool_source",
        "nonstring_name",
        "unbounded_name",
        "wrong_format",
        "missing_format",
        "wrong_chunk_size",
        "wrong_index",
        "bool_index",
        "empty_chunk",
        "oversize_chunk",
        "string_chunk_size",
        "wrong_base64_size",
        "wrong_data_length",
        "invalid_base64",
        "noncanonical_base64",
        "wrong_decoded_size",
        "wrong_count",
        "wrong_total",
        "bool_total",
        "duplicate_chunk",
        "empty_complete",
        "reset",
        "truncated_complete",
    ],
)
async def test_malformed_stream_returns_no_bytes_and_preserves_rpc_reader(case: str) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        for packet in malformed(case):
            await frame(socket, received, packet)
        await ordinary(socket, await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError):
            await asyncio.wait_for(client.download_record(FILE_REF), 2)
        assert client._streams == {}
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("max_bytes", [0, -1, MAX_BYTES + 1, True, 1.2])
async def test_invalid_limit_is_rejected_before_connection(max_bytes: object) -> None:
    client = NapCatClient("ws://127.0.0.1:3001", TOKEN)
    with pytest.raises(ValueError):
        await client.download_record(FILE_REF, max_bytes=cast(int, max_bytes))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_ref", ["", "/tmp/a.amr", "../a.amr", "https://a.test/x", r"C:\a.amr", "a\x00.amr"]
)
async def test_record_reference_cannot_be_path_scheme_or_control(file_ref: str) -> None:
    client = NapCatClient("ws://127.0.0.1:3001", TOKEN)
    with pytest.raises(ValueError):
        await client.download_record(file_ref)


@pytest.mark.asyncio
async def test_actual_transcoded_bytes_are_bounded_independently_of_source_hint() -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        for packet in packets(b"converted-ten-bytes", source_size=1):
            await frame(socket, received, packet)
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="limit"):
            await asyncio.wait_for(client.download_record(FILE_REF, max_bytes=2), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["preflight", "stream", "postflight"])
async def test_cancellation_at_each_stage_discards_late_frames_and_releases_both_maps(
    stage: str,
) -> None:
    arrived = asyncio.Event()
    release = asyncio.Event()
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        preflight = await request(socket)
        observed.append(preflight)
        if stage == "preflight":
            arrived.set()
            await release.wait()
            await ordinary(socket, preflight, {"user_id": 10001})
        else:
            await ordinary(socket, preflight, {"user_id": 10001})
            record = await request(socket)
            observed.append(record)
            stream = packets(b"v" * (CHUNK + 1))
            if stage == "stream":
                await frame(socket, record, stream[0])
                await frame(socket, record, stream[1])
                arrived.set()
                await release.wait()
                for packet in stream[2:]:
                    await frame(socket, record, packet)
            else:
                for packet in stream:
                    await frame(socket, record, packet)
                postflight = await request(socket)
                observed.append(postflight)
                arrived.set()
                await release.wait()
                await ordinary(socket, postflight, {"user_id": 10001})
        normal = await request(socket)
        observed.append(normal)
        assert normal["action"] == "get_status"
        await ordinary(socket, normal)
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        download = asyncio.create_task(client.download_record(FILE_REF))
        await asyncio.wait_for(arrived.wait(), 2)
        download.cancel()
        with pytest.raises(asyncio.CancelledError):
            await download
        assert client._streams == {} and client._pending == {}
        assert not any(task.get_name() == "qq-record-frame" for task in asyncio.all_tasks())
        release.set()
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
        assert sum(value["action"] == "download_file_record_stream" for value in observed) == (
            0 if stage == "preflight" else 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("after_header", [False, True])
async def test_disconnect_before_terminal_completion_wakes_download(after_header: bool) -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        if after_header:
            for packet in packets(b"a")[:-1]:
                await frame(socket, received, packet)
        await socket.close()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="connection lost"):
            await asyncio.wait_for(client.download_record(FILE_REF), 2)
        assert client._streams == {}


@pytest.mark.asyncio
async def test_valid_terminal_response_survives_immediate_socket_close() -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        for packet in packets(b"completed"):
            await frame(socket, received, packet)
        await socket.close()

    async with connected(peer) as client:
        assert await asyncio.wait_for(client.download_record(FILE_REF), 2) == b"completed"


@pytest.mark.asyncio
async def test_account_is_verified_before_and_after_record_stream() -> None:
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await ordinary(socket, observed[0], {"user_id": 10001})
        observed.append(await request(socket))
        for packet in packets(b"account-bound"):
            await frame(socket, observed[1], packet)
        observed.append(await request(socket))
        await ordinary(socket, observed[2], {"user_id": 10001})
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        assert await asyncio.wait_for(client.download_record(FILE_REF), 2) == b"account-bound"
        assert [value["action"] for value in observed] == [
            "get_login_info",
            "download_file_record_stream",
            "get_login_info",
        ]


@pytest.mark.asyncio
async def test_changed_account_before_stream_sends_no_record_rpc() -> None:
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await ordinary(socket, observed[0], {"user_id": 99999})
        observed.append(await request(socket))
        await ordinary(socket, observed[1])
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        with pytest.raises(NapCatRejected, match="account changed"):
            await asyncio.wait_for(client.download_record(FILE_REF), 2)
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
        assert [value["action"] for value in observed] == ["get_login_info", "get_status"]
        assert client._pending == {} and client._streams == {}


@pytest.mark.asyncio
async def test_account_switch_during_stream_rejects_completed_audio() -> None:
    streaming = asyncio.Event()
    switch = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await ordinary(socket, await request(socket), {"user_id": 10001})
        received = await request(socket)
        stream = packets(b"a" * (CHUNK + 1))
        for packet in stream[:2]:
            await frame(socket, received, packet)
        streaming.set()
        await switch.wait()
        for packet in stream[2:]:
            await frame(socket, received, packet)
        await ordinary(socket, await request(socket), {"user_id": 99999})
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        download = asyncio.create_task(client.download_record(FILE_REF))
        await asyncio.wait_for(streaming.wait(), 2)
        switch.set()
        with pytest.raises(NapCatRejected, match="account changed during"):
            await asyncio.wait_for(download, 2)
        assert client._streams == {} and client._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["image", "record"])
async def test_image_and_record_share_one_active_stream(first: str) -> None:
    arrived = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await request(socket)
        arrived.set()
        await socket.wait_closed()

    async with connected(peer) as client:
        operation = client.download_image if first == "image" else client.download_record
        active = asyncio.create_task(operation(FILE_REF))
        try:
            await asyncio.wait_for(arrived.wait(), 2)
            with pytest.raises(NapCatRejected, match="capacity"):
                await client.download_record(FILE_REF)
            with pytest.raises(NapCatRejected, match="capacity"):
                await client.download_image(FILE_REF)
            assert len(client._streams) == 1
        finally:
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
        assert client._streams == {}


@pytest.mark.asyncio
async def test_record_and_ordinary_calls_share_32_total_capacity() -> None:
    arrived = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        for _ in range(32):
            await request(socket)
        arrived.set()
        await socket.wait_closed()

    async with connected(peer) as client:
        active: list[asyncio.Task[JsonObject | bytes]] = [
            asyncio.create_task(client.call("get_status", {})) for _ in range(31)
        ]
        active.append(asyncio.create_task(client.download_record(FILE_REF)))
        try:
            await asyncio.wait_for(arrived.wait(), 2)
            assert len(client._pending) + len(client._streams) == 32
            with pytest.raises(NapCatRejected, match="capacity"):
                await client.call("get_status", {})
            with pytest.raises(NapCatRejected, match="capacity"):
                await client.download_record(FILE_REF)
        finally:
            for task in active:
                task.cancel()
            assert all(
                isinstance(value, asyncio.CancelledError)
                for value in await asyncio.gather(
                    *active,
                    return_exceptions=True,
                )
            )
        assert client._pending == {} and client._streams == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [False, True])
async def test_total_deadline_includes_preflight_and_sends_once(
    bound: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert client_module._RECORD_TIMEOUT_SECONDS == 20
    monkeypatch.setattr(client_module, "_RECORD_TIMEOUT_SECONDS", 0.02)
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        if bound:
            client.bind_account("10001")
        with pytest.raises(NapCatError, match="did not complete"):
            await asyncio.wait_for(client.download_record(FILE_REF), 2)
        assert [value["action"] for value in observed] == [
            "get_login_info" if bound else "download_file_record_stream",
        ]
        assert client._streams == {} and client._pending == {}


@pytest.mark.asyncio
async def test_oversized_frame_never_enters_stream_queue() -> None:
    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        await frame(socket, received, {"type": "stream", "data": "x" * (91 * 1024)})
        await ordinary(socket, await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="frame exceeds"):
            await asyncio.wait_for(client.download_record(FILE_REF), 2)
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}


@pytest.mark.asyncio
async def test_finite_queue_overflow_fails_record_without_blocking_normal_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    sent = asyncio.Event()
    original = client_module._ImageStream.next_frame

    async def blocked_next(stream: client_module._ImageStream) -> JsonObject:
        await gate.wait()
        return await original(stream)

    monkeypatch.setattr(client_module._ImageStream, "next_frame", blocked_next)

    async def peer(socket: ServerConnection) -> None:
        received = await request(socket)
        for _ in range(83):
            await frame(socket, received, packets(b"a")[0])
        sent.set()
        await ordinary(socket, await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        download = asyncio.create_task(client.download_record(FILE_REF))
        try:
            await asyncio.wait_for(sent.wait(), 2)
            assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
            stream = next(iter(client._streams.values()))
            assert stream.frames.maxsize == 82 and stream.frames.qsize() == 82
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
@pytest.mark.parametrize("stream_mode", ["stream-action", "normal-action"])
async def test_provider_error_does_not_expose_raw_private_diagnostics(stream_mode: str) -> None:
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
                    "message": "private owner and token and file path",
                    "wording": "provider-secret",
                }
            )
        )
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatRejected) as failure:
            await asyncio.wait_for(client.download_record(FILE_REF), 2)
        assert str(failure.value) == "QQ rejected the record download"
        assert client._streams == {} and client._pending == {}


@pytest.mark.asyncio
async def test_normal_success_response_cannot_complete_record_stream() -> None:
    async def peer(socket: ServerConnection) -> None:
        await ordinary(socket, await request(socket))
        await socket.wait_closed()

    async with connected(peer) as client:
        with pytest.raises(NapCatError, match="invalid record stream response"):
            await asyncio.wait_for(client.download_record(FILE_REF), 2)


@pytest.mark.asyncio
async def test_client_close_cleans_up_record_frame_consumer() -> None:
    arrived = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await request(socket)
        arrived.set()
        await socket.wait_closed()

    async with connected(peer) as client:
        download = asyncio.create_task(client.download_record(FILE_REF))
        await asyncio.wait_for(arrived.wait(), 2)
        await client.close()
        with pytest.raises(NapCatError, match="connection lost"):
            await asyncio.wait_for(download, 2)
        assert client._streams == {}
        assert not any(task.get_name() == "qq-record-frame" for task in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_record_rechecks_shared_stream_capacity_after_account_preflight() -> None:
    record_preflight = asyncio.Event()
    image_active = asyncio.Event()
    release_record = asyncio.Event()
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        record_login = await request(socket)
        observed.append(record_login)
        record_preflight.set()
        image_login = await request(socket)
        observed.append(image_login)
        await ordinary(socket, image_login, {"user_id": 10001})
        image = await request(socket)
        observed.append(image)
        assert image["action"] == "download_file_image_stream"
        image_active.set()
        await release_record.wait()
        await ordinary(socket, record_login, {"user_id": 10001})
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        record = asyncio.create_task(client.download_record(FILE_REF))
        await asyncio.wait_for(record_preflight.wait(), 2)
        image = asyncio.create_task(client.download_image("admitted-owner-image.png"))
        try:
            await asyncio.wait_for(image_active.wait(), 2)
            release_record.set()
            with pytest.raises(NapCatRejected, match="capacity"):
                await asyncio.wait_for(record, 2)
            assert all(value["action"] != "download_file_record_stream" for value in observed)
            assert len(client._streams) == 1
        finally:
            release_record.set()
            record.cancel()
            image.cancel()
            await asyncio.gather(record, image, return_exceptions=True)
        assert client._streams == {} and client._pending == {}


@pytest.mark.asyncio
async def test_postflight_cannot_exceed_32_total_capacity_or_return_unverified_bytes() -> None:
    streaming = asyncio.Event()
    all_calls = asyncio.Event()
    observed: list[JsonObject] = []

    async def peer(socket: ServerConnection) -> None:
        observed.append(await request(socket))
        await ordinary(socket, observed[0], {"user_id": 10001})
        record = await request(socket)
        observed.append(record)
        stream = packets(b"account-must-be-checked")
        await frame(socket, record, stream[0])
        streaming.set()
        for _ in range(31):
            observed.append(await request(socket))
        all_calls.set()
        for packet in stream[1:]:
            await frame(socket, record, packet)
        last = await request(socket)
        observed.append(last)
        await ordinary(socket, last)
        await socket.wait_closed()

    async with connected(peer) as client:
        client.bind_account("10001")
        download = asyncio.create_task(client.download_record(FILE_REF))
        await asyncio.wait_for(streaming.wait(), 2)
        calls = [asyncio.create_task(client.call("get_status", {})) for _ in range(31)]
        try:
            await asyncio.wait_for(all_calls.wait(), 2)
            with pytest.raises(NapCatRejected, match="capacity"):
                await asyncio.wait_for(download, 2)
        finally:
            for task in calls:
                task.cancel()
            download.cancel()
            await asyncio.gather(download, *calls, return_exceptions=True)
        assert client._streams == {} and client._pending == {}
        assert await asyncio.wait_for(client.call("get_status", {}), 2) == {"online": True}
        assert sum(value["action"] == "get_login_info" for value in observed) == 1
        assert sum(value["action"] == "download_file_record_stream" for value in observed) == 1
