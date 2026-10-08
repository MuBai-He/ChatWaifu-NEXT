"""Real admission fences, bounded model decisions and selected original memory evidence."""

# pyright: reportPrivateUsage=false
import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

import pytest
from chatwaifu_protocol.agent import GroupAutonomyPolicy, GroupAutonomyUpdate
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.config.group_discussion import GroupDiscussionConfig
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatClient
from chatwaifu_runtime.external_channels.autonomy import GroupObservation
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmToolCall,
    LlmToolCallRequested,
)

from services.runtime.tests.test_channel_group_application import TOKEN, App

pytest_plugins = ["services.runtime.tests.test_channel_group_application"]


class DecisionModel:
    kind = "scripted"
    supports_tool_calling = True

    def __init__(self, action: str = "wait") -> None:
        self.action = action
        self.requests: list[LlmRequest] = []
        self.started = asyncio.Event()
        self.hold: asyncio.Event | None = None

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        self.started.set()
        if self.hold is not None:
            await self.hold.wait()
        yield LlmToolCallRequested(
            LlmToolCall(
                "decision",
                "decide_behavior",
                {
                    "action": self.action,
                    "reason": "讨论中有待回答的问题",
                    "source_refs": ["100"],
                    "quiet_seconds": 60 if self.action == "wait" else None,
                },
            )
        )
        yield LlmResponseCompleted("tool_calls")


async def configure(
    app: App, mode: Literal["off", "shadow", "member"], model: DecisionModel
) -> GroupAutonomyPolicy:
    await app.enable()
    app.service.configure_discussion(GroupDiscussionConfig(enabled=True))
    service = app.container.group_autonomy
    service.decisions.model_factory = lambda: model
    app.service.observation_handler = service.observe
    app.service.autonomous_admission = service.repository.mark_turn
    app.service.autonomous_authorization = service.repository.authorize_turn
    service.revoke_actions = app.service.revoke_autonomous
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    policy = GroupAutonomyPolicy(
        route_id=route.route_id,
        route_revision=route.revision,
        mode=mode,
        merge_seconds=1,
        quiet_start=0,
        quiet_end=0,
    )
    return await service.configure(route, GroupAutonomyUpdate(expected_revision=0, policy=policy))


@pytest.mark.parametrize("mode", ["shadow", "member"])
async def test_group_model_decides_without_mention_and_shadow_never_sends(
    app: App, mode: Literal["off", "shadow", "member"]
) -> None:
    model = DecisionModel("respond")
    await configure(app, mode, model)
    await app.service.observe_group_text(
        app.message("100", text="宁宁虽然没被艾特，但这个问题你可能知道"),
        access_token=TOKEN,
    )
    worker = app.container.group_autonomy._workers[app.route.route_id]
    await asyncio.wait_for(worker, 5)
    assert len(model.requests) == 1
    if mode == "member":
        request = await asyncio.wait_for(app.provider.started.get(), 3)
        assert request.user_text.startswith("宁宁虽然")
        rows = await app.container.database.fetchall("SELECT turn_id FROM agent_autonomous_turns")
        assert len(rows) == 1
    else:
        assert not app.provider.requests
        assert not await app.container.database.fetchall("SELECT * FROM agent_autonomous_turns")
    assert not await app.container.database.fetchall("SELECT * FROM memory_records")


async def test_silence_and_decision_budget_are_durable(app: App) -> None:
    model = DecisionModel()
    policy = await configure(app, "member", model)
    repo = app.container.group_autonomy.repository
    now = datetime.now(UTC)
    assert await repo.reserve(policy, now - timedelta(seconds=9), speech=False)
    assert not await repo.reserve(policy, now, speech=False)
    assert await repo.next_observation(policy, now) > now
    await repo.set_quiet(policy.route_id, now + timedelta(minutes=1))
    await app.container.group_autonomy.stop()
    await app.container.group_autonomy.start()
    await app.service.observe_group_text(app.message("100"), access_token=TOKEN)
    await asyncio.wait_for(app.container.group_autonomy._workers[policy.route_id], 5)
    assert not model.requests and not app.provider.requests
    quiet_until = await repo.quiet_until(policy.route_id)
    assert quiet_until is not None and quiet_until > now


async def test_disable_revokes_pending_autonomous_generation_but_preserves_explicit_grants(
    app: App,
) -> None:
    model = DecisionModel("respond")
    policy = await configure(app, "member", model)
    app.provider.hold = asyncio.Event()
    await app.service.observe_group_text(app.message("100"), access_token=TOKEN)
    await asyncio.wait_for(app.container.group_autonomy._workers[policy.route_id], 5)
    await asyncio.wait_for(app.provider.started.get(), 3)
    route = await app.repository.get_route(policy.route_id)
    assert route is not None
    await app.container.group_autonomy.configure(
        route,
        GroupAutonomyUpdate(
            expected_revision=policy.revision,
            policy=policy.model_copy(update={"mode": "off"}),
        ),
    )
    await asyncio.wait_for(app.provider.cancelled.wait(), 3)
    row = await app.container.database.fetchone("SELECT turn_id FROM agent_autonomous_turns")
    assert row is not None
    from uuid import UUID, uuid4

    assert not await app.container.group_autonomy.repository.authorize_turn(UUID(row["turn_id"]))
    assert await app.container.group_autonomy.repository.authorize_turn(uuid4())


async def test_memory_selects_original_speaker_and_deduplicates_without_transcript(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = DecisionModel()
    await configure(app, "shadow", model)
    captured: list[GroupObservation] = []
    app.service.observation_handler = captured.append
    for mid, sender, text in [
        ("101", "111", "今天好热"),
        ("102", "222", "我喜欢喝无糖茶"),
    ]:
        await app.service.observe_group_text(app.message(mid, sender, text), access_token=TOKEN)
    observation = captured[-1]
    observed: list[tuple[UUID, str]] = []

    async def memory_write(
        session_id: UUID, _turn: object, _event: object, _character: str, text: str
    ) -> list[object]:
        observed.append((session_id, text))
        return []

    monkeypatch.setattr(app.container.memory, "observe_user_turn", memory_write)
    await app.container.group_memory.observe(observation, ("102",))
    await app.container.group_memory.observe(observation, ("102",))
    assert len(observed) == 1 and observed[0][1] == "我喜欢喝无糖茶"
    session = await app.container.sessions.get_session(observed[0][0])
    expected = next(m.participant_id for m in observation.route.members if m.sender_key == "222")
    assert session is not None and session.participant_id == expected
    assert not await app.container.database.fetchall("SELECT * FROM turns")
    rows = await app.container.database.fetchall(
        "SELECT payload_json FROM events WHERE event_type='agent.group_evidence'"
    )
    assert len(rows) == 1 and "今天好热" not in rows[0]["payload_json"]
    other = await app.container.sessions.create_session("default")
    assert other.user_scope != session.user_scope


async def test_decision_cannot_cite_unknown_source(app: App) -> None:
    model = DecisionModel("respond")
    app.container.behavior_decisions.model_factory = lambda: model
    with pytest.raises(ValueError, match="unknown source"):
        await app.container.behavior_decisions.decide("persona", {}, frozenset({"other"}))


async def test_selected_group_originals_use_real_memory_correction_forget_and_scope(
    app: App,
) -> None:
    await configure(app, "member", DecisionModel("wait"))
    observations: list[GroupObservation] = []
    app.service.observation_handler = observations.append
    for mid, text, expected in [
        ("701", "请记住我喜欢蓝色", "我喜欢蓝色"),
        ("702", "请记住我不喜欢蓝色", "我不喜欢蓝色"),
        ("703", "请忘记蓝色", None),
    ]:
        await app.service.observe_group_text(app.message(mid, "111", text), access_token=TOKEN)
        observation = observations[-1]
        await app.container.group_memory.observe(observation, (mid,))
        member = next(m for m in observation.route.members if m.sender_key == "111")
        session = await app.container.sessions.scene_evidence_session(
            observation.route.character_id,
            member.participant_id,
            observation.route.scene_id,
        )
        records = await app.container.memory.list(session_id=session.session_id)
        assert [r.text for r in records] == ([] if expected is None else [expected])
        assert all(r.subject_id == "participant:" + member.participant_id for r in records)
    owner = await app.container.sessions.create_session("default")
    assert not await app.container.memory.list(session_id=owner.session_id)
    assert not await app.container.database.fetchall("SELECT * FROM turns")


async def test_group_background_task_keeps_scene_grant_without_foreground_generation(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatwaifu_protocol.agent import AgentTaskCreate, TaskAuthorization
    from chatwaifu_protocol.skills import SkillInvocation, SkillRunState
    from chatwaifu_runtime.agent.capabilities import CapabilityCatalog

    from services.runtime.tests.test_agent_tasks import ComposingModel, await_state

    await configure(app, "member", DecisionModel("wait"))
    app.container.channel_groups = app.service
    app.container.qq_scene_capabilities.groups = app.service
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    member = route.members[0]
    session = await app.container.sessions.scene_evidence_session(
        route.character_id, member.participant_id, route.scene_id
    )
    binding = await app.service.task_binding(route.route_id, member.sender_key, "100")
    assert await app.container._authorize_task_channel(binding)
    tasks = app.container.agent_tasks
    model = ComposingModel()
    tasks.model_factory = lambda: model
    tasks.catalog = CapabilityCatalog(
        app.container.runtime_skills.list, app.container.runtime_skills.instructions
    )
    task = await tasks.create(
        AgentTaskCreate(
            session_id=session.session_id,
            goal="写入 notes/result.txt 并回读确认",
            authorization=TaskAuthorization(
                allowed_skill_ids=["workspace.files", "qq.scene"],
                resource_roots=["notes"],
                allow_writes=True,
                source_ref="group:100",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        ),
        channel_binding=binding,
    )
    completed = await await_state(app.container, task.task_id)
    assert completed.state.value == "succeeded"
    owner = await app.container.sessions.create_session("default")
    with pytest.raises(KeyError):
        await tasks.get(task.task_id, owner.session_id)
    with pytest.raises(FileNotFoundError):
        await app.container.workspace_skills.read(
            str(owner.session_id), {"path": "notes/result.txt"}
        )
    with pytest.raises(PermissionError):
        await tasks.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal="读私人日历",
                authorization=TaskAuthorization(
                    allowed_skill_ids=["calendar.read"],
                    source_ref="group:100",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            ),
            channel_binding=binding,
        )

    # Fresh task for an actual QQ skill call after the foreground has gone.
    await tasks.stop()
    queued = await tasks.create(
        AgentTaskCreate(
            session_id=session.session_id,
            goal="查群资料",
            authorization=TaskAuthorization(
                allowed_skill_ids=["qq.scene"],
                source_ref="group:100",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        ),
        channel_binding=binding,
    )
    from chatwaifu_protocol.agent import AgentTaskState

    await tasks.update_checkpoint(queued.task_id, state=AgentTaskState.RUNNING)
    calls: list[tuple[str, JsonObject]] = []

    async def call(
        connection: object, account: str, action: str, params: JsonObject, guard: object
    ) -> JsonObject:
        assert await guard()  # type: ignore[operator]
        calls.append((action, params))
        return {"group_name": "Fixture group"}

    monkeypatch.setattr(app.container.qq_scene_capabilities, "call", call)
    run = await app.container.runtime_skills.invoke(
        session.session_id,
        SkillInvocation(
            skill_id="qq.scene", capability="get_group_info", arguments={"action": "get_group_info"}
        ),
        origin="agent",
        task_id=queued.task_id,
    )
    final = await app.container.runtime_skills.wait_for_terminal(run.skill_run_id)
    assert final.state is SkillRunState.SUCCEEDED and calls
    assert calls[0][1] == {"group_id": route.group_id}
    current = await app.repository.get_route(route.route_id)
    assert current is not None
    policy = await app.container.group_autonomy.policy(current)
    await app.container.group_autonomy.configure(
        current,
        GroupAutonomyUpdate(
            expected_revision=policy.revision, policy=policy.model_copy(update={"mode": "off"})
        ),
    )
    assert not await app.container._authorize_task_channel(binding)
    with pytest.raises(PermissionError):
        await app.container.runtime_skills.invoke(
            session.session_id,
            SkillInvocation(
                skill_id="qq.scene",
                capability="get_group_info",
                arguments={"action": "get_group_info"},
            ),
            origin="agent",
            task_id=queued.task_id,
        )


async def test_group_task_file_uses_existing_ledger_and_unknown_send_is_never_replayed(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path
    from uuid import uuid4

    from chatwaifu_protocol.agent import AgentTaskCreate, AgentTaskState, TaskAuthorization
    from chatwaifu_protocol.channels import ChannelDeliveryStatus
    from chatwaifu_protocol.skills import SkillInvocation, SkillRunState
    from chatwaifu_runtime.external_channels.adapters.qq_napcat.delivery import NapCatDelivery
    from chatwaifu_runtime.external_channels.scheduler import ChannelDeliveryScheduler
    from chatwaifu_runtime.external_channels.service import delivery_plan_snapshot

    await configure(app, "member", DecisionModel("wait"))
    app.container.channel_groups = app.service
    app.container.qq_scene_capabilities.groups = app.service
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    member = route.members[0]
    session = await app.container.sessions.scene_evidence_session(
        route.character_id, member.participant_id, route.scene_id
    )
    binding = await app.service.task_binding(route.route_id, member.sender_key, "100")
    tasks = app.container.agent_tasks
    await tasks.stop()
    task = await tasks.create(
        AgentTaskCreate(
            session_id=session.session_id,
            goal="发回本群",
            authorization=TaskAuthorization(
                allowed_skill_ids=["channel.file"],
                allow_writes=True,
                source_ref="group:100",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        ),
        channel_binding=binding,
    )
    await tasks.update_checkpoint(task.task_id, state=AgentTaskState.RUNNING)
    artifact = await app.container.artifacts.save(
        session.session_id, "资料.txt", "text/plain", "实际内容".encode(), task_id=task.task_id
    )
    run = await app.container.runtime_skills.invoke(
        session.session_id,
        SkillInvocation(
            skill_id="channel.file",
            capability="send",
            arguments={"artifact_id": str(artifact.artifact_id)},
        ),
        origin="agent",
        task_id=task.task_id,
        turn_id=uuid4(),
        generation_id=uuid4(),
    )
    async with asyncio.timeout(5):
        while not await app.container.database.fetchone(  # noqa: ASYNC110
            "SELECT delivery_id FROM channel_deliveries WHERE task_delivery_id IS NOT NULL"
        ):
            await asyncio.sleep(0.01)
    row = await app.container.database.fetchone(
        "SELECT delivery_id FROM channel_deliveries WHERE task_delivery_id IS NOT NULL"
    )
    from uuid import UUID

    assert row is not None
    plan = await app.container.external_channel_repository.get_delivery_plan(
        UUID(row["delivery_id"])
    )
    assert plan is not None and plan.task_target is not None
    assert plan.channel_turn_id is None and plan.outbound_intent_id is None
    assert delivery_plan_snapshot(plan).schema_version == "1.2"
    connection = await app.container.external_channel_repository.get_connection(route.connection_id)
    assert connection is not None

    class FileClient:
        def __init__(self) -> None:
            self.sends = 0

        async def send_file(
            self,
            receiver: str,
            data: bytes,
            name: str,
            *,
            group_id: str,
            before_send: object,
            checkpoint: object,
        ) -> str:
            assert receiver == member.sender_key and group_id == route.group_id
            assert data == "实际内容".encode() and name == "资料.txt"
            assert await before_send()  # type: ignore[operator]
            await checkpoint()  # type: ignore[operator]
            self.sends += 1
            # The remote accepted the bytes but disconnected before returning a receipt.
            raise ConnectionError("uncertain provider receipt")

    client = FileClient()
    delivery = NapCatDelivery(
        app.container.external_channel_repository,
        cast(NapCatClient, client),
        route.connection_id,
        connection.configuration.allowed_sender_keys[0],
        Path("/unused"),
        artifacts=app.container.artifacts,
        task_authorization=app.container._authorize_task_delivery,
    )
    scheduler = ChannelDeliveryScheduler(
        app.container.external_channel_repository,
        delivery,
        app.container.event_publisher,
        connection_id=route.connection_id,
        on_plan_terminal=app.container.channel_files.on_terminal,
    )
    assert await scheduler.step()
    terminal = await app.container.runtime_skills.wait_for_terminal(run.skill_run_id)
    assert terminal.state is SkillRunState.SUCCEEDED
    assert terminal.result is not None and isinstance(terminal.result.data, dict)
    assert terminal.result.data["platform_accepted"] is False
    assert terminal.result.data["user_received"] is None
    final = await app.container.external_channel_repository.get_delivery_plan(plan.delivery_id)
    assert final is not None
    assert final.status is ChannelDeliveryStatus.FAILED and client.sends == 1
    # Restarted transport reads the persisted unknown marker; no second RPC.
    again = await delivery.execute_part(plan, plan.parts[0])
    assert again.error is not None
    assert again.error.code == "qq_delivery_unknown" and client.sends == 1


@pytest.mark.parametrize("uncertain", [False, True])
async def test_background_result_receipt_budget_and_policy_cancellation(
    app: App, uncertain: bool
) -> None:
    from pathlib import Path

    from chatwaifu_protocol.agent import AgentTaskCreate, AgentTaskState, TaskAuthorization
    from chatwaifu_protocol.channels import ChannelDeliveryStatus
    from chatwaifu_runtime.external_channels.adapters.qq_napcat.delivery import NapCatDelivery
    from chatwaifu_runtime.external_channels.scheduler import ChannelDeliveryScheduler

    policy = await configure(app, "member", DecisionModel("wait"))
    app.container.channel_groups = app.service
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    member = route.members[0]
    session = await app.container.sessions.scene_evidence_session(
        route.character_id, member.participant_id, route.scene_id
    )
    binding = await app.service.task_binding(route.route_id, member.sender_key, "100")
    binding = binding.model_copy(update={"policy_revision": policy.revision})
    service = app.container.agent_tasks
    await service.stop()

    async def completed(text: str):
        task = await service.create(
            AgentTaskCreate(
                session_id=session.session_id,
                goal=text,
                authorization=TaskAuthorization(
                    allowed_skill_ids=["workspace.files"],
                    source_ref="group:100",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            ),
            channel_binding=binding,
        )
        return await service.update_checkpoint(
            task.task_id, state=AgentTaskState.SUCCEEDED, result_text=text
        )

    task = await completed("资料整理完成，已读取确认")
    delivery_id = await app.container.channel_files.publish_task_result(task)
    assert delivery_id is not None
    assert await app.container.channel_files.publish_task_result(task) == delivery_id
    plans = await app.container.database.fetchall(
        "SELECT delivery_id FROM channel_deliveries WHERE task_delivery_id IS NOT NULL"
    )
    assert len(plans) == 1
    connection = await app.container.external_channel_repository.get_connection(route.connection_id)
    assert connection is not None

    class TextClient:
        sends = 0

        async def send_group(
            self, group_id: str, segments: list[JsonObject], *, before_send: object
        ) -> str:
            assert group_id == route.group_id and await before_send()  # type: ignore[operator]
            self.sends += 1
            assert "资料整理完成" in str(segments)
            if uncertain:
                raise ConnectionError("no receipt")
            return "native-fixture-result-receipt"

    client = TextClient()
    adapter = NapCatDelivery(
        app.container.external_channel_repository,
        cast(NapCatClient, client),
        route.connection_id,
        connection.configuration.allowed_sender_keys[0],
        Path("/unused"),
        task_authorization=app.container._authorize_task_delivery,
    )
    scheduler = ChannelDeliveryScheduler(
        app.container.external_channel_repository,
        adapter,
        app.container.event_publisher,
        connection_id=route.connection_id,
    )
    assert await scheduler.step()
    plan = await app.container.external_channel_repository.get_delivery_plan(delivery_id)
    assert plan is not None and client.sends == 1
    assert plan.status is (
        ChannelDeliveryStatus.FAILED if uncertain else ChannelDeliveryStatus.DELIVERED
    )
    if uncertain:
        again = await adapter.execute_part(plan, plan.parts[0])
        assert (
            again.error is not None
            and again.error.code == "qq_delivery_unknown"
            and client.sends == 1
        )
    following = await completed("第二个结果")
    second_id = await app.container.channel_files.publish_task_result(following)
    assert second_id is not None
    second = await app.container.external_channel_repository.get_delivery_plan(second_id)
    assert second is not None
    assert second.parts[0].not_before_at is not None
    assert second.parts[0].not_before_at > second.delivery.created_at + timedelta(seconds=20)
    assert not await scheduler.step()  # Future budget reservation is not sent early.
    policy = await app.container.group_autonomy.policy(route)
    await app.container.group_autonomy.configure(
        route,
        GroupAutonomyUpdate(
            expected_revision=policy.revision, policy=policy.model_copy(update={"mode": "off"})
        ),
    )
    cancelled = await app.container.external_channel_repository.get_delivery_plan(second_id)
    assert cancelled is not None and cancelled.status is ChannelDeliveryStatus.CANCELLED
    assert (await service.get(task.task_id, session.session_id)).state is AgentTaskState.SUCCEEDED
