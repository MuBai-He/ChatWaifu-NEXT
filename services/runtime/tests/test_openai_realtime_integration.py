"""Loopback WebSocket -> cloud adapter -> Runtime/SQLite, without public network."""
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.commands import PlaybackAckCommand, PlaybackAckPayload
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.realtime.cloud import openai as openai_module
from chatwaifu_runtime.realtime.cloud.context import ConsentRequiredError, PolicyDeniedError
from chatwaifu_runtime.realtime.cloud.openai import OpenAIRealtimeBackend, RealtimeSocket
from chatwaifu_runtime.realtime.cloud.openai_events import object_value
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from websockets.asyncio.server import ServerConnection, serve


def settings_for(tmp_path: Path, policy: str = "allow") -> Settings:
    return Settings.model_validate(
        {
            "config_dir": tmp_path / "config",
            "data_dir": tmp_path,
            "storage": {"database_path": tmp_path / "runtime.db"},
            "llm": {"provider": "demo"},
            "tts": {"provider": "fake"},
            "privacy": {"cloud_egress": policy},
            "realtime": {
                "connection_mode": "cloud_realtime",
                "cloud_backend": "openai",
                "openai": {"model": "test-model", "api_key": "local-test-key"},
            },
        }
    )


@pytest.mark.parametrize("disconnect", [False, True])
async def test_real_websocket_turn_and_late_transcript_persist_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disconnect: bool,
) -> None:
    handler_finished = asyncio.Event()
    server_writes: list[str] = []
    wire_input: list[dict[str, object]] = []
    output_pcm = b"\x01\x00" * 480

    async def handler(ws: ServerConnection) -> None:
        assert ws.request and ws.request.headers["Authorization"] == "Bearer local-test-key"
        sequence = 0

        async def send(event: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            await ws.send(json.dumps({"event_id": f"wire-{sequence}", **event}))

        await send({"type": "session.created", "session": {"id": "loopback", "type": "realtime"}})
        try:
            async for raw in ws:
                event = object_value(json.loads(raw))
                wire_input.append(event)
                if event["type"] == "session.update":
                    await send(
                        {
                            "type": "session.updated",
                            "session": {**object_value(event["session"]), "id": "loopback"},
                        }
                    )
                elif event["type"] == "input_audio_buffer.commit":
                    await send({"type": "input_audio_buffer.committed", "item_id": "input-one"})
                elif event["type"] == "response.create":
                    metadata = object_value(event["response"])["metadata"]
                    await send(
                        {
                            "type": "response.created",
                            "response": {"id": "response-one", "metadata": metadata},
                        }
                    )
                    await send(
                        {
                            "type": "response.output_audio.delta",
                            "response_id": "response-one",
                            "delta": base64.b64encode(output_pcm).decode(),
                        }
                    )
                    if disconnect:
                        await ws.close(code=1011)
                        return
                    await send(
                        {"type": "response.output_audio.done", "response_id": "response-one"}
                    )
                    await send(
                        {
                            "type": "response.output_audio_transcript.done",
                            "response_id": "response-one",
                            "transcript": "我在这里。",
                        }
                    )
                    await send(
                        {
                            "type": "response.done",
                            "response": {
                                "id": "response-one",
                                "status": "completed",
                                "usage": {
                                    "input_tokens": 4,
                                    "output_tokens": 6,
                                    "total_tokens": 10,
                                },
                            },
                        }
                    )
                    # Transcription is independently scheduled by the provider.
                    final: dict[str, object] = {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": "input-one",
                        "transcript": "在吗",
                        "event_id": "late-user-final",
                    }
                    await send(final)
                    await send(final)
                    server_writes.append("turn_finished")
        finally:
            handler_finished.set()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def connector(_url: str, key: str, seconds: float) -> RealtimeSocket:
            return await openai_module._connect(f"ws://127.0.0.1:{port}/realtime", key, seconds)

        container = RuntimeContainer(settings_for(tmp_path))
        assert isinstance(container.cloud_realtime_backend, OpenAIRealtimeBackend)
        container.cloud_realtime_backend._connector = connector
        await container.start()
        bridge = None
        try:
            session = await container.sessions.create_session("default")
            assert container.cloud_realtime_factory
            bridge = await container.cloud_realtime_factory.create_bridge(session.session_id)
            media: list[bytes] = []
            interruptions: list[InterruptionFrame] = []
            playback_markers: list[dict[str, object]] = []

            async def capture(
                frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
            ) -> None:
                if isinstance(frame, OutputAudioRawFrame):
                    media.append(frame.audio)
                if isinstance(frame, InterruptionFrame):
                    interruptions.append(frame)
                if isinstance(frame, OutputTransportMessageFrame) and isinstance(
                    frame.message, dict
                ):
                    playback_markers.append(frame.message)

            monkeypatch.setattr(bridge, "push_frame", capture)
            final_received = asyncio.Event()
            sink = bridge.coordinator.domain_sink
            original = sink.transcript_final

            async def final_transcript(
                sid: UUID,
                tid: UUID,
                gid: UUID,
                text: str,
                role: Literal["user", "assistant"],
                *,
                utterance_id: UUID | None = None,
            ) -> None:
                await original(sid, tid, gid, text, role, utterance_id=utterance_id)
                if role == "user":
                    final_received.set()

            monkeypatch.setattr(sink, "transcript_final", final_transcript)
            original_closed = sink.session_closed

            async def closed(sid: UUID, reason: str) -> None:
                await original_closed(sid, reason)
                if disconnect:
                    final_received.set()

            monkeypatch.setattr(sink, "session_closed", closed)
            await bridge._handle_user_speaking_started()
            identity = bridge.current_identity
            assert identity
            bridge._handle_input_audio(
                InputAudioRawFrame(audio=b"\x40\x00" * 3200, sample_rate=16_000, num_channels=1)
            )
            await bridge._handle_user_speaking_stopped()
            await asyncio.wait_for(final_received.wait(), 5)
            if not disconnect:
                for marker in playback_markers:
                    if marker.get("phase") == "buffered":
                        ack = await container.playback.acknowledge(
                            PlaybackAckCommand(
                                command_id=uuid4(),
                                session_id=session.session_id,
                                generation_id=identity.generation_id,
                                issued_at=datetime.now(UTC),
                                issuer="web-client",
                                payload=PlaybackAckPayload(
                                    stream_id=UUID(str(marker["stream_id"])),
                                    segment_id=UUID(str(marker["segment_id"])),
                                    phase="stopped",
                                    played_pts_ms=int(str(marker["duration_ms"])),
                                    buffered_ms=int(str(marker["duration_ms"])),
                                    client_clock_ms=int(str(marker["duration_ms"])),
                                    transport="webrtc",
                                    reason="ended",
                                ),
                            )
                        )
                        if ack.all_segments_completed and ack.turn_id:
                            await container.conversation.complete_realtime_generation(
                                session_id=session.session_id,
                                turn_id=ack.turn_id,
                                generation_id=identity.generation_id,
                                text=ack.spoken_text,
                            )
            await bridge.coordinator.stop()
            records = await container.database.fetchall(
                "SELECT event_type, envelope_json FROM events WHERE session_id = ?",
                (str(session.session_id),),
            )
            persisted = str([dict(row) for row in records])
            assert base64.b64encode(output_pcm).decode() not in persisted
            assert '"audio":' not in persisted
            assert sum(row["event_type"] == "cloud.egress_receipt" for row in records) == 1
            messages = await container.conversation.list_messages(session.session_id)
            if disconnect:
                result = await container.conversation_repository.generation_result(
                    identity.generation_id
                )
                assert result and result.state == "cancelled"
                assert not messages and interruptions
                assert (
                    sum(row["event_type"] == "assistant.generation_cancelled" for row in records)
                    == 1
                )
                return
            assert {(m["role"], m["committed_text"]) for m in messages} == {
                ("user", "在吗"),
                ("assistant", "我在这里。"),
            }
            assert len(messages) == 2
            result = await container.conversation_repository.generation_result(
                identity.generation_id
            )
            assert result and result.state == "completed" and result.output_text == "我在这里。"
            assert b"".join(media) == output_pcm
            assert server_writes == ["turn_finished"]
            assert [e["type"] for e in wire_input][-2:] == [
                "input_audio_buffer.commit",
                "response.create",
            ]
        finally:
            if bridge is not None:
                await bridge.coordinator.stop()
                await bridge.cleanup()
            await container.stop()
        await asyncio.wait_for(handler_finished.wait(), 2)


@pytest.mark.parametrize("policy", ["deny", "ask", "receipt_failure"])
async def test_real_backend_egress_gate_prevents_even_socket_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: str,
) -> None:
    container = RuntimeContainer(
        settings_for(tmp_path, "allow" if policy == "receipt_failure" else policy)
    )
    connects = 0

    async def forbidden(_url: str, _key: str, _seconds: float) -> RealtimeSocket:
        nonlocal connects
        connects += 1
        raise AssertionError("socket must never be opened")

    assert isinstance(container.cloud_realtime_backend, OpenAIRealtimeBackend)
    container.cloud_realtime_backend._connector = forbidden
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        if policy == "receipt_failure":

            async def unavailable(*_args: object, **_kwargs: object) -> None:
                raise OSError("audit unavailable")

            monkeypatch.setattr(container.event_store, "append", unavailable)
        assert container.cloud_realtime_factory
        expected = {
            "deny": PolicyDeniedError,
            "ask": ConsentRequiredError,
            "receipt_failure": RuntimeError,
        }[policy]
        with pytest.raises(expected):
            await container.cloud_realtime_factory.create_bridge(session.session_id)
        assert connects == 0
    finally:
        await container.stop()
