"""Stable, reset-aware diagnostic readback without a second facts table."""

# pyright: reportPrivateUsage=false

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from chatwaifu_protocol.base import PrivacyLevel
from chatwaifu_protocol.channel_proactive import (
    ChannelProactivePolicy,
    ChannelProactivePolicyUpdate,
)
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelConnectionStatus,
    ChannelDeliveryPartClaimRequest,
    ChannelTurnStatus,
)
from chatwaifu_protocol.events import GenericCoreEvent
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationSourceContext, ConversationTurnOptions
from chatwaifu_runtime.external_channels.models import ChannelTurnRecord


@pytest.mark.asyncio
async def test_nonparticipation_is_opt_in_paginated_and_reset_fenced(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        now = datetime.now(UTC)
        events: list[GenericCoreEvent] = []
        for index in range(3):
            event = await container.event_publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "voice.utterance_ignored",
                        "session_id": session.session_id,
                        "occurred_at": now + timedelta(seconds=index + 1),
                        "source": "runtime.voice",
                        "privacy": PrivacyLevel.LOCAL,
                        "payload": {
                            "reason": "not_addressed",
                            "text": "private voice transcript must never appear in diagnostics",
                        },
                    }
                )
            )
            events.append(event)
        reader = container.interaction_diagnostics
        assert (await reader.list_interactions(session.session_id)).items == []
        first = await reader.list_interactions(
            session.session_id, limit=2, include_nonparticipation=True
        )
        assert len(first.items) == 2
        assert first.has_more and first.next_cursor
        second = await reader.list_interactions(
            session.session_id,
            limit=2,
            cursor=first.next_cursor,
            include_nonparticipation=True,
        )
        assert len(second.items) == 1 and not second.has_more
        assert {item.interaction_id for item in [*first.items, *second.items]} == {
            event.event_id for event in events
        }
        detail = await reader.read_interaction(
            session.session_id,
            events[0].event_id,
            visible_namespaces=await container.memory.namespaces_for_session(session.session_id),
        )
        assert detail is not None
        assert detail.summary.reason == "not_addressed"
        assert detail.summary.generation_state is None
        assert "private voice transcript" not in detail.model_dump_json()

        await container.database.execute(
            "INSERT INTO memory_scope_resets(character_id, user_scope, reset_at) "
            "VALUES ('default', 'local', ?)",
            ((now + timedelta(minutes=1)).isoformat(),),
        )
        assert (
            await reader.list_interactions(session.session_id, include_nonparticipation=True)
        ).items == []
        assert (
            await reader.read_interaction(
                session.session_id,
                events[0].event_id,
                visible_namespaces=[],
            )
            is None
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_partial_channel_delivery_and_playback_ack_stay_separate(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        accepted = await container.conversation.submit_text(session.session_id, "test")
        active = container.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)
        connection_id, binding_id, channel_turn_id, delivery_id = (uuid4() for _ in range(4))
        text_part_id, image_part_id = uuid4(), uuid4()
        now = datetime.now(UTC).isoformat()
        async with container.database.transaction() as connection:
            await connection.execute(
                "INSERT INTO channel_connections(connection_id, provider_id, name, "
                "character_id, principal_scope, access_token_hash, created_at, updated_at) "
                "VALUES (?, 'test', 'test', 'default', 'local', 'hash', ?, ?)",
                (str(connection_id), now, now),
            )
            await connection.execute(
                "INSERT INTO channel_bindings(binding_id, connection_id, conversation_key, "
                "sender_key, session_id, created_at, updated_at) "
                "VALUES (?, ?, 'peer', 'peer', ?, ?, ?)",
                (str(binding_id), str(connection_id), str(session.session_id), now, now),
            )
            await connection.execute(
                "INSERT INTO channel_turns(channel_turn_id, connection_id, binding_id, "
                "external_message_id, content_sha256, conversation_key, sender_key, "
                "principal_scope, session_id, turn_id, generation_id, status, "
                "accepted_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'msg', 'hash', 'peer', 'peer', 'local', ?, ?, ?, "
                "'completed', ?, ?, ?)",
                (
                    str(channel_turn_id),
                    str(connection_id),
                    str(binding_id),
                    str(session.session_id),
                    str(accepted.turn_id),
                    str(accepted.generation_id),
                    now,
                    now,
                    now,
                ),
            )
            await connection.execute(
                "INSERT INTO channel_deliveries(delivery_id, channel_turn_id, binding_id, "
                "connection_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'failed', ?, ?)",
                (
                    str(delivery_id),
                    str(channel_turn_id),
                    str(binding_id),
                    str(connection_id),
                    now,
                    now,
                ),
            )
            for part_id, ordinal, kind, required, state, delivered_at in (
                (text_part_id, 0, "text", 1, "delivered", now),
                (image_part_id, 1, "image", 0, "failed", None),
            ):
                await connection.execute(
                    "INSERT INTO channel_delivery_parts(part_id, delivery_id, ordinal, kind, "
                    "payload_json, required, status, provider_client_id, created_at, "
                    "updated_at, delivered_at) VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?)",
                    (
                        str(part_id),
                        str(delivery_id),
                        ordinal,
                        kind,
                        required,
                        state,
                        str(part_id),
                        now,
                        now,
                        delivered_at,
                    ),
                )
            await connection.execute(
                "UPDATE playback_segments SET text = 'private audio text', "
                "state = 'completed', played_pts_ms = 100 "
                "WHERE generation_id = ? AND segment_index = 0",
                (str(accepted.generation_id),),
            )
        detail = await container.interaction_diagnostics.read_interaction(
            session.session_id,
            accepted.generation_id,
            visible_namespaces=await container.memory.namespaces_for_session(session.session_id),
        )
        assert detail is not None
        assert detail.summary.generation_state == "completed"
        assert detail.delivery_status == "failed"
        assert [(part.kind, part.status) for part in detail.delivery_parts] == [
            ("text", "delivered"),
            ("image", "failed"),
        ]
        assert (detail.playback_segments[0].state, detail.playback_segments[0].played_pts_ms) == (
            "completed",
            100,
        )
        assert any(segment.state == "queued" for segment in detail.playback_segments[1:])
        assert "private audio text" not in detail.model_dump_json()
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_outbound_receipt_diagnostics_follow_intent_lineage_after_cancellation(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        # This projection fixture controls repository admission itself.
        await container.channel_proactive.stop()
        now = datetime.now(UTC)
        session = await container.sessions.create_session("default")
        connection_id, binding_id = uuid4(), uuid4()
        deliveries = container.external_channel_repository
        proactive = container.channel_proactive_repository
        await deliveries.create_connection(
            ChannelConnectionConfiguration(
                connection_id=connection_id,
                provider_id="qq_napcat",
                name="diagnostic fixture",
                character_id="default",
                principal_scope="local",
                account_key="fixture-account",
                allowed_sender_keys=["fixture-owner"],
            ),
            access_token_hash="fixture-hash",
            created_at=now - timedelta(minutes=10),
        )
        await deliveries.create_binding(
            binding_id=binding_id,
            connection_id=connection_id,
            conversation_key="direct:fixture-owner",
            sender_key="fixture-owner",
            session_id=session.session_id,
            created_at=now - timedelta(minutes=10),
        )
        await deliveries.touch_connection(
            connection_id, status=ChannelConnectionStatus.READY, seen_at=now
        )
        await proactive.update_policy(
            connection_id,
            ChannelProactivePolicyUpdate(
                expected_revision=0,
                policy=ChannelProactivePolicy(
                    enabled=True, idle_minutes=1, quiet_hours_enabled=False
                ),
            ),
            updated_at=now - timedelta(minutes=5),
        )
        anchor_at = now - timedelta(minutes=3)
        await deliveries.create_turn(
            ChannelTurnRecord(
                channel_turn_id=uuid4(),
                connection_id=connection_id,
                binding_id=binding_id,
                external_message_id="diagnostic-anchor",
                content_sha256="a" * 64,
                account_key="fixture-account",
                conversation_key="direct:fixture-owner",
                chat_type=ChannelChatType.DIRECT,
                conversation_label=None,
                sender_key="fixture-owner",
                sender_display_name=None,
                principal_scope="local",
                session_id=session.session_id,
                turn_id=uuid4(),
                generation_id=uuid4(),
                status=ChannelTurnStatus.COMPLETED,
                reply_text=None,
                error=None,
                delivery_id=None,
                delivery_status=None,
                revision=0,
                accepted_at=anchor_at,
                created_at=anchor_at,
                updated_at=anchor_at,
                completed_at=anchor_at,
            )
        )
        reserved = await proactive.reserve_intent(connection_id, as_of=now)
        intent = reserved.intent
        assert reserved.created and intent is not None
        await proactive.claim_generation(intent.request_id, expected_revision=0, claimed_at=now)
        accepted = await container.conversation.submit_proactive(
            session.session_id,
            turn_id=intent.turn_id,
            generation_id=intent.generation_id,
            audio_stream_id=intent.audio_stream_id,
            options=ConversationTurnOptions(
                output_modes=frozenset({"text"}),
                source_context=ConversationSourceContext(
                    provider_id="qq_napcat",
                    connection_id=connection_id,
                    account_key=intent.account_key,
                    principal_scope="local",
                    chat_type="direct",
                    conversation_key=intent.conversation_key,
                    sender_key=intent.sender_key,
                    outbound_intent_id=intent.request_id,
                    source_event_key=intent.source_event_key,
                    policy_revision=intent.policy_revision,
                    route_revision=intent.route_revision,
                ),
            ),
        )
        active = container.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)
        result = await container.conversation_repository.generation_result(accepted.generation_id)
        assert result is not None and result.output_text
        planned = await proactive.create_outbound_text_plan(
            intent.request_id, reply_text=result.output_text, created_at=datetime.now(UTC)
        )
        claim = await deliveries.claim_next_delivery_part(
            ChannelDeliveryPartClaimRequest(delivery_id=planned.plan.delivery_id, lease_id=uuid4()),
            claimed_at=datetime.now(UTC),
        )
        assert claim is not None and claim.part is not None
        await proactive.settle_intent(
            intent.request_id,
            reason="operator_cancelled",
            cancel=True,
            settled_at=datetime.now(UTC),
        )
        await deliveries.reconcile_known_delivery_part_receipt(
            connection_id,
            claim.part.provider_client_id,
            "private-receipt",
            observed_at=datetime.now(UTC),
        )
        detail = await container.interaction_diagnostics.read_interaction(
            session.session_id, accepted.generation_id, visible_namespaces=[]
        )
        assert detail is not None and detail.summary.trigger == "proactive"
        assert detail.delivery_status == "delivered"
        assert [(part.kind, part.status) for part in detail.delivery_parts] == [
            ("text", "delivered")
        ]
        assert detail.playback_segments == []
        assert "private-receipt" not in detail.model_dump_json()
        assert "fixture-account" not in detail.model_dump_json()
        other_session = await container.sessions.create_session("default")
        assert (
            await container.interaction_diagnostics.read_interaction(
                other_session.session_id, accepted.generation_id, visible_namespaces=[]
            )
            is None
        )
        assert not await container._desktop_proactive_session_allowed(session.session_id)
        await deliveries.soft_delete_connection(connection_id, deleted_at=datetime.now(UTC))
        assert not await container._desktop_proactive_session_allowed(session.session_id)
        await container.database.execute(
            "INSERT INTO memory_scope_resets(character_id,user_scope,reset_at) "
            "VALUES('default','local',?)",
            ((datetime.now(UTC) + timedelta(seconds=1)).isoformat(),),
        )
        assert (
            await container.interaction_diagnostics.read_interaction(
                session.session_id, accepted.generation_id, visible_namespaces=[]
            )
            is None
        )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_recalled_candidates_are_distinct_from_selected_and_rechecked_after_forget(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        accepted = await container.conversation.submit_text(session.session_id, "test")
        active = container.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)
        selected_id, candidate_id = uuid4(), uuid4()
        namespace = (await container.memory.namespaces_for_session(session.session_id))[0]
        now = datetime.now(UTC).isoformat()
        for memory_id, state in ((selected_id, "active"), (candidate_id, "tombstoned")):
            await container.database.execute(
                "INSERT INTO memory_records(memory_id, namespace, kind, text, normalized_text, "
                "search_terms, observed_at, confidence, importance, sensitivity, state, "
                "created_at, updated_at) "
                "VALUES (?, ?, 'semantic.preference', 'private body', 'private body', '', "
                "?, 0.9, 0.8, 'private', ?, ?, ?)",
                (str(memory_id), namespace, now, state, now, now),
            )
        await container.event_publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "memory.recalled",
                    "session_id": session.session_id,
                    "turn_id": accepted.turn_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.memory",
                    "privacy": PrivacyLevel.LOCAL,
                    "payload": {
                        "memory_ids": [str(selected_id), str(candidate_id)],
                        "scores": [0.95, 0.6],
                    },
                }
            )
        )
        await container.event_publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "character.prompt_compiled",
                    "session_id": session.session_id,
                    "turn_id": accepted.turn_id,
                    "generation_id": accepted.generation_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.conversation",
                    "privacy": PrivacyLevel.LOCAL,
                    "payload": {"selected_memory_ids": [str(selected_id)]},
                }
            )
        )
        reader = container.interaction_diagnostics
        namespaces = await container.memory.namespaces_for_session(session.session_id)
        detail = await reader.read_interaction(
            session.session_id,
            accepted.generation_id,
            visible_namespaces=namespaces,
        )
        assert detail is not None
        assert detail.selected_memory_ids == [selected_id]
        assert [
            (item.memory_id, item.selected_for_prompt, item.currently_visible)
            for item in detail.memory_candidates
        ] == [
            (selected_id, True, True),
            (candidate_id, False, False),
        ]
        assert "private body" not in detail.model_dump_json()
        await container.database.execute(
            "UPDATE memory_records SET state = 'tombstoned' WHERE memory_id = ?",
            (str(selected_id),),
        )
        after_forget = await reader.read_interaction(
            session.session_id,
            accepted.generation_id,
            visible_namespaces=namespaces,
        )
        assert after_forget is not None
        assert all(item.currently_visible is False for item in after_forget.memory_candidates)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_cancelled_generation_keeps_authoritative_state_and_deduplicated_tool_failure(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        accepted = await container.conversation.submit_text(session.session_id, "test")
        active = container.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)
        run_id, call_id = uuid4(), uuid4()
        started = datetime.now(UTC)
        completed = started + timedelta(milliseconds=250)
        async with container.database.transaction() as connection:
            await connection.execute(
                "UPDATE generations SET state = 'cancelled' WHERE generation_id = ?",
                (str(accepted.generation_id),),
            )
            await connection.execute(
                "INSERT INTO skill_runs(skill_run_id, session_id, skill_id, skill_version, "
                "capability, state, arguments_json, created_at, updated_at, "
                "turn_id, generation_id) "
                "VALUES (?, ?, 'test', '1.0', 'read', 'failed', '{\"secret\":\"hidden\"}', "
                "?, ?, ?, ?)",
                (
                    str(run_id),
                    str(session.session_id),
                    started.isoformat(),
                    completed.isoformat(),
                    str(accepted.turn_id),
                    str(accepted.generation_id),
                ),
            )
            await connection.execute(
                "INSERT INTO skill_tool_calls(tool_call_id, skill_run_id, adapter, method, "
                "request_json, error_json, status, started_at, completed_at) "
                "VALUES (?, ?, 'test', 'read', '{\"secret\":\"hidden\"}', "
                "'{\"secret\":\"hidden\"}', 'failed', ?, ?)",
                (str(call_id), str(run_id), started.isoformat(), completed.isoformat()),
            )
        for _ in range(2):
            await container.event_publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "tool.call_failed",
                        "session_id": session.session_id,
                        "turn_id": accepted.turn_id,
                        "generation_id": accepted.generation_id,
                        "occurred_at": datetime.now(UTC),
                        "source": "runtime.skills",
                        "privacy": PrivacyLevel.LOCAL,
                        "payload": {"status": "failed", "secret": "hidden"},
                    }
                )
            )
        mixed_call_id = uuid4()
        await container.database.execute(
            "INSERT INTO skill_tool_calls(tool_call_id, skill_run_id, adapter, method, "
            "request_json, status, started_at, completed_at) "
            "VALUES (?, ?, 'test', 'read', '{}', 'failed', ?, ?)",
            (
                str(mixed_call_id),
                str(run_id),
                started.replace(tzinfo=None).isoformat(),
                completed.isoformat(),
            ),
        )
        detail = await container.interaction_diagnostics.read_interaction(
            session.session_id,
            accepted.generation_id,
            visible_namespaces=[],
        )
        assert detail is not None
        assert detail.summary.generation_state == "cancelled"
        assert {
            call.tool_call_id: (call.status, call.duration_ms) for call in detail.tool_calls
        } == {call_id: ("failed", 250), mixed_call_id: ("failed", None)}
        assert "hidden" not in detail.model_dump_json()
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_legacy_prompt_event_and_timeline_cursor_are_explicit(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        accepted = await container.conversation.submit_text(session.session_id, "test")
        active = container.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)
        await container.database.execute(
            "UPDATE events SET payload_json = json_remove(payload_json, '$.selected_memory_ids') "
            "WHERE session_id = ? AND event_type = 'character.prompt_compiled'",
            (str(session.session_id),),
        )
        legacy_candidate = uuid4()
        await container.event_publisher.emit(
            GenericCoreEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "memory.recalled",
                    "session_id": session.session_id,
                    "turn_id": accepted.turn_id,
                    "occurred_at": datetime.now(UTC),
                    "source": "runtime.memory",
                    "privacy": PrivacyLevel.LOCAL,
                    "payload": {"memory_ids": [str(legacy_candidate)], "scores": [0.9]},
                }
            )
        )
        for _ in range(201):
            await container.event_publisher.emit(
                GenericCoreEvent.model_validate(
                    {
                        "event_id": uuid4(),
                        "event_type": "tool.call_failed",
                        "session_id": session.session_id,
                        "turn_id": accepted.turn_id,
                        "generation_id": accepted.generation_id,
                        "occurred_at": datetime.now(UTC),
                        "source": "runtime.skills",
                        "privacy": PrivacyLevel.LOCAL,
                        "payload": {"status": "failed", "secret": "never-return"},
                    }
                )
            )
        reader = container.interaction_diagnostics
        first = await reader.read_interaction(
            session.session_id,
            accepted.generation_id,
            visible_namespaces=[],
        )
        assert first is not None
        assert first.selected_memory_ids is None
        assert first.memory_candidates[0].memory_id == legacy_candidate
        assert first.memory_candidates[0].selected_for_prompt is None
        assert first.prompt_identity is not None
        assert len(first.timeline) == 200
        assert first.truncated
        assert first.next_cursor is not None
        assert first.next_cursor == first.timeline[-1].sequence
        second = await reader.read_interaction(
            session.session_id,
            accepted.generation_id,
            visible_namespaces=[],
            after_sequence=first.next_cursor,
        )
        assert second is not None and second.timeline
        assert second.timeline[0].sequence > first.timeline[-1].sequence
        assert len({item.sequence for item in [*first.timeline, *second.timeline]}) == (
            len(first.timeline) + len(second.timeline)
        )
        assert not second.truncated
        assert "never-return" not in first.model_dump_json()
        assert "never-return" not in second.model_dump_json()
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_diagnostic_trace_reads_back_after_runtime_restart(
    runtime_settings: Settings,
) -> None:
    first = RuntimeContainer(runtime_settings)
    await first.start()
    try:
        session = await first.sessions.create_session("default")
        accepted = await first.conversation.submit_text(session.session_id, "test")
        active = first.conversation._active[session.session_id]
        assert active.task is not None
        await asyncio.wait_for(active.task, 5)
        before = await first.interaction_diagnostics.read_interaction(
            session.session_id,
            accepted.generation_id,
            visible_namespaces=await first.memory.namespaces_for_session(session.session_id),
        )
        assert before is not None
    finally:
        await first.stop()
    restarted = RuntimeContainer(runtime_settings)
    await restarted.start()
    try:
        after = await restarted.interaction_diagnostics.read_interaction(
            session.session_id,
            accepted.generation_id,
            visible_namespaces=await restarted.memory.namespaces_for_session(session.session_id),
        )
        assert after is not None
        assert after.summary == before.summary
        assert after.prompt_identity == before.prompt_identity
        assert after.delivery_parts == before.delivery_parts
    finally:
        await restarted.stop()
