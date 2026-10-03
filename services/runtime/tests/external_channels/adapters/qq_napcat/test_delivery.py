"""Durable QQ send fencing at the real SQLite repository boundary."""

import asyncio
import base64
import hashlib
import io
import json
import threading
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.channels import (
    ChannelAudioDeliveryPartPayload,
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelDeliveryPartClaimRequest,
    ChannelDeliveryPartDraft,
    ChannelDeliveryPartsCancelRequest,
    ChannelDeliveryPartStatus,
    ChannelDeliveryStatus,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_runtime.config.settings import StorageConfig
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import (
    NapCatClient,
    NapCatRejected,
    NapCatUncertain,
)
from chatwaifu_runtime.external_channels.adapters.qq_napcat.delivery import NapCatDelivery
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPartRecord,
    ChannelDeliveryPlanRecord,
    ChannelTurnRecord,
    DeliveryTransitionResult,
)
from chatwaifu_runtime.external_channels.scheduler import (
    ChannelDeliveryScheduler,
    DeliveryPartOutcome,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.sqlite_external_channels import SQLiteExternalChannelRepository
from websockets.asyncio.server import ServerConnection, serve

OWNER = "10002"


class RecordingClient(NapCatClient):
    """Provider effects are controlled, while the journal is real and durable."""

    def __init__(self, error: Exception | None = None, *, blocking: bool = False) -> None:
        self.calls: list[tuple[str, list[JsonObject]]] = []
        self.error = error
        self.blocking = blocking
        self.started = asyncio.Event()

    async def send(
        self,
        recipient: str,
        segments: list[JsonObject],
        *,
        before_send: Callable[[], Awaitable[bool]] | None = None,
    ) -> str:
        if before_send is not None and not await before_send():
            raise NapCatRejected("delivery cancelled")
        self.calls.append((recipient, segments))
        self.started.set()
        if self.blocking:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return "-87654321"


@dataclass
class State:
    database: Database
    repository: SQLiteExternalChannelRepository
    connection_id: UUID
    plan: ChannelDeliveryPlanRecord
    part: ChannelDeliveryPartRecord
    audio_root: Path
    path: Path
    storage: StorageConfig

    async def reopen(self) -> None:
        await self.database.close()
        self.database = Database(self.path, self.storage)
        await self.database.open()
        self.repository = SQLiteExternalChannelRepository(self.database)

    def executor(self, client: NapCatClient) -> NapCatDelivery:
        return NapCatDelivery(
            self.repository,
            client,
            self.connection_id,
            OWNER,
            self.audio_root,
        )


def claimed_part(
    claim: ChannelDeliveryPartRecord | DeliveryTransitionResult,
) -> ChannelDeliveryPartRecord:
    part = claim.part if isinstance(claim, DeliveryTransitionResult) else claim
    assert part is not None
    return part


async def setup(
    tmp_path: Path,
    payload: ChannelTextDeliveryPartPayload | ChannelAudioDeliveryPartPayload | None = None,
) -> State:
    path = tmp_path / "qq-delivery.db"
    storage = StorageConfig(database_path=path)
    database = Database(path, storage)
    await database.open()
    repository = SQLiteExternalChannelRepository(database)
    connection_id, session_id, binding_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    await repository.create_connection(
        ChannelConnectionConfiguration(
            connection_id=connection_id,
            provider_id="qq_napcat",
            name="QQ 私聊",
            character_id="ayachi_nene",
            principal_scope="local",
            account_key="10001",
            allowed_sender_keys=[OWNER],
        ),
        access_token_hash="fixture-token-hash",
        created_at=now,
    )
    async with database.transaction() as connection:
        await connection.execute(
            """INSERT INTO sessions(
                session_id, character_id, state, conversation_state,
                revision, next_sequence, created_at, updated_at
            ) VALUES (?, 'ayachi_nene', 'ready', 'idle', 0, 1, ?, ?)""",
            (str(session_id), now.isoformat(), now.isoformat()),
        )
    await repository.create_binding(
        binding_id=binding_id,
        connection_id=connection_id,
        conversation_key=f"direct:{OWNER}",
        sender_key=OWNER,
        session_id=session_id,
        created_at=now,
    )
    turn = ChannelTurnRecord(
        channel_turn_id=uuid4(),
        connection_id=connection_id,
        binding_id=binding_id,
        external_message_id="90001",
        content_sha256=hashlib.sha256(b"hello").hexdigest(),
        account_key="10001",
        conversation_key=f"direct:{OWNER}",
        chat_type=ChannelChatType.DIRECT,
        conversation_label="QQ owner",
        sender_key=OWNER,
        sender_display_name="owner",
        principal_scope="local",
        session_id=session_id,
        turn_id=uuid4(),
        generation_id=uuid4(),
        status=ChannelTurnStatus.ACCEPTED,
        reply_text=None,
        error=None,
        delivery_id=None,
        delivery_status=None,
        revision=0,
        accepted_at=now,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    await repository.create_turn(turn)
    payload = payload or ChannelTextDeliveryPartPayload(text="晚安")
    delivery_id = uuid4()
    try:
        drafts = (ChannelDeliveryPartDraft(ordinal=0, kind=payload.kind, payload=payload),)
        if isinstance(payload, ChannelAudioDeliveryPartPayload):
            await repository.create_delivery_plan(
                turn.channel_turn_id,
                delivery_id=delivery_id,
                parts=drafts,
                created_at=now,
            )
        else:
            await repository.complete_turn(
                turn.channel_turn_id,
                reply_text="晚安",
                delivery_id=delivery_id,
                completed_at=now,
                parts=drafts,
            )
    except BaseException:
        await database.close()
        raise
    plan = await repository.get_delivery_plan(delivery_id)
    assert plan is not None
    claim = await repository.claim_next_delivery_part(
        ChannelDeliveryPartClaimRequest(
            delivery_id=delivery_id, lease_id=uuid4(), lease_seconds=30
        ),
        claimed_at=now,
    )
    assert claim is not None
    part = claimed_part(claim)
    audio_root = tmp_path / "audio"
    audio_root.mkdir()
    return State(database, repository, connection_id, plan, part, audio_root, path, storage)


def audio_payload() -> tuple[ChannelAudioDeliveryPartPayload, bytes]:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 160)
    audio = output.getvalue()
    return ChannelAudioDeliveryPartPayload(
        asset_id=uuid4(),
        sha256=hashlib.sha256(audio).hexdigest(),
        mime_type="audio/wav",
        duration_ms=10,
        text="晚安",
    ), audio


@pytest.mark.asyncio
async def test_provider_success_survives_restart_before_scheduler_ack_without_resend(
    tmp_path: Path,
) -> None:
    state = await setup(tmp_path)
    client = RecordingClient()
    try:
        result = await state.executor(client).execute_part(state.plan, state.part)
        assert result.outcome is DeliveryPartOutcome.DELIVERED
        assert result.provider_message_id == "-87654321"
        assert client.calls == [(OWNER, [{"type": "text", "data": {"text": "晚安"}}])]
        await state.reopen()
        await state.repository.recover_expired_delivery_part_leases(
            as_of=datetime.now(UTC) + timedelta(minutes=2),
        )
        replay = await state.executor(client).execute_part(state.plan, state.part)
        assert replay.outcome is DeliveryPartOutcome.DELIVERED
        assert replay.provider_message_id == result.provider_message_id
        assert len(client.calls) == 1
        journal = json.loads(await state.repository.get_adapter_cursor(state.connection_id))
        assert journal[state.part.provider_client_id] == result.provider_message_id
    finally:
        await state.database.close()


@pytest.mark.asyncio
async def test_unknown_provider_result_survives_restart_and_blocks_resend(tmp_path: Path) -> None:
    state = await setup(tmp_path)
    client = RecordingClient(NapCatUncertain("peer disconnected after send"))
    try:
        first = await state.executor(client).execute_part(state.plan, state.part)
        assert first.outcome is DeliveryPartOutcome.FATAL_ERROR
        assert first.error is not None and first.error.code == "qq_delivery_unknown"
        assert len(client.calls) == 1
        await state.reopen()
        journal = json.loads(await state.repository.get_adapter_cursor(state.connection_id))
        assert journal[state.part.provider_client_id] == "unknown"
        client.error = None
        replay = await state.executor(client).execute_part(state.plan, state.part)
        assert replay.error is not None and replay.error.code == "qq_delivery_unknown"
        assert len(client.calls) == 1
    finally:
        await state.database.close()


@pytest.mark.asyncio
async def test_provider_ack_journal_write_failure_keeps_unknown_fence_and_blocks_resend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = await setup(tmp_path)
    client = RecordingClient()
    original_save = state.repository.set_adapter_cursor
    saves = 0

    async def fail_after_fence(
        connection_id: UUID,
        *,
        cursor: str,
        updated_at: datetime,
    ) -> None:
        nonlocal saves
        saves += 1
        if saves == 2:
            raise OSError("database unavailable after provider returned an ACK")
        await original_save(connection_id, cursor=cursor, updated_at=updated_at)

    monkeypatch.setattr(state.repository, "set_adapter_cursor", fail_after_fence)
    try:
        result = await state.executor(client).execute_part(state.plan, state.part)
        assert result.error is not None and result.error.code == "qq_delivery_unknown"
        assert len(client.calls) == 1
        await state.reopen()
        journal = json.loads(await state.repository.get_adapter_cursor(state.connection_id))
        assert journal[state.part.provider_client_id] == "unknown"
        replay = await state.executor(client).execute_part(state.plan, state.part)
        assert replay.error is not None and replay.error.code == "qq_delivery_unknown"
        assert len(client.calls) == 1
    finally:
        await state.database.close()


@pytest.mark.asyncio
async def test_send_fence_is_committed_before_provider_effect(tmp_path: Path) -> None:
    state = await setup(tmp_path)

    class FenceObservingClient(RecordingClient):
        async def send(
            self,
            recipient: str,
            segments: list[JsonObject],
            *,
            before_send: Callable[[], Awaitable[bool]] | None = None,
        ) -> str:
            journal = json.loads(await state.repository.get_adapter_cursor(state.connection_id))
            assert journal[state.part.provider_client_id] == "unknown"
            return await super().send(recipient, segments, before_send=before_send)

    client = FenceObservingClient()
    try:
        result = await state.executor(client).execute_part(state.plan, state.part)
        assert result.outcome is DeliveryPartOutcome.DELIVERED
        assert len(client.calls) == 1
    finally:
        await state.database.close()


@pytest.mark.asyncio
async def test_rejected_send_removes_unknown_fence_and_preserves_truthful_failure(
    tmp_path: Path,
) -> None:
    state = await setup(tmp_path)
    client = RecordingClient(NapCatRejected("explicit provider rejection"))
    try:
        result = await state.executor(client).execute_part(state.plan, state.part)
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
        assert result.error is not None and result.error.code == "qq_send_rejected"
        assert len(client.calls) == 1
        journal = json.loads(await state.repository.get_adapter_cursor(state.connection_id))
        assert state.part.provider_client_id not in journal
    finally:
        await state.database.close()


@pytest.mark.asyncio
async def test_cancelled_inflight_send_propagates_cancellation_and_retains_durable_fence(
    tmp_path: Path,
) -> None:
    state = await setup(tmp_path)
    client = RecordingClient(blocking=True)
    try:
        task = asyncio.create_task(state.executor(client).execute_part(state.plan, state.part))
        await asyncio.wait_for(client.started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await state.reopen()
        replay = await state.executor(client).execute_part(state.plan, state.part)
        assert replay.error is not None and replay.error.code == "qq_delivery_unknown"
        assert len(client.calls) == 1
    finally:
        await state.database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "disable"])
async def test_cancelled_or_disabled_delivery_does_not_send(tmp_path: Path, stop: str) -> None:
    state = await setup(tmp_path)
    client = RecordingClient()
    try:
        if stop == "cancel":
            await state.repository.cancel_remaining_delivery_parts(
                state.plan.delivery_id,
                ChannelDeliveryPartsCancelRequest(
                    reason="cancelled", requested_at=datetime.now(UTC)
                ),
            )
        else:
            connection = await state.repository.get_connection(state.connection_id)
            assert connection is not None
            await state.repository.update_connection(
                connection.configuration.model_copy(update={"enabled": False}),
                expected_revision=connection.revision,
                access_token_hash=None,
                updated_at=datetime.now(UTC),
            )
        result = await state.executor(client).execute_part(state.plan, state.part)
        assert result.error is not None and result.error.code == "qq_delivery_cancelled"
        assert not client.calls
        assert await state.repository.get_adapter_cursor(state.connection_id) == ""
    finally:
        await state.database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "disable"])
async def test_stopped_delivery_during_account_preflight_prevents_provider_send(
    tmp_path: Path, stop: str
) -> None:
    """The last authorization check must happen after the awaited account RPC."""
    state = await setup(tmp_path)
    login_received = asyncio.Event()
    release_login = asyncio.Event()
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        async for raw in socket:
            received = json.loads(raw)
            action = received["action"]
            actions.append(action)
            if action == "get_login_info":
                login_received.set()
                await asyncio.wait_for(release_login.wait(), timeout=5)
                data = {"user_id": 10001}
            else:
                data = {"message_id": 90009}
            await socket.send(
                json.dumps({"status": "ok", "retcode": 0, "echo": received["echo"], "data": data})
            )

    try:
        async with serve(peer, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = NapCatClient(f"ws://127.0.0.1:{port}/onebot", "fixture-token")
            await client.open()
            client.bind_account("10001")
            task = asyncio.create_task(state.executor(client).execute_part(state.plan, state.part))
            try:
                await asyncio.wait_for(login_received.wait(), timeout=2)
                journal = json.loads(await state.repository.get_adapter_cursor(state.connection_id))
                assert journal[state.part.provider_client_id] == "unknown"
                if stop == "cancel":
                    await state.repository.cancel_remaining_delivery_parts(
                        state.plan.delivery_id,
                        ChannelDeliveryPartsCancelRequest(
                            reason="superseded_by_new_inbound_message",
                            requested_at=datetime.now(UTC),
                        ),
                    )
                else:
                    connection = await state.repository.get_connection(state.connection_id)
                    assert connection is not None
                    await state.repository.update_connection(
                        connection.configuration.model_copy(update={"enabled": False}),
                        expected_revision=connection.revision,
                        access_token_hash=None,
                        updated_at=datetime.now(UTC),
                    )
                release_login.set()
                result = await asyncio.wait_for(task, timeout=2)
                assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
                assert result.error is not None and result.error.code == "qq_send_rejected"
                assert actions == ["get_login_info"]
                journal = json.loads(await state.repository.get_adapter_cursor(state.connection_id))
                assert state.part.provider_client_id not in journal
                await state.reopen()
                replay = await state.executor(client).execute_part(state.plan, state.part)
                assert replay.error is not None and replay.error.code == "qq_delivery_cancelled"
                assert actions == ["get_login_info"]
            finally:
                release_login.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
    finally:
        await state.database.close()


@pytest.mark.asyncio
async def test_audio_serializes_only_verified_asset_bytes_as_onebot_record(tmp_path: Path) -> None:
    payload, audio = audio_payload()
    state = await setup(tmp_path, payload)
    client = RecordingClient()
    (state.audio_root / f"{payload.asset_id}.wav").write_bytes(audio)
    try:
        result = await state.executor(client).execute_part(state.plan, state.part)
        assert result.outcome is DeliveryPartOutcome.DELIVERED
        assert len(client.calls) == 1
        recipient, segments = client.calls[0]
        assert recipient == OWNER
        assert len(segments) == 1 and segments[0]["type"] == "record"
        data = segments[0]["data"]
        assert isinstance(data, dict)
        file = data["file"]
        assert isinstance(file, str) and file.startswith("base64://")
        assert base64.b64decode(file.removeprefix("base64://")) == audio
        assert str(state.audio_root) not in json.dumps(segments)
    finally:
        await state.database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("problem", "code"),
    [
        ("missing", "qq_audio_unavailable"),
        ("changed", "qq_audio_changed"),
        ("oversize", "qq_audio_unavailable"),
        ("symlink", "qq_audio_unavailable"),
    ],
)
async def test_invalid_audio_fails_before_any_send_or_journal_commit(
    tmp_path: Path,
    problem: str,
    code: str,
) -> None:
    payload, audio = audio_payload()
    state = await setup(tmp_path, payload)
    path = state.audio_root / f"{payload.asset_id}.wav"
    client = RecordingClient()
    if problem == "changed":
        path.write_bytes(audio + b"altered")
    elif problem == "oversize":
        with path.open("wb") as output:
            output.truncate(8 * 1024 * 1024 + 1)
    elif problem == "symlink":
        target = tmp_path / "external.wav"
        target.write_bytes(audio)
        path.symlink_to(target)
    try:
        result = await state.executor(client).execute_part(state.plan, state.part)
        assert result.outcome is DeliveryPartOutcome.FATAL_ERROR
        assert result.error is not None and result.error.code == code
        assert not client.calls
        assert await state.repository.get_adapter_cursor(state.connection_id) == ""
    finally:
        await state.database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "disable"])
async def test_stopped_delivery_during_audio_read_prevents_late_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop: str,
) -> None:
    payload, audio = audio_payload()
    state = await setup(tmp_path, payload)
    path = state.audio_root / f"{payload.asset_id}.wav"
    path.write_bytes(audio)
    client = RecordingClient()
    read_started = asyncio.Event()
    release_read = threading.Event()
    loop = asyncio.get_running_loop()
    original_read = Path.read_bytes

    def gated_read(candidate: Path) -> bytes:
        if candidate == path:
            loop.call_soon_threadsafe(read_started.set)
            if not release_read.wait(timeout=5):
                raise TimeoutError("test did not release audio read")
        return original_read(candidate)

    monkeypatch.setattr(Path, "read_bytes", gated_read)
    task = asyncio.create_task(state.executor(client).execute_part(state.plan, state.part))
    try:
        await asyncio.wait_for(read_started.wait(), timeout=2)
        if stop == "cancel":
            await state.repository.cancel_remaining_delivery_parts(
                state.plan.delivery_id,
                ChannelDeliveryPartsCancelRequest(
                    reason="interrupted",
                    requested_at=datetime.now(UTC),
                ),
            )
        else:
            connection = await state.repository.get_connection(state.connection_id)
            assert connection is not None
            await state.repository.update_connection(
                connection.configuration.model_copy(update={"enabled": False}),
                expected_revision=connection.revision,
                access_token_hash=None,
                updated_at=datetime.now(UTC),
            )
        release_read.set()
        result = await asyncio.wait_for(task, timeout=2)
        assert result.error is not None and result.error.code == "qq_delivery_cancelled"
        assert not client.calls
        assert await state.repository.get_adapter_cursor(state.connection_id) == ""
    finally:
        release_read.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await state.database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", ["unknown", "provider_ack_without_scheduler_ack"])
async def test_fresh_scheduler_reclaims_lease_without_repeating_fenced_onebot_send(
    tmp_path: Path, receipt: str
) -> None:
    """A new scheduler must ACK the recovered part using the durable QQ journal."""
    state = await setup(tmp_path)
    actions: list[str] = []

    async def peer(socket: ServerConnection) -> None:
        async for raw in socket:
            received = json.loads(raw)
            action = received["action"]
            actions.append(action)
            if action == "get_login_info":
                data = {"user_id": 10001}
            else:
                assert action == "send_private_msg"
                if receipt == "unknown":
                    # The provider accepted the frame, then lost its response.
                    await socket.close()
                    return
                data = {"message_id": 90009}
            await socket.send(
                json.dumps({"status": "ok", "retcode": 0, "echo": received["echo"], "data": data})
            )

    try:
        async with serve(peer, "127.0.0.1", 0) as server:
            endpoint = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/onebot"
            original = NapCatClient(endpoint, "fixture-token")
            await original.open()
            original.bind_account("10001")
            try:
                result = await asyncio.wait_for(
                    state.executor(original).execute_part(state.plan, state.part), timeout=2
                )
                expected_outcome = (
                    DeliveryPartOutcome.FATAL_ERROR
                    if receipt == "unknown"
                    else DeliveryPartOutcome.DELIVERED
                )
                assert result.outcome is expected_outcome
                assert actions == ["get_login_info", "send_private_msg"]
                pending_ack = await state.repository.get_delivery_plan(state.plan.delivery_id)
                assert pending_ack is not None
                assert pending_ack.parts[0].status is ChannelDeliveryPartStatus.SENDING
            finally:
                await original.close()

            await state.reopen()
            journal = json.loads(await state.repository.get_adapter_cursor(state.connection_id))
            expected_id = "unknown" if receipt == "unknown" else "90009"
            assert journal[state.part.provider_client_id] == expected_id
            recovered = NapCatClient(endpoint, "fixture-token")
            await recovered.open()
            recovered.bind_account("10001")
            try:
                scheduler = ChannelDeliveryScheduler(
                    state.repository,
                    state.executor(recovered),
                    connection_id=state.connection_id,
                )
                # Advance only the repository/scheduler clock, preserving the original lease.
                after_expiry = datetime.now(UTC) + timedelta(minutes=2)
                assert await asyncio.wait_for(scheduler.step(now=after_expiry), timeout=2)
                completed = await state.repository.get_delivery_plan(state.plan.delivery_id)
                assert completed is not None
                part = completed.parts[0]
                assert part.part_id == state.part.part_id
                assert part.provider_client_id == state.part.provider_client_id
                assert part.attempt == state.part.attempt + 1
                assert part.lease_id is None
                if receipt == "unknown":
                    assert completed.status is ChannelDeliveryStatus.FAILED
                    assert part.status is ChannelDeliveryPartStatus.FAILED
                    assert part.last_error is not None
                    assert part.last_error.code == "qq_delivery_unknown"
                    assert part.provider_message_id is None
                else:
                    assert completed.status is ChannelDeliveryStatus.DELIVERED
                    assert part.status is ChannelDeliveryPartStatus.DELIVERED
                    assert part.provider_message_id == "90009"
                assert not await scheduler.step(now=after_expiry)
                assert actions == ["get_login_info", "send_private_msg"]
                assert not await state.repository.list_nonterminal_delivery_plans(
                    state.connection_id
                )
            finally:
                await recovered.close()
    finally:
        await state.database.close()
