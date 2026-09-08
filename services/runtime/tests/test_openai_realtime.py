"""OpenAI GA wire, identity and lifecycle regression tests; no cloud credentials."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import base64
import copy
import json
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from chatwaifu_runtime.config.settings import OpenAIRealtimeConfig, Settings
from chatwaifu_runtime.realtime.cloud import openai as openai_module
from chatwaifu_runtime.realtime.cloud.context import RealtimeContextPatchBuilder
from chatwaifu_runtime.realtime.cloud.contracts import (
    AssistantTranscriptEvent,
    AuthorizedRealtimeSessionOpenRequest,
    InputAudioCommittedEvent,
    OutputAudioEvent,
    ProviderErrorEvent,
    RealtimeInputAudioFrame,
    RealtimeSessionIntent,
    ResponseCancelledEvent,
    ResponseCompletedEvent,
    ResponseStartedEvent,
    SessionClosedEvent,
    SessionDegradedEvent,
    SessionReadyEvent,
    UserTranscriptEvent,
)
from chatwaifu_runtime.realtime.cloud.openai import (
    OpenAIRealtimeBackend,
    OpenAIRealtimeSession,
    RealtimeSocket,
)
from chatwaifu_runtime.realtime.cloud.openai_events import (
    MAX_TURNS,
    OpenAIEventMapper,
    OpenAIRealtimeError,
    object_value,
    provider_error_code,
)
from pydantic import SecretStr


class Wire:
    def __init__(self, *, acknowledge: bool = True) -> None:
        self.incoming: asyncio.Queue[str | Exception] = asyncio.Queue()
        self.writes: list[dict[str, object]] = []
        self.acknowledge = acknowledge
        self.closed = asyncio.Event()
        self.sent = asyncio.Event()
        self.reading = asyncio.Event()
        self.on_send: Callable[[dict[str, object]], Awaitable[None]] | None = None
        self.session: dict[str, object] = {"id": "provider-session", "type": "realtime"}
        self._sequence = 0
        self.feed({"type": "session.created", "session": self.session})

    def feed(self, event: dict[str, object]) -> None:
        self._sequence += 1
        self.incoming.put_nowait(json.dumps({"event_id": f"event-{self._sequence}", **event}))

    async def send(self, message: str) -> None:
        event = object_value(json.loads(message))
        self.writes.append(event)
        self.sent.set()
        if self.on_send:
            await self.on_send(event)
        if event["type"] == "session.update" and self.acknowledge:
            self.session.update(object_value(event["session"]))
            self.feed({"type": "session.updated", "session": self.session})

    async def recv(self) -> str:
        self.reading.set()
        event = await self.incoming.get()
        if isinstance(event, Exception):
            raise event
        return event

    async def close(self) -> None:
        if not self.closed.is_set():
            self.closed.set()
            self.incoming.put_nowait(EOFError("private socket details"))


def config(**overrides: object) -> OpenAIRealtimeConfig:
    return OpenAIRealtimeConfig.model_validate(
        {
            "model": "test-realtime-model",
            "api_key": "test-openai-key",
            **overrides,
        }
    )


def request() -> AuthorizedRealtimeSessionOpenRequest:
    return AuthorizedRealtimeSessionOpenRequest(
        RealtimeSessionIntent(session_id=uuid4(), character_id="default"),
        RealtimeContextPatchBuilder().build_patch(),
        uuid4(),
    )


def backend_for(wire: Wire, **overrides: object) -> OpenAIRealtimeBackend:
    async def connector(url: str, key: str, seconds: float) -> RealtimeSocket:
        assert url == "wss://api.openai.com/v1/realtime?model=test-realtime-model"
        assert key == "test-openai-key"
        assert seconds > 0
        return wire

    return OpenAIRealtimeBackend(config(**overrides), connector=connector)


@pytest.fixture
async def connection() -> AsyncIterator[tuple[Wire, OpenAIRealtimeSession]]:
    wire = Wire()
    backend = backend_for(wire)
    session = await backend.open_session(request())
    assert isinstance(session, OpenAIRealtimeSession)
    assert isinstance(await session.receive(), SessionReadyEvent)
    try:
        yield wire, session
    finally:
        await backend.close()


def audio(
    session: OpenAIRealtimeSession, generation: UUID, rate: int = 24_000
) -> RealtimeInputAudioFrame:
    return RealtimeInputAudioFrame(
        session.session_id, generation, 0, 0, rate, 1, b"\x40\x00" * (rate // 5)
    )


async def start_response(
    wire: Wire, session: OpenAIRealtimeSession, generation: UUID, *, name: str = "response-1"
) -> None:
    await session.send_audio(audio(session, generation))
    await session.commit_input()
    wire.feed({"type": "input_audio_buffer.committed", "item_id": "input-" + name})
    assert isinstance(await session.receive(), InputAudioCommittedEvent)
    metadata = object_value(object_value(wire.writes[-1]["response"])["metadata"])
    wire.feed({"type": "response.created", "response": {"id": name, "metadata": metadata}})
    started = await session.receive()
    assert isinstance(started, ResponseStartedEvent)
    assert started.generation_id == generation


async def test_session_waits_for_effective_config_and_closes_cancelled_open() -> None:
    wire = Wire(acknowledge=False)
    backend = backend_for(wire)
    opening = asyncio.create_task(backend.open_session(request()))
    await asyncio.wait_for(wire.sent.wait(), 1)
    assert not opening.done()
    assert [w["type"] for w in wire.writes] == ["session.update"]
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert wire.closed.is_set()
    await backend.close()


async def test_handshake_deadline_closes_socket() -> None:
    wire = Wire(acknowledge=False)
    backend = backend_for(wire, connect_timeout_seconds=0.01)
    with pytest.raises(OpenAIRealtimeError, match="connection_failed"):
        await backend.open_session(request())
    assert wire.closed.is_set()
    await backend.close()


@pytest.mark.parametrize(
    "mutation", ["vad", "codec", "voice", "transcription", "tools", "session_id"]
)
async def test_handshake_rejects_incompatible_effective_config(mutation: str) -> None:
    wire = Wire(acknowledge=False)

    async def acknowledge_wrong(event: dict[str, object]) -> None:
        effective = copy.deepcopy(object_value(event["session"]))
        effective["id"] = "provider-session"
        audio_config = object_value(effective["audio"])
        input_config = object_value(audio_config["input"])
        if mutation == "vad":
            input_config["turn_detection"] = {"type": "server_vad"}
        elif mutation == "codec":
            input_config["format"] = {"type": "audio/pcmu"}
        elif mutation == "voice":
            object_value(audio_config["output"])["voice"] = "different"
        elif mutation == "transcription":
            input_config["transcription"] = None
        elif mutation == "tools":
            effective["tools"] = [{"type": "function", "name": "unapproved"}]
        else:
            effective["id"] = "different-session"
        wire.feed({"type": "session.updated", "session": effective})

    wire.on_send = acknowledge_wrong
    backend = backend_for(wire)
    with pytest.raises(OpenAIRealtimeError):
        await backend.open_session(request())
    assert wire.closed.is_set()
    await backend.close()


@pytest.mark.parametrize("rate", [8000, 16000, 24000, 48000])
async def test_pcm_stream_flushes_at_commit_and_preserves_duration(
    connection: tuple[Wire, OpenAIRealtimeSession], rate: int
) -> None:
    wire, session = connection
    frame = audio(session, uuid4(), rate)
    chunk_samples = rate // 100
    for i in range(20):
        await session.send_audio(
            replace(
                frame,
                sequence=i,
                audio=frame.audio[i * chunk_samples * 2 : (i + 1) * chunk_samples * 2],
            )
        )
    await session.commit_input()
    await session.commit_input()  # duplicate VAD stop is harmless
    events = wire.writes[1:]
    assert [e["type"] for e in events][-2:] == ["input_audio_buffer.commit", "response.create"]
    assert sum(e["type"] == "input_audio_buffer.commit" for e in events) == 1
    pcm = b"".join(
        base64.b64decode(str(e["audio"]))
        for e in events
        if e["type"] == "input_audio_buffer.append"
    )
    assert len(pcm) == 24_000 // 5 * 2
    samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
    assert all(60 <= sample <= 68 for sample in samples[100:-100])


async def test_audio_transcripts_and_usage_keep_original_generation(
    connection: tuple[Wire, OpenAIRealtimeSession],
) -> None:
    wire, session = connection
    generation = uuid4()
    await start_response(wire, session, generation)
    delta: dict[str, object] = {
        "type": "response.output_audio.delta",
        "response_id": "response-1",
        "event_id": "same-audio",
        "delta": "QABA",
    }
    # Valid PCM, then the exact replay must not duplicate media.
    delta["delta"] = base64.b64encode(b"\x40\x00" * 240).decode()
    wire.feed(delta)
    first = await session.receive()
    assert isinstance(first, OutputAudioEvent)
    assert first.frame.generation_id == generation and first.frame.pts_ms == 0
    wire.feed(delta)
    wire.feed({"type": "response.output_audio.done", "response_id": "response-1"})
    final_audio = await session.receive()
    assert isinstance(final_audio, OutputAudioEvent) and final_audio.frame.is_final
    assert final_audio.frame.sequence == 1 and final_audio.frame.pts_ms == 10
    wire.feed(
        {
            "type": "response.output_audio_transcript.done",
            "response_id": "response-1",
            "transcript": "我在这里。",
        }
    )
    assert isinstance(await session.receive(), AssistantTranscriptEvent)
    wire.feed(
        {
            "type": "response.done",
            "response": {
                "id": "response-1",
                "status": "completed",
                "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
            },
        }
    )
    completed = await session.receive()
    assert isinstance(completed, ResponseCompletedEvent)
    assert completed.usage and completed.usage.total_tokens == 20
    # ASR can arrive after completion and after the next input started.
    await session.send_audio(audio(session, uuid4()))
    wire.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "input-response-1",
            "transcript": "在吗",
        }
    )
    user = await session.receive()
    assert isinstance(user, UserTranscriptEvent) and user.candidate.generation_id == generation


async def test_interruption_before_response_created_never_rebinds_late_events(
    connection: tuple[Wire, OpenAIRealtimeSession],
) -> None:
    wire, session = connection
    old, new = uuid4(), uuid4()
    await session.send_audio(audio(session, old))
    await session.commit_input()
    old_metadata = object_value(wire.writes[-1]["response"])["metadata"]
    await session.interrupt(old)
    assert [w["type"] for w in wire.writes][-2:] == ["response.cancel", "input_audio_buffer.clear"]
    await session.send_audio(audio(session, new))
    await session.commit_input()
    new_metadata = object_value(wire.writes[-1]["response"])["metadata"]
    for item in ("old-input", "new-input"):
        wire.feed({"type": "input_audio_buffer.committed", "item_id": item})
        assert isinstance(await session.receive(), InputAudioCommittedEvent)
    for name, meta, expected in (("old", old_metadata, old), ("new", new_metadata, new)):
        wire.feed({"type": "response.created", "response": {"id": name, "metadata": meta}})
        event = await session.receive()
        assert isinstance(event, ResponseStartedEvent) and event.generation_id == expected
    wire.feed(
        {
            "type": "response.output_item.added",
            "response_id": "old",
            "item": {"id": "unheard-old", "type": "message", "role": "assistant"},
        }
    )
    wire.feed({"type": "response.output_audio.delta", "response_id": "old", "delta": "AAAAAA=="})
    wire.feed(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "old-input",
            "transcript": "旧内容",
        }
    )
    wire.feed({"type": "response.output_audio.delta", "response_id": "new", "delta": "AAAAAA=="})
    next_audio = await session.receive()
    assert isinstance(next_audio, OutputAudioEvent) and next_audio.frame.generation_id == new
    assert any(
        w.get("item_id") == "unheard-old" and w["type"] == "conversation.item.delete"
        for w in wire.writes
    )
    wire.feed({"type": "response.done", "response": {"id": "old", "status": "cancelled"}})
    cancelled = await session.receive()
    assert isinstance(cancelled, ResponseCancelledEvent) and cancelled.generation_id == old


async def test_cancelled_socket_write_seals_connection_and_releases_lock(
    connection: tuple[Wire, OpenAIRealtimeSession],
) -> None:
    wire, session = connection
    entered = asyncio.Event()

    async def hang(_event: dict[str, object]) -> None:
        entered.set()
        await asyncio.Event().wait()

    wire.on_send = hang
    send = asyncio.create_task(session.send_audio(audio(session, uuid4())))
    await asyncio.wait_for(entered.wait(), 1)
    send.cancel()
    with pytest.raises(asyncio.CancelledError):
        await send
    assert wire.closed.is_set() and not session._write_lock.locked()
    with pytest.raises(OpenAIRealtimeError, match="session_closed"):
        await session.update_context(request().context_patch)


@pytest.mark.parametrize(
    "raw",
    [
        '{"type":',
        "[]",
        '{"type":"response.created","event_id":"bad","response":{"id":"no-owner"}}',
        "x" * (1_048_576 + 1),
    ],
    ids=["invalid-json", "non-object", "unowned-response", "oversized-message"],
)
async def test_invalid_wire_fails_closed_without_payload_leak(
    connection: tuple[Wire, OpenAIRealtimeSession], raw: str
) -> None:
    wire, session = connection
    wire.incoming.put_nowait(raw)
    event = await session.receive()
    assert isinstance(event, ProviderErrorEvent)
    assert raw not in repr(event)
    assert isinstance(await session.receive(), SessionClosedEvent)
    assert wire.closed.is_set()


@pytest.mark.parametrize("status", ["failed", "incomplete"])
async def test_unsuccessful_response_is_not_completed_and_flushes_session(
    connection: tuple[Wire, OpenAIRealtimeSession], status: str
) -> None:
    wire, session = connection
    generation = uuid4()
    await start_response(wire, session, generation)
    wire.feed({"type": "response.done", "response": {"id": "response-1", "status": status}})
    event = await session.receive()
    assert isinstance(event, ProviderErrorEvent) and event.error.generation_id == generation
    assert isinstance(await session.receive(), SessionClosedEvent)


async def test_asr_failure_does_not_block_native_audio(
    connection: tuple[Wire, OpenAIRealtimeSession],
) -> None:
    wire, session = connection
    await start_response(wire, session, uuid4())
    wire.feed(
        {
            "type": "conversation.item.input_audio_transcription.failed",
            "item_id": "input-response-1",
            "error": {"message": "private"},
        }
    )
    assert isinstance(await session.receive(), SessionDegradedEvent)
    wire.feed(
        {"type": "response.output_audio.delta", "response_id": "response-1", "delta": "AAAAAA=="}
    )
    assert isinstance(await session.receive(), OutputAudioEvent)
    assert not wire.closed.is_set()


async def test_backend_close_owns_pending_handshake() -> None:
    wire = Wire(acknowledge=False)
    backend = backend_for(wire)
    opening = asyncio.create_task(backend.open_session(request()))
    await asyncio.wait_for(wire.sent.wait(), 1)
    await backend.close()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert wire.closed.is_set()
    with pytest.raises(OpenAIRealtimeError, match="backend_closed"):
        await backend.open_session(request())


async def test_cancelled_close_waiter_does_not_abandon_socket_cleanup(
    connection: tuple[Wire, OpenAIRealtimeSession],
) -> None:
    wire, session = connection
    entered, release = asyncio.Event(), asyncio.Event()
    original = wire.close

    async def close() -> None:
        entered.set()
        await release.wait()
        await original()

    wire.close = close
    waiter = asyncio.create_task(session.close())
    await asyncio.wait_for(entered.wait(), 1)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not wire.closed.is_set()
    release.set()
    await session.close()
    assert wire.closed.is_set()


async def test_duplicate_old_interrupt_never_cancels_new_response(
    connection: tuple[Wire, OpenAIRealtimeSession],
) -> None:
    wire, session = connection
    old = uuid4()
    await start_response(wire, session, old)
    await session.interrupt(old)
    await session.send_audio(audio(session, uuid4()))
    await session.commit_input()
    writes = len(wire.writes)
    await session.interrupt(old)
    assert len(wire.writes) == writes


async def test_cancel_ack_error_race_and_context_update_are_safe(
    connection: tuple[Wire, OpenAIRealtimeSession],
) -> None:
    wire, session = connection
    generation = uuid4()
    await start_response(wire, session, generation)
    receiving = asyncio.create_task(session.receive())

    async def race(event: dict[str, object]) -> None:
        if event["type"] == "response.cancel":
            wire.reading.clear()
            wire.feed(
                {
                    "type": "error",
                    "error": {"event_id": event["event_id"], "code": "response_cancel_not_active"},
                }
            )
            # Wait until receive has skipped the error and is reading again, while
            # send still owns its lock. This catches registration-after-send races.
            await asyncio.wait_for(wire.reading.wait(), 1)
            wire.feed(
                {"type": "response.done", "response": {"id": "response-1", "status": "cancelled"}}
            )

    wire.on_send = race
    await session.interrupt(generation)
    assert isinstance(await asyncio.wait_for(receiving, 1), ResponseCancelledEvent)
    assert not wire.closed.is_set()
    patch = request().context_patch
    await session.update_context(patch)
    assert session.lineage.revision == 1
    assert object_value(wire.writes[-1]["session"])["instructions"] == "\n\n".join(
        c.text for c in patch.components
    )
    with pytest.raises(OpenAIRealtimeError, match="tools_not_enabled"):
        await session.submit_tool_result("unapproved", "not sent")


@pytest.mark.parametrize(
    "invalid", ["session", "generation", "channels", "rate", "odd_pcm", "oversize"]
)
async def test_invalid_input_never_leaves_socket(
    connection: tuple[Wire, OpenAIRealtimeSession], invalid: str
) -> None:
    wire, session = connection
    frame = audio(session, uuid4())
    frame = {
        "session": replace(frame, session_id=uuid4()),
        "generation": replace(frame, generation_id=None),
        "channels": replace(frame, channels=2),
        "rate": replace(frame, sample_rate=96000),
        "odd_pcm": replace(frame, audio=b"a"),
        "oversize": replace(frame, audio=b"a" * 96002),
    }[invalid]
    writes = len(wire.writes)
    with pytest.raises(OpenAIRealtimeError, match="invalid_input_frame"):
        await session.send_audio(frame)
    assert len(wire.writes) == writes


async def test_maintenance_write_deadline_seals_socket(
    connection: tuple[Wire, OpenAIRealtimeSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire, session = connection
    monkeypatch.setattr(openai_module, "WRITE_TIMEOUT_SECONDS", 0.01)

    async def hang(_event: dict[str, object]) -> None:
        await asyncio.Event().wait()

    wire.on_send = hang
    with pytest.raises(OpenAIRealtimeError, match="send_failed"):
        await session.update_context(request().context_patch)
    assert wire.closed.is_set() and not session._write_lock.locked()


async def test_resampler_history_does_not_cross_interruption(
    connection: tuple[Wire, OpenAIRealtimeSession],
) -> None:
    wire, session = connection
    old, new = uuid4(), uuid4()
    await session.send_audio(audio(session, old, 16000))
    await session.interrupt(old)
    offset = len(wire.writes)
    await session.send_audio(replace(audio(session, new, 16000), audio=b"\x00" * 6400))
    await session.commit_input()
    pcm = b"".join(
        base64.b64decode(str(e["audio"]))
        for e in wire.writes[offset:]
        if e["type"] == "input_audio_buffer.append"
    )
    assert pcm == b"\x00" * 9600


def test_pending_commit_capacity_never_evicts_or_shifts_ack_identity() -> None:
    mapper = OpenAIEventMapper(uuid4())
    for _ in range(MAX_TURNS):
        turn = mapper.register(uuid4())
        turn.interrupted = True
        mapper.pending_commits.append(turn.generation_id)
    first = mapper.pending_commits[0]
    with pytest.raises(OpenAIRealtimeError, match="capacity"):
        mapper.register(uuid4())
    mapper.normalize(
        {"type": "input_audio_buffer.committed", "item_id": "first", "event_id": "ack"}
    )
    assert mapper.item_turn("first") is mapper.turns[first]


def test_openai_config_is_explicit_and_key_is_absent_from_public_settings() -> None:
    with pytest.raises(ValueError, match="model"):
        Settings.model_validate(
            {"realtime": {"connection_mode": "cloud_realtime", "cloud_backend": "openai"}}
        )
    with pytest.raises(ValueError, match="api_key"):
        Settings.model_validate(
            {
                "realtime": {
                    "connection_mode": "cloud_realtime",
                    "cloud_backend": "openai",
                    "openai": {"model": "test-model"},
                }
            }
        )
    settings = Settings.model_validate(
        {
            "realtime": {
                "connection_mode": "cloud_realtime",
                "cloud_backend": "openai",
                "openai": config(),
            }
        }
    )
    serialized = json.dumps(settings.public_dict())
    assert "test-openai-key" not in serialized and "api_key" not in serialized
    assert settings.realtime.openai.api_key == SecretStr("test-openai-key")


@pytest.mark.parametrize(
    "wire_code,safe_suffix",
    [
        ("invalid_api_key", "authentication_failed"),
        ("insufficient_quota", "quota_exhausted"),
        ("rate_limit_exceeded", "rate_limited"),
        ("server_error", "service_unavailable"),
        ("untrusted-private-code", "request_rejected"),
    ],
)
def test_provider_errors_preserve_actionable_category_without_echoing_payload(
    wire_code: str, safe_suffix: str
) -> None:
    assert (
        provider_error_code({"code": wire_code, "message": "test-openai-key private transcript"})
        == "openai_realtime_" + safe_suffix
    )
