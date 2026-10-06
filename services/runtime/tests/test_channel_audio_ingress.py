"""Durable, cancellable audio preprocessing through the real SQLite Runtime."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import aiosqlite
import pytest
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelDeliveryPartKind,
    ChannelInboundTextMessage,
    ChannelMessageKind,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings, StorageConfig
from chatwaifu_runtime.external_channels.adapters.qq_napcat.management import credential_reference
from chatwaifu_runtime.external_channels.adapters.qq_napcat.messages import normalize
from chatwaifu_runtime.external_channels.models import (
    ChannelInboundAudioInput,
    ChannelInboundImageInput,
    ChannelTranscriptionIdentity,
)
from chatwaifu_runtime.external_channels.service import (
    ChannelBusyError,
    ChannelConflictError,
    ChannelPolicyError,
)
from chatwaifu_runtime.persistence.database import Database
from chatwaifu_runtime.persistence.migrations import MIGRATIONS
from chatwaifu_runtime.persistence.sqlite_external_channels import _turn_record
from chatwaifu_runtime.providers.model_config import ModelRoleConfig
from test_qq_channels import (
    ACCOUNT,
    OWNER,
    SPOKEN,
    _configure,
    _event,
    _Harness,
    _ingest,
    _Model,
    _pair,
    _runtime,
    _segments,
    _terminal,
)
from test_qq_recovery import _completed_plans, _plan_completed


@dataclass
class _Audio:
    transcript: object = "你好，请用文字回复我。"
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    identities: list[ChannelTranscriptionIdentity] = field(
        default_factory=list[ChannelTranscriptionIdentity]
    )
    cancelled: int = 0
    swallow_cancel: bool = False
    failure: Exception | None = None

    async def load(self, identity: ChannelTranscriptionIdentity) -> str:
        self.identities.append(identity)
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            if not self.swallow_cancel:
                raise
        if self.failure is not None:
            raise self.failure
        return cast(str, self.transcript)

    def input(self, fingerprint: str = "a" * 64) -> ChannelInboundAudioInput:
        return ChannelInboundAudioInput(fingerprint, self.load)


@dataclass
class _AudioTimeouts:
    deadlines: list[asyncio.Timeout] = field(default_factory=list[asyncio.Timeout])

    def __getattr__(self, name: str) -> object:
        return getattr(asyncio, name)

    def timeout(self, delay: float | None) -> asyncio.Timeout:
        deadline = asyncio.timeout(delay)
        self.deadlines.append(deadline)
        return deadline


@asynccontextmanager
async def _audio_runtime(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[_Harness, UUID, str]]:
    async with _runtime(settings, monkeypatch) as harness:
        gateway = harness.container.external_channels
        registration = gateway._providers["qq_napcat"]
        gateway._providers["qq_napcat"] = registration.model_copy(
            update={
                "capabilities": registration.capabilities.model_copy(
                    update={
                        "inbound_message_kinds": [
                            ChannelMessageKind.TEXT,
                            ChannelMessageKind.IMAGE,
                            ChannelMessageKind.AUDIO,
                        ]
                    }
                )
            }
        )
        connection_id = await _pair(harness)
        raw = await harness.credentials.get(credential_reference(connection_id))
        assert raw is not None
        yield harness, connection_id, str(json.loads(raw)["gateway_token"])


def _message(connection_id: UUID, message_id: int = 80) -> ChannelInboundTextMessage:
    message = normalize(
        _event("[语音]", message_id), connection_id=connection_id, account=ACCOUNT, owner=OWNER
    )
    assert message is not None
    return message


async def _assert_failure(harness: _Harness, connection_id: UUID, channel_turn_id: UUID) -> None:
    snapshot = await _terminal(harness, connection_id, channel_turn_id)
    assert snapshot.status is ChannelTurnStatus.FAILED
    assert snapshot.error is not None and snapshot.error.code == "audio_transcription_failed"
    assert snapshot.error.message == "Inbound audio could not be transcribed."
    assert snapshot.delivery_id is not None
    plan = await harness.container.external_channel_repository.get_delivery_plan(
        snapshot.delivery_id
    )
    assert plan is not None and len(plan.parts) == 1
    assert plan.parts[0].kind is ChannelDeliveryPartKind.TEXT
    assert isinstance(plan.parts[0].payload, ChannelTextDeliveryPartPayload)
    assert plan.parts[0].payload.text == "刚才发来的语音我没听清，能再说一次或发文字吗？"
    assert not harness.model.requests and not harness.synthesis
    assert (
        await harness.container.database.fetchone(
            "SELECT 1 FROM turns WHERE turn_id = ?", (str(snapshot.turn_id),)
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcript", "voice"),
    [
        ("今天天气不错，用文字回答。", False),
        ("请用语音说一句晚安。", True),
        ("請用語音說一句晚安。", True),
        ("Ｐｌｅａｓｅ ｒｅｐｌｙ ｗｉｔｈ ｖｏｉｃｅ", True),  # noqa: RUF001 - explicit NFKC fixture
        ("用語間說一句話", False),
    ],
)
async def test_audio_admits_before_load_uses_exact_identity_and_only_transcript_enters_history(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, transcript: str, voice: bool
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        audio = _Audio(f"  {transcript}  ")
        harness.model.voice_decision = voice
        subscription = _completed_plans(harness.container)
        try:
            receipt = await gateway.ingest(
                _message(connection_id), access_token=token, audio_input=audio.input()
            )
            assert receipt.status is ChannelTurnStatus.ACCEPTED
            await asyncio.wait_for(audio.entered.wait(), 3)
            turn = await harness.container.external_channel_repository.get_turn(
                receipt.channel_turn_id
            )
            assert turn is not None and turn.input_kind is ChannelMessageKind.AUDIO
            assert turn.status is ChannelTurnStatus.ACCEPTED
            assert audio.identities == [
                ChannelTranscriptionIdentity(
                    receipt.session_id, receipt.turn_id, receipt.generation_id
                )
            ]
            assert gateway.active_preprocessing_count == 1
            snapshot = await gateway.wait_for_turn(
                connection_id, receipt.channel_turn_id, wait_seconds=0
            )
            assert snapshot.status is ChannelTurnStatus.ACCEPTED and snapshot.error is None
            assert (
                await harness.container.database.fetchone(
                    "SELECT 1 FROM turns WHERE turn_id = ?", (str(receipt.turn_id),)
                )
                is None
            )
            audio.release.set()
            sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
            await _plan_completed(subscription, receipt.channel_turn_id)
            result = await _terminal(harness, connection_id, receipt.channel_turn_id)
            assert result.status is ChannelTurnStatus.COMPLETED
            assert [segment["type"] for segment in _segments(sent)] == [
                "record" if voice else "text"
            ]
            assert harness.model.requests[0].tools
            assert harness.model.requests[0].tool_choice == "auto"
            assert len(harness.synthesis) == int(voice)
            if voice:
                assert result.reply_text == SPOKEN
            row = await harness.container.database.fetchone(
                "SELECT committed_text, source_context_json FROM turns WHERE turn_id = ?",
                (str(receipt.turn_id),),
            )
            assert row is not None and row["committed_text"] == str(audio.transcript).strip()
            assert json.loads(row["source_context_json"])["connection_id"] == str(connection_id)
            assert gateway.active_preprocessing_count == 0
            assert harness.peer.sends.empty()
        finally:
            subscription.close()


@pytest.mark.asyncio
async def test_audio_dedup_never_reloads_and_changed_audio_conflicts(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        audio, other = _Audio(), _Audio()
        message = _message(connection_id)
        receipt = await gateway.ingest(message, access_token=token, audio_input=audio.input())
        await asyncio.wait_for(audio.entered.wait(), 3)
        duplicate = await gateway.ingest(
            message, access_token=token, audio_input=other.input(), supersede_inflight=True
        )
        assert duplicate.duplicate and duplicate.channel_turn_id == receipt.channel_turn_id
        with pytest.raises(ChannelConflictError):
            await gateway.ingest(message, access_token=token, audio_input=other.input("b" * 64))
        assert len(audio.identities) == 1 and not other.identities
        audio.release.set()
        task = gateway._audio_tasks[receipt.channel_turn_id]
        await task
        await _terminal(harness, connection_id, receipt.channel_turn_id)
        duplicate = await gateway.ingest(message, access_token=token, audio_input=other.input())
        assert duplicate.duplicate and not other.identities


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["sender", "scope", "account", "group", "capability", "disabled"]
)
async def test_audio_policy_denial_happens_before_any_load_or_turn_admission(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        message, audio = _message(connection_id), _Audio()
        changes = {
            "sender": {"sender_key": "another"},
            "scope": {"principal_scope": "another"},
            "account": {"account_key": "another"},
            "group": {"chat_type": ChannelChatType.GROUP},
        }
        if invalid in changes:
            message = message.model_copy(update=changes[invalid])
        elif invalid == "capability":
            registration = gateway._providers["qq_napcat"]
            gateway._providers["qq_napcat"] = registration.model_copy(
                update={
                    "capabilities": registration.capabilities.model_copy(
                        update={"inbound_message_kinds": [ChannelMessageKind.TEXT]}
                    )
                }
            )
        else:
            current = await gateway.get_connection(connection_id)
            await gateway.update_connection(
                current.configuration.model_copy(update={"enabled": False}),
                expected_revision=current.revision,
                rotate_access_token=False,
            )
        with pytest.raises(ChannelPolicyError):
            await gateway.ingest(message, access_token=token, audio_input=audio.input())
        assert not audio.identities and not harness.model.requests
        assert (
            await harness.container.external_channel_repository.find_turn_by_external_message(
                connection_id, message.external_message_id
            )
            is None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["", "  \n ", "x" * 20001, None, 3, "failure", "timeout", "timeout_late"]
)
async def test_invalid_failed_or_timed_out_stt_has_one_sanitized_durable_text_notice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, invalid: object
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        audio = _Audio(invalid)
        timeouts = _AudioTimeouts()
        if invalid == "failure":
            audio.failure = RuntimeError("SECRET URL AND PAYLOAD MUST NEVER BE PERSISTED")
        if invalid in {"timeout", "timeout_late"}:
            monkeypatch.setattr("chatwaifu_runtime.external_channels.service.asyncio", timeouts)
            if invalid == "timeout_late":
                audio.swallow_cancel = True
                audio.transcript = "请用语音回复我"
        else:
            audio.release.set()
        receipt = await harness.container.external_channels.ingest(
            _message(connection_id), access_token=token, audio_input=audio.input()
        )
        await asyncio.wait_for(audio.entered.wait(), 15)
        task = harness.container.external_channels._audio_tasks.get(receipt.channel_turn_id)
        if invalid in {"timeout", "timeout_late"}:
            # Expire the real production timeout only after the held loader has
            # entered. SQLite/runner scheduling must not consume a 20ms budget
            # before the cancellation and late-result behavior can be exercised.
            assert task is not None and len(timeouts.deadlines) == 1
            timeouts.deadlines[0].reschedule(asyncio.get_running_loop().time())
        if task is not None:
            await asyncio.wait_for(task, 15)
        if invalid in {"timeout", "timeout_late"}:
            assert audio.cancelled == 1
        await _assert_failure(harness, connection_id, receipt.channel_turn_id)
        repeated = await harness.container.external_channels.ingest(
            _message(connection_id), access_token=token, audio_input=audio.input()
        )
        assert repeated.duplicate and len(audio.identities) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["interrupt", "supersede", "disable", "delete", "stop"])
@pytest.mark.parametrize("swallow_cancel", [False, True])
async def test_cancellation_joins_audio_and_never_submits_a_late_transcript_or_notice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, action: str, swallow_cancel: bool
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        audio = _Audio("请用语音回复我", swallow_cancel=swallow_cancel)
        receipt = await gateway.ingest(
            _message(connection_id), access_token=token, audio_input=audio.input()
        )
        await asyncio.wait_for(audio.entered.wait(), 3)
        task = gateway._audio_tasks[receipt.channel_turn_id]
        async with asyncio.timeout(5):
            if action == "interrupt":
                cancelled = await gateway.interrupt(
                    connection_id, receipt.channel_turn_id, access_token=token, reason="test_cancel"
                )
                assert cancelled.accepted and cancelled.status is ChannelTurnStatus.CANCELLED
            elif action == "supersede":
                current = await _ingest(harness, connection_id, "只用文字回复", 81)
                await _terminal(harness, connection_id, current.channel_turn_id)
                assert len(harness.model.requests) == 1 and harness.model.requests[0].tools
                assert not harness.synthesis
            elif action == "disable":
                current = await gateway.get_connection(connection_id)
                await gateway.update_connection(
                    current.configuration.model_copy(update={"enabled": False}),
                    expected_revision=current.revision,
                    rotate_access_token=False,
                )
            elif action == "delete":
                await gateway.delete_connection(connection_id)
            else:
                await gateway.stop()
        assert task.done() and audio.cancelled == 1
        turn = await harness.container.external_channel_repository.get_turn(receipt.channel_turn_id)
        assert (
            turn is not None
            and turn.status is ChannelTurnStatus.CANCELLED
            and turn.delivery_id is None
        )
        assert gateway.active_preprocessing_count == 0 and not harness.synthesis
        assert (
            await harness.container.database.fetchone(
                "SELECT 1 FROM turns WHERE turn_id = ?", (str(receipt.turn_id),)
            )
            is None
        )
        if action != "supersede":
            assert not harness.model.requests and harness.peer.sends.empty()


@pytest.mark.asyncio
async def test_audio_restored_without_live_task_fails_once_without_download_or_model(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        _, _, turn, duplicate = await gateway._admit_ingress(
            _message(connection_id), access_token=token, audio_fingerprint="a" * 64
        )
        assert not duplicate and turn.input_kind is ChannelMessageKind.AUDIO
        # Capture committed WAL state before orderly stop now cancels every
        # durable audio admission, including one without a registered task.
        crash_path = runtime_settings.data_dir / "abrupt-restart.db"
        assert runtime_settings.storage.database_path is not None
        with sqlite3.connect(runtime_settings.storage.database_path) as source:
            with sqlite3.connect(crash_path) as snapshot:
                source.backup(snapshot)
    restarted = RuntimeContainer(
        runtime_settings.model_copy(
            update={
                "storage": runtime_settings.storage.model_copy(update={"database_path": crash_path})
            }
        )
    )
    _configure(restarted, monkeypatch, harness.credentials)

    # New service objects have no callback registry; only the committed SQLite
    # admission survives. Real model creation is instrumented, never requested.
    def create_model(_configuration: ModelRoleConfig) -> _Model:
        return harness.model

    monkeypatch.setattr(restarted.model_configurations, "create_chat_provider", create_model)
    restored = _Harness(
        restarted,
        harness.peer,
        harness.model,
        harness.credentials,
        harness.health,
        harness.synthesis,
    )
    await restarted.start()
    try:
        await _assert_failure(restored, connection_id, turn.channel_turn_id)
        await restarted.external_channels.start()
        rows = await restarted.database.fetchall(
            "SELECT d.delivery_id FROM channel_deliveries d WHERE d.channel_turn_id = ?",
            (str(turn.channel_turn_id),),
        )
        assert len(rows) == 1 and restarted.external_channels.active_preprocessing_count == 0
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_audio_does_not_hold_ingress_lock_across_conversation_preparation(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        entered, cancelled = asyncio.Event(), asyncio.Event()
        original = harness.container.memory.retrieve_context
        first = True

        async def block_first(*args: object, **kwargs: object) -> object:
            nonlocal first
            if first:
                first = False
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return await original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(harness.container.memory, "retrieve_context", block_first)
        audio = _Audio()
        audio.release.set()
        receipt = await gateway.ingest(
            _message(connection_id), access_token=token, audio_input=audio.input()
        )
        await asyncio.wait_for(entered.wait(), 3)
        snapshot = await gateway.wait_for_turn(
            connection_id, receipt.channel_turn_id, wait_seconds=0
        )
        assert snapshot.status is ChannelTurnStatus.ACCEPTED
        current = await asyncio.wait_for(_ingest(harness, connection_id, "新文字", 81), 5)
        await asyncio.wait_for(cancelled.wait(), 3)
        assert (
            await _terminal(harness, connection_id, current.channel_turn_id)
        ).status is ChannelTurnStatus.COMPLETED
        prior = await harness.container.external_channel_repository.get_turn(
            receipt.channel_turn_id
        )
        assert (
            prior is not None
            and prior.status is ChannelTurnStatus.CANCELLED
            and prior.delivery_id is None
        )
        assert len(harness.model.requests) == 1 and not harness.synthesis


@pytest.mark.asyncio
async def test_audio_limits_exclusivity_and_fingerprint_validation(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        audio = _Audio()
        image = ChannelInboundImageInput("a" * 64, cast(object, None))  # type: ignore[arg-type]
        for image_input, burst_intake, raw_images in (
            (image, False, ()),
            (None, True, ()),
            (None, False, (object(),)),
        ):
            with pytest.raises(ChannelPolicyError):
                await gateway.ingest(
                    _message(connection_id),
                    access_token=token,
                    audio_input=audio.input(),
                    image_input=image_input,
                    burst_intake=burst_intake,
                    raw_images=raw_images,
                )
        for fingerprint in ("A" * 64, "a" * 63, "z" * 64, None):
            with pytest.raises(ValueError):
                audio.input(cast(str, fingerprint))
        assert "load" not in repr(audio.input())
        monkeypatch.setattr(
            "chatwaifu_runtime.external_channels.service.MAX_AUDIO_PREPROCESSING_TASKS", 0
        )
        with pytest.raises(ChannelBusyError):
            await gateway.ingest(
                _message(connection_id), access_token=token, audio_input=audio.input()
            )
        assert not audio.identities


@pytest.mark.asyncio
async def test_audio_input_kind_migration_is_atomic_and_old_rows_default_to_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.db"
    storage = StorageConfig(database_path=path)
    old = tuple(migration for migration in MIGRATIONS if migration[0] < 37)
    database = Database(path, storage, migrations=old)
    await database.open()
    await database.close()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        ids = [str(uuid4()) for _ in range(6)]
        connection.execute(
            """INSERT INTO channel_turns (
            channel_turn_id, connection_id, binding_id, external_message_id,
            content_sha256, conversation_key, chat_type, sender_key, principal_scope,
            session_id, turn_id, generation_id, status, revision,
            accepted_at, created_at, updated_at)
            VALUES (?, ?, ?, 'old', ?, 'conversation', 'direct', 'owner', 'local',
                    ?, ?, ?, 'accepted', 0, ?, ?, ?)""",
            (*ids[:3], "a" * 64, *ids[3:], *("2026-10-03T00:00:00+00:00",) * 3),
        )
        connection.commit()

    audio_script = next(script for version, script in MIGRATIONS if version == 37)
    broken = Database(
        path,
        storage,
        migrations=(*old, (37, audio_script + "INSERT INTO missing_table VALUES (1);")),
    )
    with pytest.raises(aiosqlite.OperationalError):
        await broken.open()
    with sqlite3.connect(path) as connection:
        assert "input_kind" not in {
            row[1] for row in connection.execute("PRAGMA table_info(channel_turns)")
        }
        assert connection.execute("SELECT max(version) FROM schema_migrations").fetchone() == (36,)
    current = Database(path, storage)
    await current.open()
    try:
        column = next(
            row
            for row in await current.fetchall("PRAGMA table_info(channel_turns)")
            if row["name"] == "input_kind"
        )
        assert column["notnull"] == 1 and column["dflt_value"] == "'text'"
        # A legacy SELECT result without the column remains readable by the
        # adapter; all omitted insert columns receive SQLite's TEXT default.
        row = await current.fetchone(
            "SELECT *, NULL AS delivery_status FROM channel_turns WHERE external_message_id='old'"
        )
        assert row is not None and _turn_record(row).input_kind is ChannelMessageKind.TEXT
        legacy = {key: row[key] for key in row.keys() if key != "input_kind"}
        assert _turn_record(legacy).input_kind is ChannelMessageKind.TEXT
        with pytest.raises(aiosqlite.IntegrityError):
            await current.execute("UPDATE channel_turns SET input_kind='file'")
    finally:
        await current.close()


@pytest.mark.asyncio
async def test_historical_voice_quote_remains_data_when_model_chooses_text(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        subscription = _completed_plans(harness.container)
        try:
            prior = await _ingest(harness, connection_id, "请用语音说晚安", 79)
            await asyncio.wait_for(harness.peer.sends.get(), 5)
            await _plan_completed(subscription, prior.channel_turn_id)
            assert (await _terminal(harness, connection_id, prior.channel_turn_id)).status is (
                ChannelTurnStatus.COMPLETED
            )
            audio = _Audio("刚才这句话是什么意思？用文字说。")
            audio.release.set()
            receipt = await harness.container.external_channels.ingest(
                _message(connection_id).model_copy(update={"reply_to_external_message_id": "79"}),
                access_token=token,
                audio_input=audio.input(),
            )
            sent = await asyncio.wait_for(harness.peer.sends.get(), 5)
            await _plan_completed(subscription, receipt.channel_turn_id)
            assert [segment["type"] for segment in _segments(sent)] == ["reply", "text"]
            assert len(harness.synthesis) == 1 and harness.model.requests[-1].tools
            assert harness.model.requests[-1].tool_choice == "auto"
            request = harness.model.requests[-1]
            assert request.user_text == audio.transcript
            reference = next(
                text for _, text in request.context if "Historical reply reference" in text
            )
            payload = json.loads(reference.split("\n")[-1])
            assert payload["available"] is True and payload["text"] == "请用语音说晚安"
            row = await harness.container.database.fetchone(
                "SELECT source_context_json FROM turns WHERE turn_id = ?", (str(receipt.turn_id),)
            )
            assert row is not None
            assert json.loads(row["source_context_json"])["reply_to_external_message_id"] == "79"
        finally:
            subscription.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "binding", "character", "scope"])
@pytest.mark.parametrize("stt_failure", [False, True])
async def test_audio_final_fence_rejects_changed_owner_binding_or_route_without_notice(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, change: str, stt_failure: bool
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        audio = _Audio()
        if stt_failure:
            audio.failure = RuntimeError("private backend detail")
        receipt = await gateway.ingest(
            _message(connection_id), access_token=token, audio_input=audio.input()
        )
        await asyncio.wait_for(audio.entered.wait(), 3)
        task = gateway._audio_tasks[receipt.channel_turn_id]
        if change == "owner":
            current = await gateway.get_connection(connection_id)
            await gateway.update_connection(
                current.configuration.model_copy(update={"allowed_sender_keys": ["different"]}),
                expected_revision=current.revision,
                rotate_access_token=False,
            )
        elif change == "binding":
            await harness.container.database.execute(
                "UPDATE channel_bindings SET sender_key='different' WHERE connection_id=?",
                (str(connection_id),),
            )
        else:
            # Management forbids changing either routing field; simulate a
            # persisted configuration replacement during a live STT callback.
            column = "character_id" if change == "character" else "principal_scope"
            await harness.container.database.execute(
                f"UPDATE channel_connections SET {column}='different' WHERE connection_id=?",
                (str(connection_id),),
            )
        audio.release.set()
        await asyncio.gather(task, return_exceptions=True)
        turn = await harness.container.external_channel_repository.get_turn(receipt.channel_turn_id)
        assert (
            turn is not None
            and turn.status is ChannelTurnStatus.CANCELLED
            and turn.delivery_id is None
        )
        assert not harness.model.requests and not harness.synthesis and harness.peer.sends.empty()


@pytest.mark.asyncio
async def test_audio_terminal_callback_can_stop_gateway_without_self_await(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        stopped = asyncio.Event()

        async def on_terminal(_turn: object) -> None:
            await gateway.stop()
            stopped.set()

        gateway.add_turn_terminal_listener(on_terminal)
        audio = _Audio("")
        audio.release.set()
        receipt = await gateway.ingest(
            _message(connection_id), access_token=token, audio_input=audio.input()
        )
        task = gateway._audio_tasks[receipt.channel_turn_id]
        await asyncio.wait_for(stopped.wait(), 3)
        await asyncio.wait_for(task, 3)
        assert gateway.active_preprocessing_count == 0
        await _assert_failure(harness, connection_id, receipt.channel_turn_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["stop", "disable", "delete", "interrupt"])
async def test_lifecycle_change_between_durable_admission_and_registration_starts_no_io(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        repository = harness.container.external_channel_repository
        original = repository.create_turn
        persisted, release = asyncio.Event(), asyncio.Event()

        async def pause_after_commit(turn: object) -> object:
            result = await original(turn)  # type: ignore[arg-type]
            persisted.set()
            await release.wait()
            return result

        monkeypatch.setattr(repository, "create_turn", pause_after_commit)
        audio = _Audio()
        ingest = asyncio.create_task(
            gateway.ingest(
                _message(connection_id, 180), access_token=token, audio_input=audio.input()
            )
        )
        try:
            await asyncio.wait_for(persisted.wait(), 3)
            assert gateway.active_preprocessing_count == 0
            async with asyncio.timeout(3):
                if action == "stop":
                    await gateway.stop()
                elif action == "disable":
                    current = await gateway.get_connection(connection_id)
                    await gateway.update_connection(
                        current.configuration.model_copy(update={"enabled": False}),
                        expected_revision=current.revision,
                        rotate_access_token=False,
                    )
                elif action == "delete":
                    await gateway.delete_connection(connection_id)
                else:
                    turns = await repository.list_inflight_turns(connection_id)
                    await gateway.interrupt(
                        connection_id,
                        turns[0].channel_turn_id,
                        access_token=token,
                        reason="test_registration_gap",
                    )
            assert gateway.active_preprocessing_count == 0
            release.set()
            receipt = await asyncio.wait_for(ingest, 3)
            turn = await repository.get_turn(receipt.channel_turn_id)
            assert turn is not None and turn.status is ChannelTurnStatus.CANCELLED
            assert not audio.identities and gateway.active_preprocessing_count == 0
            assert (
                not harness.model.requests and not harness.synthesis and harness.peer.sends.empty()
            )
            if action == "delete":
                assert await repository.get_connection(connection_id) is None
        finally:
            release.set()
            await asyncio.gather(ingest, return_exceptions=True)


@pytest.mark.asyncio
async def test_owner_revocation_cancels_conversation_preparation_before_model_start(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _audio_runtime(runtime_settings, monkeypatch) as (harness, connection_id, token):
        gateway = harness.container.external_channels
        preparing, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = harness.container.memory.retrieve_context

        async def block_preparation(*args: object, **kwargs: object) -> object:
            preparing.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return await original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(harness.container.memory, "retrieve_context", block_preparation)
        audio = _Audio("只用文字回答这轮录音。")
        audio.release.set()
        receipt = await gateway.ingest(
            _message(connection_id, 181), access_token=token, audio_input=audio.input()
        )
        task = gateway._audio_tasks[receipt.channel_turn_id]
        await asyncio.wait_for(preparing.wait(), 3)
        current = await gateway.get_connection(connection_id)
        original_update = harness.container.external_channel_repository.update_connection
        stopped_before_commit = False

        async def verify_revocation_fence(*args: object, **kwargs: object) -> object:
            nonlocal stopped_before_commit
            # The cancellation must already be requested before update yields
            # to its database write; releasing preparation here cannot run LLM.
            stopped_before_commit = (
                task.cancelling() > 0 and gateway.active_preprocessing_count == 0
            )
            release.set()
            return await original_update(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            harness.container.external_channel_repository,
            "update_connection",
            verify_revocation_fence,
        )
        await asyncio.wait_for(
            gateway.update_connection(
                current.configuration.model_copy(update={"allowed_sender_keys": ["different"]}),
                expected_revision=current.revision,
                rotate_access_token=False,
            ),
            3,
        )
        assert stopped_before_commit and cancelled.is_set() and task.done()
        turn = await harness.container.external_channel_repository.get_turn(receipt.channel_turn_id)
        assert (
            turn is not None
            and turn.status is ChannelTurnStatus.CANCELLED
            and turn.delivery_id is None
        )
        assert not harness.model.requests and not harness.synthesis and harness.peer.sends.empty()
        assert gateway.active_preprocessing_count == 0
