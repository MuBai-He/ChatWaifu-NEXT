"""Permissioned quote content stays historical and survives provider cache loss."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.qq_napcat.messages import normalize
from chatwaifu_runtime.external_channels.service import ChannelPolicyError
from chatwaifu_runtime.persistence.sqlite_photo_memory import SQLitePhotoMemoryRepository
from test_photo_memory_repository import _seed_source_chain, make_candidate
from test_qq_channels import (
    ACCOUNT,
    OWNER,
    SPOKEN,
    _event,
    _ingest,
    _pair,
    _runtime,
    _segments,
    _terminal,
)
from test_qq_recovery import _completed_plans, _plan_completed


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_kind", ["inbound", "outbound", "unknown", "redacted", "reset"])
async def test_real_gateway_resolves_same_binding_quotes_without_authorizing_old_voice_request(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch, reference_kind: str
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        subscription = _completed_plans(harness.container)
        repository = harness.container.external_channel_repository
        try:
            first = await _ingest(harness, connection_id, "请用语音说一句晚安", 61)
            await asyncio.wait_for(harness.peer.sends.get(), 5)
            await _plan_completed(subscription, first.channel_turn_id)
            terminal = await _terminal(harness, connection_id, first.channel_turn_id)
            assert terminal.delivery_id is not None
            plan = await repository.get_delivery_plan(terminal.delivery_id)
            assert plan is not None and plan.parts[0].provider_message_id is not None
            turn = await repository.get_turn(first.channel_turn_id)
            assert turn is not None
            outbound_id = plan.parts[0].provider_message_id
            reference = "61" if reference_kind == "inbound" else outbound_id
            if reference_kind == "unknown":
                reference = "-999999"
            elif reference_kind == "redacted":
                await harness.container.database.execute(
                    "INSERT INTO photo_context_redactions "
                    "(generation_id, session_id, principal_scope, character_id, created_at) "
                    "VALUES (?, ?, 'local', 'default', ?)",
                    (
                        str(first.generation_id),
                        str(first.session_id),
                        datetime.now(UTC).isoformat(),
                    ),
                )
            elif reference_kind == "reset":
                await harness.container.database.execute(
                    "INSERT INTO memory_scope_resets (character_id, user_scope, reset_at) "
                    "VALUES ('default', 'local', ?)",
                    (datetime.now(UTC).isoformat(),),
                )
            assert (
                await repository.resolve_quoted_message(connection_id, uuid4(), reference) is None
            )
            assert (
                await repository.resolve_quoted_message(uuid4(), turn.binding_id, reference) is None
            )
            event = _event("刚才这句话是什么意思？", 62)
            event["message"] = [
                {"type": "reply", "data": {"id": reference}},
                {"type": "text", "data": {"text": "刚才这句话是什么意思？"}},
            ]
            await harness.peer.peers[-1].send(json.dumps(event))
            reply = await asyncio.wait_for(harness.peer.sends.get(), 5)
            assert _segments(reply)[0] == {"type": "reply", "data": {"id": "62"}}
            quoted_turn = await repository.find_turn_by_external_message(connection_id, "62")
            assert quoted_turn is not None
            await _plan_completed(subscription, quoted_turn.channel_turn_id)
            request = harness.model.requests[-1]
            assert request.user_text == "刚才这句话是什么意思？" and request.tools
            assert request.tool_choice == "auto"
            assert len(harness.synthesis) == 1
            reference_context = next(
                text for _, text in request.context if "Historical reply reference" in text
            )
            payload = cast(JsonObject, json.loads(reference_context.split("\n")[-1]))
            if reference_kind in {"unknown", "redacted", "reset"}:
                assert payload == {"available": False}
            else:
                assert payload["available"] is True
                assert payload["text"] == (
                    "请用语音说一句晚安" if reference_kind == "inbound" else SPOKEN
                )
                assert payload["speaker"] == (
                    "user" if reference_kind == "inbound" else "assistant"
                )
            row = await harness.container.database.fetchone(
                "SELECT source_context_json FROM turns WHERE turn_id = ?",
                (str(quoted_turn.turn_id),),
            )
            assert row is not None
            assert (
                json.loads(str(row["source_context_json"]))["reply_to_external_message_id"]
                == reference
            )
            assert await repository.quoted_reply_target(quoted_turn.channel_turn_id) == "62"
            assert not any(call["action"] == "get_msg" for call in harness.peer.calls)
        finally:
            harness.container.event_hub.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_quote_loading_is_owned_by_generation_and_cancelled_before_new_turn(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        first = await _ingest(harness, connection_id, "历史内容", 71)
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        await _terminal(harness, connection_id, first.channel_turn_id)
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def blocked(_connection: UUID, _binding: UUID, _reference: str) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(
            harness.container.external_channel_repository, "resolve_quoted_message", blocked
        )
        event = _event("引用问题", 72)
        event["message"] = [
            {"type": "reply", "data": {"id": "71"}},
            {"type": "text", "data": {"text": "引用问题"}},
        ]
        await harness.peer.peers[-1].send(json.dumps(event))
        await asyncio.wait_for(entered.wait(), 5)
        await harness.peer.peers[-1].send(json.dumps(_event("现在的问题", 73)))
        await asyncio.wait_for(cancelled.wait(), 5)
        await asyncio.wait_for(harness.peer.sends.get(), 5)
        assert [request.user_text for request in harness.model.requests] == [
            "历史内容",
            "现在的问题",
        ]
        assert harness.peer.sends.empty() and not harness.synthesis


@pytest.mark.asyncio
async def test_ambiguous_reference_stays_unavailable_after_one_candidate_is_redacted(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        subscription = _completed_plans(harness.container)
        repository = harness.container.external_channel_repository
        try:
            first = await _ingest(harness, connection_id, "原消息", 81)
            await asyncio.wait_for(harness.peer.sends.get(), 5)
            await _plan_completed(subscription, first.channel_turn_id)
            second = await _ingest(harness, connection_id, "另一条消息", 82)
            await asyncio.wait_for(harness.peer.sends.get(), 5)
            await _plan_completed(subscription, second.channel_turn_id)
            turn = await repository.get_turn(first.channel_turn_id)
            assert turn is not None
            await harness.container.database.execute(
                "UPDATE channel_delivery_parts SET provider_message_id = '81' WHERE delivery_id "
                "= (SELECT delivery_id FROM channel_turns WHERE channel_turn_id = ?)",
                (str(second.channel_turn_id),),
            )
            assert (
                await repository.resolve_quoted_message(connection_id, turn.binding_id, "81")
                is None
            )
            await harness.container.database.execute(
                "INSERT INTO photo_context_redactions "
                "(generation_id, session_id, principal_scope, character_id, created_at) "
                "VALUES (?, ?, 'local', 'default', ?)",
                (str(first.generation_id), str(first.session_id), datetime.now(UTC).isoformat()),
            )
            assert (
                await repository.resolve_quoted_message(connection_id, turn.binding_id, "81")
                is None
            )
        finally:
            harness.container.event_hub.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_ephemeral_policy_cannot_be_dropped_by_burst_early_return(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        message = normalize(
            _event("[图片]", 83), connection_id=connection_id, account=ACCOUNT, owner=OWNER
        )
        assert message is not None
        with pytest.raises(ChannelPolicyError, match="Ephemeral"):
            await harness.container.external_channels.ingest(
                message, access_token="unused", image_retention_allowed=False, burst_intake=True
            )
        assert (
            await harness.container.external_channel_repository.find_turn_by_external_message(
                connection_id, "83"
            )
            is None
        )
        assert not harness.model.requests and harness.peer.sends.empty()


@pytest.mark.asyncio
async def test_quote_outside_recent_history_registers_deletion_lineage(
    runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _runtime(runtime_settings, monkeypatch) as harness:
        connection_id = await _pair(harness)
        subscription = _completed_plans(harness.container)
        repository = harness.container.external_channel_repository

        async def no_recent_history(*_args: object, **_kwargs: object) -> tuple[()]:
            return ()

        monkeypatch.setattr(
            harness.container.conversation_repository, "recent_history", no_recent_history
        )
        try:
            photos = SQLitePhotoMemoryRepository(harness.container.database)
            settings = await photos.get_settings("local", "default")
            settings = await photos.update_settings(
                "local", "default", retention_enabled=True, expected_revision=settings.revision
            )
            source_connection, source_generation, _ = await _seed_source_chain(
                harness.container.database, scope="local", character_id="default"
            )
            photo = await photos.save(
                "local",
                "default",
                make_candidate(source_connection, source_generation),
                expected_revision=settings.revision,
            )
            assert photo is not None
            harness.model.release = asyncio.Event()
            first = await _ingest(harness, connection_id, "历史照片描述", 91)
            await asyncio.wait_for(harness.model.received.get(), 5)
            recalled = await photos.register_recall(
                "local", "default", (photo.photo_id,), generation_id=first.generation_id
            )
            assert recalled
            harness.model.release.set()
            await asyncio.wait_for(harness.peer.sends.get(), 5)
            await _plan_completed(subscription, first.channel_turn_id)
            terminal = await _terminal(harness, connection_id, first.channel_turn_id)
            assert terminal.delivery_id is not None
            plan = await repository.get_delivery_plan(terminal.delivery_id)
            assert plan is not None and plan.parts[0].provider_message_id is not None

            event = _event("引用旧描述", 92)
            event["message"] = [
                {"type": "reply", "data": {"id": plan.parts[0].provider_message_id}},
                {"type": "text", "data": {"text": "引用旧描述"}},
            ]
            await harness.peer.peers[-1].send(json.dumps(event))
            await asyncio.wait_for(harness.peer.sends.get(), 5)
            derived = await repository.find_turn_by_external_message(connection_id, "92")
            assert derived is not None
            await _plan_completed(subscription, derived.channel_turn_id)
            assert not harness.model.requests[-1].history
            dependency = await harness.container.database.fetchone(
                "SELECT 1 FROM conversation_history_dependencies WHERE source_generation_id = ? "
                "AND derived_generation_id = ?",
                (str(first.generation_id), str(derived.generation_id)),
            )
            assert dependency is not None
            deletion = await photos.delete("local", "default", photo.photo_id)
            assert {item.generation_id for item in deletion.affected_generations} >= {
                first.generation_id,
                derived.generation_id,
            }
            assert derived.delivery_id is not None
            derived_plan = await repository.get_delivery_plan(derived.delivery_id)
            assert (
                derived_plan is not None and derived_plan.parts[0].provider_message_id is not None
            )
            assert (
                await repository.resolve_quoted_message(
                    connection_id, derived.binding_id, derived_plan.parts[0].provider_message_id
                )
                is None
            )
        finally:
            harness.container.event_hub.unsubscribe(subscription)
