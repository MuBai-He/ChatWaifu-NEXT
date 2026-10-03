"""Bounded OneBot 11 WebSocket transport; no Runtime domain or SQL dependencies."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

from chatwaifu_protocol.base import JsonObject, JsonValue
from websockets.asyncio.client import ClientConnection, connect

_SOCKET_LOGGER = logging.getLogger("qq.websocket")
_SOCKET_LOGGER.disabled = (
    True  # WebSocket debug frames contain credentials and private message content.
)
_IMAGE_MAX_BYTES = 5 * 1024 * 1024
_IMAGE_CHUNK_BYTES = 64 * 1024
_IMAGE_TIMEOUT_SECONDS = 20
_RECORD_TIMEOUT_SECONDS = 20
_RECORD_MAX_CHUNKS = 256
_IMAGE_MAX_FRAME_BYTES = 90 * 1024
# A complete 5 MiB response can arrive before its consumer is scheduled.
_IMAGE_QUEUE_FRAMES = _IMAGE_MAX_BYTES // _IMAGE_CHUNK_BYTES + 2


class NapCatError(RuntimeError):
    """Sanitized error; raw provider messages can contain tokens or user content."""


class NapCatRejected(NapCatError):
    """The provider explicitly rejected an operation."""


class NapCatUncertain(NapCatError):
    """An API request may have executed; never retry a send automatically."""


def validate_image_file_ref(value: object) -> str:
    """Accept only an admitted provider basename, never a URL or filesystem path.

    NapCat also searches its own file registry by filename. The caller must first
    authorize the account/owner/message that supplied this reference.
    """
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value in {".", ".."}
        or len(value) > 255
        or len(value.encode("utf-8")) > 255
        or any(
            character in "/\\:" or ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise ValueError("QQ image reference must be a provider filename")
    return value


@dataclass(slots=True)
class _ImageStream:
    frames: asyncio.Queue[JsonObject]
    failure: asyncio.Future[NapCatError]
    terminal_received: bool = False
    media_kind: str = "image"

    def fail(self, error: NapCatError) -> None:
        if not self.failure.done():
            self.failure.set_result(error)

    async def next_frame(self) -> JsonObject:
        queued = asyncio.create_task(self.frames.get(), name=f"qq-{self.media_kind}-frame")
        try:
            await asyncio.wait((queued, self.failure), return_when=asyncio.FIRST_COMPLETED)
            if self.failure.done():
                raise self.failure.result()
            return queued.result()
        finally:
            if not queued.done():
                queued.cancel()
            await asyncio.gather(queued, return_exceptions=True)


def _image_integer(data: JsonObject, key: str, *, media_kind: str = "image") -> int:
    value = data.get(key)
    if type(value) is not int:
        raise NapCatError(f"QQ returned invalid {media_kind} stream metadata")
    return value


def _image_packet(response: JsonObject, *, media_kind: str = "image") -> JsonObject:
    if (
        response.get("status") != "ok"
        or type(response.get("retcode")) is not int
        or response.get("retcode") != 0
    ):
        raise NapCatRejected(f"QQ rejected the {media_kind} download")
    data = response.get("data")
    if response.get("stream") != "stream-action" or not isinstance(data, dict):
        raise NapCatError(f"QQ returned an invalid {media_kind} stream response")
    return cast(JsonObject, data)


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
        self._streams: dict[str, _ImageStream] = {}
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
        for stream in self._streams.values():
            if not stream.terminal_received:
                stream.fail(NapCatError(f"QQ connection lost during {stream.media_kind} download"))

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
                if isinstance(echo, str) and echo in self._streams:
                    stream = self._streams[echo]
                    if stream.failure.done() or stream.terminal_received:
                        continue
                    frame_size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
                    if frame_size > _IMAGE_MAX_FRAME_BYTES:
                        stream.fail(
                            NapCatError(f"QQ {stream.media_kind} stream frame exceeds its limit")
                        )
                        continue
                    try:
                        stream.frames.put_nowait(event)
                    except asyncio.QueueFull:
                        stream.fail(NapCatError(f"QQ {stream.media_kind} stream capacity exceeded"))
                    else:
                        packet = event.get("data")
                        if event.get("status") == "failed" or (
                            isinstance(packet, dict)
                            and packet.get("type") == "response"
                            and packet.get("data_type") == "file_complete"
                        ):
                            # Like a resolved RPC, a queued final response survives
                            # immediate socket teardown; the consumer still validates it.
                            stream.terminal_received = True
                elif isinstance(echo, str) and echo in self._pending:
                    future = self._pending[echo]
                    if not future.done():
                        if event.get("stream") == "stream-action":
                            future.set_exception(
                                NapCatUncertain("QQ returned an unexpected stream")
                            )
                        else:
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
        if len(self._pending) + len(self._streams) >= 32:
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

    async def download_image(self, file_ref: str, *, max_bytes: int = _IMAGE_MAX_BYTES) -> bytes:
        """Download bounded image bytes over the authenticated pinned NapCat stream API.

        This only checks the wire stream. MIME, decoded image dimensions, animation
        and account/owner admission remain the media loader's responsibilities.
        """
        validate_image_file_ref(file_ref)
        if type(max_bytes) is not int or not 0 < max_bytes <= _IMAGE_MAX_BYTES:
            raise ValueError("QQ image byte limit must be between 1 byte and 5 MiB")
        if self._socket is None or (self._reader and self._reader.done()):
            raise NapCatRejected("QQ connection is unavailable before image download")
        echo = uuid4().hex
        stream: _ImageStream | None = None
        try:
            async with asyncio.timeout(_IMAGE_TIMEOUT_SECONDS):
                if self._streams or len(self._pending) >= 32:
                    raise NapCatRejected("QQ request capacity exceeded")
                if self._account is not None:
                    login = await self.call("get_login_info", {})
                    if str(login.get("user_id")) != self._account:
                        raise NapCatRejected("QQ account changed before image download")
                # Preflight awaits a separate RPC; capacity may have changed meanwhile.
                if self._streams or len(self._pending) >= 32:
                    raise NapCatRejected("QQ request capacity exceeded")
                stream = _ImageStream(
                    asyncio.Queue(maxsize=_IMAGE_QUEUE_FRAMES),
                    asyncio.get_running_loop().create_future(),
                )
                self._streams[echo] = stream
                await self._socket.send(
                    json.dumps(
                        {
                            "action": "download_file_image_stream",
                            "params": {"file": file_ref, "chunk_size": _IMAGE_CHUNK_BYTES},
                            "echo": echo,
                        }
                    )
                )
                header = _image_packet(await stream.next_frame())
                if header.get("type") != "stream" or header.get("data_type") != "file_info":
                    raise NapCatError("QQ image stream has no file header")
                expected_size = _image_integer(header, "file_size")
                if not 0 < expected_size <= max_bytes:
                    raise NapCatError("QQ image size exceeds its limit or is empty")
                if _image_integer(header, "chunk_size") != _IMAGE_CHUNK_BYTES:
                    raise NapCatError("QQ image stream has an invalid chunk size")
                name = header.get("file_name")
                if not isinstance(name, str) or len(name) > 1024:
                    raise NapCatError("QQ image stream returned an invalid display filename")
                decoded = bytearray()
                chunks = 0
                while True:
                    packet = _image_packet(await stream.next_frame())
                    kind = packet.get("data_type")
                    if packet.get("type") == "response" and kind == "file_complete":
                        if (
                            _image_integer(packet, "total_chunks") != chunks
                            or _image_integer(packet, "total_bytes") != len(decoded)
                            or len(decoded) != expected_size
                        ):
                            raise NapCatError("QQ image stream ended with inconsistent totals")
                        if self._account is not None:
                            login = await self.call("get_login_info", {})
                            if str(login.get("user_id")) != self._account:
                                raise NapCatRejected("QQ account changed during image download")
                        return bytes(decoded)
                    if packet.get("type") != "stream" or kind != "file_chunk":
                        raise NapCatError("QQ image stream has an invalid packet sequence")
                    size = _image_integer(packet, "size")
                    if (
                        _image_integer(packet, "index") != chunks
                        or size != min(_IMAGE_CHUNK_BYTES, expected_size - len(decoded))
                        or size <= 0
                    ):
                        raise NapCatError("QQ image stream has an invalid chunk sequence")
                    encoded = packet.get("data")
                    if (
                        not isinstance(encoded, str)
                        or len(encoded) != ((size + 2) // 3) * 4
                        or _image_integer(packet, "base64_size") != len(encoded)
                    ):
                        raise NapCatError("QQ image stream has an invalid encoded length")
                    try:
                        chunk = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error):
                        raise NapCatError("QQ image stream contains invalid base64") from None
                    if len(chunk) != size or base64.b64encode(chunk).decode("ascii") != encoded:
                        raise NapCatError("QQ image stream contains invalid base64")
                    decoded.extend(chunk)
                    chunks += 1
        except asyncio.CancelledError:
            raise
        except NapCatError:
            raise
        except Exception:
            raise NapCatError("QQ image download did not complete") from None
        finally:
            self._streams.pop(echo, None)
            if stream is not None and not stream.failure.done():
                stream.failure.cancel()

    async def download_record(self, file_ref: str, *, max_bytes: int = _IMAGE_MAX_BYTES) -> bytes:
        """Read bounded converted WAV bytes from an owner-admitted provider reference.

        NapCat's header may report compressed source size. Validate the returned
        chunk totals independently; WAV decoding and duration limits belong to
        the media loader. No provider filename is opened by Runtime.
        """
        validate_image_file_ref(file_ref)
        if type(max_bytes) is not int or not 0 < max_bytes <= _IMAGE_MAX_BYTES:
            raise ValueError("QQ record byte limit must be between 1 byte and 5 MiB")
        if self._socket is None or (self._reader and self._reader.done()):
            raise NapCatRejected("QQ connection is unavailable before record download")
        account = self._account
        echo = uuid4().hex
        stream: _ImageStream | None = None
        try:
            async with asyncio.timeout(_RECORD_TIMEOUT_SECONDS):
                if self._streams or len(self._pending) >= 32:
                    raise NapCatRejected("QQ request capacity exceeded")
                if account is not None:
                    login = await self.call("get_login_info", {})
                    if str(login.get("user_id")) != account or self._account != account:
                        raise NapCatRejected("QQ account changed before record download")
                if self._streams or len(self._pending) >= 32:
                    raise NapCatRejected("QQ request capacity exceeded")
                stream = _ImageStream(
                    asyncio.Queue(maxsize=_IMAGE_QUEUE_FRAMES),
                    asyncio.get_running_loop().create_future(),
                    media_kind="record",
                )
                self._streams[echo] = stream
                await self._socket.send(
                    json.dumps(
                        {
                            "action": "download_file_record_stream",
                            "params": {
                                "file": file_ref,
                                "chunk_size": _IMAGE_CHUNK_BYTES,
                                "out_format": "wav",
                            },
                            "echo": echo,
                        }
                    )
                )
                header = _image_packet(await stream.next_frame(), media_kind="record")
                if header.get("type") != "stream" or header.get("data_type") != "file_info":
                    raise NapCatError("QQ record stream has no file header")
                source_size = _image_integer(header, "file_size", media_kind="record")
                if not 0 < source_size <= max_bytes:
                    raise NapCatError("QQ record source size exceeds its limit or is empty")
                if _image_integer(header, "chunk_size", media_kind="record") != _IMAGE_CHUNK_BYTES:
                    raise NapCatError("QQ record stream has an invalid chunk size")
                if header.get("out_format") != "wav":
                    raise NapCatError("QQ record stream returned an invalid output format")
                name = header.get("file_name")
                if not isinstance(name, str) or len(name) > 1024:
                    raise NapCatError("QQ record stream returned an invalid display filename")
                decoded = bytearray()
                chunks = 0
                while True:
                    packet = _image_packet(await stream.next_frame(), media_kind="record")
                    kind = packet.get("data_type")
                    if packet.get("type") == "response" and kind == "file_complete":
                        if (
                            chunks == 0
                            or _image_integer(packet, "total_chunks", media_kind="record") != chunks
                            or _image_integer(packet, "total_bytes", media_kind="record")
                            != len(decoded)
                        ):
                            raise NapCatError("QQ record stream ended with inconsistent totals")
                        if account is not None:
                            login = await self.call("get_login_info", {})
                            if str(login.get("user_id")) != account or self._account != account:
                                raise NapCatRejected("QQ account changed during record download")
                        return bytes(decoded)
                    if packet.get("type") != "stream" or kind != "file_chunk":
                        raise NapCatError("QQ record stream has an invalid packet sequence")
                    size = _image_integer(packet, "size", media_kind="record")
                    if (
                        _image_integer(packet, "index", media_kind="record") != chunks
                        or not 0 < size <= _IMAGE_CHUNK_BYTES
                        or chunks >= _RECORD_MAX_CHUNKS
                    ):
                        raise NapCatError("QQ record stream has an invalid chunk sequence")
                    if len(decoded) + size > max_bytes:
                        raise NapCatError("QQ converted record exceeds its byte limit")
                    encoded = packet.get("data")
                    if (
                        not isinstance(encoded, str)
                        or len(encoded) != ((size + 2) // 3) * 4
                        or _image_integer(packet, "base64_size", media_kind="record")
                        != len(encoded)
                    ):
                        raise NapCatError("QQ record stream has an invalid encoded length")
                    try:
                        chunk = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error):
                        raise NapCatError("QQ record stream contains invalid base64") from None
                    if len(chunk) != size or base64.b64encode(chunk).decode("ascii") != encoded:
                        raise NapCatError("QQ record stream contains invalid base64")
                    decoded.extend(chunk)
                    chunks += 1
        except asyncio.CancelledError:
            raise
        except NapCatError:
            raise
        except Exception:
            raise NapCatError("QQ record download did not complete") from None
        finally:
            self._streams.pop(echo, None)
            if stream is not None and not stream.failure.done():
                stream.failure.cancel()

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
