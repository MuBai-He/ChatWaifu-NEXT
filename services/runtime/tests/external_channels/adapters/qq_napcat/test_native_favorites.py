"""Native favorites use actual RPC framing, owned uploads and acceptance readback."""
# pyright: reportPrivateUsage=false

import asyncio
import base64
import hashlib
import io
import json
import random
from dataclasses import dataclass, field
from typing import cast

import pytest
from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import (
    NapCatError,
    NapCatUncertain,
)
from PIL import Image
from websockets.asyncio.server import ServerConnection

from services.runtime.tests.external_channels.adapters.qq_napcat.test_client import connected


def _png() -> bytes:
    image = Image.frombytes("RGB", (256, 256), random.Random(42).randbytes(256 * 256 * 3))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@dataclass
class _FavoritePeer:
    mode: str
    account: str = "900"
    allowed: bool = True
    calls: list[JsonObject] = field(default_factory=list[JsonObject])
    chunks: list[bytes] = field(default_factory=list[bytes])
    stream: str = ""
    filename: str = ""
    checkpointed: bool = False
    added: bool = False
    received_chunk: asyncio.Event = field(default_factory=asyncio.Event)
    reset: asyncio.Event = field(default_factory=asyncio.Event)

    async def guard(self) -> bool:
        return self.allowed

    async def checkpoint(self) -> None:
        self.checkpointed = True

    async def handle(self, socket: ServerConnection) -> None:
        async for raw in socket:
            request = cast(JsonObject, json.loads(raw))
            self.calls.append(request)
            params = cast(JsonObject, request["params"])
            action = request["action"]
            data: JsonValue = {}
            status, retcode = "ok", 0
            if action == "get_login_info":
                data = {"user_id": self.account}
            elif action == "upload_file_stream":
                if params.get("reset"):
                    assert params["stream_id"] == self.stream
                    assert set(params) == {"stream_id", "reset"}
                    self.reset.set()
                    status, retcode = "failed", 1  # Pinned NapCat's successful reset convention.
                elif params.get("is_complete"):
                    content = b"".join(self.chunks)
                    sha = hashlib.sha256(content).hexdigest()
                    data = {
                        "type": "response",
                        "status": "file_complete",
                        "stream_id": self.stream,
                        "received_chunks": len(self.chunks),
                        "total_chunks": len(self.chunks),
                        "file_path": "/tmp/" + self.filename,
                        "file_size": len(content),
                        "sha256": sha,
                    }
                    if self.mode == "bad_sha":
                        data["sha256"] = "0" * 64
                    if self.mode == "foreign_path":
                        data["file_path"] = "/tmp/someone-elses-file.png"
                    if self.mode == "bad_packet_type":
                        data["type"] = "stream"
                    if self.mode == "bad_chunk_count":
                        data["received_chunks"] = 0
                    if self.mode == "revoked":
                        self.allowed = False
                    if self.mode == "account_changed":
                        self.account = "901"
                else:
                    self.stream = cast(str, params["stream_id"])
                    self.filename = cast(str, params["filename"])
                    assert self.filename == f"cw2-sticker-{self.stream}.png"
                    assert params["chunk_index"] == len(self.chunks)
                    chunk = base64.b64decode(cast(str, params["chunk_data"]), validate=True)
                    assert 0 < len(chunk) <= 65536 and params["file_retention"] == 120_000
                    self.chunks.append(chunk)
                    self.received_chunk.set()
                    if self.mode == "cancelled":
                        continue
                    data = {
                        "type": "stream",
                        "status": "chunk_received",
                        "stream_id": self.stream,
                        "received_chunks": len(self.chunks),
                        "total_chunks": params["total_chunks"],
                    }
            elif action == "add_custom_face":
                assert self.checkpointed and self.allowed and self.account == "900"
                assert params == {"file": "/tmp/" + self.filename, "is_origin": True}
                self.added = True
            elif action == "fetch_custom_face_detail":
                data = (
                    [{"md5": hashlib.md5(b"".join(self.chunks), usedforsecurity=False).hexdigest()}]
                    if self.added and self.mode != "missing_readback"
                    else []
                )
            else:
                raise AssertionError("Unexpected native favorite action")
            await socket.send(
                json.dumps(
                    {
                        "echo": request["echo"],
                        "status": status,
                        "retcode": retcode,
                        "data": data,
                        "stream": (
                            "stream-action"
                            if action == "upload_file_stream" and self.mode != "normal_upload"
                            else "normal-action"
                        ),
                    }
                )
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "confirmed",
        "missing_readback",
        "bad_sha",
        "foreign_path",
        "bad_packet_type",
        "bad_chunk_count",
        "normal_upload",
        "revoked",
        "account_changed",
        "cancelled",
    ],
)
async def test_native_favorite_requires_owned_bytes_current_authority_and_readback(
    mode: str,
) -> None:
    peer = _FavoritePeer(mode)
    image = _png()
    async with connected(peer.handle) as client:
        client.bind_account("900")
        if mode == "cancelled":
            task = asyncio.create_task(
                client.add_sticker_favorite(
                    image,
                    before_add=peer.guard,
                    checkpoint=peer.checkpoint,
                )
            )
            await asyncio.wait_for(peer.received_chunk.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(peer.reset.wait(), 2)
            assert not client._pending
        elif mode in {"confirmed", "missing_readback"}:
            assert await client.add_sticker_favorite(
                image,
                before_add=peer.guard,
                checkpoint=peer.checkpoint,
            ) is (mode == "confirmed")
            assert b"".join(peer.chunks) == image and len(peer.chunks) > 1
        else:
            with pytest.raises(NapCatError):
                await client.add_sticker_favorite(
                    image, before_add=peer.guard, checkpoint=peer.checkpoint
                )
    assert peer.added is (mode in {"confirmed", "missing_readback"})
    assert peer.checkpointed is peer.added
    assert peer.reset.is_set() is (
        mode
        in {
            "bad_sha",
            "foreign_path",
            "bad_packet_type",
            "bad_chunk_count",
            "normal_upload",
            "cancelled",
        }
    )
    assert not any(call["action"] == "clean_stream_temp_file" for call in peer.calls)


@pytest.mark.asyncio
async def test_upload_stream_support_does_not_accept_stream_for_ordinary_rpc() -> None:
    async def handle(socket: ServerConnection) -> None:
        async for raw in socket:
            request = json.loads(raw)
            await socket.send(
                json.dumps(
                    {
                        "echo": request["echo"],
                        "status": "ok",
                        "retcode": 0,
                        "data": {"type": "stream", "user_id": "900"},
                        "stream": "stream-action",
                    }
                )
            )

    async with connected(handle) as client:
        with pytest.raises(NapCatUncertain):
            await client.call("get_login_info", {})
