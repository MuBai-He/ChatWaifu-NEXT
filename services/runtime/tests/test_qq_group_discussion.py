"""Actual adapter → authority → cache → Conversation → fixed text delivery with fixture peers."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import json
from typing import cast
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channel_groups import ChannelGroupPauseReason
from chatwaifu_protocol.channels import ChannelTurnStatus
from chatwaifu_runtime.external_channels.models import ChannelConnectionRecord
from chatwaifu_runtime.memory.service import UserTurnMemoryObservation
from chatwaifu_runtime.providers.openai_compatible import build_messages
from test_qq_group_runtime import ALICE, BOB, GROUP, OTHER_GROUP, _group_event, _Runtime
from test_qq_group_runtime import runtime as runtime


def _ordinary(mid: int, text: str, sender: str = ALICE, group: str = GROUP) -> JsonObject:
    event = _group_event(mid, text, sender=sender, group=group)
    event["message"] = [{"type": "text", "data": {"text": text}}]
    return event


async def test_listening_is_silent_ephemeral_attributed_and_carries_final_reference(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = await runtime.route()
    original_complete = runtime.container.model_configurations.complete

    # Any model call before @ is a failure, including memory extraction/summary.
    def prohibited(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("unmentioned dialogue invoked a model")

    monkeypatch.setattr(runtime.container.model_configurations, "complete", prohibited)
    for event in (
        _ordinary(4001, "我们选本地部署吧，数据不能离开家", ALICE),
        _ordinary(4002, "我担心维护成本，云端每月 20 元", BOB),
        _ordinary(4003, "那谁来维护还没确定", ALICE),
    ):
        await runtime.send(event)
    assert not runtime.model.requests and runtime.peer.group_sends.empty()
    count = await runtime.container.database.fetchone("SELECT count(*) AS n FROM turns")
    assert count is not None and count["n"] == 0
    memory_count = await runtime.container.database.fetchone(
        "SELECT count(*) AS n FROM memory_records"
    )
    assert memory_count is not None and memory_count["n"] == 0
    monkeypatch.setattr(runtime.container.model_configurations, "complete", original_complete)
    projected: list[UserTurnMemoryObservation] = []

    async def observe(observation: UserTurnMemoryObservation) -> None:
        projected.append(observation)

    monkeypatch.setattr(runtime.container.memory, "enqueue_user_turn", observe)
    await runtime.send(_group_event(4004, "刚才这个选择，你怎么看？"))
    request = await asyncio.wait_for(runtime.model.started.get(), 5)
    await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert (await runtime.terminal(route, 4004)).turn.status is ChannelTurnStatus.COMPLETED
    assert request.user_text == "刚才这个选择，你怎么看？"
    assert not request.tools and not request.images and not runtime.base.synthesis
    packet = cast(JsonObject, json.loads(request.current_turn_evidence[-1]))
    records = cast(list[JsonObject], packet["recent_originals"])
    assert [r["message_id"] for r in records] == ["4001", "4002", "4003"]
    assert [r["participant_id"] for r in records] == [
        runtime.participants[ALICE],
        runtime.participants[BOB],
        runtime.participants[ALICE],
    ]
    assert "数据不能离开家" in str(records[0]["text"])
    assert "维护成本" in str(records[1]["text"])
    assert [o.text for o in projected] == [request.user_text]
    rows = await runtime.container.database.fetchall(
        "SELECT committed_text FROM turns WHERE role='user'"
    )
    assert [row["committed_text"] for row in rows] == [request.user_text]


@pytest.mark.parametrize("with_discussion", [True, False])
async def test_bare_mention_reaches_text_delivery_without_cache_or_memory_pollution(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
    with_discussion: bool,
) -> None:
    route = await runtime.route()
    if with_discussion:
        await runtime.send(_ordinary(4051, "我倾向本地部署，资料不上传", ALICE))
        await runtime.send(_ordinary(4052, "我倾向云端，维护负责人还没定", BOB))
    projected: list[UserTurnMemoryObservation] = []

    async def observe(observation: UserTurnMemoryObservation) -> None:
        projected.append(observation)

    monkeypatch.setattr(runtime.container.memory, "enqueue_user_turn", observe)
    event = _group_event(4053, "")
    event["message"] = [{"type": "at", "data": {"qq": runtime.peer.account}}]
    await runtime.send(event)
    request = await asyncio.wait_for(runtime.model.started.get(), 5)
    await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert (await runtime.terminal(route, 4053)).turn.status is ChannelTurnStatus.COMPLETED
    assert not request.tools and not request.images and not runtime.base.synthesis
    assert not projected
    formal = await runtime.container.database.fetchall(
        "SELECT committed_text FROM turns WHERE role='user'"
    )
    assert [r["committed_text"] for r in formal] == ["[仅 @ 角色]"]
    assert all(
        message.message_id != "4053"
        for cache in runtime.container.channel_groups._discussion._groups.values()
        for message in cache.messages
    )
    if with_discussion:
        packet = cast(JsonObject, json.loads(request.current_turn_evidence[-1]))
        records = cast(list[JsonObject], packet["recent_originals"])
        assert [m["message_id"] for m in records] == ["4051", "4052"]
        assert "接着群里最近的话题" in request.user_text
    else:
        assert not request.current_turn_evidence
        assert "问我想聊什么" in request.user_text
    await runtime.send(event)
    assert len(runtime.model.requests) == 1  # provider redelivery is still deduplicated


async def test_latest_discussion_is_after_previous_missing_image_replies(
    runtime: _Runtime,
) -> None:
    route = await runtime.route()
    runtime.model.responses["你看看图片"] = "明明连图片都没有发……"
    runtime.model.responses["你看看"] = "可这里真的什么都没有啊……"
    for mid, text in ((4061, "你看看图片"), (4062, "你看看")):
        await runtime.send(_group_event(mid, text))
        await asyncio.wait_for(runtime.model.started.get(), 5)
        await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
        await runtime.terminal(route, mid)
    await runtime.send(_ordinary(4063, "有人说小林是夜猫子", BOB))
    await runtime.send(_group_event(4064, "你怎么看"))
    request = await asyncio.wait_for(runtime.model.started.get(), 5)
    await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    await runtime.terminal(route, 4064)
    wire = build_messages(request)
    assert "有人说小林是夜猫子" in str(wire[-2]["content"])
    assert wire[-2]["role"] == "user" and wire[-1]["content"] == "你怎么看"
    assert any("可这里真的什么都没有" in str(m["content"]) for m in wire[:-2])


async def test_unknown_off_group_and_non_speaker_never_gain_reply_permissions(
    runtime: _Runtime,
) -> None:
    off = await runtime.route(OTHER_GROUP, enable=False)
    route = await runtime.route()
    observed = await runtime.observe()
    await runtime.request(
        "PUT",
        f"{runtime.path}/group-routes/{route.route_id}",
        {
            "enabled": True,
            "expected_revision": route.revision,
            "speaker_sender_keys": [ALICE],
            "observation_id": str(observed.observation_id),
        },
    )
    route = await runtime.read_route(route.route_id)
    await runtime.send(_ordinary(4101, "authorized listener opinion", BOB))
    await runtime.send(_ordinary(4102, "unknown sentinel", "444"))
    await runtime.send(_ordinary(4103, "off group sentinel", ALICE, OTHER_GROUP))
    await runtime.send(_group_event(4104, "not granted", sender=BOB))
    assert not runtime.model.requests and runtime.peer.group_sends.empty()
    await runtime.send(_group_event(4105, "你怎么看？"))
    request = await asyncio.wait_for(runtime.model.started.get(), 5)
    await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    await runtime.terminal(route, 4105)
    assert "authorized listener opinion" in str(request.current_turn_evidence)
    assert "unknown sentinel" not in str(build_messages(request))
    assert "off group sentinel" not in str(build_messages(request))
    assert off.route_id != route.route_id


@pytest.mark.parametrize("change", ["disable", "reconnect", "member", "link"])
async def test_group_lifecycle_clears_context_and_requires_revalidation(
    runtime: _Runtime,
    change: str,
) -> None:
    route = await runtime.route()
    await runtime.send(_ordinary(4201, "old audience sentinel"))
    assert runtime.container.channel_groups._discussion._groups
    if change == "disable":
        route = await runtime.update(route, enabled=False)
    elif change == "link":
        member = route.members[0]
        link = await runtime.container.channel_group_repository.get_link(member.link_id)
        assert link is not None
        await runtime.request(
            "PUT",
            f"{runtime.path}/participant-links/{link.link_id}",
            {
                "enabled": False,
                "expected_revision": link.revision,
            },
        )
        assert not runtime.container.channel_groups._discussion._groups
        return
    else:
        await runtime.container.channel_groups.pause_connection(
            runtime.connection_id,
            ChannelGroupPauseReason.MEMBERSHIP_CHANGED
            if change == "member"
            else ChannelGroupPauseReason.RECONNECT,
            GROUP if change == "member" else None,
        )
        route = await runtime.read_route(route.route_id)
    assert not runtime.container.channel_groups._discussion._groups
    await runtime.send(_ordinary(4202, "paused sentinel"))
    await runtime.send(_group_event(4203, "paused mention"))
    assert not runtime.model.requests
    route = await runtime.enable(route)
    await runtime.send(_group_event(4204, "你怎么看？"))
    request = await asyncio.wait_for(runtime.model.started.get(), 5)
    await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    await runtime.terminal(route, 4204)
    assert "old audience sentinel" not in str(build_messages(request))
    assert "paused sentinel" not in str(build_messages(request))


async def test_late_summary_after_reconnect_cannot_reach_chat_or_delivery(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = await runtime.route()
    for i in range(12):
        await runtime.send(
            _ordinary(
                4301 + i, f"讨论 {i}: " + "有限预算与不同意见。" * 35, ALICE if i % 2 == 0 else BOB
            )
        )
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def late(*_args: object, **_kwargs: object) -> str:
        if _args[0] != "memory_summary":
            return '{"memories": []}'
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            current = asyncio.current_task()
            assert current is not None
            current.uncancel()
            return '{"message_ids":["4301","4302"]}'
        raise AssertionError("unreachable")

    monkeypatch.setattr(runtime.container.model_configurations, "complete", late)
    await runtime.send(_group_event(4320, "刚才的分歧你怎么看？"))
    await asyncio.wait_for(entered.wait(), 5)
    await runtime.container.channel_groups.pause_connection(
        runtime.connection_id, ChannelGroupPauseReason.RECONNECT
    )
    await asyncio.wait_for(cancelled.wait(), 5)
    assert not runtime.model.requests and runtime.peer.group_sends.empty()
    assert not runtime.container.channel_groups._discussion._groups
    assert (await runtime.turn(route, 4320)).turn.status is ChannelTurnStatus.CANCELLED


async def test_unmentioned_flood_reserves_admission_for_mentions(
    runtime: _Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = await runtime.route()
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.container.channel_groups._authenticator
    assert original is not None

    async def held(connection_id: UUID, token: str) -> ChannelConnectionRecord:
        entered.set()
        await release.wait()
        return await original(connection_id, token)

    monkeypatch.setattr(runtime.container.channel_groups, "_authenticator", held)
    try:
        await runtime.send(_ordinary(4401, "first"), join_admission=False)
        await asyncio.wait_for(entered.wait(), 3)
        for mid in range(4402, 4420):
            await runtime.send(_ordinary(mid, f"flood {mid}"), join_admission=False)
        await runtime.send(_group_event(4420, "最后这个你怎么看？"), join_admission=False)
        assert len(runtime.container.qq_channels._group_observation_tasks) == 3
        assert len(runtime.container.qq_channels._group_ingress_tasks) == 4
    finally:
        release.set()
    await asyncio.wait_for(
        asyncio.gather(*tuple(runtime.container.qq_channels._group_ingress_tasks)), 5
    )
    await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert (await runtime.terminal(route, 4420)).turn.status is ChannelTurnStatus.COMPLETED
