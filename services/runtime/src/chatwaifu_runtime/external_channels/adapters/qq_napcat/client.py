"""Bounded OneBot 11 WebSocket transport; no Runtime domain or SQL dependencies."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

from chatwaifu_protocol.base import JsonObject, JsonValue
from websockets.asyncio.client import ClientConnection, connect
from websockets.protocol import State

from .expressions import FACE_LABELS
from .groups import (
    GROUP_NOTICE_TYPES,
    NapCatGroupMemberList,
    NapCatGroupMembershipNotice,
    normalize_group_notice,
    qq_group_identifier,
)

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
_RPC_TIMEOUT_SECONDS = 10
_GROUP_TIMEOUT_SECONDS = 20


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
        self._upload_requests: set[str] = set()
        self._streams: dict[str, _ImageStream] = {}
        self._events: asyncio.Queue[JsonObject | None] = asyncio.Queue(maxsize=64)
        self._account: str | None = None
        self._account_revision = 0
        self._group_observation_epoch = 0
        self._pending_group_notices = 0
        self._group_transport_revoked = False
        self._on_group_notice: Callable[[NapCatGroupMembershipNotice], None] | None = None
        self._on_group_transport_invalidated: Callable[[], None] | None = None

    def set_group_observers(
        self,
        *,
        membership_notice: Callable[[NapCatGroupMembershipNotice], None],
        transport_invalidated: Callable[[], None],
    ) -> None:
        """Install synchronous fences; callbacks must not perform I/O or await.

        The host commits durable pauses separately. Reader callbacks revoke work
        even while its ordinary event consumer is occupied by private media.
        """
        self._on_group_notice = membership_notice
        self._on_group_transport_invalidated = transport_invalidated

    def _revoke_group_transport(self) -> None:
        if self._group_transport_revoked:
            return
        self._group_transport_revoked = True
        self._group_observation_epoch += 1
        if self._on_group_transport_invalidated is not None:
            try:
                self._on_group_transport_invalidated()
            except Exception:
                # A broken host observer cannot keep the transport authorized or
                # prevent socket/reader cleanup. Never log callback content.
                logging.getLogger(__name__).error("QQ group transport observer failed")

    def bind_account(self, account: str) -> None:
        was_revoked = self._group_transport_revoked
        if self._account is not None:
            self._revoke_group_transport()
        self._account = account
        self._account_revision += 1
        # An explicit rebind invalidates host route authority, while this
        # property remains an observation of the still-live socket. The host
        # must pause routes and require a new operator authorization.
        self._group_transport_revoked = was_revoked

    @property
    def bound_account(self) -> str | None:
        """Transport binding only; this does not prove login or route authority."""
        return self._account

    @property
    def group_dispatch_ready(self) -> bool:
        """Fail closed on disconnected transport or unconsumed membership notices.

        The host must separately authorize its fixed route and audience. This
        observation cannot prove current QQ membership or atomic send safety.
        """
        return (
            self._account is not None
            and qq_group_identifier(self._account) == self._account
            and self._socket is not None
            and self._socket.state is State.OPEN
            and self._reader is not None
            and not self._reader.done()
            and not self._reader.cancelling()
            and self._pending_group_notices == 0
            and not self._group_transport_revoked
        )

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
        try:
            self._revoke_group_transport()
        finally:
            if self._reader:
                self._reader.cancel()
            try:
                if self._socket:
                    await self._socket.close()
            finally:
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
                        if (
                            event.get("stream") == "stream-action"
                            and echo not in self._upload_requests
                        ):
                            future.set_exception(
                                NapCatUncertain("QQ returned an unexpected stream")
                            )
                        elif echo in self._upload_requests and len(raw) > 16_384:
                            future.set_exception(
                                NapCatError("QQ upload response exceeds its limit")
                            )
                        else:
                            future.set_result(event)
                elif event.get("post_type") == "message":
                    # Overflow closes the connection instead of dropping an admitted event silently.
                    self._events.put_nowait(event)
                elif (
                    event.get("post_type") == "notice"
                    and isinstance(event.get("notice_type"), str)
                    and event.get("notice_type") in GROUP_NOTICE_TYPES
                    and self._account is not None
                ):
                    group_id = qq_group_identifier(event.get("group_id"))
                    notice = (
                        normalize_group_notice(event, account=self._account, group_id=group_id)
                        if group_id is not None
                        else None
                    )
                    if notice is None:
                        raise NapCatError("QQ returned an invalid group membership notice")
                    self._group_observation_epoch += 1
                    self._pending_group_notices += 1
                    try:
                        if self._on_group_notice is not None:
                            self._on_group_notice(notice)
                        self._events.put_nowait(notice.to_event())
                    except BaseException:
                        self._pending_group_notices -= 1
                        raise
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            try:
                self._revoke_group_transport()
            finally:
                self._fail_pending()
                if not self._events.full():
                    self._events.put_nowait(None)
                if self._socket:
                    await self._socket.close()

    async def event(self) -> JsonObject:
        """Read one event; fence group routes synchronously before awaiting notice work."""
        if self._reader and self._reader.done() and self._events.empty():
            raise NapCatError("QQ connection closed")
        value = await self._events.get()
        if value is None:
            raise NapCatError("QQ connection closed")
        if value.get("post_type") == "notice" and value.get("notice_type") in GROUP_NOTICE_TYPES:
            self._pending_group_notices -= 1
        return value

    async def call(self, action: str, params: JsonObject) -> JsonObject:
        response = await self._request(action, params)
        data = response.get("data")
        return cast(JsonObject, data) if isinstance(data, dict) else {}

    async def call_array(
        self, action: str, params: JsonObject, *, max_items: int = 33
    ) -> tuple[JsonObject, ...]:
        """Bounded object arrays share the ordinary RPC capacity and deadlines."""
        if type(max_items) is not int or not 1 <= max_items <= 33:
            raise ValueError("QQ array limit must be between 1 and 33")
        response = await self._request(action, params)
        data = response.get("data")
        if (
            not isinstance(data, list)
            or len(data) > max_items
            or any(not isinstance(item, dict) for item in data)
        ):
            raise NapCatError("QQ returned an invalid bounded array")
        return tuple(cast(JsonObject, item) for item in data)

    async def _request(self, action: str, params: JsonObject) -> JsonObject:
        if self._socket is None or (self._reader and self._reader.done()):
            raise NapCatRejected("QQ connection is unavailable before request")
        if len(self._pending) + len(self._streams) >= 32:
            raise NapCatRejected("QQ request capacity exceeded")
        echo = uuid4().hex
        future: asyncio.Future[JsonObject] = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        if action == "upload_file_stream":
            # Pinned NapCat wraps each upload reply as stream-action, even though
            # the action yields a single finite response for this request echo.
            self._upload_requests.add(echo)
        try:
            async with asyncio.timeout(_RPC_TIMEOUT_SECONDS):
                await self._socket.send(
                    json.dumps({"action": action, "params": params, "echo": echo})
                )
                response = await future
            if response.get("status") != "ok" or response.get("retcode") != 0:
                raise NapCatRejected("QQ rejected the requested operation")
            if action == "upload_file_stream" and response.get("stream") != "stream-action":
                raise NapCatError("QQ returned an invalid upload response")
            return response
        except asyncio.CancelledError:
            raise
        except NapCatError:
            raise
        except Exception as error:
            raise NapCatUncertain("QQ request result is unknown") from error
        finally:
            self._pending.pop(echo, None)
            self._upload_requests.discard(echo)
            if not future.done():
                future.cancel()

    def _account_matches(self, account: str | None, revision: int) -> bool:
        return self._account == account and self._account_revision == revision

    def _check_group_observation(self, epoch: int) -> None:
        if (
            self._group_transport_revoked
            or self._pending_group_notices
            or self._group_observation_epoch != epoch
        ):
            raise NapCatRejected("QQ group membership observation changed or is pending")

    async def _account_preflight(self, account: str | None, revision: int) -> None:
        if not self._account_matches(account, revision):
            raise NapCatRejected("QQ account binding changed before operation")
        if account is not None:
            login = await self.call("get_login_info", {})
            if qq_group_identifier(login.get("user_id")) != account or not self._account_matches(
                account, revision
            ):
                raise NapCatRejected("QQ account changed before operation")

    async def favorite_hashes(self) -> frozenset[str]:
        """Read a bounded native favorite projection; provider URLs never leave the adapter."""
        account, revision = self._account, self._account_revision
        if account is None:
            raise NapCatRejected("QQ favorites require a bound account")
        await self._account_preflight(account, revision)
        response = await self._request("fetch_custom_face_detail", {"count": 100})
        entries = response.get("data")
        if not isinstance(entries, list) or len(entries) > 100:
            raise NapCatError("QQ returned invalid favorite details")
        hashes: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise NapCatError("QQ returned invalid favorite details")
            md5 = entry.get("md5")
            if not isinstance(md5, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", md5):
                raise NapCatError("QQ returned invalid favorite hashes")
            hashes.add(md5.lower())
        await self._account_preflight(account, revision)
        return frozenset(hashes)

    async def add_sticker_favorite(
        self,
        image: bytes,
        *,
        before_add: Callable[[], Awaitable[bool]],
        checkpoint: Callable[[], Awaitable[None]],
    ) -> bool:
        """Upload only normalized PNG bytes, then favorite our exact returned temporary file.

        The caller durably fences unknown mutation results. A returned True requires
        native MD5 readback; a transport receipt alone does not establish acceptance.
        """
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or not 0 < len(image) <= _IMAGE_MAX_BYTES:
            raise ValueError("QQ favorites require a bounded normalized PNG")
        account, revision = self._account, self._account_revision
        if account is None:
            raise NapCatRejected("QQ favorites require a bound account")
        stream_id = uuid4().hex
        filename = f"cw2-sticker-{stream_id}.png"
        sha = hashlib.sha256(image).hexdigest()
        chunks = (len(image) + _IMAGE_CHUNK_BYTES - 1) // _IMAGE_CHUNK_BYTES
        upload_completed = False
        try:
            async with asyncio.timeout(20):
                await self._account_preflight(account, revision)
                if not await before_add():
                    raise NapCatRejected("QQ sticker source revoked before upload")
                for index in range(chunks):
                    if not self._account_matches(account, revision):
                        raise NapCatRejected("QQ account changed during sticker upload")
                    chunk = image[index * _IMAGE_CHUNK_BYTES : (index + 1) * _IMAGE_CHUNK_BYTES]
                    uploaded = await self.call(
                        "upload_file_stream",
                        {
                            "stream_id": stream_id,
                            "filename": filename,
                            "total_chunks": chunks,
                            "file_size": len(image),
                            "expected_sha256": sha,
                            "file_retention": 120_000,
                            "chunk_index": index,
                            "chunk_data": base64.b64encode(chunk).decode("ascii"),
                        },
                    )
                    if (
                        uploaded.get("stream_id") != stream_id
                        or uploaded.get("type") != "stream"
                        or uploaded.get("status") != "chunk_received"
                        or type(uploaded.get("received_chunks")) is not int
                        or uploaded.get("received_chunks") != index + 1
                        or type(uploaded.get("total_chunks")) is not int
                        or uploaded.get("total_chunks") != chunks
                    ):
                        raise NapCatError("QQ returned invalid upload progress")
                uploaded = await self.call(
                    "upload_file_stream",
                    {
                        "stream_id": stream_id,
                        "is_complete": True,
                        "file_retention": 120_000,
                    },
                )
                path = uploaded.get("file_path")
                if (
                    uploaded.get("stream_id") != stream_id
                    or uploaded.get("type") != "response"
                    or uploaded.get("status") != "file_complete"
                    or type(uploaded.get("received_chunks")) is not int
                    or uploaded.get("received_chunks") != chunks
                    or type(uploaded.get("total_chunks")) is not int
                    or uploaded.get("total_chunks") != chunks
                    or type(uploaded.get("file_size")) is not int
                    or uploaded.get("file_size") != len(image)
                    or uploaded.get("sha256") != sha
                    or not isinstance(path, str)
                    or len(path) > 4096
                    or any(ord(c) < 32 for c in path)
                    or ".." in PurePosixPath(path).parts
                    or ".." in PureWindowsPath(path).parts
                    or not (
                        (PurePosixPath(path).is_absolute() and PurePosixPath(path).name == filename)
                        or (
                            PureWindowsPath(path).is_absolute()
                            and PureWindowsPath(path).name == filename
                        )
                    )
                ):
                    raise NapCatError("QQ returned invalid completed upload")
                upload_completed = True
                await self._account_preflight(account, revision)
                if not await before_add():
                    raise NapCatRejected("QQ sticker source revoked before favorite")
                await checkpoint()
                if not self._account_matches(account, revision) or not await before_add():
                    raise NapCatRejected("QQ sticker source revoked before favorite")
                await self.call("add_custom_face", {"file": path, "is_origin": True})
                await self._account_preflight(account, revision)
                md5 = hashlib.md5(image, usedforsecurity=False).hexdigest()
                return md5 in await self.favorite_hashes()
        except asyncio.CancelledError:
            raise
        finally:
            if not upload_completed:
                # Reset only our UUID-owned stream. Never run provider-global cleanup.
                # NapCat reports a successful reset as a rejected RPC; cleanup is auxiliary.
                try:
                    async with asyncio.timeout(1):
                        await self.call(
                            "upload_file_stream", {"stream_id": stream_id, "reset": True}
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass

    async def get_group_member_list(self, group_id: str) -> NapCatGroupMemberList:
        """Observe a small audience; no_cache is not proof of fresh membership."""
        if qq_group_identifier(group_id) != group_id:
            raise ValueError("QQ group must be a canonical identifier")
        account, revision = self._account, self._account_revision
        if account is None or qq_group_identifier(account) != account:
            raise NapCatRejected("QQ group operations require a bound account")
        epoch = self._group_observation_epoch
        self._check_group_observation(epoch)
        try:
            async with asyncio.timeout(_GROUP_TIMEOUT_SECONDS):
                await self._account_preflight(account, revision)
                self._check_group_observation(epoch)
                members = await self.call_array(
                    "get_group_member_list", {"group_id": group_id, "no_cache": True}
                )
                ids: set[str] = set()
                for member in members:
                    user_id = qq_group_identifier(member.get("user_id"))
                    if (
                        qq_group_identifier(member.get("group_id")) != group_id
                        or user_id is None
                        or user_id in ids
                    ):
                        raise NapCatError("QQ returned inconsistent group membership")
                    ids.add(user_id)
                if account not in ids or not 2 <= len(ids) - 1 <= 32:
                    raise NapCatError("QQ group requires self and 2 to 32 other members")
                if not self._account_matches(account, revision):
                    raise NapCatRejected("QQ account binding changed during observation")
                self._check_group_observation(epoch)
                await self._account_preflight(account, revision)
                self._check_group_observation(epoch)
                return NapCatGroupMemberList(
                    account, group_id, tuple(sorted(ids - {account}, key=int))
                )
        except TimeoutError:
            raise NapCatError("QQ group observation did not complete") from None

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
        account, revision = self._account, self._account_revision
        await self._account_preflight(account, revision)
        if before_send is not None and not await before_send():
            raise NapCatRejected("QQ delivery cancelled before send")
        if not self._account_matches(account, revision):
            raise NapCatRejected("QQ account binding changed before send")
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

    async def send_group(
        self,
        group_id: str,
        segments: list[JsonObject],
        *,
        before_send: Callable[[], Awaitable[bool]],
    ) -> str:
        """Send captured text/native faces or one validated expression image to a fixed group."""
        if qq_group_identifier(group_id) != group_id:
            raise ValueError("QQ group must be a canonical identifier")
        if not 1 <= len(segments) <= 128:
            raise ValueError("QQ group replies require bounded text segments")
        captured: list[JsonValue] = []
        size = 0
        images = 0
        visible = False
        for segment in segments:
            data = segment.get("data")
            text = data.get("text") if isinstance(data, dict) else None
            if segment.get("type") == "text" and isinstance(text, str) and text:
                visible = visible or bool(text.strip())
                size += len(text)
                captured.append({"type": "text", "data": {"text": text}})
            elif (
                segment.get("type") == "face"
                and isinstance(data, dict)
                and isinstance(data.get("id"), str)
                and data["id"] in FACE_LABELS
            ):
                visible = True
                captured.append({"type": "face", "data": {"id": data["id"]}})
            elif segment.get("type") == "image" and isinstance(data, dict):
                file = data.get("file")
                if (
                    not isinstance(file, str)
                    or not file.startswith("base64://")
                    or len(file) > 7_000_000
                ):
                    raise ValueError("QQ group images require bounded owned Base64 bytes")
                try:
                    raw = base64.b64decode(file.removeprefix("base64://"), validate=True)
                except (ValueError, binascii.Error):
                    raise ValueError("QQ group image Base64 is invalid") from None
                if (
                    not 0 < len(raw) <= _IMAGE_MAX_BYTES
                    or images
                    or not raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))
                    or type(data.get("sub_type")) is not int
                    or data.get("sub_type") != 1
                ):
                    raise ValueError("QQ group image is not an owned bounded expression")
                images += 1
                visible = True
                captured.append({"type": "image", "data": {"file": file, "sub_type": 1}})
            else:
                raise ValueError("QQ group reply segment is unsupported")
            if size > 20_000:
                raise ValueError("QQ group reply exceeds its text limit")
        if not visible:
            raise ValueError("QQ group replies require visible content")
        account, revision = self._account, self._account_revision
        if account is None or qq_group_identifier(account) != account:
            raise NapCatRejected("QQ group operations require a bound account")
        epoch = self._group_observation_epoch
        self._check_group_observation(epoch)
        await self._account_preflight(account, revision)
        self._check_group_observation(epoch)
        if not await before_send():
            raise NapCatRejected("QQ group delivery cancelled before send")
        if not self._account_matches(account, revision):
            raise NapCatRejected("QQ account binding changed before group send")
        self._check_group_observation(epoch)
        response = await self.call("send_group_msg", {"group_id": group_id, "message": captured})
        message_id = response.get("message_id")
        if type(message_id) not in {str, int} or not re.fullmatch(
            r"-?[0-9]{1,20}", str(message_id)
        ):
            raise NapCatUncertain("QQ returned no stable message identifier")
        return str(message_id)
