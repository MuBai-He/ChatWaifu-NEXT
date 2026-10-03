"""Owner record ingress traverses the actual Runtime and local OneBot socket."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import io
import json
import wave
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import ChannelDeliveryStatus, ChannelMessageKind, ChannelTurnStatus
from chatwaifu_runtime.bootstrap import container as bootstrap_container
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatClient
from chatwaifu_runtime.realtime.contracts import SttRequest, SttResult
from test_qq_channels import OWNER, TEXT_REPLY, _event, _pair, _runtime, _segments, _terminal


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\x00" * 480)
    return output.getvalue()


def _record(message_id: int, *, sender: str = OWNER) -> JsonObject:
    event = _event("", message_id, sender=sender)
    event["message"] = [
        {
            "type": "record",
            "data": {
                "file": "owner-voice.silk",
                "file_size": 123,
                "url": "https://private.invalid/unused-record",
                "path": "/private/unused-record",
                "summary": "请用语音回复我",
            },
        }
    ]
    return event


@dataclass
class _Stt:
    kind: str = "faster_whisper_worker"
    text: str = "这是本轮录音转写"
    requests: list[SttRequest] = field(default_factory=list[SttRequest])
    cancellations: list[UUID] = field(default_factory=list[UUID])
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event | None = None
    error: Exception | None = None
    deactivations: int = 0

    async def transcribe(self, request: SttRequest) -> SttResult:
        self.requests.append(request)
        self.entered.set()
        try:
            if self.release is not None:
                await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if self.error is not None:
            raise self.error
        return SttResult(self.text, "zh", "faster-whisper")

    async def cancel(self, generation_id: UUID) -> None:
        self.cancellations.append(generation_id)

    async def deactivate(self) -> bool:
        self.deactivations += 1
        return False

    async def close(self) -> None:
        return None


def _inject_stt(monkeypatch: pytest.MonkeyPatch, stt: _Stt) -> None:
    def build(_settings: Settings) -> _Stt:
        return stt

    monkeypatch.setattr(bootstrap_container, "build_stt_backend", build)


@pytest.mark.parametrize("transcript", ["这是本轮录音转写", "请用语音回复我"])
async def test_owner_record_is_admitted_before_stt_and_uses_fresh_transcript_only(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, transcript: str
) -> None:
    stt = _Stt(text=transcript)
    _inject_stt(monkeypatch, stt)
    async with _runtime(runtime_settings, monkeypatch) as harness:
        assert harness.container.stt is stt
        connection_id = await _pair(harness)
        downloads: list[str] = []

        async def download(_client: NapCatClient, file_ref: str, *, max_bytes: int) -> bytes:
            admitted = (
                await harness.container.external_channel_repository.find_turn_by_external_message(
                    connection_id, "60"
                )
            )
            assert admitted is not None and admitted.sender_key == OWNER
            assert admitted.input_kind is ChannelMessageKind.AUDIO
            assert admitted.status is ChannelTurnStatus.ACCEPTED
            assert max_bytes == 5 * 1024 * 1024
            assert not stt.requests and not harness.model.requests
            assert harness.container.external_channels.active_preprocessing_count == 1
            downloads.append(file_ref)
            return _wav()

        monkeypatch.setattr(NapCatClient, "download_record", download)
        await harness.peer.peers[-1].send(json.dumps(_record(59, sender="30003")))
        event = _record(60)
        await harness.peer.peers[-1].send(json.dumps(event))
        request = await asyncio.wait_for(harness.model.received.get(), timeout=5)
        assert request.user_text == transcript and not request.images
        assert request.tools and request.tool_choice == "auto"
        sent = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        expected_kind = "record" if transcript == "请用语音回复我" else "text"
        assert _segments(sent)[0]["type"] == expected_kind
        turn = await harness.container.external_channel_repository.find_turn_by_external_message(
            connection_id, "60"
        )
        assert turn is not None
        result = await _terminal(harness, connection_id, turn.channel_turn_id)
        assert result.status is ChannelTurnStatus.COMPLETED
        assert len(stt.requests) == 1 and downloads == ["owner-voice.silk"]
        identity = stt.requests[0].identity
        assert (identity.session_id, identity.turn_id, identity.generation_id) == (
            turn.session_id,
            turn.turn_id,
            turn.generation_id,
        )
        assert stt.requests[0].audio == b"\x00" * 480 and stt.requests[0].language == "zh"
        source = await harness.container.conversation_repository.generation_user_input_context(
            turn.generation_id
        )
        assert source is not None and source.user_text == transcript
        assert harness.container.external_channels.active_preprocessing_count == 0

        # Ordered later text proves duplicate processing without replaying STT.
        await harness.peer.peers[-1].send(json.dumps(event))
        await harness.peer.peers[-1].send(json.dumps(_event("继续用文字回答", 61)))
        following = await asyncio.wait_for(harness.model.received.get(), timeout=5)
        assert following.user_text == "继续用文字回答" and following.tools
        assert following.tool_choice == "auto"
        await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert len(stt.requests) == 1 and downloads == ["owner-voice.silk"]
        assert len(harness.synthesis) == int(transcript == "请用语音回复我")


async def test_stt_failure_sends_one_durable_notice_without_conversation_placeholder(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    stt = _Stt(error=RuntimeError("private-record-name-and-token"))
    _inject_stt(monkeypatch, stt)

    async def download(_client: NapCatClient, file_ref: str, *, max_bytes: int) -> bytes:
        assert file_ref == "owner-voice.silk" and max_bytes == 5 * 1024 * 1024
        return _wav()

    monkeypatch.setattr(NapCatClient, "download_record", download)
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        await harness.peer.peers[-1].send(json.dumps(_record(62)))
        sent = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        segments = _segments(sent)
        assert len(segments) == 1 and segments[0]["type"] == "text"
        notice = cast(JsonObject, segments[0]["data"])["text"]
        assert isinstance(notice, str) and "语音" in notice and "文字" in notice
        assert "private" not in notice and "silk" not in notice
        turn = await harness.container.external_channel_repository.find_turn_by_external_message(
            connection_id, "62"
        )
        assert turn is not None
        result = await _terminal(harness, connection_id, turn.channel_turn_id)
        assert result.status is ChannelTurnStatus.FAILED
        assert result.error is not None and result.error.code == "audio_transcription_failed"
        assert result.delivery_id is not None
        plan = await harness.container.external_channel_repository.get_delivery_plan(
            result.delivery_id
        )
        assert plan is not None and plan.status is ChannelDeliveryStatus.DELIVERED
        assert not harness.model.requests and not harness.synthesis
        assert stt.cancellations == [turn.generation_id]
        assert (
            await harness.container.conversation_repository.generation_user_input_context(
                turn.generation_id
            )
            is None
        )
        assert harness.peer.sends.empty()


async def test_new_text_cancels_stt_and_preparation_keeps_resource_unload_busy(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    stt = _Stt(release=asyncio.Event())
    _inject_stt(monkeypatch, stt)

    async def download(_client: NapCatClient, file_ref: str, *, max_bytes: int) -> bytes:
        return _wav()

    monkeypatch.setattr(NapCatClient, "download_record", download)
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        await harness.peer.peers[-1].send(json.dumps(_record(63)))
        await asyncio.wait_for(stt.entered.wait(), timeout=5)
        assert not harness.model.requests and harness.container.conversation.active_count == 0
        assert harness.container.external_channels.active_preprocessing_count == 1
        with pytest.raises(RuntimeError, match="不能休眠"):
            await harness.container.resources.sleep_now()
        assert stt.deactivations == 0
        await harness.peer.peers[-1].send(json.dumps(_event("取消录音，继续文字聊", 64)))
        await asyncio.wait_for(stt.cancelled.wait(), timeout=5)
        request = await asyncio.wait_for(harness.model.received.get(), timeout=5)
        assert request.user_text == "取消录音，继续文字聊" and request.tools
        sent = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert _segments(sent) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
        old = await harness.container.external_channel_repository.find_turn_by_external_message(
            connection_id, "63"
        )
        assert old is not None
        result = await _terminal(harness, connection_id, old.channel_turn_id)
        assert result.status is ChannelTurnStatus.CANCELLED
        assert stt.cancellations == [old.generation_id]
        assert harness.container.external_channels.active_preprocessing_count == 0
        assert harness.peer.sends.empty()
