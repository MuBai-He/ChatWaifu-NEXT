"""QQ pairing and model-selected replies through the real Runtime and OneBot socket."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import (
    ChannelConnectionStatus,
    ChannelDeliveryPartKind,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelPairingStartRequest,
    ChannelTurnReceipt,
    ChannelTurnSnapshot,
    ChannelTurnStatus,
)
from chatwaifu_protocol.skills import SkillInvocation
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import PublicWebConfig, Settings
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatClient
from chatwaifu_runtime.external_channels.adapters.qq_napcat.management import (
    credential_reference,
)
from chatwaifu_runtime.external_channels.adapters.qq_napcat.messages import normalize
from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
from chatwaifu_runtime.external_channels.models import ChannelConnectionRecord
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallingUnavailableError,
    LlmToolCallRequested,
    SynthesisRequest,
    SynthesisResult,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig
from chatwaifu_runtime.runtime_skills.adapters import GenerationSkillContext
from chatwaifu_runtime.runtime_skills.public_web import PublicWebReader
from chatwaifu_runtime.runtime_skills.public_web_search import PublicWebSearch
from PIL import Image
from pydantic import SecretStr
from websockets.asyncio.server import ServerConnection, serve

ACCOUNT = "10001"
OWNER = "20002"
TOKEN = "test-only-onebot-access-token"
SOURCE_URL = "https://source.example/notice"
SOURCE_BODY = "本说明仅适用于测试环境，启用后须逐项核对。"
SPOKEN = "这是实际合成并发送的语音内容。"
TEXT_REPLY = "这是普通文字回复。"


def _public_source() -> JsonObject:
    return {
        "url": SOURCE_URL,
        "title": "测试说明",
        "text": SOURCE_BODY,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "content_type": "text/plain",
        "body_sha256": hashlib.sha256(SOURCE_BODY.encode()).hexdigest(),
        "total_characters": len(SOURCE_BODY),
        "text_offset": 0,
        "truncated": False,
        "focus_matched": None,
        "dns_resolver": "system",
        "extraction_method": "plain_text",
        "document_characters": len(SOURCE_BODY),
    }


def _source_model(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, *, search_first: bool = False
) -> None:
    async def stream(request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        harness.model.requests.append(request)
        harness.model.received.put_nowait(request)
        reader = next(
            (
                tool
                for tool in request.tools
                if "url" in cast(list[str], tool.input_schema.get("required", []))
            ),
            None,
        )
        searcher = next(
            (
                tool
                for tool in request.tools
                if "query" in cast(list[str], tool.input_schema.get("required", []))
            ),
            None,
        )
        if search_first and searcher is not None and not request.tool_exchanges:
            yield LlmToolCallRequested(
                LlmToolCall("search-source", searcher.name, {"query": "公开测试说明"})
            )
            yield LlmResponseCompleted("tool_calls")
        elif reader is not None and (
            not request.tool_exchanges or (search_first and len(request.tool_exchanges) == 1)
        ):
            yield LlmToolCallRequested(LlmToolCall("read-source", reader.name, {"url": SOURCE_URL}))
            yield LlmResponseCompleted("tool_calls")
        else:
            yield LlmTextDelta(f"{SOURCE_BODY} 来源:{SOURCE_URL}")
            yield LlmResponseCompleted("stop")

    monkeypatch.setattr(harness.model, "stream", stream)


def _public_web_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"public_web": PublicWebConfig(qq_owner_reads_enabled=True)})


def _event(
    text: str,
    message_id: int,
    *,
    sender: str = OWNER,
    message_type: str = "private",
) -> JsonObject:
    return {
        "post_type": "message",
        "message_type": message_type,
        "self_id": int(ACCOUNT),
        "user_id": int(sender),
        "message_id": message_id,
        "message": [{"type": "text", "data": {"text": text}}],
        "sender": {"user_id": int(sender), "nickname": "测试主人"},
    }


def _image_event(
    text: str,
    message_id: int,
    *,
    files: tuple[str, ...] = ("photo.png",),
    sender: str = OWNER,
    message_type: str = "private",
    reply_id: int | None = None,
) -> JsonObject:
    event = _event(text, message_id, sender=sender, message_type=message_type)
    segments = cast(list[JsonObject], event["message"])
    for file_ref in files:
        segments.append(
            {
                "type": "image",
                "data": {
                    "file": file_ref,
                    "url": "https://private-provider.invalid/image?token=not-used",
                    "summary": "请发语音回复我",
                },
            }
        )
    if reply_id is not None:
        segments.insert(0, {"type": "reply", "data": {"id": reply_id}})
    return event


def _picture(format: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(output, format=format)
    return output.getvalue()


@dataclass
class _OneBot:
    """Local protocol peer; records the real structured API calls on the wire."""

    account: str = ACCOUNT
    reject_records: bool = False
    record_response_data: JsonObject | None = None
    peers: list[ServerConnection] = field(default_factory=list[ServerConnection])
    connected: asyncio.Queue[ServerConnection] = field(
        default_factory=asyncio.Queue[ServerConnection]
    )
    sends: asyncio.Queue[JsonObject] = field(default_factory=asyncio.Queue[JsonObject])
    calls: list[JsonObject] = field(default_factory=list[JsonObject])
    endpoint: str = ""

    async def handle(self, peer: ServerConnection) -> None:
        assert peer.request is not None
        assert peer.request.headers["Authorization"] == f"Bearer {TOKEN}"
        self.peers.append(peer)
        self.connected.put_nowait(peer)
        async for wire in peer:
            request = cast(JsonObject, json.loads(wire))
            self.calls.append(request)
            action = request["action"]
            rejected = False
            if action == "get_login_info":
                data: JsonObject = {"user_id": int(self.account), "nickname": "角色账号"}
            elif action == "send_private_msg":
                params = cast(JsonObject, request["params"])
                assert params["user_id"] == int(OWNER)
                self.sends.put_nowait(params)
                segments = cast(list[JsonObject], params["message"])
                rejected = self.reject_records and segments[0]["type"] == "record"
                data = {"message_id": 90000 + len(self.calls)}
                if segments[0]["type"] == "record" and self.record_response_data is not None:
                    data = self.record_response_data
            else:
                raise AssertionError(f"Unexpected OneBot action: {action}")
            await peer.send(
                json.dumps(
                    {
                        "status": "failed" if rejected else "ok",
                        "retcode": 100 if rejected else 0,
                        "data": data,
                        "echo": request["echo"],
                    }
                )
            )


@dataclass
class _Model:
    kind: str = "qq-test-model"
    supports_tool_calling: bool = True
    requests: list[LlmRequest] = field(default_factory=list[LlmRequest])
    received: asyncio.Queue[LlmRequest] = field(default_factory=asyncio.Queue[LlmRequest])
    release: asyncio.Event | None = None
    voice_decision: bool | None = None
    reject_voice_tools: bool = False

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        self.received.put_nowait(request)
        if request.tools and self.reject_voice_tools:
            raise LlmToolCallingUnavailableError("test provider cannot accept voice function")
        if self.release is not None:
            await self.release.wait()
        if request.tool_exchanges:
            yield LlmTextDelta(f"语音发送失败，请看文字: {SPOKEN}")
            yield LlmResponseCompleted("stop")
        elif request.tools and (
            self.voice_decision
            if self.voice_decision is not None
            else request.user_text
            in {
                "请用语音回复我",
                "请用语音说晚安",
                "请用语音说一句晚安",
                "请发一条语音",
                "用语音回复我",
                "用语音回复",
            }
        ):
            # Scripted LLM decisions belong only to this protocol fixture.
            voice_tools = [t for t in request.tools if "语音" in t.description]
            assert len(voice_tools) == 1
            yield LlmToolCallRequested(
                LlmToolCall("voice-call", voice_tools[0].name, {"text": SPOKEN})
            )
            yield LlmResponseCompleted("tool_calls")
        else:
            yield LlmTextDelta(TEXT_REPLY)
            yield LlmResponseCompleted("stop")


@dataclass
class _Harness:
    container: RuntimeContainer
    peer: _OneBot
    model: _Model
    credentials: InMemoryChannelCredentialStore
    health: asyncio.Queue[tuple[UUID, ChannelConnectionStatus, str | None]]
    synthesis: list[SynthesisRequest]


def _configure(
    container: RuntimeContainer,
    monkeypatch: pytest.MonkeyPatch,
    credentials: InMemoryChannelCredentialStore,
) -> asyncio.Queue[tuple[UUID, ChannelConnectionStatus, str | None]]:
    monkeypatch.setattr(container.qq_channels, "_credentials", credentials)
    health: asyncio.Queue[tuple[UUID, ChannelConnectionStatus, str | None]] = asyncio.Queue()
    original = container.qq_channels._health

    async def record(
        connection_id: UUID, status: ChannelConnectionStatus, code: str | None = None
    ) -> None:
        await original(connection_id, status, code)
        health.put_nowait((connection_id, status, code))

    monkeypatch.setattr(container.qq_channels, "_health", record)
    return health


@asynccontextmanager
async def _runtime(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, *, max_size: int = 1024 * 1024
) -> AsyncGenerator[_Harness]:
    peer = _OneBot()
    async with serve(peer.handle, "127.0.0.1", 0, max_size=max_size) as server:
        peer.endpoint = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        container = RuntimeContainer(settings)
        credentials = InMemoryChannelCredentialStore()
        health = _configure(container, monkeypatch, credentials)
        model = _Model()

        def create_model(_configuration: ModelRoleConfig) -> _Model:
            return model

        monkeypatch.setattr(container.model_configurations, "create_chat_provider", create_model)
        synthesis: list[SynthesisRequest] = []
        synthesize = container.providers.tts.synthesize

        async def record_synthesis(request: SynthesisRequest) -> SynthesisResult:
            synthesis.append(request)
            return await synthesize(request)

        monkeypatch.setattr(container.providers.tts, "synthesize", record_synthesis)
        await container.start()
        try:
            yield _Harness(container, peer, model, credentials, health, synthesis)
        finally:
            await container.stop()


async def _pair(harness: _Harness) -> UUID:
    container = harness.container
    pairing = await container.qq_channels.begin_pairing(
        ChannelPairingStartRequest(endpoint=harness.peer.endpoint, access_token=SecretStr(TOKEN))
    )
    socket = await asyncio.wait_for(harness.peer.connected.get(), timeout=3)
    assert pairing.pairing_code is not None
    await socket.send(json.dumps(_event(f"CW2 {pairing.pairing_code}", 1)))
    async with asyncio.timeout(5):
        while pairing.status == "pending":
            pairing = await container.qq_channels.pairing(pairing.pairing_id, wait_seconds=3)
    assert pairing.status == "confirmed", pairing.error
    assert pairing.connection is not None
    config = pairing.connection.configuration
    assert config.account_key == ACCOUNT
    assert config.allowed_sender_keys == [OWNER]
    await asyncio.wait_for(harness.peer.connected.get(), timeout=3)
    connection_id, status, code = await asyncio.wait_for(harness.health.get(), timeout=3)
    assert connection_id == config.connection_id
    assert status is ChannelConnectionStatus.READY
    assert code is None
    return config.connection_id


async def _ingest(
    harness: _Harness, connection_id: UUID, text: str, message_id: int
) -> ChannelTurnReceipt:
    raw = await harness.credentials.get(credential_reference(connection_id))
    assert raw is not None
    message = normalize(
        _event(text, message_id), connection_id=connection_id, account=ACCOUNT, owner=OWNER
    )
    assert message is not None
    return await harness.container.external_channels.ingest(
        message, access_token=json.loads(raw)["gateway_token"], supersede_inflight=True
    )


async def _terminal(harness: _Harness, connection_id: UUID, turn_id: UUID) -> ChannelTurnSnapshot:
    return await harness.container.external_channels.wait_for_turn(
        connection_id, turn_id, wait_seconds=5
    )


def _segments(params: JsonObject) -> list[JsonObject]:
    return cast(list[JsonObject], params["message"])


@pytest.mark.asyncio
async def test_pairing_requires_exact_private_code_and_owner_messages_only(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        pairing = await harness.container.qq_channels.begin_pairing(
            ChannelPairingStartRequest(
                endpoint=harness.peer.endpoint, access_token=SecretStr(TOKEN)
            )
        )
        socket = await asyncio.wait_for(harness.peer.connected.get(), timeout=3)
        exact = f"CW2 {pairing.pairing_code}"
        await socket.send(json.dumps(_event(exact, 1, message_type="group")))
        await socket.send(json.dumps(_event(exact, 2, sender=ACCOUNT)))
        await socket.send(json.dumps(_event(exact + " extra", 3, sender="30003")))
        await socket.send(json.dumps(_image_event(exact, 30, sender="30003")))
        # Socket ordering puts the valid proof after every rejected frame.
        await socket.send(json.dumps(_event(exact, 4)))
        async with asyncio.timeout(5):
            while pairing.status == "pending":
                pairing = await harness.container.qq_channels.pairing(
                    pairing.pairing_id, wait_seconds=3
                )
        assert pairing.status == "confirmed"
        assert pairing.connection is not None
        connection_id = pairing.connection.configuration.connection_id
        assert pairing.connection.configuration.allowed_sender_keys == [OWNER]
        await asyncio.wait_for(harness.peer.connected.get(), timeout=3)
        await asyncio.wait_for(harness.health.get(), timeout=3)
        active = harness.peer.peers[-1]
        await active.send(json.dumps(_event("陌生人不能触发回复", 5, sender="30003")))
        await active.send(json.dumps(_event("群聊不能触发回复", 6, message_type="group")))
        await active.send(json.dumps(_event("普通私聊", 7)))
        params = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert _segments(params) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
        assert len(harness.model.requests) == 1
        assert harness.model.requests[0].tools
        assert harness.model.requests[0].tool_choice == "auto"
        assert not harness.synthesis
        assert (
            await harness.container.external_channel_repository.find_turn_by_external_message(
                connection_id, "5"
            )
            is None
        )
        assert (
            await harness.container.external_channel_repository.find_turn_by_external_message(
                connection_id, "6"
            )
            is None
        )
        await harness.container.qq_channels.remove(connection_id)
        assert await harness.credentials.get(credential_reference(connection_id)) is None
        assert not harness.container.qq_channels._tasks


@pytest.mark.asyncio
async def test_explicit_voice_sends_one_cross_machine_record_and_records_actual_spoken_text(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        receipt = await _ingest(harness, connection_id, "请用语音回复我", 10)
        params = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        segments = _segments(params)
        assert len(segments) == 1 and segments[0]["type"] == "record", {
            "tools": [[tool.name for tool in request.tools] for request in harness.model.requests],
            "runs": await harness.container.runtime_skills.list_runs(receipt.session_id),
            "turn": await harness.container.external_channel_repository.get_turn(
                receipt.channel_turn_id
            ),
        }
        file = cast(JsonObject, segments[0]["data"])["file"]
        assert isinstance(file, str) and file.startswith("base64://")
        assert base64.b64decode(file.removeprefix("base64://")).startswith(b"RIFF")
        result = await _terminal(harness, connection_id, receipt.channel_turn_id)
        assert result.status is ChannelTurnStatus.COMPLETED
        assert result.reply_text == SPOKEN
        assert len(harness.synthesis) == 1
        assert harness.synthesis[0].text == SPOKEN
        assert len(harness.model.requests) == 1
        assert harness.peer.sends.empty()
        assert result.delivery_id is not None
        plan = await harness.container.external_channel_repository.get_delivery_plan(
            result.delivery_id
        )
        assert plan is not None and plan.status is ChannelDeliveryStatus.DELIVERED
        assert len(plan.parts) == 1 and plan.parts[0].kind is ChannelDeliveryPartKind.AUDIO
        history = await harness.container.conversation_repository.recent_history(
            result.session_id, uuid4(), limit=8
        )
        assert [item.text for item in history if item.role == "assistant"] == [SPOKEN]
        assert not list(harness.container.channel_voice.audio_root.glob("*.wav"))
        duplicate = await _ingest(harness, connection_id, "请用语音回复我", 10)
        assert duplicate.duplicate is True
        assert duplicate.channel_turn_id == receipt.channel_turn_id
        assert len(harness.synthesis) == 1 and harness.peer.sends.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("choose_voice", [False, True])
async def test_model_chooses_reply_medium_for_the_same_input_without_voice_keywords(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, choose_voice: bool
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        harness.model.voice_decision = choose_voice
        receipt = await _ingest(harness, connection_id, "今天有点累", 101)
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        request = harness.model.requests[0]
        assert len(request.tools) == 1 and request.tool_choice == "auto"
        assert "runtime_channel_reply_decision" in request.system_prompt
        assert "do not require particular keywords" in request.system_prompt
        assert _segments(sent)[0]["type"] == ("record" if choose_voice else "text")
        result = await _terminal(harness, connection_id, receipt.channel_turn_id)
        assert result.status is ChannelTurnStatus.COMPLETED
        assert result.reply_text == (SPOKEN if choose_voice else TEXT_REPLY)
        assert len(harness.synthesis) == int(choose_voice)
        assert len(harness.model.requests) == 1
        assert harness.peer.sends.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["请用语音说晚安", "不要发语音", "只根据上面的内容回答"])
async def test_model_can_choose_text_with_voice_tool_available_without_forced_retry(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        harness.model.voice_decision = False
        receipt = await _ingest(harness, connection_id, text, 102)
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        request = harness.model.requests[0]
        assert request.tools and request.tool_choice == "auto"
        assert "Honor a request to hear your voice or to" in request.system_prompt
        assert _segments(sent) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
        assert (await _terminal(harness, connection_id, receipt.channel_turn_id)).status is (
            ChannelTurnStatus.COMPLETED
        )
        assert len(harness.model.requests) == 1 and not harness.synthesis


@pytest.mark.asyncio
async def test_model_without_function_calling_still_answers_in_text(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        harness.model.supports_tool_calling = False
        receipt = await _ingest(harness, connection_id, "想听听你的声音", 103)
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert not harness.model.requests[0].tools
        assert _segments(sent)[0] == {"type": "text", "data": {"text": TEXT_REPLY}}
        assert not harness.synthesis
        assert (await _terminal(harness, connection_id, receipt.channel_turn_id)).status is (
            ChannelTurnStatus.COMPLETED
        )


@pytest.mark.asyncio
async def test_optional_voice_function_rejection_retries_plain_text_without_execution(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        harness.model.reject_voice_tools = True
        receipt = await _ingest(harness, connection_id, "想听听你的声音", 105)
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert len(harness.model.requests) == 2
        assert harness.model.requests[0].tools and not harness.model.requests[1].tools
        assert "Voice delivery is unavailable" in harness.model.requests[1].system_prompt
        assert _segments(sent)[0] == {"type": "text", "data": {"text": TEXT_REPLY}}
        assert not harness.synthesis and harness.peer.sends.empty()
        assert (await _terminal(harness, connection_id, receipt.channel_turn_id)).status is (
            ChannelTurnStatus.COMPLETED
        )


@pytest.mark.asyncio
async def test_rejected_voice_gets_one_truthful_text_fallback(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        harness.peer.reject_records = True
        connection_id = await _pair(harness)
        receipt = await _ingest(harness, connection_id, "请发一条语音", 11)
        first = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert _segments(first)[0]["type"] == "record"
        try:
            fallback = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        except TimeoutError:
            turn = await harness.container.external_channel_repository.get_turn(
                receipt.channel_turn_id
            )
            plan = (
                await harness.container.external_channel_repository.get_delivery_plan(
                    turn.delivery_id
                )
                if turn is not None and turn.delivery_id is not None
                else None
            )
            pytest.fail(
                f"Voice rejected without fallback: turn={turn!r}, plan={plan!r}, "
                f"runs={await harness.container.runtime_skills.list_runs(receipt.session_id)!r}"
            )
        assert _segments(fallback)[0]["type"] == "text"
        sent_text = cast(JsonObject, _segments(fallback)[0]["data"])["text"]
        assert isinstance(sent_text, str) and SPOKEN in sent_text and "语音" in sent_text
        result = await _terminal(harness, connection_id, receipt.channel_turn_id)
        assert result.status is ChannelTurnStatus.COMPLETED
        assert result.reply_text == sent_text
        assert harness.peer.sends.empty()
        assert len(harness.synthesis) == 1
        assert len(harness.model.requests) == 1
        runs = await harness.container.runtime_skills.list_runs(receipt.session_id)
        assert len(runs) == 1 and runs[0].result is not None
        data = runs[0].result.data
        assert isinstance(data, dict)
        assert data["delivery_status"] == "text_fallback"
        assert data["spoken_text"] == sent_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_data",
    [{}, {"message_id": "invalid-receipt"}, {"message_id": True}],
    ids=["missing-id", "invalid-id", "boolean-id"],
)
async def test_uncertain_voice_receipt_falls_back_once_and_survives_runtime_restart(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, response_data: JsonObject
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        harness.peer.record_response_data = response_data
        connection_id = await _pair(harness)
        receipt = await _ingest(harness, connection_id, "请发一条语音", 106)
        audio_send = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        fallback_send = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert _segments(audio_send)[0]["type"] == "record"
        fallback = _segments(fallback_send)
        assert len(fallback) == 1 and fallback[0]["type"] == "text"
        fallback_text = cast(JsonObject, fallback[0]["data"])["text"]
        assert fallback_text == f"这条语音的发送结果未确认，先把内容发成文字: {SPOKEN}"
        result = await _terminal(harness, connection_id, receipt.channel_turn_id)
        assert result.status is ChannelTurnStatus.COMPLETED
        assert result.reply_text == fallback_text
        assert result.delivery_id is not None
        repository = harness.container.external_channel_repository
        plan = await repository.get_delivery_plan(result.delivery_id)
        assert plan is not None and plan.status is ChannelDeliveryStatus.DELIVERED
        assert plan.plan_version == 2 and len(plan.parts) == 2
        audio, text = plan.parts
        assert audio.kind is ChannelDeliveryPartKind.AUDIO and not audio.required
        assert audio.status is ChannelDeliveryPartStatus.FAILED
        assert audio.last_error is not None and audio.last_error.code == "qq_delivery_unknown"
        assert audio.provider_message_id is None
        assert text.kind is ChannelDeliveryPartKind.TEXT and text.required
        assert text.status is ChannelDeliveryPartStatus.DELIVERED
        assert text.provider_message_id is not None
        cursor = await repository.get_adapter_cursor(connection_id)
        assert cursor is not None and json.loads(cursor)[audio.provider_client_id] == "unknown"
        history = await harness.container.conversation_repository.recent_history(
            result.session_id, uuid4(), limit=8
        )
        assert [item.text for item in history if item.role == "assistant"] == [fallback_text]
        assert len(harness.model.requests) == 1 and len(harness.synthesis) == 1
        assert harness.peer.sends.empty()
        assert not list(harness.container.channel_voice.audio_root.glob("*.wav"))

        await harness.container.stop()
        restarted = RuntimeContainer(runtime_settings)
        health = _configure(restarted, monkeypatch, harness.credentials)

        def create_model(_configuration: ModelRoleConfig) -> _Model:
            return harness.model

        monkeypatch.setattr(restarted.model_configurations, "create_chat_provider", create_model)
        await restarted.start()
        try:
            await asyncio.wait_for(harness.peer.connected.get(), timeout=3)
            current_id, status, code = await asyncio.wait_for(health.get(), timeout=3)
            assert current_id == connection_id and status is ChannelConnectionStatus.READY
            assert code is None
            duplicate = await _ingest(
                replace(harness, container=restarted), connection_id, "请发一条语音", 106
            )
            assert duplicate.duplicate and duplicate.channel_turn_id == receipt.channel_turn_id
            restored = await restarted.external_channel_repository.get_delivery_plan(
                result.delivery_id
            )
            assert restored == plan
            cursor = await restarted.external_channel_repository.get_adapter_cursor(connection_id)
            assert cursor is not None and json.loads(cursor)[audio.provider_client_id] == "unknown"
            assert len(harness.model.requests) == 1
            resumed = replace(harness, container=restarted)
            fresh = await _ingest(resumed, connection_id, "这次请文字回复", 107)
            sent = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
            assert _segments(sent) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
            fresh_result = await _terminal(resumed, connection_id, fresh.channel_turn_id)
            assert fresh_result.status is ChannelTurnStatus.COMPLETED
            assert len(harness.model.requests) == 2 and len(harness.synthesis) == 1
            assert (
                len([call for call in harness.peer.calls if call["action"] == "send_private_msg"])
                == 3
            )
        finally:
            await restarted.stop()
        assert harness.peer.sends.empty()
        assert not restarted.qq_channels._tasks and not restarted.qq_channels._schedulers


@pytest.mark.asyncio
async def test_synthesis_failure_still_sends_truthful_text_without_record(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)

        async def fail_synthesis(request: SynthesisRequest) -> SynthesisResult:
            harness.synthesis.append(request)
            raise RuntimeError("Test TTS unavailable")

        monkeypatch.setattr(harness.container.providers.tts, "synthesize", fail_synthesis)
        receipt = await _ingest(harness, connection_id, "用语音回复我", 15)
        params = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert _segments(params)[0]["type"] == "text"
        sent_text = cast(JsonObject, _segments(params)[0]["data"])["text"]
        assert isinstance(sent_text, str) and "语音发送失败" in sent_text
        result = await _terminal(harness, connection_id, receipt.channel_turn_id)
        assert result.status is ChannelTurnStatus.COMPLETED
        assert result.reply_text == sent_text
        assert len(harness.synthesis) == 1
        assert len(harness.model.requests) == 2
        assert harness.model.requests[-1].tool_exchanges[0].results[0].is_error
        assert harness.peer.sends.empty()
        assert not list(harness.container.channel_voice.audio_root.glob("*.wav"))


@pytest.mark.asyncio
async def test_account_changed_after_pairing_cannot_receive_a_stale_bound_reply(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        harness.peer.account = "10099"
        subscription = harness.container.event_hub.subscribe(
            lambda event: event.get("event_type") == "channel.delivery_plan_failed", queue_size=4
        )
        try:
            receipt = await _ingest(harness, connection_id, "普通文字回复", 16)
            event = await asyncio.wait_for(subscription.receive(), timeout=5)
            result = await _terminal(harness, connection_id, receipt.channel_turn_id)
            assert result.delivery_id is not None
            plan = await harness.container.external_channel_repository.get_delivery_plan(
                result.delivery_id
            )
            assert plan is not None and plan.status is ChannelDeliveryStatus.FAILED
            assert str(result.delivery_id) in json.dumps(event)
            assert harness.peer.sends.empty()
        finally:
            harness.container.event_hub.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_new_owner_turn_cancels_inflight_tts_and_cannot_send_stale_voice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        started = asyncio.Event()
        cancelled = asyncio.Event()
        never_complete = asyncio.Event()

        async def blocked(request: SynthesisRequest) -> SynthesisResult:
            harness.synthesis.append(request)
            started.set()
            try:
                await never_complete.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("Blocked TTS must be cancelled")

        monkeypatch.setattr(harness.container.providers.tts, "synthesize", blocked)
        old = await _ingest(harness, connection_id, "用语音回复", 12)
        await asyncio.wait_for(started.wait(), timeout=5)
        new = await _ingest(harness, connection_id, "改为普通文字", 13)
        await asyncio.wait_for(cancelled.wait(), timeout=3)
        params = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert _segments(params) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
        old_result = await _terminal(harness, connection_id, old.channel_turn_id)
        new_result = await _terminal(harness, connection_id, new.channel_turn_id)
        assert old_result.status is ChannelTurnStatus.CANCELLED
        assert new_result.status is ChannelTurnStatus.COMPLETED
        assert harness.peer.sends.empty()
        assert not list(harness.container.channel_voice.audio_root.glob("*.wav"))


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["manual", "external_mcp", "agent"])
async def test_voice_runtime_skill_rejects_unrelated_or_manual_execution(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        session = await harness.container.sessions.create_session("default")
        with pytest.raises(PermissionError, match="current authorized channel request"):
            await harness.container.runtime_skills.invoke(
                session.session_id,
                SkillInvocation(
                    skill_id="channel.voice", capability="send_voice", arguments={"text": SPOKEN}
                ),
                turn_id=uuid4(),
                generation_id=uuid4(),
                origin=cast(Literal["manual", "external_mcp", "agent"], origin),
            )
        assert not harness.synthesis and not harness.peer.calls


@pytest.mark.asyncio
async def test_current_text_turn_cannot_manually_escalate_to_voice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        harness.model.release = asyncio.Event()
        receipt = await _ingest(harness, connection_id, "解释发语音是什么意思", 14)
        request = await asyncio.wait_for(harness.model.received.get(), timeout=3)
        assert request.tools and request.tool_choice == "auto"
        with pytest.raises(PermissionError, match="current authorized channel request"):
            await harness.container.runtime_skills.invoke(
                receipt.session_id,
                SkillInvocation(
                    skill_id="channel.voice", capability="send_voice", arguments={"text": SPOKEN}
                ),
                turn_id=receipt.turn_id,
                generation_id=receipt.generation_id,
                origin="manual",
            )
        harness.model.release.set()
        params = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert _segments(params)[0]["type"] == "text"
        assert not harness.synthesis


@pytest.mark.asyncio
async def test_model_voice_permission_does_not_expand_to_another_audio_provider(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        harness.model.release = asyncio.Event()
        receipt = await _ingest(harness, connection_id, "今天有点累", 104)
        await asyncio.wait_for(harness.model.received.get(), 3)
        connection = await harness.container.external_channel_repository.get_connection(
            connection_id
        )
        assert connection is not None
        other = replace(
            connection,
            configuration=connection.configuration.model_copy(
                update={"provider_id": "other_audio"}
            ),
        )

        async def other_connection(_connection_id: UUID) -> ChannelConnectionRecord:
            return other

        def supports_other_audio(_provider_id: str) -> bool:
            return True

        with monkeypatch.context() as scoped:
            scoped.setattr(
                harness.container.external_channel_repository, "get_connection", other_connection
            )
            scoped.setattr(harness.container.channel_voice, "_supports_audio", supports_other_audio)
            with pytest.raises(PermissionError, match="current authorized channel request"):
                await harness.container.runtime_skills.invoke(
                    receipt.session_id,
                    SkillInvocation(
                        skill_id="channel.voice",
                        capability="send_voice",
                        arguments={"text": SPOKEN},
                    ),
                    turn_id=receipt.turn_id,
                    generation_id=receipt.generation_id,
                    origin="agent",
                )
        harness.model.release.set()
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert _segments(sent)[0]["type"] == "text" and not harness.synthesis


@pytest.mark.asyncio
@pytest.mark.parametrize("caption", ["", "请用语音回复我"])
async def test_owner_image_reaches_vision_once_without_retention_or_voice_escalation(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, caption: str
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        data = _picture()
        downloads: list[str] = []
        observations: list[str] = []

        async def download(_client: NapCatClient, file_ref: str, *, max_bytes: int) -> bytes:
            # The provider read starts only after the durable owner turn exists.
            admitted = (
                await harness.container.external_channel_repository.find_turn_by_external_message(
                    connection_id, "40"
                )
            )
            assert admitted is not None and admitted.sender_key == OWNER
            assert max_bytes == 5 * 1024 * 1024
            downloads.append(file_ref)
            return data

        async def observe(*_args: object, **_kwargs: object) -> None:
            observations.append("unexpected retention")

        monkeypatch.setattr(NapCatClient, "download_image", download)
        monkeypatch.setattr(harness.container.photo_observer, "observe_batch", observe)
        monkeypatch.setattr(harness.container.sticker_library._classifier, "classify", observe)
        event = _image_event(caption, 40, reply_id=999)
        await harness.peer.peers[-1].send(json.dumps(event))
        request = await asyncio.wait_for(harness.model.received.get(), timeout=5)
        assert request.user_text == (caption or "[图片]")
        assert len(request.images) == 1 and request.images[0].data == data
        assert request.tools and request.tool_choice == "auto"
        sent = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        expected_kind = "record" if caption else "text"
        assert _segments(sent)[0] == {"type": "reply", "data": {"id": "40"}}
        assert _segments(sent)[1]["type"] == expected_kind
        turn = await harness.container.external_channel_repository.find_turn_by_external_message(
            connection_id, "40"
        )
        assert turn is not None
        result = await _terminal(harness, connection_id, turn.channel_turn_id)
        assert result.status is ChannelTurnStatus.COMPLETED
        assert not observations
        assert not (await harness.container.sticker_repository.snapshot("local", "default")).items
        assert downloads == ["photo.png"]
        assert len(harness.synthesis) == int(bool(caption))

        # A later wire event proves the duplicate frame was processed without reload.
        await harness.peer.peers[-1].send(json.dumps(event))
        await harness.peer.peers[-1].send(json.dumps(_event("继续文字聊天", 41)))
        next_request = await asyncio.wait_for(harness.model.received.get(), timeout=5)
        assert next_request.user_text == "继续文字聊天" and not next_request.images
        await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert downloads == ["photo.png"] and not observations
        assert len(harness.model.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["too_many", "expired", "unsupported_format"])
async def test_admitted_image_failure_sends_one_durable_notice_without_model_request(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        downloads: list[str] = []

        async def download(_client: NapCatClient, file_ref: str, *, max_bytes: int) -> bytes:
            downloads.append(file_ref)
            if failure == "expired":
                raise RuntimeError("private-filename-and-token")
            output = io.BytesIO()
            Image.new("RGB", (12, 8), "red").save(output, format="BMP")
            return output.getvalue()

        monkeypatch.setattr(NapCatClient, "download_image", download)
        files = (
            tuple(f"photo{i}.png" for i in range(5)) if failure == "too_many" else ("photo.png",)
        )
        await harness.peer.peers[-1].send(json.dumps(_image_event("看看图片", 42, files=files)))
        sent = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        segments = _segments(sent)
        assert len(segments) == 1 and segments[0]["type"] == "text"
        notice = cast(JsonObject, segments[0]["data"])["text"]
        assert isinstance(notice, str) and "图片" in notice and "再发" in notice
        assert "private" not in notice and "photo" not in notice
        turn = await harness.container.external_channel_repository.find_turn_by_external_message(
            connection_id, "42"
        )
        assert turn is not None
        result = await _terminal(harness, connection_id, turn.channel_turn_id)
        assert result.status is ChannelTurnStatus.FAILED
        assert result.error is not None and result.error.code == "image_input_error"
        assert result.delivery_id is not None
        plan = await harness.container.external_channel_repository.get_delivery_plan(
            result.delivery_id
        )
        assert plan is not None and plan.status is ChannelDeliveryStatus.DELIVERED
        assert not harness.model.requests and not harness.synthesis
        assert len(downloads) == (0 if failure == "too_many" else 1)
        assert harness.peer.sends.empty()


@pytest.mark.asyncio
async def test_cleared_image_context_and_new_text_cancel_loading_without_stale_vision_or_reply(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def download(_client: NapCatClient, file_ref: str, *, max_bytes: int) -> bytes:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return _picture()

        monkeypatch.setattr(NapCatClient, "download_image", download)
        await harness.peer.peers[-1].send(json.dumps(_image_event("看图", 43)))
        await asyncio.wait_for(entered.wait(), timeout=5)
        # Explicit context revocation removes the recent descriptor; a following
        # turn must still cancel the original loader and reject stale vision.
        harness.container.external_channels.fence_recent_images(connection_id)
        await harness.peer.peers[-1].send(json.dumps(_event("取消图片，直接文字聊", 44)))
        await asyncio.wait_for(cancelled.wait(), timeout=5)
        request = await asyncio.wait_for(harness.model.received.get(), timeout=5)
        assert request.user_text == "取消图片，直接文字聊" and not request.images
        sent = await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert _segments(sent) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
        old = await harness.container.external_channel_repository.find_turn_by_external_message(
            connection_id, "43"
        )
        assert old is not None
        result = await _terminal(harness, connection_id, old.channel_turn_id)
        assert result.status is ChannelTurnStatus.CANCELLED
        assert len(harness.model.requests) == 1 and harness.peer.sends.empty()


@pytest.mark.asyncio
async def test_non_owner_group_and_unsafe_image_refs_never_start_provider_download(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        downloads: list[str] = []

        async def download(_client: NapCatClient, file_ref: str, *, max_bytes: int) -> bytes:
            downloads.append(file_ref)
            return _picture()

        monkeypatch.setattr(NapCatClient, "download_image", download)
        socket = harness.peer.peers[-1]
        await socket.send(json.dumps(_image_event("陌生人图片", 45, sender="30003")))
        await socket.send(json.dumps(_image_event("群聊图片", 46, message_type="group")))
        await socket.send(json.dumps(_image_event("路径图片", 47, files=("../private.png",))))
        await socket.send(json.dumps(_event("正常文字消息", 48)))
        request = await asyncio.wait_for(harness.model.received.get(), timeout=5)
        assert request.user_text == "正常文字消息" and not request.images
        await asyncio.wait_for(harness.peer.sends.get(), timeout=5)
        assert not downloads
        for message_id in ("45", "46", "47"):
            assert (
                await harness.container.external_channel_repository.find_turn_by_external_message(
                    connection_id, message_id
                )
                is None
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_credentials", [True, False])
async def test_restart_reports_missing_credentials_or_changed_account_without_reply(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, missing_credentials: bool
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        await harness.container.stop()
        if missing_credentials:
            await harness.credentials.delete(credential_reference(connection_id))
        else:
            harness.peer.account = "10099"
        restarted = RuntimeContainer(runtime_settings)
        health = _configure(restarted, monkeypatch, harness.credentials)
        await restarted.start()
        try:
            current_id, status, code = await asyncio.wait_for(health.get(), timeout=3)
            assert current_id == connection_id and status is ChannelConnectionStatus.ERROR
            expected = "qq_credentials_missing" if missing_credentials else "qq_account_changed"
            assert code == expected
            snapshot = await restarted.external_channels.get_connection(connection_id)
            assert snapshot.status is ChannelConnectionStatus.ERROR
            assert snapshot.last_error is not None and snapshot.last_error.code == expected
            assert harness.peer.sends.empty()
        finally:
            await restarted.stop()
        assert not restarted.qq_channels._tasks
        assert not restarted.qq_channels._schedulers


@pytest.mark.asyncio
async def test_cancel_pairing_and_shutdown_close_sockets_without_enrolling(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        pairing = await harness.container.qq_channels.begin_pairing(
            ChannelPairingStartRequest(
                endpoint=harness.peer.endpoint, access_token=SecretStr(TOKEN)
            )
        )
        peer = await asyncio.wait_for(harness.peer.connected.get(), timeout=3)
        await harness.container.qq_channels.cancel_pairing(pairing.pairing_id)
        snapshot = await harness.container.qq_channels.pairing(pairing.pairing_id)
        assert snapshot.status == "cancelled" and snapshot.pairing_code is None
        await asyncio.wait_for(peer.wait_closed(), timeout=3)
        assert not await harness.container.external_channels.list_connections()
        assert not harness.container.qq_channels._pair_tasks
        second = await harness.container.qq_channels.begin_pairing(
            ChannelPairingStartRequest(
                endpoint=harness.peer.endpoint, access_token=SecretStr(TOKEN)
            )
        )
        second_peer = await asyncio.wait_for(harness.peer.connected.get(), timeout=3)
        await harness.container.stop()
        await asyncio.wait_for(second_peer.wait_closed(), timeout=3)
        assert not harness.container.qq_channels._pair_tasks
        assert not harness.container.qq_channels._tasks
        snapshot = await harness.container.qq_channels.pairing(second.pairing_id)
        assert snapshot.status == "cancelled"


@pytest.mark.asyncio
async def test_owner_public_read_and_followup_use_receipts_without_confirmation_or_voice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[JsonObject] = []

    async def read(_self: PublicWebReader, arguments: JsonObject) -> JsonObject:
        reads.append(arguments)
        return _public_source()

    monkeypatch.setattr(PublicWebReader, "read", read)
    async with _runtime(_public_web_settings(runtime_settings), monkeypatch) as harness:
        connection_id = await _pair(harness)
        _source_model(harness, monkeypatch)
        receipt = await _ingest(
            harness, connection_id, f"请读取 {SOURCE_URL} 官方网页并总结。", 201
        )
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        terminal = await _terminal(harness, connection_id, receipt.channel_turn_id)
        assert terminal.status is ChannelTurnStatus.COMPLETED
        assert SOURCE_BODY in (terminal.reply_text or "")
        assert reads == [{"url": SOURCE_URL}]
        assert _segments(sent)[0]["type"] == "text"
        assert not harness.synthesis
        assert not await harness.container.runtime_skills.pending_confirmations(receipt.session_id)
        assert all(
            "text" not in cast(list[str], tool.input_schema.get("required", []))
            for tool in harness.model.requests[0].tools
        )
        assert any(SOURCE_BODY in str(request.tool_exchanges) for request in harness.model.requests)

        await _ingest(
            harness, connection_id, "只根据刚才已读取的资料整理成清单，不要重新联网。", 202
        )
        followup = await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert _segments(followup)[0]["type"] == "text"
        assert reads == [{"url": SOURCE_URL}]
        assert any(SOURCE_BODY in str(request.context) for request in harness.model.requests[1:])
        assert harness.peer.sends.empty()
        with pytest.raises(PermissionError, match="Non-interactive"):
            await harness.container.runtime_skills.invoke(
                receipt.session_id,
                SkillInvocation(
                    skill_id="web.read", capability="read", arguments={"url": SOURCE_URL}
                ),
                allow_confirmation=False,
            )
        assert reads == [{"url": SOURCE_URL}]


@pytest.mark.asyncio
async def test_public_web_opt_in_keeps_ordinary_reply_medium_a_model_choice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(_public_web_settings(runtime_settings), monkeypatch) as harness:
        connection_id = await _pair(harness)
        harness.model.voice_decision = True
        await _ingest(harness, connection_id, "今天有点累", 203)
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        tools = harness.model.requests[0].tools
        assert len(tools) == 4
        assert {t.name for t in tools} >= {
            "discover_capabilities",
            "inspect_capability",
            "activate_capabilities",
        }
        assert _segments(sent)[0]["type"] == "record"
        assert len(harness.synthesis) == 1


@pytest.mark.asyncio
async def test_owner_public_read_rechecks_authorization_before_adapter_execution(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[JsonObject] = []

    async def read(_self: PublicWebReader, arguments: JsonObject) -> JsonObject:
        reads.append(arguments)
        return _public_source()

    monkeypatch.setattr(PublicWebReader, "read", read)
    async with _runtime(_public_web_settings(runtime_settings), monkeypatch) as harness:
        connection_id = await _pair(harness)
        _source_model(harness, monkeypatch)
        policy = harness.container.runtime_skills._generation_permission_policy
        assert policy is not None
        checks = 0

        async def revoked(context: GenerationSkillContext, skill_id: str) -> bool:
            nonlocal checks
            checks += 1
            return await policy(context, skill_id) if checks == 1 else False

        monkeypatch.setattr(
            harness.container.runtime_skills, "_generation_permission_policy", revoked
        )
        receipt = await _ingest(
            harness, connection_id, f"请读取 {SOURCE_URL} 官方网页并总结。", 204
        )
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        await _terminal(harness, connection_id, receipt.channel_turn_id)
        assert checks == 2
        assert not reads
        rows = await harness.container.database.fetchall(
            "SELECT state, error_json FROM skill_runs WHERE skill_id = 'web.read'"
        )
        assert len(rows) == 1 and rows[0]["state"] == "failed"
        assert "stale_channel_request" in str(rows[0]["error_json"])


@pytest.mark.asyncio
async def test_new_owner_input_cancels_public_read_without_stale_delivery(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def read(_self: PublicWebReader, _arguments: JsonObject) -> JsonObject:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return _public_source()

    monkeypatch.setattr(PublicWebReader, "read", read)
    async with _runtime(_public_web_settings(runtime_settings), monkeypatch) as harness:
        connection_id = await _pair(harness)
        original_stream = harness.model.stream
        _source_model(harness, monkeypatch)
        old = await _ingest(harness, connection_id, f"请读取 {SOURCE_URL} 官方网页并总结。", 205)
        await asyncio.wait_for(started.wait(), 5)
        monkeypatch.setattr(harness.model, "stream", original_stream)
        new = await _ingest(harness, connection_id, "先停止，只用文字回答。", 206)
        await asyncio.wait_for(cancelled.wait(), 5)
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert _segments(sent) == [{"type": "text", "data": {"text": TEXT_REPLY}}]
        assert (
            await _terminal(harness, connection_id, old.channel_turn_id)
        ).status is ChannelTurnStatus.CANCELLED
        assert (
            await _terminal(harness, connection_id, new.channel_turn_id)
        ).status is ChannelTurnStatus.COMPLETED
        assert harness.peer.sends.empty()
        assert not harness.container.runtime_skills._policy_authorized_runs


@pytest.mark.asyncio
async def test_owner_public_search_reads_discovered_body_before_text_delivery(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations: list[str] = []

    async def search(_self: PublicWebSearch, arguments: JsonObject) -> JsonObject:
        operations.append("search")
        return {
            "provider": "duckduckgo_lite",
            "query": arguments["query"],
            "effective_query": arguments["query"],
            "search_url": "https://lite.duckduckgo.com/lite/?q=fixture",
            "retrieved_at": datetime.now(UTC).isoformat(),
            "body_sha256": "a" * 64,
            "dns_resolver": "system",
            "results": [{"url": SOURCE_URL, "title": "测试说明", "snippet": "须读取原文"}],
            "total_matched": 1,
            "filtered_count": 0,
            "truncated": False,
            "empty_reason": None,
        }

    async def read(_self: PublicWebReader, arguments: JsonObject) -> JsonObject:
        assert arguments["url"] == SOURCE_URL
        operations.append("read")
        return _public_source()

    monkeypatch.setattr(PublicWebSearch, "search", search)
    monkeypatch.setattr(PublicWebReader, "read", read)
    async with _runtime(_public_web_settings(runtime_settings), monkeypatch) as harness:
        connection_id = await _pair(harness)
        _source_model(harness, monkeypatch, search_first=True)
        receipt = await _ingest(harness, connection_id, "请搜索公开测试说明，读取来源并总结。", 207)
        sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
        result = await _terminal(harness, connection_id, receipt.channel_turn_id)
        assert operations == ["search", "read"]
        assert result.status is ChannelTurnStatus.COMPLETED
        assert SOURCE_BODY in (result.reply_text or "")
        assert _segments(sent)[0]["type"] == "text"
        assert not harness.synthesis
        assert not await harness.container.runtime_skills.pending_confirmations(receipt.session_id)
        assert harness.peer.sends.empty()
