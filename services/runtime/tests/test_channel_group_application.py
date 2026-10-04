"""Real SQLite/Conversation group coordination with local, event-controlled models."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channel_groups import (
    ChannelGroupAudienceRequest,
    ChannelGroupPauseReason,
    ChannelGroupRouteCreate,
    ChannelGroupRouteSnapshot,
    ChannelGroupRouteUpdate,
    ChannelGroupTurnCancelRequest,
    ChannelParticipantLinkCreate,
    ChannelParticipantLinkUpdate,
)
from chatwaifu_protocol.channels import (
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartStatus,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_protocol.events import UserTurnCommittedEvent, UserTurnCommittedPayload
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_protocol.session import GenerationState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationSourceContext
from chatwaifu_runtime.external_channels.group_models import (
    ChannelGroupAdmission,
    ChannelGroupAdmissionResult,
    ChannelGroupAuthorization,
    ChannelGroupInboundDescriptor,
    ChannelGroupPlanResult,
    ChannelGroupRouteLineage,
    ChannelGroupTransition,
)
from chatwaifu_runtime.external_channels.groups import ChannelGroupService
from chatwaifu_runtime.external_channels.service import (
    ChannelAuthenticationError,
    ChannelBusyError,
    ChannelConflictError,
    ChannelNotFoundError,
    ChannelPolicyError,
)
from chatwaifu_runtime.persistence.sqlite_channel_groups import SQLiteChannelGroupRepository
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig

TOKEN = "local-fixture-token-at-least-16"


class Answer:
    kind = "demo"
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []
        self.started: asyncio.Queue[LlmRequest] = asyncio.Queue()
        self.hold: asyncio.Event | None = None
        self.cancelled = asyncio.Event()
        self.ignore_cancel = False
        self.fail = False
        self.reply_text: str | None = None

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        self.started.put_nowait(request)
        if self.hold is not None:
            try:
                await self.hold.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.ignore_cancel:
                    raise
                task = asyncio.current_task()
                assert task is not None
                task.uncancel()
                await self.hold.wait()
        if self.fail:
            raise RuntimeError("private provider detail must not escape")
        yield LlmTextDelta(self.reply_text or f"reply:{request.user_text}")
        yield LlmResponseCompleted("stop")


@dataclass
class App:
    container: RuntimeContainer
    service: ChannelGroupService
    repository: SQLiteChannelGroupRepository
    provider: Answer
    connection_id: UUID
    route: ChannelGroupRouteSnapshot
    ready: bool = True

    def message(
        self, raw_id: str = "1", sender: str = "111", text: str = "hello"
    ) -> ChannelGroupInboundDescriptor:
        return ChannelGroupInboundDescriptor(
            self.connection_id, "900", "500", sender, raw_id, text, datetime.now(UTC)
        )

    async def enable(self, speakers: list[str] | None = None) -> None:
        observation = await self.service.observe_audience(
            self.connection_id, ChannelGroupAudienceRequest(group_id="500")
        )
        self.route = await self.service.update_route(
            self.connection_id,
            self.route.route_id,
            ChannelGroupRouteUpdate(
                enabled=True,
                expected_revision=self.route.revision,
                observation_id=observation.observation_id,
                speaker_sender_keys=speakers if speakers is not None else ["111", "222"],
            ),
        )

    async def ingest(self, raw_id: str = "1", sender: str = "111", text: str = "hello"):
        return await self.service.ingest_group(
            self.message(raw_id, sender, text), access_token=TOKEN
        )

    async def join(self, channel_turn_id: UUID) -> None:
        actor = self.service._workflows.get(channel_turn_id)
        if actor is not None and actor.task is not None:
            await asyncio.wait_for(asyncio.shield(actor.task), 3)


@pytest.fixture
async def app(runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[App]:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    repository = SQLiteChannelGroupRepository(container.database, container.event_store)
    service = ChannelGroupService(
        repository,
        container.external_channel_repository,
        container.conversation,
        container.sessions,
        container.event_publisher,
        conversation_repository=container.conversation_repository,
    )
    await service.start()
    connection_id = uuid4()
    await container.external_channel_repository.create_connection(
        ChannelConnectionConfiguration(
            connection_id=connection_id,
            provider_id="qq_napcat",
            name="local fixture",
            character_id="default",
            principal_scope="local",
            account_key="900",
            allowed_sender_keys=["999"],
            enabled=True,
        ),
        access_token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(),
        created_at=datetime.now(UTC),
    )
    await container.external_channel_repository.touch_connection(
        connection_id, status=ChannelConnectionStatus.READY, seen_at=datetime.now(UTC)
    )
    service.set_authenticator(container.external_channels.authenticate_group_transport)
    service.set_transport_ready(lambda _connection: True)

    async def reader(_connection: UUID, _group: str) -> tuple[str, tuple[str, ...]]:
        return "900", ("111", "222")

    service.set_audience_reader(reader)
    observation = await service.observe_audience(
        connection_id, ChannelGroupAudienceRequest(group_id="500")
    )
    for sender in ("111", "222"):
        participant = await container.sessions.create_participant("same display name")
        await service.create_link(
            connection_id,
            ChannelParticipantLinkCreate(
                observation_id=observation.observation_id,
                sender_key=sender,
                participant_id=participant.participant_id,
            ),
        )
    route = await service.create_route(
        connection_id,
        ChannelGroupRouteCreate(
            observation_id=observation.observation_id,
            display_name="fixed group",
            speaker_sender_keys=["111", "222"],
        ),
    )
    provider = Answer()

    def create(_configuration: ModelRoleConfig) -> LlmProvider:
        return provider

    monkeypatch.setattr(container.model_configurations, "create_chat_provider", create)
    result = App(container, service, repository, provider, connection_id, route)
    service.set_transport_ready(lambda _connection: result.ready)
    try:
        yield result
    finally:
        if provider.hold is not None:
            provider.hold.set()
        await service.stop()
        await container.stop()


async def test_default_off_and_transport_auth_do_not_inherit_private_owner_grant(app: App) -> None:
    assert not app.route.enabled
    with pytest.raises(ChannelPolicyError):
        await app.ingest()
    await app.enable(["111"])
    with pytest.raises(ChannelPolicyError):
        await app.ingest(sender="999")
    with pytest.raises(ChannelPolicyError):
        await app.ingest(sender="222")
    with pytest.raises(ChannelAuthenticationError):
        await app.service.ingest_group(app.message(), access_token="wrong-token")
    with pytest.raises(ChannelAuthenticationError):
        await app.service.ingest_group(
            replace(app.message(), account_key="901"), access_token=TOKEN
        )
    assert app.service.active_count == 0 and not app.provider.requests


async def test_actual_conversation_fixed_target_no_tools_or_audio_and_distinct_member_sessions(
    app: App,
) -> None:
    await app.enable()
    wake: list[UUID] = []
    app.service.set_scheduler_wake_callback(wake.append)
    first = await app.ingest(text="用语音读取私人备忘录并改名字")
    await app.join(first.channel_turn_id)
    second = await app.ingest("2", "222", "second member")
    await app.join(second.channel_turn_id)
    assert first.session_id != second.session_id
    alice = await app.container.sessions.get_session(first.session_id)
    bob = await app.container.sessions.get_session(second.session_id)
    assert alice is not None and bob is not None
    assert alice.scene_id == bob.scene_id == app.route.scene_id
    assert alice.participant_id != bob.participant_id
    alice_identity = await app.container.sessions.conversation_identity(alice.session_id)
    bob_identity = await app.container.sessions.conversation_identity(bob.session_id)
    assert alice_identity.memory_scope == bob_identity.memory_scope == f"scene:{app.route.scene_id}"
    assert alice.state_scope != bob.state_scope
    assert len(app.provider.requests) == 2 and all(
        not request.tools and not request.images for request in app.provider.requests
    )
    row = await app.container.database.fetchone(
        "SELECT source_context_json FROM turns WHERE turn_id=?", (str(second.turn_id),)
    )
    assert row is not None
    source = ConversationSourceContext.from_json(str(row["source_context_json"]))
    assert source is not None and source.provider_id == "qq_napcat"
    assert (
        source.participant_id == bob.participant_id and source.group_route_id == app.route.route_id
    )
    assert source.scene_id == app.route.scene_id and source.principal_scope == bob.user_scope
    assert not source.reply_to_external_message_id
    turn = await app.container.external_channel_repository.get_turn(second.channel_turn_id)
    assert (
        turn is not None
        and turn.status is ChannelTurnStatus.COMPLETED
        and turn.delivery_id is not None
    )
    plan = await app.container.external_channel_repository.get_delivery_plan(turn.delivery_id)
    assert plan is not None and plan.group_target is not None and plan.part_count == 1
    assert plan.group_target.group_id == "500" and plan.group_target.scene_id == bob.scene_id
    assert await app.service.authorize_delivery(plan)
    app.ready = False
    assert not await app.service.authorize_delivery(plan)
    assert wake == [app.connection_id, app.connection_id]
    assert not list(app.container.settings.data_dir.glob("audio/*.wav"))


async def test_group_short_contract_and_bubbles_preserve_one_canonical_turn(app: App) -> None:
    await app.enable()
    app.provider.reply_text = "嗯，在呀。\n\n怎么啦？"
    receipt = await app.ingest(text="在吗？")
    await app.join(receipt.channel_turn_id)
    assert "usually 5-30 Chinese characters" in app.provider.requests[0].system_prompt
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is not None
    plan = await app.container.external_channel_repository.get_delivery_plan(turn.delivery_id)
    assert plan is not None and plan.part_count == 2 and plan.group_target is not None
    assert [part.ordinal for part in plan.parts] == [0, 1]
    assert all(part.required for part in plan.parts)
    assert (
        "".join(
            part.payload.text
            for part in plan.parts
            if isinstance(part.payload, ChannelTextDeliveryPartPayload)
        )
        == turn.reply_text
        == app.provider.reply_text
    )
    assert await app.service.authorize_delivery(plan)
    row = await app.container.database.fetchone(
        "SELECT count(*) FROM turns WHERE session_id=? AND role='assistant'",
        (str(receipt.session_id),),
    )
    assert row is not None and row[0] == 1


async def test_group_receipt_schedules_tail_and_new_mention_cancels_only_unsent_bubbles(
    app: App,
) -> None:
    await app.enable()
    app.provider.reply_text = "嗯，在呀。\n\n怎么啦？"
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is not None
    repository = app.container.external_channel_repository
    first = await repository.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(delivery_id=turn.delivery_id, lease_id=uuid4()),
        claimed_at=datetime.now(UTC),
    )
    assert first is not None and first.part is not None and first.part.lease_id is not None
    assert first.plan.part_count == 2 and first.part.delay_after_ms > 0
    acknowledged_at = datetime.now(UTC)
    transition = await repository.acknowledge_delivery_part(
        ChannelDeliveryPartAcknowledgement(
            delivery_id=turn.delivery_id,
            part_id=first.part.part_id,
            lease_id=first.part.lease_id,
            status=ChannelDeliveryPartStatus.DELIVERED,
            provider_message_id="local-bubble-receipt",
            acknowledged_at=acknowledged_at,
        ),
        updated_at=acknowledged_at,
    )
    assert transition.plan.parts[1].not_before_at == acknowledged_at + timedelta(
        milliseconds=first.part.delay_after_ms
    )
    assert await app.service.authorize_delivery(transition.plan)
    early = await repository.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(delivery_id=turn.delivery_id, lease_id=uuid4()),
        claimed_at=acknowledged_at,
    )
    assert early is None
    later = await app.ingest("2", "222", "new current question")
    await app.join(later.channel_turn_id)
    old = await repository.get_delivery_plan(turn.delivery_id)
    assert old is not None
    assert old.parts[0].status is ChannelDeliveryPartStatus.DELIVERED
    assert old.parts[0].provider_message_id == "local-bubble-receipt"
    assert old.parts[1].status is ChannelDeliveryPartStatus.CANCELLED
    assert not await app.service.authorize_delivery(old)


async def test_group_technical_code_is_complete_and_bypasses_casual_bubbles(app: App) -> None:
    await app.enable()
    app.provider.reply_text = "代码如下:\n\n```python\nprint(1 + 1)\n```\n\n输出为 2。"
    receipt = await app.ingest(text="给出完整代码并解释输出。")
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is not None
    plan = await app.container.external_channel_repository.get_delivery_plan(turn.delivery_id)
    assert plan is not None and plan.part_count == 1
    assert isinstance(plan.parts[0].payload, ChannelTextDeliveryPartPayload)
    assert plan.parts[0].payload.text == turn.reply_text == app.provider.reply_text
    assert await app.service.authorize_delivery(plan)


async def test_redelivery_with_new_timestamp_never_moves_head_or_replays_and_sender_conflicts(
    app: App,
) -> None:
    await app.enable()
    first = await app.ingest()
    await app.join(first.channel_turn_id)
    head = await app.container.database.fetchone(
        "SELECT * FROM channel_group_route_heads WHERE route_id=?", (str(app.route.route_id),)
    )
    duplicate = await app.service.ingest_group(
        replace(app.message(), received_at=datetime.now(UTC) + timedelta(seconds=1)),
        access_token=TOKEN,
    )
    assert duplicate.duplicate and duplicate.channel_turn_id == first.channel_turn_id
    assert len(app.provider.requests) == 1
    head_after = await app.container.database.fetchone(
        "SELECT * FROM channel_group_route_heads WHERE route_id=?", (str(app.route.route_id),)
    )
    assert head is not None and head_after is not None and tuple(head) == tuple(head_after)
    with pytest.raises(ChannelConflictError):
        await app.ingest(sender="222")
    with pytest.raises(ChannelConflictError):
        await app.ingest(text="mutated")


@pytest.mark.parametrize(
    "reason",
    [
        ChannelGroupPauseReason.MEMBERSHIP_CHANGED,
        ChannelGroupPauseReason.ACCOUNT_CHANGED,
        ChannelGroupPauseReason.CONNECTION_DISABLED,
        ChannelGroupPauseReason.CONNECTION_DELETED,
        ChannelGroupPauseReason.RECONNECT,
    ],
)
async def test_pause_fences_slow_prepare_before_any_model(
    app: App, monkeypatch: pytest.MonkeyPatch, reason: ChannelGroupPauseReason
) -> None:
    await app.enable()
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.container.memory.retrieve_context

    async def slow(*args: object, **kwargs: object) -> MemoryContextPacket:
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(app.container.memory, "retrieve_context", slow)
    receipt = await app.ingest()
    await asyncio.wait_for(entered.wait(), 2)
    await app.service.pause_connection(app.connection_id, reason)
    release.set()
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    route = await app.repository.get_route(app.route.route_id)
    assert (
        turn is not None and turn.status is ChannelTurnStatus.CANCELLED and turn.delivery_id is None
    )
    assert route is not None and not route.enabled and route.pause_reason is reason
    assert not app.provider.requests and app.service.active_count == 0


async def test_sync_fence_during_auth_revokes_even_if_callback_uncancels(app: App) -> None:
    await app.enable()
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.container.external_channels.authenticate_group_transport

    async def authentication(connection_id: UUID, token: str):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            assert task is not None
            task.uncancel()
            await release.wait()
        return await original(connection_id, token)

    app.service.set_authenticator(authentication)
    pending = asyncio.create_task(app.ingest())
    await asyncio.wait_for(entered.wait(), 2)
    app.service.fence_connection(app.connection_id, "membership_changed", "500")
    release.set()
    with pytest.raises(ChannelPolicyError):
        await pending
    assert app.service.active_count == 0 and not app.provider.requests
    assert not (await app.service.list_turns(app.connection_id, app.route.route_id)).items


async def test_slow_provider_swallowing_cancel_has_no_late_plan_and_latest_pending_only(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await app.enable()
    app.provider.hold = asyncio.Event()
    app.provider.ignore_cancel = True
    first = await app.ingest("1", "111", "first")
    await asyncio.wait_for(app.provider.started.get(), 2)
    second_task = asyncio.create_task(app.ingest("2", "222", "second must never start"))
    await asyncio.wait_for(app.provider.cancelled.wait(), 2)
    # New ingress can replace the durable pending while cancellation/join waits outside locks.
    committed = asyncio.Event()
    original = app.repository.admit_group_turn

    async def admit(admission: ChannelGroupAdmission) -> ChannelGroupAdmissionResult:
        result = await original(admission)
        if admission.message.external_message_id == "3":
            committed.set()
        return result

    monkeypatch.setattr(app.repository, "admit_group_turn", admit)
    third_task = asyncio.create_task(app.ingest("3", "111", "latest"))
    await asyncio.wait_for(committed.wait(), 2)
    app.provider.hold.set()
    results = await asyncio.gather(second_task, third_task, return_exceptions=True)
    third = results[1]
    assert not isinstance(third, BaseException)
    await app.join(third.channel_turn_id)
    assert [request.user_text for request in app.provider.requests] == ["first", "latest"]
    old = await app.container.external_channel_repository.get_turn(first.channel_turn_id)
    middle = await app.repository.find_group_turn(app.connection_id, "500", "2")
    assert old is not None and old.delivery_id is None and old.status is ChannelTurnStatus.CANCELLED
    assert (
        middle is not None
        and middle.turn.delivery_id is None
        and middle.turn.status is ChannelTurnStatus.CANCELLED
    )
    assert app.service.active_count == 0


async def test_failed_generation_is_failed_without_fabricated_reply_plan(app: App) -> None:
    await app.enable()
    app.provider.fail = True
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    result = await app.container.conversation_repository.generation_result(receipt.generation_id)
    assert turn is not None and turn.status is ChannelTurnStatus.FAILED
    assert turn.delivery_id is None and turn.reply_text is None
    assert turn.error is not None and turn.error.message == "Group reply unavailable"
    assert result is not None and result.state is GenerationState.FAILED
    event = await app.container.database.fetchone(
        "SELECT envelope_json FROM events WHERE session_id=? AND event_type='channel.turn_failed'",
        (str(receipt.session_id),),
    )
    assert event is not None and str(receipt.generation_id) in str(event[0])
    assert "private provider detail" not in str(event[0])


async def test_stale_operator_cas_does_not_cancel_current_generation(app: App) -> None:
    await app.enable()
    app.provider.hold = asyncio.Event()
    receipt = await app.ingest()
    await asyncio.wait_for(app.provider.started.get(), 2)
    with pytest.raises(ChannelConflictError):
        await app.service.update_route(
            app.connection_id,
            app.route.route_id,
            ChannelGroupRouteUpdate(enabled=False, expected_revision=1, speaker_sender_keys=[]),
        )
    with pytest.raises(ChannelConflictError):
        await app.service.cancel_turn(
            app.connection_id,
            app.route.route_id,
            receipt.channel_turn_id,
            ChannelGroupTurnCancelRequest(expected_revision=99),
        )
    assert not app.provider.cancelled.is_set()
    app.provider.hold.set()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert (
        turn is not None
        and turn.status is ChannelTurnStatus.COMPLETED
        and turn.delivery_id is not None
    )


async def test_registered_prepare_sync_does_not_claim_generation_missing(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.enable()
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.repository.begin_group_turn

    async def begin(*args: object, **kwargs: object) -> bool:
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(app.repository, "begin_group_turn", begin)
    receipt = await app.ingest()
    await asyncio.wait_for(entered.wait(), 2)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None
    assert (await app.service.sync_turn(turn)).status is ChannelTurnStatus.ACCEPTED
    release.set()
    await app.join(receipt.channel_turn_id)


async def test_restart_pauses_old_route_without_replay(app: App) -> None:
    await app.enable()
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    await app.service.stop()
    await app.service.start()
    route = await app.repository.get_route(app.route.route_id)
    assert (
        route is not None
        and not route.enabled
        and route.pause_reason is ChannelGroupPauseReason.RECONNECT
    )
    assert len(app.provider.requests) == 1
    with pytest.raises(ChannelPolicyError):
        await app.ingest("2")


async def test_link_revoke_pauses_routes_and_keeps_account_identity_immutable(app: App) -> None:
    await app.enable()
    app.provider.hold = asyncio.Event()
    receipt = await app.ingest()
    await asyncio.wait_for(app.provider.started.get(), 2)
    link = (await app.service.list_links(app.connection_id)).items[0]
    await app.service.update_link(
        app.connection_id,
        link.link_id,
        ChannelParticipantLinkUpdate(enabled=False, expected_revision=link.revision),
    )
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None and route.pause_reason is ChannelGroupPauseReason.LINK_REVOKED
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is None


async def test_group_scene_reset_hook_commits_pause_without_start_lock_join_deadlock(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.enable()
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.container.memory.retrieve_context

    async def slow(*args: object, **kwargs: object) -> MemoryContextPacket:
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(app.container.memory, "retrieve_context", slow)
    receipt = await app.ingest()
    await asyncio.wait_for(entered.wait(), 2)
    session = await app.container.sessions.get_session(receipt.session_id)
    assert session is not None
    async with app.container.conversation._start_lock:
        await asyncio.wait_for(app.service.before_scope_reset(session), 2)
    release.set()
    await asyncio.gather(
        *(item.task for item in app.service._workflows.values() if item.task is not None),
        return_exceptions=True,
    )
    assert not app.provider.requests
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None and route.pause_reason is ChannelGroupPauseReason.SCENE_RESET


async def test_stop_fences_unregistered_durable_admission_window(app: App) -> None:
    await app.enable()
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.container.external_channels.authenticate_group_transport

    async def auth(connection_id: UUID, token: str):
        entered.set()
        await release.wait()
        return await original(connection_id, token)

    app.service.set_authenticator(auth)
    incoming = asyncio.create_task(app.ingest())
    await asyncio.wait_for(entered.wait(), 2)
    await app.service.stop()
    assert incoming.cancelled() and app.service.active_count == 0
    assert not app.provider.requests


async def test_default_authenticator_and_default_transport_are_fail_closed(app: App) -> None:
    await app.enable()
    app.service._authenticator = None
    with pytest.raises(ChannelAuthenticationError):
        await app.ingest()
    app.service.set_authenticator(app.container.external_channels.authenticate_group_transport)
    app.ready = False
    with pytest.raises(ChannelPolicyError):
        await app.ingest()


async def test_known_receipt_history_survives_revocation_and_cannot_claim_withdrawal(
    app: App,
) -> None:
    await app.enable()
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is not None
    claim = await app.container.external_channel_repository.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(
            delivery_id=turn.delivery_id, lease_seconds=30, lease_id=uuid4()
        ),
        claimed_at=datetime.now(UTC),
    )
    assert claim is not None and claim.part is not None and claim.part.lease_id is not None
    await app.container.external_channel_repository.acknowledge_delivery_part(
        ChannelDeliveryPartAcknowledgement(
            delivery_id=turn.delivery_id,
            part_id=claim.part.part_id,
            lease_id=claim.part.lease_id,
            status=ChannelDeliveryPartStatus.DELIVERED,
            provider_message_id="local-known-receipt",
            acknowledged_at=datetime.now(UTC),
        ),
        updated_at=datetime.now(UTC),
    )
    await app.service.pause_connection(
        app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED
    )
    page = await app.service.list_turns(app.connection_id, app.route.route_id)
    assert (
        len(page.items) == 1
        and page.items[0].provider_receipt_present
        and not page.items[0].cancelable
    )
    assert page.items[0].turn.status is ChannelTurnStatus.COMPLETED


async def test_global_capacity_bounds_inflight_auth_registrations(app: App) -> None:
    await app.enable()
    entered: asyncio.Queue[None] = asyncio.Queue()
    release = asyncio.Event()
    original = app.container.external_channels.authenticate_group_transport

    async def auth(connection_id: UUID, token: str):
        entered.put_nowait(None)
        await release.wait()
        return await original(connection_id, token)

    app.service.set_authenticator(auth)
    tasks = [asyncio.create_task(app.ingest(str(index + 1))) for index in range(32)]
    for _ in range(32):
        await asyncio.wait_for(entered.get(), 2)
    assert app.service.active_count == 32
    with pytest.raises(ChannelBusyError):
        await app.ingest("33")
    app.service.fence_connection(app.connection_id, "capacity_test_cleanup")
    await asyncio.gather(*tasks, return_exceptions=True)
    assert app.service.active_count == 0 and not app.provider.requests


async def test_async_authorization_result_cannot_override_synchronous_notice_fence(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.enable()
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.repository.authorize_group_turn

    async def authorize(lineage: ChannelGroupRouteLineage) -> ChannelGroupAuthorization:
        result = await original(lineage)
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            assert task is not None
            task.uncancel()
            await release.wait()
        return result

    monkeypatch.setattr(app.repository, "authorize_group_turn", authorize)
    receipt = await app.ingest()
    await asyncio.wait_for(entered.wait(), 2)
    app.service.fence_connection(app.connection_id, "membership_changed", "500")
    release.set()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert (
        turn is not None
        and turn.status is ChannelTurnStatus.CANCELLED
        and turn.delivery_id is None
        and not app.provider.requests
    )


async def test_delivery_authorization_rechecks_notice_after_last_database_await(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.enable()
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is not None
    plan = await app.container.external_channel_repository.get_delivery_plan(turn.delivery_id)
    assert plan is not None
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.repository.authorize_group_turn

    async def authorize(lineage: ChannelGroupRouteLineage) -> ChannelGroupAuthorization:
        result = await original(lineage)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(app.repository, "authorize_group_turn", authorize)
    pending = asyncio.create_task(app.service.authorize_delivery(plan))
    await asyncio.wait_for(entered.wait(), 2)
    app.service.fence_connection(app.connection_id, "membership_changed", "500")
    release.set()
    assert not await pending


async def test_plan_return_after_notice_cancels_unsent_part_without_fabricating_receipt(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.enable()
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.repository.create_group_plan

    async def create(
        lineage: ChannelGroupRouteLineage,
        *,
        reply_text: str,
        delivery_id: UUID,
        completed_at: datetime,
        parts: tuple[ChannelDeliveryPartDraft, ...] | None = None,
    ) -> ChannelGroupPlanResult:
        result = await original(
            lineage,
            reply_text=reply_text,
            delivery_id=delivery_id,
            completed_at=completed_at,
            parts=parts,
        )
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            assert task is not None
            task.uncancel()
            await release.wait()
        return result

    monkeypatch.setattr(app.repository, "create_group_plan", create)
    wake: list[UUID] = []
    app.service.set_scheduler_wake_callback(wake.append)
    receipt = await app.ingest()
    await asyncio.wait_for(entered.wait(), 2)
    app.service.fence_connection(app.connection_id, "membership_changed", "500")
    release.set()
    await app.join(receipt.channel_turn_id)
    page = await app.service.list_turns(app.connection_id, app.route.route_id)
    assert page.items[0].turn.status is ChannelTurnStatus.COMPLETED
    assert not page.items[0].provider_receipt_present and not page.items[0].cancelable and not wake
    assert page.items[0].turn.delivery_id is not None
    plan = await app.container.external_channel_repository.get_delivery_plan(
        page.items[0].turn.delivery_id
    )
    assert plan is not None and plan.parts[0].status is ChannelDeliveryPartStatus.CANCELLED
    assert not await app.service.authorize_delivery(plan)


async def test_private_history_and_memory_are_not_inherited_by_group_member_session(
    app: App,
) -> None:
    await app.enable()
    participant = app.route.members[0].participant_id
    private = await app.container.sessions.create_session("default", participant_id=participant)
    now = datetime.now(UTC)
    turn_id = uuid4()
    text = "请记住我喜欢PRIVATE_SENTINEL_SECRET"
    async with app.container.database.transaction() as connection:
        await connection.execute(
            "INSERT INTO turns(turn_id,session_id,role,committed_text,committed_at,created_at) "
            "VALUES (?,?,'user',?,?,?)",
            (str(turn_id), str(private.session_id), text, now.isoformat(), now.isoformat()),
        )
    event = await app.container.event_store.append(
        UserTurnCommittedEvent(
            event_id=uuid4(),
            session_id=private.session_id,
            turn_id=turn_id,
            sequence=0,
            occurred_at=now,
            source="test",
            payload=UserTurnCommittedPayload(text=text),
        )
    )
    await app.container.memory.observe_user_turn(
        private.session_id, turn_id, event.event_id, "default", text
    )
    assert await app.container.memory.list(session_id=private.session_id)
    receipt = await app.ingest(text="我喜欢什么？")
    await app.join(receipt.channel_turn_id)
    request = app.provider.requests[0]
    assert "PRIVATE_SENTINEL_SECRET" not in str(
        (request.system_prompt, request.history, request.context, request.recalled_memory_texts)
    )
    group = await app.container.sessions.get_session(receipt.session_id)
    assert group is not None and group.user_scope != private.user_scope
    assert not await app.container.memory.list(session_id=receipt.session_id)


async def test_speaker_grant_change_preserves_scene_but_cancels_old_revision(app: App) -> None:
    await app.enable()
    old_scene = app.route.scene_id
    app.provider.hold = asyncio.Event()
    receipt = await app.ingest()
    await asyncio.wait_for(app.provider.started.get(), 2)
    await app.enable(["222"])
    assert app.route.scene_id == old_scene
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is None
    with pytest.raises(ChannelPolicyError):
        await app.ingest("2", "111")
    app.provider.hold.set()
    second = await app.ingest("2", "222")
    await app.join(second.channel_turn_id)
    assert len(app.provider.requests) == 2


async def test_explicit_cancel_finishes_durable_turn_and_never_creates_plan(app: App) -> None:
    await app.enable()
    app.provider.hold = asyncio.Event()
    receipt = await app.ingest()
    await asyncio.wait_for(app.provider.started.get(), 2)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None
    result = await app.service.cancel_turn(
        app.connection_id,
        app.route.route_id,
        receipt.channel_turn_id,
        ChannelGroupTurnCancelRequest(expected_revision=turn.revision),
    )
    assert result.turn.status is ChannelTurnStatus.CANCELLED
    assert (
        result.turn.delivery_id is None
        and not result.cancelable
        and not result.provider_receipt_present
    )
    assert app.service.active_count == 0


async def test_admission_cancel_after_durable_commit_before_registration_is_closed(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.enable()
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.repository.admit_group_turn

    async def admit(admission: ChannelGroupAdmission) -> ChannelGroupAdmissionResult:
        result = await original(admission)
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            task = asyncio.current_task()
            assert task is not None
            task.uncancel()
            await release.wait()
        return result

    monkeypatch.setattr(app.repository, "admit_group_turn", admit)
    incoming = asyncio.create_task(app.ingest())
    await asyncio.wait_for(entered.wait(), 2)
    app.service.fence_connection(app.connection_id, "connection_deleted")
    release.set()
    with pytest.raises(ChannelPolicyError):
        await incoming
    page = await app.service.list_turns(app.connection_id, app.route.route_id)
    assert len(page.items) == 1 and page.items[0].turn.status is ChannelTurnStatus.CANCELLED
    assert not app.provider.requests and app.service.active_count == 0
    head = await app.container.database.fetchone(
        "SELECT active_channel_turn_id,pending_channel_turn_id "
        "FROM channel_group_route_heads WHERE route_id=?",
        (str(app.route.route_id),),
    )
    assert head is not None and head[0] is None and head[1] is None


async def test_audience_change_keeps_route_id_creates_scene_and_never_imports_old_scene_history(
    app: App,
) -> None:
    await app.enable()
    first = await app.ingest(text="old scene private-to-original-audience text")
    await app.join(first.channel_turn_id)
    old_scene = app.route.scene_id

    async def reader(_connection: UUID, _group: str) -> tuple[str, tuple[str, ...]]:
        return "900", ("111", "333")

    app.service.set_audience_reader(reader)
    observation = await app.service.observe_audience(
        app.connection_id, ChannelGroupAudienceRequest(group_id="500")
    )
    participant = await app.container.sessions.create_participant("same display name")
    await app.service.create_link(
        app.connection_id,
        ChannelParticipantLinkCreate(
            observation_id=observation.observation_id,
            sender_key="333",
            participant_id=participant.participant_id,
        ),
    )
    old_route_id = app.route.route_id
    app.route = await app.service.update_route(
        app.connection_id,
        old_route_id,
        ChannelGroupRouteUpdate(
            enabled=True,
            expected_revision=app.route.revision,
            observation_id=observation.observation_id,
            speaker_sender_keys=["111", "333"],
        ),
    )
    assert app.route.route_id == old_route_id and app.route.scene_id != old_scene
    second = await app.ingest("2", "333", "new audience")
    await app.join(second.channel_turn_id)
    assert "old scene private-to-original-audience text" not in str(
        app.provider.requests[-1].history
    )
    old = await app.container.external_channel_repository.get_turn(first.channel_turn_id)
    assert old is not None and old.delivery_id is not None
    plan = await app.container.external_channel_repository.get_delivery_plan(old.delivery_id)
    assert plan is not None and plan.parts[0].status is ChannelDeliveryPartStatus.CANCELLED
    assert not await app.service.authorize_delivery(plan)
    history = await app.service.list_turns(app.connection_id, old_route_id)
    assert {item.scene_id for item in history.items} == {old_scene, app.route.scene_id}


async def test_delivery_requires_exact_durable_target_and_single_unchanged_immediate_text(
    app: App,
) -> None:
    await app.enable()
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is not None
    plan = await app.container.external_channel_repository.get_delivery_plan(turn.delivery_id)
    assert plan is not None and plan.group_target is not None
    assert await app.service.authorize_delivery(plan)
    assert not await app.service.authorize_delivery(replace(plan, group_target=None))
    assert not await app.service.authorize_delivery(
        replace(plan, group_target=plan.group_target.model_copy(update={"group_id": "501"}))
    )
    part = plan.parts[0]
    assert not await app.service.authorize_delivery(
        replace(
            plan,
            parts=(replace(part, payload=ChannelTextDeliveryPartPayload(text="mutated reply")),),
        )
    )
    assert not await app.service.authorize_delivery(
        replace(plan, parts=(replace(part, delay_after_ms=1),))
    )
    assert not await app.service.authorize_delivery(replace(plan, parts=(part, part)))


async def test_observation_account_change_or_notice_during_read_cannot_become_enable_evidence(
    app: App,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def reader(_connection: UUID, _group: str) -> tuple[str, tuple[str, ...]]:
        entered.set()
        await release.wait()
        return "900", ("111", "222")

    app.service.set_audience_reader(reader)
    incoming = asyncio.create_task(
        app.service.observe_audience(app.connection_id, ChannelGroupAudienceRequest(group_id="500"))
    )
    await asyncio.wait_for(entered.wait(), 2)
    app.service.fence_connection(app.connection_id, "account_changed")
    release.set()
    with pytest.raises(ChannelPolicyError):
        await incoming
    assert app.service.active_count == 0


async def test_fresh_route_observation_is_required_and_expiry_does_not_disable_enabled_route(
    app: App,
) -> None:
    await app.enable()
    # This proof advances only the application clock: already-enabled routes have no TTL.
    app.service._clock = lambda: datetime.now(UTC) + timedelta(minutes=2)
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.status is ChannelTurnStatus.COMPLETED
    with pytest.raises(ChannelPolicyError):
        await app.service.update_route(
            app.connection_id,
            app.route.route_id,
            ChannelGroupRouteUpdate(
                enabled=True,
                expected_revision=app.route.revision,
                observation_id=app.route.observation_id,
                speaker_sender_keys=["111", "222"],
            ),
        )


async def test_start_fails_closed_on_durable_accepted_work_with_no_process_input(app: App) -> None:
    await app.enable()
    session = await app.container.sessions.create_session(
        "default", participant_id=app.route.members[0].participant_id, scene_id=app.route.scene_id
    )
    admitted = await app.repository.admit_group_turn(
        ChannelGroupAdmission(
            app.message(),
            app.route.route_id,
            app.route.revision,
            session.session_id,
            uuid4(),
            uuid4(),
            uuid4(),
            datetime.now(UTC),
        )
    )
    recovered = ChannelGroupService(
        app.repository,
        app.container.external_channel_repository,
        app.container.conversation,
        app.container.sessions,
        app.container.event_publisher,
        conversation_repository=app.container.conversation_repository,
    )
    await recovered.start()
    try:
        turn = await app.container.external_channel_repository.get_turn(
            admitted.turn.channel_turn_id
        )
        route = await app.repository.get_route(app.route.route_id)
        assert (
            turn is not None
            and turn.status is ChannelTurnStatus.CANCELLED
            and turn.delivery_id is None
        )
        assert route is not None and not route.enabled
        assert recovered.active_count == 0 and not app.provider.requests
    finally:
        await recovered.stop()


async def test_actual_experience_reset_pauses_before_data_change_and_keeps_plan_receipt_facts(
    app: App,
) -> None:
    await app.enable()
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    app.container.conversation.set_before_scope_reset_hook(app.service.before_scope_reset)
    await asyncio.wait_for(app.container.conversation.reset(receipt.session_id), 3)
    route = await app.repository.get_route(app.route.route_id)
    assert (
        route is not None
        and not route.enabled
        and route.pause_reason is ChannelGroupPauseReason.SCENE_RESET
    )
    page = await app.service.list_turns(app.connection_id, app.route.route_id)
    assert len(page.items) == 1 and page.items[0].turn.status is ChannelTurnStatus.COMPLETED
    assert not page.items[0].cancelable and not page.items[0].provider_receipt_present


async def test_group_only_notice_invalidates_inflight_observation_without_relying_on_ready(
    app: App,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def reader(_connection: UUID, _group: str) -> tuple[str, tuple[str, ...]]:
        entered.set()
        await release.wait()
        return "900", ("111", "222")

    app.service.set_audience_reader(reader)
    incoming = asyncio.create_task(
        app.service.observe_audience(app.connection_id, ChannelGroupAudienceRequest(group_id="500"))
    )
    await asyncio.wait_for(entered.wait(), 2)
    app.service.fence_connection(app.connection_id, "membership_changed", "500")
    release.set()
    with pytest.raises(ChannelConflictError):
        await incoming
    assert app.ready


async def test_reconnect_requires_new_observation_even_when_previous_one_is_unexpired(
    app: App,
) -> None:
    await app.enable()
    previous = app.route.observation_id
    await app.service.pause_connection(app.connection_id, ChannelGroupPauseReason.RECONNECT)
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    app.route = app.route.model_copy(update={"revision": route.revision})
    with pytest.raises(ChannelPolicyError, match="new audience observation"):
        await app.service.update_route(
            app.connection_id,
            app.route.route_id,
            ChannelGroupRouteUpdate(
                enabled=True,
                expected_revision=route.revision,
                observation_id=previous,
                speaker_sender_keys=["111", "222"],
            ),
        )
    await app.enable()
    assert app.route.enabled and app.route.observation_id != previous


def _fill_group_epochs(app: App, *, start: int = 1000, count: int = 128) -> None:
    for group in range(start, start + count):
        app.service.fence_connection(app.connection_id, "membership_changed", str(group))


async def test_unknown_group_cap_pauses_real_route_then_requires_explicit_resume(
    app: App,
) -> None:
    await app.enable()
    app.provider.hold = asyncio.Event()
    receipt = await app.ingest()
    await asyncio.wait_for(app.provider.started.get(), 2)
    _fill_group_epochs(app)
    assert len(app.service._group_epochs) == len(app.service._blocked_groups) == 128
    app.service.fence_connection(app.connection_id, "membership_changed", "9000")
    assert (app.connection_id, "9000") not in app.service._group_epochs
    assert app.connection_id in app.service._blocked_connections
    assert app.service._workflows[receipt.channel_turn_id].revoked
    await app.service.pause_connection(
        app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED, "9000"
    )
    route = await app.repository.get_route(app.route.route_id)
    assert (
        route is not None
        and not route.enabled
        and route.pause_reason is ChannelGroupPauseReason.MEMBERSHIP_CHANGED
    )
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert (
        turn is not None and turn.status is ChannelTurnStatus.CANCELLED and turn.delivery_id is None
    )
    assert app.connection_id not in app.service._blocked_connections
    app.route = app.route.model_copy(update={"revision": route.revision})
    await app.enable()
    app.provider.hold.set()
    fresh = await app.ingest("2")
    await app.join(fresh.channel_turn_id)
    latest = await app.container.external_channel_repository.get_turn(fresh.channel_turn_id)
    assert latest is not None and latest.status is ChannelTurnStatus.COMPLETED
    # Further unknown notices cannot create metadata entries or silently resume any route.
    for group in range(9001, 9011):
        await app.service.pause_connection(
            app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED, str(group)
        )
    assert len(app.service._group_epochs) == 128 and not app.service._blocked_groups
    assert app.connection_id not in app.service._blocked_connections
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None and not route.enabled


async def test_existing_epoch_at_capacity_keeps_group_specific_cancellation(app: App) -> None:
    await app.enable()
    await app.service.pause_connection(
        app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED, "500"
    )
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    app.route = app.route.model_copy(update={"revision": route.revision})
    await app.enable()
    observation = await app.service.observe_audience(
        app.connection_id, ChannelGroupAudienceRequest(group_id="600")
    )
    other = await app.service.create_route(
        app.connection_id,
        ChannelGroupRouteCreate(
            observation_id=observation.observation_id,
            display_name="other actual group",
            speaker_sender_keys=["111", "222"],
        ),
    )
    other = await app.service.update_route(
        app.connection_id,
        other.route_id,
        ChannelGroupRouteUpdate(
            enabled=True,
            expected_revision=other.revision,
            observation_id=observation.observation_id,
            speaker_sender_keys=["111", "222"],
        ),
    )
    _fill_group_epochs(app, count=127)
    app.provider.hold = asyncio.Event()
    first = await app.ingest()
    second = await app.service.ingest_group(
        replace(app.message("2", text="other"), group_id="600"), access_token=TOKEN
    )
    for _ in range(2):
        await asyncio.wait_for(app.provider.started.get(), 2)
    connection_epoch = app.service._connection_epochs.get(app.connection_id, 0)
    app.service.fence_connection(app.connection_id, "membership_changed", "500")
    assert app.service._connection_epochs.get(app.connection_id, 0) == connection_epoch
    assert app.connection_id not in app.service._blocked_connections
    assert not app.service._workflows[second.channel_turn_id].revoked
    await app.service.pause_connection(
        app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED, "500"
    )
    other_fresh = await app.repository.get_route(other.route_id)
    assert other_fresh is not None and other_fresh.enabled
    app.provider.hold.set()
    await app.join(second.channel_turn_id)
    old = await app.container.external_channel_repository.get_turn(first.channel_turn_id)
    latest = await app.container.external_channel_repository.get_turn(second.channel_turn_id)
    assert old is not None and old.status is ChannelTurnStatus.CANCELLED and old.delivery_id is None
    assert latest is not None and latest.status is ChannelTurnStatus.COMPLETED
    assert len(app.service._group_epochs) == 128


async def test_overflow_epoch_prevents_late_admission_after_durable_pause_and_resume(
    app: App,
) -> None:
    await app.enable()
    entered, caught, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = app.container.external_channels.authenticate_group_transport

    async def authenticate(connection_id: UUID, token: str):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            caught.set()
            task = asyncio.current_task()
            assert task is not None
            task.uncancel()
            await release.wait()
        return await original(connection_id, token)

    app.service.set_authenticator(authenticate)
    incoming = asyncio.create_task(app.ingest())
    await asyncio.wait_for(entered.wait(), 2)
    _fill_group_epochs(app)
    app.service.fence_connection(app.connection_id, "membership_changed", "9000")
    await asyncio.wait_for(caught.wait(), 2)
    await app.service.pause_connection(
        app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED, "9000"
    )
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    app.route = app.route.model_copy(update={"revision": route.revision})
    await app.enable()
    assert app.connection_id not in app.service._blocked_connections
    release.set()
    with pytest.raises(ChannelPolicyError):
        await incoming
    assert not app.provider.requests and app.service.active_count == 0
    assert not (await app.service.list_turns(app.connection_id, app.route.route_id)).items


async def test_overflow_epoch_prevents_late_observation_after_pause_and_resume(app: App) -> None:
    await app.enable()
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_reader(_connection: UUID, _group: str) -> tuple[str, tuple[str, ...]]:
        entered.set()
        await release.wait()
        return "900", ("111", "222")

    app.service.set_audience_reader(held_reader)
    incoming = asyncio.create_task(
        app.service.observe_audience(app.connection_id, ChannelGroupAudienceRequest(group_id="500"))
    )
    await asyncio.wait_for(entered.wait(), 2)
    _fill_group_epochs(app)
    await app.service.pause_connection(
        app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED, "9000"
    )
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    app.route = app.route.model_copy(update={"revision": route.revision})

    async def fresh_reader(_connection: UUID, _group: str) -> tuple[str, tuple[str, ...]]:
        return "900", ("111", "222")

    app.service.set_audience_reader(fresh_reader)
    await app.enable()
    release.set()
    with pytest.raises(ChannelConflictError):
        await incoming
    assert app.route.enabled and app.connection_id not in app.service._blocked_connections


async def test_overflow_epoch_prevents_late_delivery_authorization_after_pause_and_resume(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.enable()
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is not None
    plan = await app.container.external_channel_repository.get_delivery_plan(turn.delivery_id)
    assert plan is not None
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.repository.authorize_group_turn

    async def authorize(lineage: ChannelGroupRouteLineage) -> ChannelGroupAuthorization:
        authorized = await original(lineage)
        assert authorized.allowed
        entered.set()
        await release.wait()
        return authorized

    monkeypatch.setattr(app.repository, "authorize_group_turn", authorize)
    pending = asyncio.create_task(app.service.authorize_delivery(plan))
    await asyncio.wait_for(entered.wait(), 2)
    _fill_group_epochs(app)
    await app.service.pause_connection(
        app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED, "9000"
    )
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None
    app.route = app.route.model_copy(update={"revision": route.revision})
    await app.enable()
    release.set()
    assert not await pending
    assert app.connection_id not in app.service._blocked_connections


async def test_unbound_history_keeps_confirmed_receipt_but_all_mutations_deny(app: App) -> None:
    await app.enable()
    receipt = await app.ingest()
    await app.join(receipt.channel_turn_id)
    turn = await app.container.external_channel_repository.get_turn(receipt.channel_turn_id)
    assert turn is not None and turn.delivery_id is not None
    claim = await app.container.external_channel_repository.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(delivery_id=turn.delivery_id, lease_id=uuid4()),
        claimed_at=datetime.now(UTC),
    )
    assert claim is not None and claim.part is not None and claim.part.lease_id is not None
    await app.container.external_channel_repository.acknowledge_delivery_part(
        ChannelDeliveryPartAcknowledgement(
            delivery_id=turn.delivery_id,
            part_id=claim.part.part_id,
            lease_id=claim.part.lease_id,
            status=ChannelDeliveryPartStatus.DELIVERED,
            provider_message_id="fixture-confirmed-unbind-receipt",
            acknowledged_at=datetime.now(UTC),
        ),
        updated_at=datetime.now(UTC),
    )
    await app.service.pause_connection(
        app.connection_id, ChannelGroupPauseReason.CONNECTION_DELETED
    )
    assert await app.container.external_channel_repository.soft_delete_connection(
        app.connection_id, deleted_at=datetime.now(UTC)
    )
    assert await app.container.external_channel_repository.get_connection(app.connection_id) is None
    routes = await app.service.list_routes(app.connection_id)
    links = await app.service.list_links(app.connection_id)
    history = await app.service.list_turns(app.connection_id, app.route.route_id)
    assert len(routes.items) == 1 and len(links.items) == 2 and len(history.items) == 1
    assert history.items[0].provider_receipt_present and not history.items[0].cancelable
    assert history.items[0].turn.status is ChannelTurnStatus.COMPLETED
    with pytest.raises(ChannelNotFoundError):
        await app.service.observe_audience(
            app.connection_id, ChannelGroupAudienceRequest(group_id="500")
        )
    with pytest.raises(ChannelNotFoundError):
        await app.service.update_route(
            app.connection_id,
            app.route.route_id,
            ChannelGroupRouteUpdate(
                enabled=False, expected_revision=routes.items[0].revision, speaker_sender_keys=[]
            ),
        )
    with pytest.raises(ChannelNotFoundError):
        await app.service.update_link(
            app.connection_id,
            links.items[0].link_id,
            ChannelParticipantLinkUpdate(enabled=True, expected_revision=links.items[0].revision),
        )
    with pytest.raises(ChannelNotFoundError):
        await app.service.cancel_turn(
            app.connection_id,
            app.route.route_id,
            receipt.channel_turn_id,
            ChannelGroupTurnCancelRequest(expected_revision=history.items[0].turn.revision),
        )
    with pytest.raises(ChannelNotFoundError):
        await app.ingest("2")
    assert len(app.provider.requests) == 1


async def test_constructor_safe_stop_uses_no_unopened_database(runtime_settings: Settings) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.channel_groups.stop()
    await container.channel_groups.stop()
    assert not container.channel_groups._started and container.channel_groups.active_count == 0


@pytest.mark.parametrize("later_notice", [False, True])
async def test_wide_pause_clears_only_captured_group_blocks_and_keeps_epochs(
    app: App, monkeypatch: pytest.MonkeyPatch, later_notice: bool
) -> None:
    await app.enable()
    app.service.fence_connection(app.connection_id, "membership_changed", "500")
    _fill_group_epochs(app, count=127)
    assert len(app.service._group_epochs) == 128
    captured_group_epoch = app.service._group_epochs[(app.connection_id, "500")]
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.repository.pause_routes

    async def pause(
        *,
        reason: ChannelGroupPauseReason,
        updated_at: datetime,
        connection_id: UUID | None = None,
        group_id: str | None = None,
        scene_id: str | None = None,
        link_id: UUID | None = None,
    ) -> tuple[ChannelGroupTransition, ...]:
        result = await original(
            reason=reason,
            updated_at=updated_at,
            connection_id=connection_id,
            group_id=group_id,
            scene_id=scene_id,
            link_id=link_id,
        )
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(app.repository, "pause_routes", pause)
    pending = asyncio.create_task(
        app.service.pause_connection(
            app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED, "9000"
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    if later_notice:
        app.service.fence_connection(app.connection_id, "membership_changed", "500")
    release.set()
    await pending
    assert app.connection_id not in app.service._blocked_connections
    assert len(app.service._group_epochs) == 128
    assert app.service._group_epochs[(app.connection_id, "500")] == captured_group_epoch + int(
        later_notice
    )
    if later_notice:
        assert (app.connection_id, "500") in app.service._blocked_groups
        with pytest.raises(ChannelPolicyError):
            await app.service.observe_audience(
                app.connection_id, ChannelGroupAudienceRequest(group_id="500")
            )
        await app.service.pause_connection(
            app.connection_id, ChannelGroupPauseReason.MEMBERSHIP_CHANGED, "500"
        )
    assert (app.connection_id, "500") not in app.service._blocked_groups
    route = await app.repository.get_route(app.route.route_id)
    assert route is not None and not route.enabled
    app.route = app.route.model_copy(update={"revision": route.revision})
    await app.enable()
    assert app.route.enabled
