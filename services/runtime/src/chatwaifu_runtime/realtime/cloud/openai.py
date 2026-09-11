"""Runtime-owned OpenAI Realtime GA WebSocket adapter (no browser credentials)."""

# The resampler's C extension has no complete typing stubs.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID, uuid4

import numpy as np
import soxr  # pyright: ignore[reportMissingTypeStubs]
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from chatwaifu_runtime.config.settings import OpenAIRealtimeConfig
from chatwaifu_runtime.realtime.cloud.contracts import (
    AuthorizedRealtimeSessionOpenRequest,
    CloudRealtimeSession,
    ProviderErrorEvent,
    RealtimeCapabilities,
    RealtimeContextPatch,
    RealtimeInputAudioFrame,
    RealtimeProviderError,
    RealtimeProviderEvent,
    RealtimeSessionLineage,
    ResponseCancelledEvent,
    SessionClosedEvent,
    SessionReadyEvent,
)
from chatwaifu_runtime.realtime.cloud.openai_events import (
    BACKEND_ID,
    PCM_RATE,
    OpenAIEventMapper,
    OpenAIRealtimeError,
    OpenAITurn,
    object_value,
    provider_error_code,
    string_value,
)

MAX_WIRE_BYTES = 1_048_576
WRITE_TIMEOUT_SECONDS = 5.0


class RealtimeSocket(Protocol):
    """Private transport seam; raw JSON remains inside this adapter."""

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


type SocketConnector = Callable[[str, str, float], Awaitable[RealtimeSocket]]


async def _connect(url: str, api_key: str, handshake_seconds: float) -> RealtimeSocket:
    # WebSocket debug logging can contain handshake headers and raw audio/text.
    wire_logger = logging.Logger("chatwaifu.openai_realtime.wire", level=logging.WARNING)
    wire_logger.addHandler(logging.NullHandler())
    return await connect(
        url,
        additional_headers={"Authorization": f"Bearer {api_key}"},
        open_timeout=handshake_seconds,
        close_timeout=1,
        max_size=MAX_WIRE_BYTES,
        max_queue=16,
        compression=None,
        logger=wire_logger,
    )


class OpenAIRealtimeBackend:
    backend_id = BACKEND_ID

    def __init__(
        self, config: OpenAIRealtimeConfig, *, connector: SocketConnector = _connect
    ) -> None:
        self._config = config
        self._connector = connector
        self._sessions: set[OpenAIRealtimeSession] = set()
        self._opening: set[asyncio.Task[OpenAIRealtimeSession]] = set()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def capabilities(self) -> RealtimeCapabilities:
        return RealtimeCapabilities(
            backend_id=self.backend_id,
            input_sample_rate=PCM_RATE,
            supported_input_modalities=("audio",),
            supported_output_modalities=("audio",),
            supports_server_vad=False,
            supports_tool_call=True,
            max_session_duration_seconds=3600,
        )

    async def open_session(
        self, request: AuthorizedRealtimeSessionOpenRequest
    ) -> CloudRealtimeSession:
        if self._closed:
            raise OpenAIRealtimeError("openai_realtime_backend_closed")
        if len(self._sessions) + len(self._opening) >= 8:
            raise OpenAIRealtimeError("openai_realtime_session_capacity")
        task = asyncio.create_task(self._open(request), name="openai-realtime-open")
        self._opening.add(task)
        try:
            return await task
        finally:
            self._opening.discard(task)

    async def _open(self, request: AuthorizedRealtimeSessionOpenRequest) -> OpenAIRealtimeSession:
        socket: RealtimeSocket | None = None
        try:
            model = request.intent.model or self._config.model
            key = self._config.api_key
            if not model or key is None or not key.get_secret_value().strip():
                raise OpenAIRealtimeError("openai_realtime_configuration_missing")
            if request.intent.channels != 1 or request.intent.sample_rate != PCM_RATE:
                raise OpenAIRealtimeError("openai_realtime_output_format_unsupported")
            async with asyncio.timeout(self._config.connect_timeout_seconds):
                socket = await self._connector(
                    "wss://api.openai.com/v1/realtime?" + urlencode({"model": model}),
                    key.get_secret_value(),
                    self._config.connect_timeout_seconds,
                )
                session = OpenAIRealtimeSession(
                    socket, request, self._config, on_close=self._sessions.discard
                )
                await session.initialize()
                if self._closed:
                    raise OpenAIRealtimeError("openai_realtime_backend_closed")
                self._sessions.add(session)
                return session
        except BaseException as error:
            if socket is not None:
                try:
                    async with asyncio.timeout(1.5):
                        await socket.close()
                except Exception:
                    pass
            if isinstance(error, (asyncio.CancelledError, OpenAIRealtimeError)):
                raise
            if isinstance(error, InvalidStatus):
                status = error.response.status_code
                if status in (401, 403):
                    raise OpenAIRealtimeError("openai_realtime_authentication_failed") from None
                if status == 429:
                    raise OpenAIRealtimeError("openai_realtime_rate_limited") from None
            raise OpenAIRealtimeError("openai_realtime_connection_failed") from None

    async def close(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._finish_close(), name="openai-realtime-backend-close"
            )
        await asyncio.shield(self._close_task)

    async def _finish_close(self) -> None:
        opening = tuple(self._opening)
        for task in opening:
            task.cancel()
        await asyncio.gather(*opening, return_exceptions=True)
        await asyncio.gather(*(session.close() for session in tuple(self._sessions)))


class OpenAIRealtimeSession:
    def __init__(
        self,
        socket: RealtimeSocket,
        request: AuthorizedRealtimeSessionOpenRequest,
        config: OpenAIRealtimeConfig,
        *,
        on_close: Callable[[OpenAIRealtimeSession], None],
    ) -> None:
        self.session_id = request.session_id
        self.lineage = RealtimeSessionLineage(self.session_id, backend_id=BACKEND_ID)
        self._socket = socket
        self._request = request
        self._config = config
        self._on_close = on_close
        self._mapper = OpenAIEventMapper(self.session_id)
        self._write_lock = asyncio.Lock()
        self._read_lock = asyncio.Lock()
        self._events: deque[RealtimeProviderEvent] = deque()
        self._input: OpenAITurn | None = None
        self._input_rate = 0
        self._input_bytes = 0
        self._resampler: soxr.ResampleStream | None = None
        self._cancel_events: deque[str] = deque(maxlen=64)
        self._closed = False
        self._closed_delivered = False
        self._close_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        created = await self._read_wire()
        if created.get("type") == "error":
            raise OpenAIRealtimeError(provider_error_code(created.get("error")))
        if created.get("type") != "session.created":
            raise OpenAIRealtimeError("openai_realtime_handshake_failed")
        provider_id = string_value(object_value(created.get("session")).get("id"))
        self.lineage = replace(self.lineage, provider_session_id=provider_id)
        tools_payload = self._wire_tools()
        tool_choice = "auto" if self._request.tools else "none"
        await self._send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": self._instructions(self._request.context_patch),
                    "tools": tools_payload,
                    "tool_choice": tool_choice,
                    "tracing": None,
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": PCM_RATE},
                            "turn_detection": None,
                            "transcription": {"model": self._config.transcription_model},
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": PCM_RATE},
                            "voice": self._request.intent.voice_id or self._config.voice,
                        },
                    },
                },
            }
        )
        # A transport connection isn't readiness. Verify the effective session before PCM.
        for _ in range(16):
            event = await self._read_wire()
            if event.get("type") == "error":
                raise OpenAIRealtimeError(provider_error_code(event.get("error")))
            if event.get("type") == "session.updated":
                session = object_value(event.get("session"))
                self._validate_session(session)
                self._events.append(SessionReadyEvent(self.session_id, provider_id, BACKEND_ID))
                return
        raise OpenAIRealtimeError("openai_realtime_handshake_failed")

    def _wire_tools(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            }
            for tool in self._request.tools
        ]

    def _validate_session(self, session: dict[str, object]) -> None:
        audio = object_value(session.get("audio"))
        input_audio = object_value(audio.get("input"))
        output_audio = object_value(audio.get("output"))
        expected_tools = self._wire_tools()
        expected_tool_choice = "auto" if self._request.tools else "none"
        if (
            session.get("id") != self.lineage.provider_session_id
            or session.get("type") != "realtime"
            or session.get("output_modalities") != ["audio"]
            or "turn_detection" not in input_audio
            or input_audio["turn_detection"] is not None
            or input_audio.get("format") != {"type": "audio/pcm", "rate": PCM_RATE}
            or output_audio.get("format") != {"type": "audio/pcm", "rate": PCM_RATE}
            or object_value(input_audio.get("transcription")).get("model")
            != self._config.transcription_model
            or output_audio.get("voice") != (self._request.intent.voice_id or self._config.voice)
            or session.get("tools") != expected_tools
            or session.get("tool_choice") != expected_tool_choice
        ):
            raise OpenAIRealtimeError("openai_realtime_session_configuration_mismatch")

    @staticmethod
    def _instructions(patch: RealtimeContextPatch) -> str:
        return "\n\n".join(component.text for component in patch.components)

    async def _send(self, event: dict[str, object], *, cancel_event: bool = False) -> str:
        if self._closed:
            raise OpenAIRealtimeError("openai_realtime_session_closed")
        event_id = "cw_" + uuid4().hex
        if cancel_event:
            self._cancel_events.append(event_id)
        try:
            # Also bounds maintenance writes initiated by the receive loop, which
            # don't pass through the media bridge's per-operation deadline.
            async with asyncio.timeout(WRITE_TIMEOUT_SECONDS):
                await self._socket.send(json.dumps({**event, "event_id": event_id}))
        except BaseException as error:
            # A cancelled fragmented/blocked send has uncertain network delivery.
            # Seal it rather than reusing a potentially corrupt input buffer.
            await self.close()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise OpenAIRealtimeError("openai_realtime_send_failed") from None
        return event_id

    async def send_audio(self, frame: RealtimeInputAudioFrame) -> None:
        if (
            frame.session_id != self.session_id
            or frame.generation_id is None
            or frame.channels != 1
            or not 8000 <= frame.sample_rate <= 48000
            or len(frame.audio) % 2
            or not frame.audio
            or len(frame.audio) > 96_000
        ):
            raise OpenAIRealtimeError("openai_realtime_invalid_input_frame")
        async with self._write_lock:
            if self._input is not None and self._input.done:
                self._input = None
            if self._input is None:
                self._input = self._mapper.register(
                    frame.generation_id, tools_exposed=bool(self._request.tools)
                )
                self._input_rate = frame.sample_rate
                self._input_bytes = 0
                self._resampler = (
                    None
                    if frame.sample_rate == PCM_RATE
                    else soxr.ResampleStream(
                        frame.sample_rate,
                        PCM_RATE,
                        1,
                        dtype="float32",
                        quality="LQ",
                    )
                )
            turn = self._input
            if (
                turn.generation_id != frame.generation_id
                or turn.requested
                or turn.interrupted
                or frame.sample_rate != self._input_rate
            ):
                raise OpenAIRealtimeError("openai_realtime_input_generation_mismatch")
            audio = self._resample(frame.audio)
            if audio:
                await self._send_audio_bytes(audio)

    def _resample(self, audio: bytes, *, final: bool = False) -> bytes:
        if self._resampler is None:
            return audio
        # Keep conversion deterministic (including silence); SoXR's integer output
        # adds dithering. Round and saturate explicitly instead of wrapping peaks.
        samples = np.frombuffer(audio, dtype="<i2").astype(np.float32)
        converted = self._resampler.resample_chunk(samples, last=final)
        return np.clip(np.rint(converted), -32768, 32767).astype("<i2").tobytes()

    async def _send_audio_bytes(self, audio: bytes) -> None:
        await self._send(
            {"type": "input_audio_buffer.append", "audio": base64.b64encode(audio).decode("ascii")}
        )
        self._input_bytes += len(audio)

    async def commit_input(self) -> None:
        async with self._write_lock:
            turn = self._input
            if turn is None or turn.interrupted:
                raise OpenAIRealtimeError("openai_realtime_empty_input")
            if turn.requested:
                return
            tail = self._resample(b"", final=True)
            if tail:
                await self._send_audio_bytes(tail)
            if self._input_bytes == 0:
                raise OpenAIRealtimeError("openai_realtime_empty_input")
            # Register before awaiting the socket: an ACK may arrive during send.
            self._mapper.pending_commits.append(turn.generation_id)
            await self._send({"type": "input_audio_buffer.commit"})
            turn.requested = True
            response_payload: dict[str, object] = {
                "metadata": self._mapper.metadata(turn),
            }
            if turn.tools_exposed:
                response_payload["output_modalities"] = ["text"]
            await self._send({"type": "response.create", "response": response_payload})

    async def interrupt(self, generation_id: UUID, reason: str = "user_barge_in") -> None:
        async with self._write_lock:
            turn = self._mapper.turns.get(generation_id)
            if turn is None or turn.interrupted:
                # An admitted utterance can be interrupted before its first PCM.
                return
            turn.interrupted = True
            if turn.requested and not turn.done:
                cancel: dict[str, object] = {"type": "response.cancel"}
                if turn.continuation_response_id is not None:
                    cancel["response_id"] = turn.continuation_response_id
                elif turn.response_id is not None:
                    cancel["response_id"] = turn.response_id
                await self._send(cancel, cancel_event=True)
            if self._input is turn:
                await self._send({"type": "input_audio_buffer.clear"})
                self._input = None
                self._resampler = None
            await self._delete_interrupted_items()

    async def _delete_interrupted_items(self) -> None:
        # Without sample-accurate playback ACK, conservatively remove the interrupted
        # assistant item from provider history rather than inventing a played offset.
        for turn in self._mapper.turns.values():
            if turn.interrupted:
                for item_id in list(turn.output_items - turn.deleted_items):
                    turn.deleted_items.add(item_id)
                    await self._send({"type": "conversation.item.delete", "item_id": item_id})

    async def update_context(self, patch: RealtimeContextPatch) -> None:
        async with self._write_lock:
            await self._send(
                {
                    "type": "session.update",
                    "session": {"type": "realtime", "instructions": self._instructions(patch)},
                }
            )
            self.lineage = replace(
                self.lineage, revision=self.lineage.revision + 1, updated_at=datetime.now(UTC)
            )

    async def submit_tool_result(
        self, call_id: str, output: str, generation_id: UUID | None = None
    ) -> None:
        async with self._write_lock:
            if self._closed:
                raise OpenAIRealtimeError("openai_realtime_session_closed")
            if not self._request.tools:
                raise OpenAIRealtimeError("openai_realtime_tools_not_enabled")
            if generation_id is None:
                raise OpenAIRealtimeError("openai_realtime_missing_response_identity")
            turn = self._mapper.turns.get(generation_id)
            if turn is None or turn.interrupted or turn.done:
                return
            item_id = f"item_fco_{uuid4().hex[:16]}"
            turn.output_items.add(item_id)
            await self._send(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "id": item_id,
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    },
                }
            )

    async def request_continuation(
        self, generation_id: UUID, *, disable_tools: bool = True
    ) -> None:
        async with self._write_lock:
            await self._request_continuation_locked(generation_id, disable_tools=disable_tools)

    async def _request_continuation_locked(
        self, generation_id: UUID, *, disable_tools: bool = True
    ) -> None:
        if self._closed:
            raise OpenAIRealtimeError("openai_realtime_session_closed")
        turn = self._mapper.turns.get(generation_id)
        if turn is None or turn.interrupted or turn.done:
            return
        for item_id in tuple(turn.decision_items):
            if item_id not in turn.deleted_items:
                turn.deleted_items.add(item_id)
                await self._send({"type": "conversation.item.delete", "item_id": item_id})
        self._mapper.reserve_continuation(generation_id)
        response_payload: dict[str, object] = {
            "output_modalities": ["audio"],
            "metadata": self._mapper.metadata(turn),
        }
        if disable_tools:
            response_payload["tools"] = []
            response_payload["tool_choice"] = "none"
        await self._send(
            {
                "type": "response.create",
                "response": response_payload,
            }
        )

    async def _read_wire(self) -> dict[str, object]:
        raw = await self._socket.recv()
        if len(raw) > MAX_WIRE_BYTES:
            raise OpenAIRealtimeError("openai_realtime_wire_capacity")
        try:
            return object_value(json.loads(raw))
        except (ValueError, UnicodeError, RecursionError):
            raise OpenAIRealtimeError("openai_realtime_invalid_event") from None

    async def receive(self) -> RealtimeProviderEvent:
        async with self._read_lock:
            while True:
                if self._events:
                    return self._events.popleft()
                if self._closed:
                    if self._closed_delivered:
                        raise StopAsyncIteration
                    self._closed_delivered = True
                    return SessionClosedEvent(
                        self.session_id, BACKEND_ID, "provider_connection_closed"
                    )
                try:
                    event = await self._read_wire()
                    if event.get("type") == "error":
                        error = object_value(event.get("error"))
                        if (
                            error.get("event_id") in self._cancel_events
                            and error.get("code") == "response_cancel_not_active"
                        ):
                            continue
                        raise OpenAIRealtimeError(provider_error_code(error))
                    if event.get("type") == "session.updated":
                        self._validate_session(object_value(event.get("session")))
                        continue
                    normalized = self._mapper.normalize(event)
                    self._events.extend(normalized)
                    async with self._write_lock:
                        await self._delete_interrupted_items()
                    if any(
                        isinstance(item, ProviderErrorEvent)
                        or (
                            isinstance(item, ResponseCancelledEvent)
                            and item.reason == "provider_cancelled"
                        )
                        for item in normalized
                    ):
                        await self.close()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await self.close()
                    code = (
                        str(error)
                        if isinstance(error, OpenAIRealtimeError)
                        else "openai_realtime_connection_lost"
                    )
                    return ProviderErrorEvent(
                        RealtimeProviderError(
                            self.session_id,
                            BACKEND_ID,
                            code,
                            "Cloud voice connection ended. Start a new voice session to retry.",
                            retryable=code
                            in {
                                "openai_realtime_connection_lost",
                                "openai_realtime_rate_limited",
                                "openai_realtime_service_unavailable",
                            },
                        )
                    )

    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        while True:
            try:
                yield await self.receive()
            except StopAsyncIteration:
                return

    async def close(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._finish_close(), name="openai-realtime-close"
            )
        await asyncio.shield(self._close_task)

    async def _finish_close(self) -> None:
        try:
            async with asyncio.timeout(1.5):
                await self._socket.close()
        except Exception:
            # Closing is best effort and bounded; raw socket errors are not public.
            pass
        finally:
            self._on_close(self)
