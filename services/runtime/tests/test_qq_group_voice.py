"""Group voice through the actual container, SQLite and loopback OneBot."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from chatwaifu_protocol.channel_groups import ChannelGroupPauseReason, ChannelGroupRouteSnapshot
from chatwaifu_protocol.channel_settings import ChannelRuntimePolicy, ChannelRuntimeSettingsUpdate
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartKind,
    ChannelDeliveryStatus,
    ChannelTurnStatus,
)
from chatwaifu_protocol.skills import SkillInvocation
from chatwaifu_runtime.external_channels.group_voice import requests_group_voice
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
    LlmToolCall,
    LlmToolCallRequested,
    SynthesisRequest,
    SynthesisResult,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig

from services.runtime.tests import test_qq_channels as private
from services.runtime.tests.test_qq_group_runtime import (
    ALICE,
    BOB,
    OTHER_GROUP,
    _group_event,
    _notice,
    _Runtime,
)
from services.runtime.tests.test_qq_group_runtime import (
    runtime as runtime,
)

SPOKEN = "早上好，今天也加油呀。"
TEXT = "普通文字回复。"


class _VoiceModel:
    kind = "group-voice-fixture"
    supports_tool_calling = True

    def __init__(self, *, choose_voice: bool = True) -> None:
        self.requests: list[LlmRequest] = []
        self.choose_voice = choose_voice

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        if request.tools and self.choose_voice and not request.tool_exchanges:
            assert len(request.tools) == 1 and request.tool_choice == "auto"
            yield LlmToolCallRequested(
                LlmToolCall("group-voice", request.tools[0].name, {"text": SPOKEN})
            )
            yield LlmResponseCompleted("tool_calls")
        else:
            yield LlmTextDelta(TEXT)
            yield LlmResponseCompleted("stop")


def _model(runtime: _Runtime, monkeypatch: pytest.MonkeyPatch) -> _VoiceModel:
    model = _VoiceModel()

    def provider(_config: ModelRoleConfig) -> _VoiceModel:
        return model

    monkeypatch.setattr(runtime.container.model_configurations, "create_chat_provider", provider)
    return model


async def _voice_route(runtime: _Runtime) -> ChannelGroupRouteSnapshot:
    route = await runtime.route()
    observed = await runtime.observe(route.group_id)
    response = await runtime.request(
        "PUT",
        f"{runtime.path}/group-routes/{route.route_id}",
        {
            "enabled": True,
            "expected_revision": route.revision,
            "observation_id": str(observed.observation_id),
            "speaker_sender_keys": [m.sender_key for m in route.members if m.can_speak],
            "allow_requested_voice": True,
        },
    )
    return ChannelGroupRouteSnapshot.model_validate(response.json())


@pytest.mark.parametrize(
    "text,expected",
    [
        ("发个语音骂他", True),
        ("用语音说一句早上好", True),
        ("请把刚才的回答读出来", True),
        ("能用你的声音说句晚安吗", True),
        ("能不能用语音说句晚安", True),
        ("可不可以发个语音", True),
        ("send me a voice reply please", True),
        ("read this aloud please", True),
        ("请朗读一下这段话", True),
        ("朗读这段话", True),
        ("你好", False),
        ("[仅 @ 角色]", False),
        ("不要发语音，文字就好", False),
        ("别用语音说", False),
        ("他说\u2018发个语音\u2019。你怎么看？", False),
        ("`用语音回复` 这几个字是什么意思", False),
        ("上次让你发个语音，这次聊别的", False),
        ("为什么不能发语音", False),
        ("朗读是什么意思", False),
        ("语音回复是什么", False),
        ("我喜欢朗读", False),
    ],
)
def test_only_a_current_direct_request_opens_group_voice(text: str, expected: bool) -> None:
    assert requests_group_voice(text) is expected


@pytest.mark.parametrize("sender", [ALICE, BOB])
@pytest.mark.parametrize("private_voice_enabled", [False, True])
async def test_opted_in_member_voice_then_text_and_other_group_stays_text(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch, sender: str, private_voice_enabled: bool
) -> None:
    await runtime.container.channel_settings.update(
        ChannelRuntimeSettingsUpdate(
            expected_revision=0,
            policy=ChannelRuntimePolicy(qq_owner_voice_reply_enabled=private_voice_enabled),
        )
    )
    model = _model(runtime, monkeypatch)
    route = await _voice_route(runtime)
    other = await runtime.route(OTHER_GROUP)
    assert route.allow_requested_voice and not other.allow_requested_voice
    await runtime.send(_group_event(810, "用语音说早上好", sender=sender))
    try:
        sent = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    except TimeoutError:
        row = await runtime.container.channel_group_repository.find_group_turn(
            runtime.connection_id, route.group_id, "810"
        )
        assert row is not None
        plan = (
            await runtime.container.external_channel_repository.get_delivery_plan(
                row.turn.delivery_id
            )
            if row.turn.delivery_id is not None
            else None
        )
        pytest.fail(
            f"turn={row.turn!r}; plan={plan!r}; runs="
            f"{await runtime.container.runtime_skills.list_runs(row.turn.session_id)!r}; "
            f"synthesis={len(runtime.base.synthesis)}"
        )
    assert sent["group_id"] == route.group_id
    assert private._segments(sent)[0]["type"] == "record", {
        "wire": sent,
        "tools": [[t.name for t in r.tools] for r in model.requests],
        "synthesis_count": len(runtime.base.synthesis),
        "exchanges": [repr(r.tool_exchanges) for r in model.requests],
    }
    assert len(private._segments(sent)) == 1
    done = await runtime.terminal(route, 810)
    assert done.turn.reply_text == SPOKEN and done.provider_receipt_present
    assert done.turn.status is ChannelTurnStatus.COMPLETED
    assert len(runtime.base.synthesis) == 1 and len(model.requests) == 1
    assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
    assert not list(runtime.container.channel_voice.audio_root.glob("*.wav"))
    await runtime.send(_group_event(811, "你好", sender=sender))
    ordinary = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert private._segments(ordinary)[0]["type"] == "text"
    await runtime.terminal(route, 811)
    assert not model.requests[-1].tools and len(runtime.base.synthesis) == 1
    await runtime.send(_group_event(812, "用语音说早上好", group=OTHER_GROUP, sender=sender))
    denied = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert private._segments(denied)[0]["type"] == "text"
    await runtime.terminal(other, 812)
    assert not model.requests[-1].tools and len(runtime.base.synthesis) == 1
    # An old operator client omitting the field preserves opt-in across revalidation.
    retained = await runtime.enable(route)
    assert retained.allow_requested_voice


async def test_group_model_may_choose_text_even_when_current_request_opens_voice(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _model(runtime, monkeypatch)
    model.choose_voice = False
    route = await _voice_route(runtime)
    await runtime.send(_group_event(820, "用语音说早上好"))
    sent = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert private._segments(sent)[0]["type"] == "text"
    await runtime.terminal(route, 820)
    assert model.requests[0].tools and not runtime.base.synthesis
    assert len(model.requests) == 1


async def test_group_audio_rejection_retains_audit_and_one_truthful_text_fallback(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _model(runtime, monkeypatch)
    route = await _voice_route(runtime)
    runtime.peer.reject_records = True
    await runtime.send(_group_event(830, "用语音说早上好"))
    attempted = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    fallback = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert private._segments(attempted)[0]["type"] == "record"
    assert private._segments(fallback)[0]["type"] == "text"
    done = await runtime.terminal(route, 830)
    assert done.turn.reply_text and "语音没能发出去" in done.turn.reply_text
    assert SPOKEN in done.turn.reply_text and done.turn.delivery_id is not None
    plan = await runtime.container.external_channel_repository.get_delivery_plan(
        done.turn.delivery_id
    )
    assert plan is not None and plan.plan_version == 2
    assert [p.kind for p in plan.parts] == [
        ChannelDeliveryPartKind.AUDIO,
        ChannelDeliveryPartKind.TEXT,
    ]
    assert not plan.parts[0].required and plan.parts[1].required
    assert runtime.peer.group_sends.empty() and not runtime.peer.sends.qsize()


@pytest.mark.parametrize(
    "revoke", ["new_mention", "disable", "membership", "reconnect", "late_tts"]
)
async def test_group_voice_cancels_during_tts_without_stale_audio_or_fallback(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch, revoke: str
) -> None:
    _model(runtime, monkeypatch)
    route = await _voice_route(runtime)
    started, cancelled, hold = asyncio.Event(), asyncio.Event(), asyncio.Event()
    synthesize = runtime.container.providers.tts.synthesize

    async def blocked(request: SynthesisRequest) -> SynthesisResult:
        runtime.base.synthesis.append(request)
        started.set()
        try:
            await hold.wait()
        except asyncio.CancelledError:
            cancelled.set()
            if revoke == "late_tts":
                current = asyncio.current_task()
                assert current is not None
                current.uncancel()
                return await synthesize(request)
            raise
        raise AssertionError("revoked TTS must not publish")

    monkeypatch.setattr(runtime.container.providers.tts, "synthesize", blocked)
    await runtime.send(_group_event(840, "用语音说早上好"))
    await asyncio.wait_for(started.wait(), 5)
    admitted = await runtime.container.channel_group_repository.find_group_turn(
        runtime.connection_id, route.group_id, "840"
    )
    assert admitted is not None
    with pytest.raises(PermissionError, match="current authorized channel request"):
        await runtime.container.runtime_skills.invoke(
            admitted.turn.session_id,
            SkillInvocation(
                skill_id="channel.voice", capability="send_voice", arguments={"text": SPOKEN}
            ),
            origin="manual",
            turn_id=admitted.turn.turn_id,
            generation_id=admitted.turn.generation_id,
        )
    with pytest.raises(ValueError, match="Owner skill permissions"):
        await runtime.container.runtime_skills.invoke(
            admitted.turn.session_id,
            SkillInvocation(skill_id="runtime.status", capability="read", arguments={}),
        )
    if revoke in {"new_mention", "late_tts"}:
        await runtime.send(_group_event(841, "改成文字", sender=BOB))
        sent = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
        assert private._segments(sent)[0]["type"] == "text"
        await runtime.terminal(route, 841)
    elif revoke == "disable":
        await runtime.update(route, enabled=False)
    elif revoke == "reconnect":
        await runtime.peer.peers[-1].close()
        await runtime.pause(ChannelGroupPauseReason.RECONNECT)
    else:
        await runtime.peer.peers[-1].send(json.dumps(_notice()))
        await runtime.pause(ChannelGroupPauseReason.MEMBERSHIP_CHANGED)
    await asyncio.wait_for(cancelled.wait(), 3)
    old = await runtime.terminal(route, 840)
    assert old.turn.status is ChannelTurnStatus.CANCELLED
    assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
    assert not list(runtime.container.channel_voice.audio_root.glob("*.wav"))


async def test_listening_and_bare_mention_cannot_reuse_another_voice_request(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _model(runtime, monkeypatch)
    route = await _voice_route(runtime)
    listening = _group_event(850, "用语音说早上好")
    listening["message"] = [{"type": "text", "data": {"text": "用语音说早上好"}}]
    await runtime.send(listening)
    assert not model.requests and not runtime.base.synthesis and runtime.peer.group_sends.empty()
    await runtime.send(_group_event(851, "", sender=BOB))
    sent = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert private._segments(sent)[0]["type"] == "text"
    await runtime.terminal(route, 851)
    assert not model.requests[0].tools and not runtime.base.synthesis


async def test_unknown_group_voice_receipt_is_not_replayed_after_reconnect(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _model(runtime, monkeypatch)
    route = await _voice_route(runtime)
    runtime.peer.drop_next_group_receipt = True
    await runtime.send(_group_event(860, "用语音说早上好"))
    sent = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert private._segments(sent)[0]["type"] == "record"
    await runtime.pause(ChannelGroupPauseReason.RECONNECT)
    await asyncio.wait_for(runtime.peer.connected.get(), 5)
    async with asyncio.timeout(5):
        while True:
            _id, status, _code = await runtime.base.health.get()
            if status.value == "ready":
                break
    paused = await runtime.read_route(route.route_id)
    assert not paused.enabled and paused.allow_requested_voice
    journal = await runtime.container.external_channel_repository.get_adapter_cursor(
        runtime.connection_id
    )
    assert journal is not None and list(json.loads(journal).values()) == ["unknown"]
    revalidated = await runtime.enable(paused)
    await runtime.send(_group_event(860, "用语音说早上好"))
    old = await runtime.turn(revalidated, 860)
    assert not old.provider_receipt_present
    assert old.turn.delivery_status is not ChannelDeliveryStatus.DELIVERED
    assert len(model.requests) == 1 and len(runtime.base.synthesis) == 1
    assert len([c for c in runtime.peer.calls if c["action"] == "send_group_msg"]) == 1
    assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
    assert not list(runtime.container.channel_voice.audio_root.glob("*.wav"))
    await runtime.send(_group_event(861, "你好"))
    ordinary = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert private._segments(ordinary)[0]["type"] == "text"
    await runtime.terminal(revalidated, 861)
    assert not model.requests[-1].tools and len(runtime.base.synthesis) == 1
