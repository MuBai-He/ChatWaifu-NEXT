"""File bytes, current authorization, cancellation and uncertain sends over real WebSockets."""

# pyright: reportPrivateUsage=false
import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import cast

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import (
    NapCatError,
)
from websockets.asyncio.server import ServerConnection

from services.runtime.tests.external_channels.adapters.qq_napcat.test_client import connected


@dataclass
class FilePeer:
    mode: str
    allowed: bool = True
    checkpointed: bool = False
    sends: int = 0
    stream: str = ""
    name: str = ""
    chunks: list[bytes] = field(default_factory=list[bytes])
    upload_started: asyncio.Event = field(default_factory=asyncio.Event)
    reset: asyncio.Event = field(default_factory=asyncio.Event)

    async def guard(self) -> bool:
        return self.allowed

    async def checkpoint(self) -> None:
        self.checkpointed = True
        if self.mode == "revoked":
            self.allowed = False

    async def handle(self, socket: ServerConnection) -> None:
        async for raw in socket:
            request = cast(JsonObject, json.loads(raw))
            params = cast(JsonObject, request["params"])
            data: JsonObject = {}
            stream = "normal-action"
            if request["action"] == "get_login_info":
                data = {"user_id": "900"}
            elif request["action"] == "upload_file_stream":
                stream = "stream-action"
                if params.get("reset"):
                    assert params == {"stream_id": self.stream, "reset": True}
                    self.reset.set()
                elif params.get("is_complete"):
                    content = b"".join(self.chunks)
                    data = {
                        "type": "response",
                        "status": "file_complete",
                        "stream_id": self.stream,
                        "file_path": "/tmp/"
                        + (self.name if self.mode != "foreign" else "other.txt"),
                        "file_size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest()
                        if self.mode != "corrupt"
                        else "0" * 64,
                    }
                else:
                    self.stream, self.name = str(params["stream_id"]), str(params["filename"])
                    self.chunks.append(base64.b64decode(str(params["chunk_data"]), validate=True))
                    self.upload_started.set()
                    if self.mode == "cancel":
                        continue
                    data = {
                        "type": "stream",
                        "status": "chunk_received",
                        "stream_id": self.stream,
                        "received_chunks": len(self.chunks),
                        "total_chunks": params["total_chunks"],
                    }
            elif request["action"] == "upload_private_file":
                assert self.checkpointed and self.allowed
                assert params == {
                    "user_id": "999",
                    "file": "/tmp/" + self.name,
                    "name": "result.docx",
                }
                self.sends += 1
                if self.mode == "unknown":
                    await socket.close()
                    return
                data = {} if self.mode == "no_receipt" else {"message_id": 123}
            else:
                raise AssertionError("unreviewed file action")
            await socket.send(
                json.dumps(
                    {
                        "echo": request["echo"],
                        "status": "ok",
                        "retcode": 0,
                        "data": data,
                        "stream": stream,
                    }
                )
            )


@pytest.mark.parametrize(
    "mode", ["receipt", "no_receipt", "foreign", "corrupt", "revoked", "unknown", "cancel"]
)
async def test_file_transfer_never_sends_unverified_or_revoked_bytes(mode: str) -> None:
    peer = FilePeer(mode)
    content = b"verified-document-" * 9000
    async with connected(peer.handle) as client:
        client.bind_account("900")
        operation = client.send_file(
            "999", content, "result.docx", before_send=peer.guard, checkpoint=peer.checkpoint
        )
        if mode == "cancel":
            task = asyncio.create_task(operation)
            await asyncio.wait_for(peer.upload_started.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(peer.reset.wait(), 2)
        elif mode in {"receipt", "no_receipt"}:
            assert await operation == ("123" if mode == "receipt" else None)
            assert b"".join(peer.chunks) == content
        else:
            with pytest.raises(NapCatError):
                await operation
        assert not client._pending
    assert peer.sends == (1 if mode in {"receipt", "no_receipt", "unknown"} else 0)
    assert peer.reset.is_set() is (mode in {"foreign", "corrupt", "cancel"})
