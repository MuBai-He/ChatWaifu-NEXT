"""Real container, operator API, SQLite and loopback OneBot group acceptance.

The only substituted boundaries are the local provider and the protocol peer.
Events gate preparation, cancellation and acknowledgements; no timing sleeps
stand in for transport ordering or durable completion.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast
from uuid import UUID

import pytest
from chatwaifu_protocol.base import JsonObject, JsonValue
from chatwaifu_protocol.channel_groups import (
    ChannelGroupAudienceSnapshot,
    ChannelGroupPauseReason,
    ChannelGroupRoutePage,
    ChannelGroupRouteSnapshot,
    ChannelGroupTurnPage,
    ChannelGroupTurnSnapshot,
)
from chatwaifu_protocol.channels import (
    ChannelConnectionSnapshot,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelTurnStatus,
)
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime import main
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupRouteMember,
    ChannelGroupTransition,
)
from chatwaifu_runtime.external_channels.models import DeliveryTransitionResult
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig
from httpx import ASGITransport, AsyncClient, Response
from websockets.asyncio.server import ServerConnection

from services.runtime.tests import test_qq_channels as private

GROUP = "500"
OTHER_GROUP = "501"
ALICE = "111"
BOB = "222"
NEW_MEMBER = "333"


@dataclass
class _GroupPeer(private._OneBot):
    members: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {GROUP: (ALICE, BOB), OTHER_GROUP: (ALICE, BOB)}
    )
    group_sends: asyncio.Queue[JsonObject] = field(default_factory=asyncio.Queue[JsonObject])
    drop_next_group_receipt: bool = False

    async def handle(self, peer: ServerConnection) -> None:
        assert peer.request is not None
        assert peer.request.headers["Authorization"] == f"Bearer {private.TOKEN}"
        self.peers.append(peer)
        self.connected.put_nowait(peer)
        async for wire in peer:
            request = cast(JsonObject, json.loads(wire))
            self.calls.append(request)
            params = cast(JsonObject, request["params"])
            action = request["action"]
            data: JsonValue
            if action == "get_login_info":
                data = {"user_id": int(self.account), "nickname": "role"}
            elif action == "get_group_member_list":
                group = str(params["group_id"])
                assert params["no_cache"] is True
                data = [
                    {"group_id": group, "user_id": sender, "nickname": "same name"}
                    for sender in (self.account, *self.members[group])
                ]
            elif action == "send_group_msg":
                assert params["group_id"] in self.members
                self.group_sends.put_nowait(params)
                if self.drop_next_group_receipt:
                    self.drop_next_group_receipt = False
                    await peer.close()
                    return
                data = {"message_id": 90000 + len(self.calls)}
            elif action == "send_private_msg":
                assert params["user_id"] == int(private.OWNER)
                self.sends.put_nowait(params)
                data = {"message_id": 90000 + len(self.calls)}
            else:
                raise AssertionError(f"unexpected OneBot action: {action}")
            await peer.send(
                json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": request["echo"]})
            )


class _GroupModel:
    kind = "loopback-group-model"
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []
        self.started: asyncio.Queue[LlmRequest] = asyncio.Queue()
        self.holds: dict[str, asyncio.Event] = {}
        self.cancelled: asyncio.Queue[UUID] = asyncio.Queue()
        self.swallow_cancel: set[str] = set()

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        self.started.put_nowait(request)
        hold = self.holds.get(request.user_text)
        if hold is not None:
            try:
                await hold.wait()
            except asyncio.CancelledError:
                self.cancelled.put_nowait(request.generation_id)
                if request.user_text not in self.swallow_cancel:
                    raise
                current = asyncio.current_task()
                assert current is not None
                current.uncancel()
                await hold.wait()
        yield LlmTextDelta(f"reply:{request.user_text}")
        yield LlmResponseCompleted("stop")


def _group_event(
    raw_id: int,
    text: str = "group text",
    *,
    sender: str = ALICE,
    group: str = GROUP,
    reply_id: int | None = None,
) -> JsonObject:
    segments: list[JsonValue] = [
        {"type": "at", "data": {"qq": private.ACCOUNT}},
        {"type": "text", "data": {"text": text}},
    ]
    if reply_id is not None:
        segments.insert(0, {"type": "reply", "data": {"id": str(reply_id)}})
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "self_id": int(private.ACCOUNT),
        "group_id": int(group),
        "user_id": int(sender),
        "message_id": raw_id,
        "sender": {"user_id": int(sender), "nickname": "same name"},
        "message": segments,
    }


def _notice() -> JsonObject:
    return {
        "post_type": "notice",
        "notice_type": "group_increase",
        "sub_type": "approve",
        "self_id": int(private.ACCOUNT),
        "group_id": int(GROUP),
        "user_id": int(NEW_MEMBER),
        "operator_id": 0,
    }


@dataclass
class _Runtime:
    base: private._Harness
    peer: _GroupPeer
    model: _GroupModel
    http: AsyncClient
    connection_id: UUID
    dispatched: asyncio.Queue[JsonObject]
    paused: asyncio.Queue[ChannelGroupPauseReason]
    participants: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def container(self) -> RuntimeContainer:
        return self.base.container

    @property
    def path(self) -> str:
        return f"/v1/channel-connections/{self.connection_id}"

    async def request(
        self, method: str, path: str, body: JsonObject | None = None, *, status: int = 200
    ) -> Response:
        response = await asyncio.wait_for(self.http.request(method, path, json=body), 5)
        assert response.status_code == status, response.text
        return response

    async def observe(self, group: str = GROUP) -> ChannelGroupAudienceSnapshot:
        response = await self.request(
            "POST", f"{self.path}/group-audience-observations", {"group_id": group}
        )
        return ChannelGroupAudienceSnapshot.model_validate(response.json())

    async def link(self, observation: ChannelGroupAudienceSnapshot, sender: str) -> None:
        participant = cast(
            JsonObject,
            (
                await self.request(
                    "POST", "/v1/participants", {"display_name": "same name"}, status=201
                )
            ).json(),
        )
        participant_id = cast(str, participant["participant_id"])
        await self.request(
            "POST",
            f"{self.path}/participant-links",
            {
                "observation_id": str(observation.observation_id),
                "sender_key": sender,
                "participant_id": participant_id,
            },
        )
        self.participants[sender] = participant_id

    async def route(self, group: str = GROUP, *, enable: bool = True) -> ChannelGroupRouteSnapshot:
        observed = await self.observe(group)
        assert observed.member_ids == list(self.peer.members[group])
        for sender in observed.member_ids:
            if sender not in self.participants:
                await self.link(observed, sender)
        response = await self.request(
            "POST",
            f"{self.path}/group-routes",
            {
                "observation_id": str(observed.observation_id),
                "display_name": "fixture fixed audience",
                "speaker_sender_keys": cast(JsonValue, observed.member_ids),
            },
        )
        route = ChannelGroupRouteSnapshot.model_validate(response.json())
        assert not route.enabled
        return await self.enable(route) if enable else route

    async def enable(self, route: ChannelGroupRouteSnapshot) -> ChannelGroupRouteSnapshot:
        observed = await self.observe(route.group_id)
        return await self.update(route, enabled=True, observation=observed)

    async def update(
        self,
        route: ChannelGroupRouteSnapshot,
        *,
        enabled: bool,
        observation: ChannelGroupAudienceSnapshot | None = None,
        revision: int | None = None,
        status: int = 200,
    ) -> ChannelGroupRouteSnapshot:
        response = await self.request(
            "PUT",
            f"{self.path}/group-routes/{route.route_id}",
            {
                "enabled": enabled,
                "expected_revision": route.revision if revision is None else revision,
                "observation_id": str(observation.observation_id) if observation else None,
                "speaker_sender_keys": [member.sender_key for member in route.members],
            },
            status=status,
        )
        return ChannelGroupRouteSnapshot.model_validate(response.json()) if status == 200 else route

    async def read_route(self, route_id: UUID) -> ChannelGroupRouteSnapshot:
        response = await self.request("GET", f"{self.path}/group-routes")
        routes = ChannelGroupRoutePage.model_validate(response.json())
        return next(route for route in routes.items if route.route_id == route_id)

    async def send(self, event: JsonObject, *, join_admission: bool = True) -> None:
        await self.peer.peers[-1].send(json.dumps(event))
        dispatched = await asyncio.wait_for(self.dispatched.get(), 3)
        assert dispatched["message_id"] == event["message_id"]
        if not join_admission:
            return
        # The real host creates admissions asynchronously; await their owned tasks,
        # never replace their authorization, repository or Conversation behavior.
        await asyncio.wait_for(
            asyncio.gather(*tuple(self.container.qq_channels._group_ingress_tasks)), 3
        )

    async def turn(self, route: ChannelGroupRouteSnapshot, raw_id: int) -> ChannelGroupTurnSnapshot:
        record = await self.container.channel_group_repository.find_group_turn(
            self.connection_id, route.group_id, str(raw_id)
        )
        assert record is not None
        response = await self.request("GET", f"{self.path}/group-routes/{route.route_id}/turns")
        page = ChannelGroupTurnPage.model_validate(response.json())
        return next(
            item for item in page.items if item.turn.channel_turn_id == record.turn.channel_turn_id
        )

    async def terminal(
        self, route: ChannelGroupRouteSnapshot, raw_id: int
    ) -> ChannelGroupTurnSnapshot:
        sub = self.container.event_hub.subscribe(queue_size=128)
        try:
            async with asyncio.timeout(5):
                while True:
                    snapshot = await self.turn(route, raw_id)
                    if snapshot.turn.status in {
                        ChannelTurnStatus.FAILED,
                        ChannelTurnStatus.CANCELLED,
                    }:
                        return snapshot
                    if (
                        snapshot.turn.status is ChannelTurnStatus.COMPLETED
                        and snapshot.turn.delivery_status
                        in {
                            ChannelDeliveryStatus.DELIVERED,
                            ChannelDeliveryStatus.FAILED,
                            ChannelDeliveryStatus.CANCELLED,
                        }
                    ):
                        return snapshot
                    await sub.receive()
        finally:
            self.container.event_hub.unsubscribe(sub)

    async def pause(self, reason: ChannelGroupPauseReason) -> None:
        async with asyncio.timeout(5):
            while await self.paused.get() is not reason:
                pass


@pytest.fixture(params=[False, True], ids=["web-disabled", "owner-web-enabled"])
async def runtime(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> AsyncIterator[_Runtime]:
    monkeypatch.setattr(private, "_OneBot", _GroupPeer)
    if request.param is True:
        runtime_settings = private._public_web_settings(runtime_settings)
    async with private._runtime(runtime_settings, monkeypatch) as base:
        peer = cast(_GroupPeer, base.peer)
        model = _GroupModel()

        def provider(_config: ModelRoleConfig) -> LlmProvider:
            return model

        monkeypatch.setattr(base.container.model_configurations, "create_chat_provider", provider)
        connection_id = await private._pair(base)
        dispatched: asyncio.Queue[JsonObject] = asyncio.Queue()
        dispatch = base.container.qq_channels._dispatch_group

        def record_dispatch(event: JsonObject, connection: UUID, account: str, token: str) -> None:
            dispatch(event, connection, account, token)
            dispatched.put_nowait(event)

        monkeypatch.setattr(base.container.qq_channels, "_dispatch_group", record_dispatch)
        paused: asyncio.Queue[ChannelGroupPauseReason] = asyncio.Queue()
        pause = base.container.channel_groups.pause_connection

        async def record_pause(
            connection: UUID, reason: ChannelGroupPauseReason, group_id: str | None = None
        ) -> None:
            await pause(connection, reason, group_id)
            paused.put_nowait(reason)

        monkeypatch.setattr(base.container.channel_groups, "pause_connection", record_pause)

        def existing_container(_settings: Settings) -> RuntimeContainer:
            return base.container

        monkeypatch.setattr(main, "RuntimeContainer", existing_container)
        app = main.create_app(runtime_settings)
        assert app.state.container is base.container
        async with AsyncClient(
            transport=ASGITransport(app),
            base_url="http://127.0.0.1",
            headers={"Authorization": f"Bearer {base.container.capability_token}"},
        ) as http:
            result = _Runtime(base, peer, model, http, connection_id, dispatched, paused)
            try:
                yield result
            finally:
                for hold in model.holds.values():
                    hold.set()


async def test_operator_apis_default_off_two_members_fixed_text_and_isolated_state(
    runtime: _Runtime,
) -> None:
    row = await runtime.container.database.fetchone(
        "SELECT max(version) AS v FROM schema_migrations"
    )
    assert row is not None and row["v"] == 41
    unauthenticated = await runtime.http.get(
        f"{runtime.path}/group-routes", headers={"Authorization": ""}
    )
    assert unauthenticated.status_code == 401
    route = await runtime.route(enable=False)
    await runtime.send(_group_event(10, "off route"))
    assert not runtime.model.requests and runtime.peer.group_sends.empty()
    sentinel = "fixture-owner-private-history-sentinel"
    await runtime.peer.peers[-1].send(json.dumps(private._event(sentinel, 9)))
    private_reply = await asyncio.wait_for(runtime.peer.sends.get(), 5)
    assert private_reply["user_id"] == int(private.OWNER)
    route = await runtime.enable(route)
    snapshots: list[ChannelGroupTurnSnapshot] = []
    for raw_id, sender in ((11, ALICE), (12, BOB)):
        await runtime.send(_group_event(raw_id, "我喜欢蓝色", sender=sender))
        sent = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
        assert sent == {
            "group_id": GROUP,
            "message": [{"type": "text", "data": {"text": "reply:我喜欢蓝色"}}],
        }
        snapshot = await runtime.terminal(route, raw_id)
        assert snapshot.provider_receipt_present
        assert snapshot.turn.delivery_status is ChannelDeliveryStatus.DELIVERED
        snapshots.append(snapshot)
    assert len(runtime.model.requests) == 3
    group_requests = runtime.model.requests[1:]
    assert all(not request.tools and not request.images for request in group_requests)
    assert all(
        sentinel not in request.system_prompt
        and sentinel not in str(request.history)
        and sentinel not in str(request.context)
        and sentinel not in str(request.recalled_memory_texts)
        for request in group_requests
    )
    assert not runtime.base.synthesis and runtime.peer.sends.empty()
    assert len({s.turn.session_id for s in snapshots}) == 2
    assert {s.scene_id for s in snapshots} == {route.scene_id}
    assert {s.participant_id for s in snapshots} == set(runtime.participants.values())
    identities = [
        await runtime.container.sessions.conversation_identity(s.turn.session_id) for s in snapshots
    ]
    assert {identity.memory_scope for identity in identities} == {f"scene:{route.scene_id}"}
    assert len({identity.state_scope for identity in identities}) == 2
    state_rows = await runtime.container.database.fetchall(
        "SELECT user_scope FROM relationship_states"
    )
    assert {identity.state_scope for identity in identities}.issubset(
        str(row["user_scope"]) for row in state_rows
    )
    for identity in identities:
        assert identity.state_scope == f"scene_member:{route.scene_id}:{identity.participant_id}"
        state = await runtime.container.character_kernel.snapshot(
            "default", user_scope=identity.state_scope
        )
        assert state.user_scope == identity.state_scope
    rows = await runtime.container.database.fetchall(
        "SELECT source_context_json FROM turns "
        "WHERE json_extract(source_context_json,'$.chat_type')='group'"
    )
    sources = [cast(JsonObject, json.loads(row["source_context_json"])) for row in rows]
    assert sources
    assert all(source["provider_id"] == "qq_napcat" for source in sources)
    assert {cast(str, source["participant_id"]) for source in sources} == set(
        runtime.participants.values()
    )
    assert all(source["scene_id"] == route.scene_id for source in sources)


@pytest.mark.parametrize("reply_id", [90000, -90000])
async def test_quoted_mention_reaches_model_and_fixed_delivery_once(
    runtime: _Runtime, reply_id: int
) -> None:
    route = await runtime.route(enable=False)
    await runtime.send(_group_event(31, "disabled quote", reply_id=reply_id))
    assert not runtime.model.requests and runtime.peer.group_sends.empty()
    route = await runtime.enable(route)
    event = _group_event(32, "为什么呀", reply_id=reply_id)
    await runtime.send(event)
    sent = await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    assert sent == {
        "group_id": GROUP,
        "message": [{"type": "text", "data": {"text": "reply:为什么呀"}}],
    }
    snapshot = await runtime.terminal(route, 32)
    assert snapshot.turn.status is ChannelTurnStatus.COMPLETED
    assert snapshot.turn.delivery_status is ChannelDeliveryStatus.DELIVERED
    assert snapshot.provider_receipt_present
    assert snapshot.scene_id == route.scene_id
    assert snapshot.participant_id == runtime.participants[ALICE]
    await runtime.send(event)
    assert len(runtime.model.requests) == 1
    request = runtime.model.requests[0]
    assert request.user_text == "为什么呀" and not request.tools and not request.images
    assert not any(call["action"] == "get_msg" for call in runtime.peer.calls)
    assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
    assert not runtime.base.synthesis


async def test_wire_rejections_dedup_and_raw_ids_are_scoped_to_the_fixed_group(
    runtime: _Runtime,
) -> None:
    route = await runtime.route()
    other = await runtime.route(OTHER_GROUP)
    ordinary = _group_event(20)
    ordinary["message"] = [{"type": "text", "data": {"text": "普通群消息"}}]
    fake_at = _group_event(21)
    fake_at["message"] = [{"type": "text", "data": {"text": f"[CQ:at,qq={private.ACCOUNT}] 你好"}}]
    mixed = _group_event(22)
    mixed["message"] = [
        *cast(list[JsonValue], mixed["message"]),
        {"type": "record", "data": {"file": "fixture.amr"}},
    ]
    for event in (
        ordinary,
        fake_at,
        mixed,
        _group_event(23, sender="444"),
        _group_event(24, group="999"),
    ):
        await runtime.send(event)
    # A registered group member still cannot acquire the separate private-owner route.
    await runtime.peer.peers[-1].send(
        json.dumps(private._event("private sentinel", 25, sender=ALICE))
    )
    for managed in (route, other):
        event = _group_event(30, "same raw identity", group=managed.group_id)
        await runtime.send(event)
        await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
        await runtime.terminal(managed, 30)
        await runtime.send(event)
    await runtime.send(_group_event(30, "conflicting payload"))
    first, second = await runtime.turn(route, 30), await runtime.turn(other, 30)
    assert first.turn.channel_turn_id != second.turn.channel_turn_id
    assert first.turn.external_message_id == second.turn.external_message_id == "30"
    assert first.turn.conversation_key == f"group:{GROUP}"
    assert second.turn.conversation_key == f"group:{OTHER_GROUP}"
    assert len(runtime.model.requests) == 2
    assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
    count = await runtime.container.database.fetchone("SELECT count(*) AS n FROM channel_turns")
    assert count is not None and count["n"] == 2


async def test_membership_reader_revokes_during_private_prepare_and_rejects_uncancelled_late_output(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = await runtime.route()
    hold = asyncio.Event()
    runtime.model.holds["held group"] = hold
    runtime.model.swallow_cancel.add("held group")
    private_hold = asyncio.Event()
    runtime.model.holds["blocked private preparation"] = private_hold
    await runtime.send(_group_event(40, "held group"))
    request = await asyncio.wait_for(runtime.model.started.get(), 3)
    group_turn = await runtime.turn(route, 40)
    generation_task = runtime.container.conversation._active[group_turn.turn.session_id].task
    assert generation_task is not None
    entered, release_private = asyncio.Event(), asyncio.Event()
    retrieve = runtime.container.memory.retrieve_context

    async def block_private(
        session_id: UUID,
        turn_id: UUID,
        character_id: str,
        query: str,
        *,
        token_budget: int = 700,
        limit: int = 12,
    ) -> MemoryContextPacket:
        if query == "blocked private preparation":
            entered.set()
            await release_private.wait()
        return await retrieve(
            session_id, turn_id, character_id, query, token_budget=token_budget, limit=limit
        )

    monkeypatch.setattr(runtime.container.memory, "retrieve_context", block_private)
    try:
        await runtime.peer.peers[-1].send(
            json.dumps(private._event("blocked private preparation", 41))
        )
        await asyncio.wait_for(entered.wait(), 3)
        runtime.peer.members[GROUP] = (ALICE, BOB, NEW_MEMBER)
        await runtime.peer.peers[-1].send(json.dumps(_notice()))
        assert await asyncio.wait_for(runtime.model.cancelled.get(), 3) == request.generation_id
        assert not release_private.is_set()
        assert not runtime.container.qq_channels._clients[
            runtime.connection_id
        ].group_dispatch_ready
        hold.set()
        # Observe completion without adding a second cancellation from the test;
        # the reader's fence must defeat uncancelled late output on its own.
        await asyncio.wait_for(
            asyncio.shield(asyncio.gather(generation_task, return_exceptions=True)), 3
        )
        result = await runtime.container.conversation_repository.generation_result(
            request.generation_id
        )
        assert result is not None and result.state is GenerationState.CANCELLED
        row = await runtime.container.database.fetchone(
            "SELECT count(*) AS n FROM events "
            "WHERE json_extract(envelope_json,'$.generation_id')=? "
            "AND event_type='assistant.generation_completed'",
            (str(request.generation_id),),
        )
        assert row is not None and row["n"] == 0
        release_private.set()
        await runtime.pause(ChannelGroupPauseReason.MEMBERSHIP_CHANGED)
        # Resume normal private preparation, but hold and cancel its model so the
        # test isolates revocation without emitting an unrelated private reply.
        private_request = await asyncio.wait_for(runtime.model.started.get(), 3)
        assert private_request.user_text == "blocked private preparation"
        private_turn = (
            await runtime.container.external_channel_repository.find_turn_by_external_message(
                runtime.connection_id, "41"
            )
        )
        assert private_turn is not None
        await runtime.container.conversation.cancel(
            private_turn.session_id, expected_generation_id=private_request.generation_id
        )
        paused = await runtime.read_route(route.route_id)
        assert (
            not paused.enabled and paused.pause_reason is ChannelGroupPauseReason.MEMBERSHIP_CHANGED
        )
        snapshot = await runtime.terminal(route, 40)
        assert (
            snapshot.turn.status is ChannelTurnStatus.CANCELLED
            and not snapshot.provider_receipt_present
        )
        old_observation = await runtime.container.channel_group_repository.get_observation(
            route.observation_id
        )
        assert old_observation is not None
        old = ChannelGroupAudienceSnapshot(
            observation_id=old_observation.observation_id,
            connection_id=old_observation.connection_id,
            connection_revision=old_observation.connection_revision,
            account_key=old_observation.account_key,
            group_id=old_observation.group_id,
            member_ids=list(old_observation.member_ids),
            member_fingerprint=old_observation.member_fingerprint,
            observed_at=old_observation.observed_at,
            expires_at=old_observation.expires_at,
        )
        await runtime.update(paused, enabled=True, observation=old, status=403)
        fresh = await runtime.observe()
        await runtime.link(fresh, NEW_MEMBER)
        revalidated = await runtime.update(paused, enabled=True, observation=fresh)
        assert revalidated.enabled and revalidated.scene_id != route.scene_id
        assert [request.user_text for request in runtime.model.requests] == [
            "held group",
            "blocked private preparation",
        ]
        assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
        assert not runtime.base.synthesis
    finally:
        hold.set()
        release_private.set()
        private_hold.set()


@pytest.mark.parametrize("action", ["route_disable", "reset", "reconnect"])
@pytest.mark.parametrize("quoted", [False, True])
async def test_stale_cas_preserves_live_group_then_successful_pause_cancels_without_late_send(
    runtime: _Runtime, action: str, quoted: bool
) -> None:
    route = await runtime.route()
    hold = asyncio.Event()
    runtime.model.holds["active"] = hold
    runtime.model.swallow_cancel.add("active")
    await runtime.send(_group_event(50, "active", reply_id=90000 if quoted else None))
    request = await asyncio.wait_for(runtime.model.started.get(), 3)
    snapshot = await runtime.turn(route, 50)
    await runtime.update(route, enabled=False, revision=route.revision - 1, status=409)
    connection = ChannelConnectionSnapshot.model_validate(
        (await runtime.request("GET", runtime.path)).json()
    )
    await runtime.request(
        "PUT",
        f"{runtime.path}?expected_revision={connection.revision + 1}",
        cast(JsonObject, connection.configuration.model_dump(mode="json")),
        status=409,
    )
    fresh = await runtime.read_route(route.route_id)
    assert fresh.enabled and fresh.revision == route.revision
    assert runtime.model.cancelled.empty()
    assert (
        runtime.container.conversation.active_generation_id(snapshot.turn.session_id)
        == request.generation_id
    )
    pending: asyncio.Task[object] | None = None
    try:
        if action == "route_disable":
            pending = asyncio.create_task(runtime.update(route, enabled=False))
        elif action == "reset":
            pending = asyncio.create_task(
                runtime.container.conversation.reset(snapshot.turn.session_id)
            )
        else:
            await runtime.peer.peers[-1].close()
        assert await asyncio.wait_for(runtime.model.cancelled.get(), 3) == request.generation_id
        hold.set()
        if pending is not None:
            await asyncio.wait_for(cast(asyncio.Task[object], pending), 5)
        else:
            await runtime.pause(ChannelGroupPauseReason.RECONNECT)
        fresh = await runtime.read_route(route.route_id)
        assert not fresh.enabled
        assert (
            fresh.pause_reason
            is {
                "route_disable": ChannelGroupPauseReason.OPERATOR_DISABLED,
                "reset": ChannelGroupPauseReason.SCENE_RESET,
                "reconnect": ChannelGroupPauseReason.RECONNECT,
            }[action]
        )
        terminal = await runtime.terminal(route, 50)
        assert terminal.turn.status is ChannelTurnStatus.CANCELLED
        assert not terminal.provider_receipt_present
        assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
        assert len(runtime.model.requests) == 1
    finally:
        hold.set()
        if pending is not None and not pending.done():
            await asyncio.gather(pending, return_exceptions=True)


async def test_route_wide_pending_member_cannot_revive_after_operator_disable(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = await runtime.route()
    hold = asyncio.Event()
    runtime.model.holds["old active"] = hold
    runtime.model.swallow_cancel.add("old active")
    await runtime.send(_group_event(55, "old active", sender=ALICE))
    first = await asyncio.wait_for(runtime.model.started.get(), 3)
    await runtime.send(_group_event(56, "latest pending", sender=BOB), join_admission=False)
    assert await asyncio.wait_for(runtime.model.cancelled.get(), 3) == first.generation_id
    pending = await runtime.turn(route, 56)
    assert pending.turn.status is ChannelTurnStatus.ACCEPTED
    assert pending.participant_id == runtime.participants[BOB]
    assert len(runtime.model.requests) == 1
    committed = asyncio.Event()
    repository = runtime.container.channel_group_repository
    update = repository.update_route

    async def disable() -> object:
        return await runtime.update(route, enabled=False)

    # Observe the actual atomic SQL transition, before application joins the
    # intentionally uncancelled old model. No alternative transition is injected.
    async def record_commit(
        route_id: UUID,
        *,
        expected_revision: int,
        enabled: bool,
        observation_id: UUID | None,
        members: tuple[ChannelGroupRouteMember, ...],
        scene_id: str,
        updated_at: datetime,
    ) -> ChannelGroupTransition:
        result = await update(
            route_id,
            expected_revision=expected_revision,
            enabled=enabled,
            observation_id=observation_id,
            members=members,
            scene_id=scene_id,
            updated_at=updated_at,
        )
        committed.set()
        return result

    monkeypatch.setattr(repository, "update_route", record_commit)
    work = asyncio.create_task(disable())
    try:
        await asyncio.wait_for(committed.wait(), 3)
        hold.set()
        await asyncio.wait_for(work, 5)
        await asyncio.wait_for(
            asyncio.gather(*tuple(runtime.container.qq_channels._group_ingress_tasks)), 3
        )
        for raw_id in (55, 56):
            snapshot = await runtime.terminal(route, raw_id)
            assert snapshot.turn.status is ChannelTurnStatus.CANCELLED
            assert not snapshot.provider_receipt_present
        assert len(runtime.model.requests) == 1
        assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
    finally:
        hold.set()
        await asyncio.gather(work, return_exceptions=True)


async def test_known_wire_success_reconciles_after_revocation_without_a_second_send(
    runtime: _Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = await runtime.route()
    ack_entered, release_ack, reconciled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    repository = runtime.container.external_channel_repository
    acknowledge, reconcile = (
        repository.acknowledge_delivery_part,
        repository.reconcile_known_delivery_part_receipt,
    )

    async def blocked_ack(
        acknowledgement: ChannelDeliveryPartAcknowledgement, *, updated_at: datetime
    ) -> DeliveryTransitionResult:
        if acknowledgement.status is ChannelDeliveryPartStatus.DELIVERED:
            ack_entered.set()
            await release_ack.wait()
        return await acknowledge(acknowledgement, updated_at=updated_at)

    async def record_reconcile(
        connection_id: UUID,
        provider_client_id: str,
        provider_message_id: str,
        *,
        observed_at: datetime,
    ) -> DeliveryTransitionResult:
        transition = await reconcile(
            connection_id, provider_client_id, provider_message_id, observed_at=observed_at
        )
        if transition.plan.status is ChannelDeliveryStatus.DELIVERED:
            reconciled.set()
        return transition

    monkeypatch.setattr(repository, "acknowledge_delivery_part", blocked_ack)
    monkeypatch.setattr(repository, "reconcile_known_delivery_part_receipt", record_reconcile)
    try:
        await runtime.send(_group_event(60, "known success"))
        await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
        await asyncio.wait_for(ack_entered.wait(), 3)
        journal = cast(
            dict[str, str], json.loads(await repository.get_adapter_cursor(runtime.connection_id))
        )
        assert len(journal) == 1 and "unknown" not in journal.values()
        paused = await runtime.update(route, enabled=False)
        assert not paused.enabled
        release_ack.set()
        await asyncio.wait_for(reconciled.wait(), 5)
        snapshot = await runtime.turn(route, 60)
        assert snapshot.provider_receipt_present
        assert snapshot.turn.delivery_status is ChannelDeliveryStatus.DELIVERED
        assert len([call for call in runtime.peer.calls if call["action"] == "send_group_msg"]) == 1
        assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
    finally:
        release_ack.set()


async def test_unknown_wire_result_survives_reconnect_and_explicit_reenable_without_replay(
    runtime: _Runtime,
) -> None:
    route = await runtime.route()
    runtime.peer.drop_next_group_receipt = True
    await runtime.send(_group_event(70, "unknown receipt"))
    await asyncio.wait_for(runtime.peer.group_sends.get(), 5)
    await runtime.pause(ChannelGroupPauseReason.RECONNECT)
    await asyncio.wait_for(runtime.peer.connected.get(), 5)
    async with asyncio.timeout(5):
        while True:
            _id, status, _code = await runtime.base.health.get()
            if status.value == "ready":
                break
    paused = await runtime.read_route(route.route_id)
    assert not paused.enabled
    journal = cast(
        dict[str, str],
        json.loads(
            await runtime.container.external_channel_repository.get_adapter_cursor(
                runtime.connection_id
            )
        ),
    )
    assert list(journal.values()) == ["unknown"]
    revalidated = await runtime.enable(paused)
    await runtime.send(_group_event(70, "unknown receipt"))
    snapshot = await runtime.turn(revalidated, 70)
    assert not snapshot.provider_receipt_present
    assert snapshot.turn.delivery_status is not ChannelDeliveryStatus.DELIVERED
    assert len(runtime.model.requests) == 1
    assert len([call for call in runtime.peer.calls if call["action"] == "send_group_msg"]) == 1
    assert runtime.peer.group_sends.empty() and runtime.peer.sends.empty()
    journal = cast(
        dict[str, str],
        json.loads(
            await runtime.container.external_channel_repository.get_adapter_cursor(
                runtime.connection_id
            )
        ),
    )
    assert list(journal.values()) == ["unknown"]
