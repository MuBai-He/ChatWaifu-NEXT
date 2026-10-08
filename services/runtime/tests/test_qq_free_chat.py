"""Free conversation stays model-selected, scoped, silent on wait and cancellable."""

# pyright: reportPrivateUsage=false
import asyncio
import json
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from chatwaifu_protocol.agent import DecisionRecord
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channel_settings import ChannelRuntimeSettingsUpdate
from chatwaifu_protocol.channels import ChannelTurnStatus
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.qq_napcat.audio import NapCatAudioTranscriber
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatClient
from chatwaifu_runtime.external_channels.adapters.qq_napcat.messages import (
    normalize_group_inbound,
    normalize_poke,
)
from chatwaifu_runtime.external_channels.service import ChannelPolicyError
from test_channel_audio_ingress import _Audio, _audio_runtime, _message
from test_qq_channels import ACCOUNT, OWNER, _ingest

from services.runtime.tests.test_agent_autonomy import DecisionModel, configure
from services.runtime.tests.test_channel_group_application import TOKEN, App

pytest_plugins = ["services.runtime.tests.test_channel_group_application"]


def poke() -> JsonObject:
    return {
        "post_type": "notice",
        "notice_type": "notify",
        "sub_type": "poke",
        "self_id": 900,
        "user_id": 111,
        "target_id": 900,
        "time": int(datetime.now(UTC).timestamp()),
        "group_id": 500,
    }


def test_poke_is_a_stable_bot_targeted_observation_not_a_forced_command() -> None:
    event = poke()
    first = normalize_poke(event, account="900")
    assert first is not None
    assert first == normalize_poke(dict(reversed(list(event.items()))), account="900")
    message = normalize_group_inbound(
        first,
        connection_id=uuid4(),
        account="900",
        group_id="500",
        allowed_senders=frozenset({"111"}),
        allow_unmentioned_text=True,
    )
    assert message is not None and not message.bot_mentioned
    assert "并未发出文字请求" in message.text
    for patch in (
        {"target_id": 112},
        {"user_id": 900},
        {"self_id": 901},
        {"time": True},
        {"user_id": "0111"},
        {"group_id": True},
    ):
        assert normalize_poke({**event, **patch}, account="900") is None


def test_group_record_requires_opt_in_and_cannot_hide_mixed_content() -> None:
    event = normalize_poke(poke(), account="900")
    assert event is not None
    event["message"] = [{"type": "record", "data": {"file": "voice.amr"}}]

    def parse(*, allow_audio: bool = False):
        return normalize_group_inbound(
            event,
            connection_id=uuid4(),
            account="900",
            group_id="500",
            allowed_senders=None,
            allow_unmentioned_text=True,
            allow_audio=allow_audio,
        )

    assert parse() is None
    result = parse(allow_audio=True)
    assert result is not None and result.record is not None and result.text == "[语音]"
    event["message"] = [
        {"type": "record", "data": {"file": "voice.amr"}},
        {"type": "text", "data": {"text": "unparsed mixed content"}},
    ]
    assert parse(allow_audio=True) is None


async def test_group_audio_authorizes_before_loading_and_selects_reply_from_transcript(
    app: App,
) -> None:
    model = DecisionModel("respond")
    await configure(app, "member", model)
    loaded: list[str] = []

    async def load() -> str:
        loaded.append("called")
        return "宁宁，这条语音里的问题你会吗？"

    with pytest.raises(ChannelPolicyError):
        await app.service.observe_group_audio(
            app.message("100", sender="999", text="[语音]"),
            access_token=TOKEN,
            text_loader=load,
        )
    assert not loaded
    await app.service.observe_group_audio(
        app.message("100", text="[语音]"),
        access_token=TOKEN,
        text_loader=load,
    )
    await asyncio.wait_for(app.container.group_autonomy._workers[app.route.route_id], 5)
    request = await asyncio.wait_for(app.provider.started.get(), 3)
    assert request.user_text.startswith("宁宁，这条语音") and loaded == ["called"]
    assert "[语音]" not in model.requests[0].user_text


@pytest.mark.parametrize("voice", [False, True])
async def test_private_wait_is_silent_and_voice_decision_sees_transcript(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    voice: bool,
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        c = harness.container
        settings = c.channel_settings.get()
        await c.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=settings.revision,
                policy=settings.policy.model_copy(update={"qq_free_chat_enabled": True}),
            )
        )
        seen: list[JsonObject] = []

        async def decide(
            _persona: str, situation: JsonObject, refs: frozenset[str]
        ) -> DecisionRecord:
            seen.append(situation)
            return DecisionRecord(action="wait", reason="主人已结束话题", source_refs=list(refs))

        monkeypatch.setattr(c.behavior_decisions, "decide", decide)
        if voice:
            audio = _Audio("不用回复了，我先去忙。")
            audio.release.set()
            receipt = await c.external_channels.ingest(
                _message(connection_id),
                access_token=token,
                audio_input=audio.input(),
            )
        else:
            receipt = await _ingest(harness, connection_id, "不用回复了，我先去忙。", 80)
        result = await c.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            wait_seconds=5,
        )
        assert result.status is ChannelTurnStatus.CANCELLED and result.error is None
        assert len(seen) == 1 and "不用回复了" in str(seen[0]["messages"])
        assert not harness.model.requests and not harness.synthesis and harness.peer.sends.empty()
        assert not await c.database.fetchall("SELECT * FROM turns")


async def test_private_policy_change_cancels_held_decision_without_late_reply(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, _token):
        c = harness.container
        original = c.channel_settings.get()
        await c.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=original.revision,
                policy=original.policy.model_copy(update={"qq_free_chat_enabled": True}),
            )
        )
        started = asyncio.Event()
        hold = asyncio.Event()

        async def decide(
            _persona: str, _situation: JsonObject, refs: frozenset[str]
        ) -> DecisionRecord:
            started.set()
            await hold.wait()
            return DecisionRecord(action="respond", reason="回应问题", source_refs=list(refs))

        monkeypatch.setattr(c.behavior_decisions, "decide", decide)
        pending = asyncio.create_task(_ingest(harness, connection_id, "宁宁你好", 81))
        await asyncio.wait_for(started.wait(), 3)
        turn_id = next(iter(c.external_channels._response_tasks))
        snapshot = await c.external_channels.wait_for_turn(connection_id, turn_id, wait_seconds=0)
        assert snapshot.status is ChannelTurnStatus.ACCEPTED
        current = c.channel_settings.get()
        await c.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=current.revision,
                policy=original.policy,
            )
        )
        with pytest.raises(asyncio.CancelledError):
            await pending
        hold.set()
        assert not c.external_channels._response_tasks
        assert not harness.model.requests and harness.peer.sends.empty()


async def test_private_respond_reaches_real_delivery_fixture(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, _token):
        c = harness.container
        original = c.channel_settings.get()
        await c.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=original.revision,
                policy=original.policy.model_copy(update={"qq_free_chat_enabled": True}),
            )
        )

        async def decide(
            _persona: str, _situation: JsonObject, refs: frozenset[str]
        ) -> DecisionRecord:
            return DecisionRecord(action="respond", reason="回应主人", source_refs=list(refs))

        monkeypatch.setattr(c.behavior_decisions, "decide", decide)
        receipt = await _ingest(harness, connection_id, "宁宁你好", 82)
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        result = await c.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            wait_seconds=5,
        )
        assert result.status is ChannelTurnStatus.COMPLETED
        assert len(harness.model.requests) == 1


async def test_real_socket_poke_reaches_private_behavior_without_forced_reply(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, _token):
        c = harness.container
        original = c.channel_settings.get()
        await c.channel_settings.update(
            ChannelRuntimeSettingsUpdate(
                expected_revision=original.revision,
                policy=original.policy.model_copy(update={"qq_free_chat_enabled": True}),
            )
        )
        observed = asyncio.Event()
        inputs: list[JsonObject] = []

        async def decide(
            _persona: str, situation: JsonObject, refs: frozenset[str]
        ) -> DecisionRecord:
            inputs.append(situation)
            observed.set()
            return DecisionRecord(action="wait", reason="不需要回应戳一戳", source_refs=list(refs))

        monkeypatch.setattr(c.behavior_decisions, "decide", decide)
        event = {
            **poke(),
            "self_id": int(ACCOUNT),
            "target_id": int(ACCOUNT),
            "user_id": int(OWNER),
            "group_id": 0,
        }
        await harness.peer.peers[-1].send(json.dumps(event))
        await asyncio.wait_for(observed.wait(), 5)
        assert "当前发言者戳了你" in str(inputs[0]["messages"])
        rows = await c.database.fetchall("SELECT channel_turn_id FROM channel_turns")
        assert len(rows) == 1
        from uuid import UUID

        terminal = await c.external_channels.wait_for_turn(
            connection_id,
            UUID(rows[0]["channel_turn_id"]),
            wait_seconds=5,
        )
        assert terminal.status is ChannelTurnStatus.CANCELLED
        assert not harness.model.requests and harness.peer.sends.empty()


@pytest.mark.parametrize("matching_source", [False, True])
async def test_group_transport_checks_audio_source_before_transcription(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
    matching_source: bool,
) -> None:
    model = DecisionModel("wait")
    await configure(app, "member", model)
    manager = app.container.qq_channels
    manager._groups = app.service
    manager.free_chat_enabled = lambda: True

    async def free(_descriptor: object) -> bool:
        return True

    manager.group_free_chat = free

    def ready(_id: object) -> bool:
        return True

    monkeypatch.setattr(manager, "_group_transport_ready", ready)
    event: JsonObject = {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "self_id": 900,
        "group_id": 500,
        "user_id": 111,
        "sender": {"user_id": 111},
        "message_id": 100,
        "message": [{"type": "record", "data": {"file": "voice.amr"}}],
    }
    calls: list[str] = []

    class Client:
        async def call(self, action: str, params: JsonObject) -> JsonObject:
            assert action == "get_msg" and params == {"message_id": "100"}
            calls.append(action)
            return {**event, "group_id": 500 if matching_source else 501}

    async def transcribe(
        _self: object, _transport: object, _record: object, _identity: object
    ) -> str:
        calls.append("transcribe")
        return "语音里的原始内容"

    monkeypatch.setattr(NapCatAudioTranscriber, "prepare", transcribe)
    manager._clients[app.connection_id] = cast(NapCatClient, Client())
    try:
        manager._dispatch_group(event, app.connection_id, "900", TOKEN)
        tasks = tuple(manager._group_ingress_tasks)
        assert len(tasks) == 1
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
        if matching_source:
            await asyncio.wait_for(app.container.group_autonomy._workers[app.route.route_id], 5)
            assert calls == ["get_msg", "transcribe"] and len(model.requests) == 1
        else:
            assert calls == ["get_msg"] and not model.requests
        assert not app.provider.requests
    finally:
        manager._clients.pop(app.connection_id, None)
