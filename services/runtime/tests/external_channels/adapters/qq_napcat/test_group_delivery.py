"""Fixed group delivery uses local OneBot peers and a durable SQLite send journal.

Group records are typed overlays: migration 40 and route admission belong to the
application slice. Cursor persistence and reopening use the actual repository.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelDeliveryPartKind,
    ChannelDeliveryStatus,
    ChannelGroupDeliveryTarget,
    ChannelImageDeliveryPartPayload,
    ChannelMessageKind,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatClient
from chatwaifu_runtime.external_channels.adapters.qq_napcat.delivery import NapCatDelivery
from chatwaifu_runtime.external_channels.models import (
    ChannelConnectionRecord,
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelTurnRecord,
)
from chatwaifu_runtime.external_channels.scheduler import DeliveryPartOutcome
from chatwaifu_runtime.persistence.sqlite_external_channels import SQLiteExternalChannelRepository
from websockets.asyncio.server import ServerConnection

from services.runtime.tests.external_channels.adapters.qq_napcat.test_delivery import (
    OWNER,
    State,
    audio_payload,
    setup,
)
from services.runtime.tests.external_channels.adapters.qq_napcat.test_group_client import (
    ACCOUNT,
    GROUP,
    connected,
    membership_notice,
    reply,
    request,
)

SCENE = "group_scene_fixture"
RECEIPT = "-90002"
Authorization = Callable[[ChannelDeliveryPlanRecord], Awaitable[bool]]


class GroupRepository(SQLiteExternalChannelRepository):
    def __init__(
        self,
        state: State,
        connection: ChannelConnectionRecord | None,
        plan: ChannelDeliveryPlanRecord | None,
        turn: ChannelTurnRecord | None,
    ) -> None:
        super().__init__(state.database)
        self.connection = connection
        self.plan = plan
        self.turn = turn
        self.quoted_reads = 0
        self.on_turn_read: Callable[[], Awaitable[None]] | None = None

    async def get_connection(self, connection_id: UUID) -> ChannelConnectionRecord | None:
        assert connection_id == self._fixture_connection_id()
        return self.connection

    def _fixture_connection_id(self) -> UUID:
        assert self.plan is not None
        return self.plan.connection_id

    async def get_delivery_plan(self, delivery_id: UUID) -> ChannelDeliveryPlanRecord | None:
        assert self.plan is None or delivery_id == self.plan.delivery_id
        return self.plan

    async def get_turn(self, channel_turn_id: UUID) -> ChannelTurnRecord | None:
        assert self.turn is None or channel_turn_id == self.turn.channel_turn_id
        if self.on_turn_read is not None:
            await self.on_turn_read()
        return self.turn

    async def quoted_reply_target(self, channel_turn_id: UUID) -> str | None:
        self.quoted_reads += 1
        return "90001"


@dataclass
class GroupState:
    state: State
    repository: GroupRepository
    plan: ChannelDeliveryPlanRecord
    part: ChannelDeliveryPartRecord

    def executor(
        self, client: NapCatClient, authorize: Authorization | None = None
    ) -> NapCatDelivery:
        return NapCatDelivery(
            self.repository,
            client,
            self.state.connection_id,
            OWNER,
            self.state.audio_root,
            group_authorization=authorize,
        )

    async def journal(self) -> dict[str, str]:
        cursor = await self.repository.get_adapter_cursor(self.state.connection_id)
        return json.loads(cursor) if cursor else {}

    async def reopen(self) -> None:
        previous = self.repository
        await self.state.reopen()
        self.repository = GroupRepository(
            self.state, previous.connection, previous.plan, previous.turn
        )


@pytest.fixture
async def group_state(tmp_path: Path) -> AsyncGenerator[GroupState]:
    state = await setup(tmp_path)
    try:
        assert state.plan.channel_turn_id is not None
        source = await state.repository.get_turn(state.plan.channel_turn_id)
        connection = await state.repository.get_connection(state.connection_id)
        assert source is not None and connection is not None
        route_id = uuid4()
        target = ChannelGroupDeliveryTarget(
            connection_id=state.connection_id,
            account_key=ACCOUNT,
            group_id=GROUP,
            route_id=route_id,
            route_revision=7,
            channel_turn_id=source.channel_turn_id,
            scene_id=SCENE,
            audience_fingerprint="a" * 64,
        )
        turn = replace(
            source,
            chat_type=ChannelChatType.GROUP,
            conversation_key=f"group:{GROUP}",
            principal_scope=f"scene:{SCENE}",
            group_route_id=route_id,
            group_route_revision=7,
            group_lineage_version=1,
        )
        plan = replace(state.plan, group_target=target)
        current = replace(
            plan,
            delivery=replace(plan.delivery, status=ChannelDeliveryStatus.SENDING),
            parts=(state.part,),
        )
        yield GroupState(state, GroupRepository(state, connection, current, turn), plan, state.part)
    finally:
        await state.database.close()


async def allowed(plan: ChannelDeliveryPlanRecord) -> bool:
    assert plan.group_target is not None
    return True


async def quiet_peer(socket: ServerConnection) -> None:
    await socket.wait_closed()


async def test_group_text_goes_only_to_fixed_group_without_private_reply(
    group_state: GroupState,
) -> None:
    actions: list[JsonObject] = []
    checks: list[ChannelDeliveryPlanRecord] = []

    async def authorize(plan: ChannelDeliveryPlanRecord) -> bool:
        checks.append(plan)
        return True

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(login)
        await reply(socket, login, {"user_id": ACCOUNT})
        outgoing = await request(socket)
        actions.append(outgoing)
        assert outgoing["params"] == {
            "group_id": GROUP,
            "message": [{"type": "text", "data": {"text": "晚安"}}],
        }
        await reply(socket, outgoing, {"message_id": RECEIPT})
        await socket.wait_closed()

    async with connected(peer) as client:
        result = await asyncio.wait_for(
            group_state.executor(client, authorize).execute_part(
                group_state.plan, group_state.part
            ),
            2,
        )
        assert result.outcome is DeliveryPartOutcome.DELIVERED
        assert result.provider_message_id == RECEIPT
        assert client._pending == {}
    assert [item["action"] for item in actions] == ["get_login_info", "send_group_msg"]
    assert checks == [group_state.plan] * 4
    assert group_state.repository.quoted_reads == 0
    assert await group_state.journal() == {group_state.part.provider_client_id: RECEIPT}


@pytest.mark.parametrize("callback", ["absent", "denied"])
async def test_group_requires_explicit_host_authorization(
    group_state: GroupState, callback: str
) -> None:
    async def denied(plan: ChannelDeliveryPlanRecord) -> bool:
        return False

    async with connected(quiet_peer) as client:
        result = await asyncio.wait_for(
            group_state.executor(client, denied if callback == "denied" else None).execute_part(
                group_state.plan, group_state.part
            ),
            2,
        )
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
        assert client._pending == {}
    assert await group_state.journal() == {}


@pytest.mark.parametrize(
    "defect",
    [
        "disabled",
        "deleted",
        "connection_account",
        "connection_id",
        "wrong_source",
        "source_cancelled",
        "source_audio",
        "wrong_turn_account",
        "wrong_group",
        "wrong_scene",
        "wrong_route",
        "wrong_revision",
        "legacy_lineage",
        "target_revision",
        "target_fingerprint",
        "cancelled",
        "terminal",
        "part_id",
        "part_parent",
        "current_parent",
        "provider_key",
        "payload",
        "ordinal",
        "optional",
        "expired",
        "lease",
        "multi_part",
        "outbound",
        "audio",
        "image",
    ],
)
async def test_invalid_group_metadata_part_or_source_never_becomes_private_send(
    group_state: GroupState, defect: str
) -> None:
    repo, plan, part = group_state.repository, group_state.plan, group_state.part
    assert repo.connection is not None and repo.turn is not None and repo.plan is not None
    assert plan.group_target is not None
    if defect == "disabled":
        repo.connection = replace(
            repo.connection,
            configuration=repo.connection.configuration.model_copy(update={"enabled": False}),
        )
    elif defect == "deleted":
        repo.connection = replace(repo.connection, deleted_at=datetime.now(UTC))
    elif defect == "connection_account":
        repo.connection = replace(
            repo.connection,
            configuration=repo.connection.configuration.model_copy(update={"account_key": "10009"}),
        )
    elif defect == "connection_id":
        plan = replace(plan, delivery=replace(plan.delivery, connection_id=uuid4()))
    elif defect == "wrong_source":
        repo.turn = replace(repo.turn, chat_type=ChannelChatType.DIRECT)
    elif defect == "source_cancelled":
        repo.turn = replace(repo.turn, status=ChannelTurnStatus.CANCELLED)
    elif defect == "source_audio":
        repo.turn = replace(repo.turn, input_kind=ChannelMessageKind.AUDIO)
    elif defect == "wrong_turn_account":
        repo.turn = replace(repo.turn, account_key="10009")
    elif defect == "wrong_group":
        repo.turn = replace(repo.turn, conversation_key="group:20009")
    elif defect == "wrong_scene":
        repo.turn = replace(repo.turn, principal_scope="local")
    elif defect == "wrong_route":
        repo.turn = replace(repo.turn, group_route_id=uuid4())
    elif defect == "wrong_revision":
        repo.turn = replace(repo.turn, group_route_revision=8)
    elif defect == "legacy_lineage":
        repo.turn = replace(repo.turn, group_lineage_version=0)
    elif defect in {"target_revision", "target_fingerprint"}:
        update = (
            {"route_revision": 8}
            if defect == "target_revision"
            else {"audience_fingerprint": "b" * 64}
        )
        repo.plan = replace(repo.plan, group_target=plan.group_target.model_copy(update=update))
    elif defect == "cancelled":
        repo.plan = replace(
            repo.plan, delivery=replace(repo.plan.delivery, cancel_requested_at=datetime.now(UTC))
        )
    elif defect == "terminal":
        repo.plan = replace(
            repo.plan, delivery=replace(repo.plan.delivery, status=ChannelDeliveryStatus.CANCELLED)
        )
    elif defect == "part_id":
        part = replace(part, part_id=uuid4())
    elif defect == "part_parent":
        part = replace(part, delivery_id=uuid4())
    elif defect == "current_parent":
        repo.plan = replace(repo.plan, parts=(replace(repo.plan.parts[0], delivery_id=uuid4()),))
    elif defect == "provider_key":
        part = replace(part, provider_client_id="forged-provider-key")
    elif defect == "payload":
        part = replace(part, payload=ChannelTextDeliveryPartPayload(text="替换内容"))
    elif defect == "ordinal":
        part = replace(part, ordinal=1)
    elif defect == "optional":
        part = replace(part, required=False)
    elif defect == "expired":
        part = replace(part, lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        repo.plan = replace(repo.plan, parts=(part,))
    elif defect == "lease":
        part = replace(part, lease_id=uuid4())
    elif defect == "multi_part":
        plan = replace(plan, parts=(part, replace(part, ordinal=1)))
    elif defect == "outbound":
        plan = replace(
            plan, delivery=replace(plan.delivery, channel_turn_id=None, outbound_intent_id=uuid4())
        )
    elif defect == "audio":
        payload, _ = audio_payload()
        part = replace(part, kind=ChannelDeliveryPartKind.AUDIO, payload=payload)
        plan = replace(plan, parts=(part,))
        repo.plan = replace(repo.plan, parts=(part,))
    elif defect == "image":
        payload = ChannelImageDeliveryPartPayload(
            sticker_id="fixture", sha256="a" * 64, mime_type="image/png"
        )
        part = replace(part, kind=ChannelDeliveryPartKind.IMAGE, payload=payload)
        plan = replace(plan, parts=(part,))
        repo.plan = replace(repo.plan, parts=(part,))
    async with connected(quiet_peer) as client:
        result = await asyncio.wait_for(
            group_state.executor(client, allowed).execute_part(plan, part), 2
        )
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
        assert client._pending == {}
    assert await group_state.journal() == {}
    assert repo.quoted_reads == 0


async def test_legacy_group_without_target_cannot_fall_back_to_owner(
    group_state: GroupState,
) -> None:
    async with connected(quiet_peer) as client:
        plan = replace(group_state.plan, group_target=None)
        result = await asyncio.wait_for(
            group_state.executor(client, allowed).execute_part(plan, group_state.part), 2
        )
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
    assert await group_state.journal() == {}


@pytest.mark.parametrize(
    "change", ["cancel", "revision", "disabled", "account", "rebind", "notice"]
)
async def test_changed_authority_during_account_preflight_has_zero_group_rpc(
    group_state: GroupState, change: str
) -> None:
    actions: list[str] = []
    active: NapCatClient | None = None

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        repo = group_state.repository
        assert repo.plan is not None and repo.connection is not None
        if change == "cancel":
            repo.plan = replace(
                repo.plan,
                delivery=replace(repo.plan.delivery, cancel_requested_at=datetime.now(UTC)),
            )
        elif change == "revision":
            assert repo.plan.group_target is not None
            repo.plan = replace(
                repo.plan,
                group_target=repo.plan.group_target.model_copy(update={"route_revision": 8}),
            )
        elif change == "disabled":
            repo.connection = replace(
                repo.connection,
                configuration=repo.connection.configuration.model_copy(update={"enabled": False}),
            )
        elif change == "rebind":
            assert active is not None
            active.bind_account("10009")
            active.bind_account(ACCOUNT)
        elif change == "notice":
            await socket.send(json.dumps(membership_notice()))
        await reply(socket, login, {"user_id": "10009" if change == "account" else ACCOUNT})
        await socket.wait_closed()

    async with connected(peer) as client:
        active = client
        result = await asyncio.wait_for(
            group_state.executor(client, allowed).execute_part(group_state.plan, group_state.part),
            2,
        )
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
        assert client._pending == {}
    assert actions == ["get_login_info"]
    assert await group_state.journal() == {}


async def test_notice_pending_before_execution_prevents_even_login(
    group_state: GroupState,
) -> None:
    notice_sent = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        await socket.send(json.dumps(membership_notice()))
        # A correlated harmless RPC provides a reader-order barrier, without
        # consuming the notice which must still fence group transmission.
        barrier = await request(socket)
        assert barrier["action"] == "fixture_barrier"
        await reply(socket, barrier, {})
        notice_sent.set()
        await socket.wait_closed()

    async with connected(peer) as client:
        await asyncio.wait_for(client.call("fixture_barrier", {}), 2)
        await asyncio.wait_for(notice_sent.wait(), 2)
        assert client._pending_group_notices == 1
        result = await asyncio.wait_for(
            group_state.executor(client, allowed).execute_part(group_state.plan, group_state.part),
            2,
        )
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
        assert client._pending == {}
    assert await group_state.journal() == {}


@pytest.mark.parametrize("change", ["notice_consumed", "cancel", "revision", "guard_denied"])
async def test_change_inside_last_guard_is_rechecked_before_group_rpc(
    group_state: GroupState, change: str
) -> None:
    guard_entered, notice_done = asyncio.Event(), asyncio.Event()
    actions: list[str] = []
    calls = 0
    active: NapCatClient | None = None

    async def authorize(plan: ChannelDeliveryPlanRecord) -> bool:
        nonlocal calls
        calls += 1
        if calls == 3:
            guard_entered.set()
            if change == "notice_consumed":
                assert active is not None
                notice = await asyncio.wait_for(active.event(), 2)
                assert notice["notice_type"] == "group_increase"
                notice_done.set()
            elif change == "cancel":
                assert group_state.repository.plan is not None
                current = group_state.repository.plan
                group_state.repository.plan = replace(
                    current,
                    delivery=replace(current.delivery, cancel_requested_at=datetime.now(UTC)),
                )
            elif change == "revision":
                assert group_state.repository.plan is not None and plan.group_target is not None
                group_state.repository.plan = replace(
                    group_state.repository.plan,
                    group_target=plan.group_target.model_copy(update={"route_revision": 8}),
                )
            elif change == "guard_denied":
                return False
        return True

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        await reply(socket, login, {"user_id": ACCOUNT})
        await asyncio.wait_for(guard_entered.wait(), 2)
        if change == "notice_consumed":
            await socket.send(json.dumps(membership_notice()))
            await asyncio.wait_for(notice_done.wait(), 2)
        await socket.wait_closed()

    async with connected(peer) as client:
        active = client
        result = await asyncio.wait_for(
            group_state.executor(client, authorize).execute_part(
                group_state.plan, group_state.part
            ),
            2,
        )
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
        assert client._pending == {} and client._pending_group_notices == 0
    assert calls == (4 if change == "notice_consumed" else 3)
    assert actions == ["get_login_info"]
    assert await group_state.journal() == {}


async def test_cancelled_blocked_login_retains_unknown_across_reopen_no_resend(
    group_state: GroupState,
) -> None:
    entered, cancelled = asyncio.Event(), asyncio.Event()
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        entered.set()
        await asyncio.wait_for(cancelled.wait(), 2)
        await reply(socket, login, {"user_id": ACCOUNT})
        # Late RPC response cannot initiate a send after the cancelled waiter.
        await socket.wait_closed()

    async with connected(peer) as client:
        task = asyncio.create_task(
            group_state.executor(client, allowed).execute_part(group_state.plan, group_state.part)
        )
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cancelled.set()
        assert client._pending == {}
        assert await group_state.journal() == {group_state.part.provider_client_id: "unknown"}
        await group_state.reopen()
        result = await asyncio.wait_for(
            group_state.executor(client, allowed).execute_part(group_state.plan, group_state.part),
            2,
        )
        assert result.error is not None and result.error.code == "qq_delivery_unknown"
    assert actions == ["get_login_info"]


@pytest.mark.parametrize("send_receipt", [False, True])
async def test_success_or_unknown_survives_reopen_and_revocation_without_login(
    group_state: GroupState, send_receipt: bool
) -> None:
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        await reply(socket, login, {"user_id": ACCOUNT})
        outgoing = await request(socket)
        actions.append(str(outgoing["action"]))
        if send_receipt:
            await reply(socket, outgoing, {"message_id": RECEIPT})
            await socket.wait_closed()
        else:
            await socket.close()

    async with connected(peer) as client:
        result = await asyncio.wait_for(
            group_state.executor(client, allowed).execute_part(group_state.plan, group_state.part),
            2,
        )
        assert result.outcome is (
            DeliveryPartOutcome.DELIVERED if send_receipt else DeliveryPartOutcome.FATAL_ERROR
        )
        await group_state.reopen()
        # No connection, target authority or usable transport remains. Provider
        # facts precede all new-send permissions and never create another RPC.
        group_state.repository.connection = None
        result = await asyncio.wait_for(
            group_state.executor(client).execute_part(group_state.plan, group_state.part), 2
        )
        if send_receipt:
            assert result.outcome is DeliveryPartOutcome.DELIVERED
            assert result.provider_message_id == RECEIPT
        else:
            assert result.error is not None and result.error.code == "qq_delivery_unknown"
        assert client._pending == {}
    assert actions == ["get_login_info", "send_group_msg"]
    assert await group_state.journal() == {
        group_state.part.provider_client_id: RECEIPT if send_receipt else "unknown"
    }


async def test_group_notice_does_not_block_existing_private_delivery(tmp_path: Path) -> None:
    state = await setup(tmp_path)
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        login = await request(socket)
        actions.append(str(login["action"]))
        await socket.send(json.dumps(membership_notice()))
        await reply(socket, login, {"user_id": ACCOUNT})
        outgoing = await request(socket)
        actions.append(str(outgoing["action"]))
        assert outgoing["params"] == {
            "user_id": int(OWNER),
            "message": [{"type": "text", "data": {"text": "晚安"}}],
        }
        await reply(socket, outgoing, {"message_id": RECEIPT})
        await socket.wait_closed()

    try:
        async with connected(peer) as client:
            result = await asyncio.wait_for(
                state.executor(client).execute_part(state.plan, state.part), 2
            )
            assert result.outcome is DeliveryPartOutcome.DELIVERED
            assert client._pending_group_notices == 1
        assert actions == ["get_login_info", "send_private_msg"]
    finally:
        await state.database.close()


@pytest.mark.parametrize("existing_count", [128, 256])
async def test_group_send_never_collects_unresolved_journal_and_obeys_hard_cap(
    group_state: GroupState, existing_count: int
) -> None:
    previous = {f"unresolved-{index}": "unknown" for index in range(existing_count)}
    await group_state.repository.set_adapter_cursor(
        group_state.state.connection_id,
        cursor=json.dumps(previous),
        updated_at=datetime.now(UTC),
    )
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        if existing_count == 256:
            await socket.wait_closed()
            return
        login = await request(socket)
        actions.append(str(login["action"]))
        await reply(socket, login, {"user_id": ACCOUNT})
        outgoing = await request(socket)
        actions.append(str(outgoing["action"]))
        await reply(socket, outgoing, {"message_id": RECEIPT})
        await socket.wait_closed()

    async with connected(peer) as client:
        result = await asyncio.wait_for(
            group_state.executor(client, allowed).execute_part(group_state.plan, group_state.part),
            2,
        )
        if existing_count == 256:
            assert result.error is not None and result.error.code == "qq_send_journal_full"
        else:
            assert result.outcome is DeliveryPartOutcome.DELIVERED
    journal = await group_state.journal()
    assert all(journal.get(key) == value for key, value in previous.items())
    assert len(journal) == existing_count + (existing_count < 256)
    assert actions == ([] if existing_count == 256 else ["get_login_info", "send_group_msg"])


@pytest.mark.parametrize("bound_account", ["10009", "0", "010001", ""])
async def test_initial_wrong_bound_account_has_zero_group_or_login_rpc(
    group_state: GroupState, bound_account: str
) -> None:
    async with connected(quiet_peer) as client:
        client.bind_account(bound_account)
        assert client.bound_account == bound_account
        result = await asyncio.wait_for(
            group_state.executor(client, allowed).execute_part(group_state.plan, group_state.part),
            2,
        )
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
        assert client._pending == {}
    assert await group_state.journal() == {}


async def test_readonly_group_readiness_tracks_binding_pending_notice_and_disconnect() -> None:
    disconnect = asyncio.Event()

    async def peer(socket: ServerConnection) -> None:
        first = await request(socket)
        assert first["action"] == "fixture_barrier"
        await socket.send(json.dumps(membership_notice()))
        await reply(socket, first, {})
        await asyncio.wait_for(disconnect.wait(), 2)
        await socket.close()

    unopened = NapCatClient("ws://127.0.0.1:1/onebot", "fixture-token")
    assert unopened.bound_account is None and not unopened.group_dispatch_ready
    unopened.bind_account(ACCOUNT)
    assert unopened.bound_account == ACCOUNT and not unopened.group_dispatch_ready

    async with connected(peer) as client:
        assert client.bound_account == ACCOUNT and client.group_dispatch_ready
        await asyncio.wait_for(client.call("fixture_barrier", {}), 2)
        assert client._pending_group_notices == 1 and not client.group_dispatch_ready
        notice = await asyncio.wait_for(client.event(), 2)
        assert notice["notice_type"] == "group_increase"
        assert client.group_dispatch_ready
        # Ready means a transport observation only; the host still has to fence
        # its route synchronously before yielding to notice processing.
        client.bind_account("0")
        assert not client.group_dispatch_ready
        client.bind_account(ACCOUNT)
        assert client.group_dispatch_ready
        disconnect.set()
        assert client._reader is not None
        await asyncio.wait_for(client._reader, 2)
        assert not client.group_dispatch_ready
    assert client.bound_account == ACCOUNT and not client.group_dispatch_ready


async def test_group_readiness_is_false_after_membership_queue_overflow() -> None:
    async def peer(socket: ServerConnection) -> None:
        for _ in range(65):
            await socket.send(json.dumps(membership_notice()))
        await socket.wait_closed()

    async with connected(peer) as client:
        assert client._reader is not None
        await asyncio.wait_for(client._reader, 2)
        assert not client.group_dispatch_ready
        assert client._events.qsize() == client._events.maxsize == 64
        assert client._pending_group_notices == 64 and client._pending == {}


async def test_route_revocation_during_post_guard_storage_is_checked_last(
    group_state: GroupState,
) -> None:
    granted = True
    reads = 0
    actions: list[str] = []

    async def on_read() -> None:
        nonlocal reads, granted
        reads += 1
        if reads == 4:
            # The record-query after the post-login host guard can yield to a
            # revocation. Fixed historical source fields remain unchanged.
            granted = False

    async def authorize(plan: ChannelDeliveryPlanRecord) -> bool:
        return granted

    async def peer(socket: ServerConnection) -> None:
        async for raw in socket:
            outgoing: JsonObject = json.loads(raw)
            actions.append(str(outgoing["action"]))
            await reply(
                socket,
                outgoing,
                {"user_id": ACCOUNT}
                if outgoing["action"] == "get_login_info"
                else {"message_id": RECEIPT},
            )

    group_state.repository.on_turn_read = on_read
    async with connected(peer) as client:
        result = await asyncio.wait_for(
            group_state.executor(client, authorize).execute_part(
                group_state.plan, group_state.part
            ),
            2,
        )
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
    assert reads == 4 and not granted
    assert actions == ["get_login_info"]
    assert await group_state.journal() == {}
