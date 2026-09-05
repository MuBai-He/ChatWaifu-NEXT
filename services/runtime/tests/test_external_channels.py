"""External Channel Gateway identity, continuity, and delivery acceptance tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelDeliveryAcknowledgement,
    ChannelDeliveryClaimRequest,
    ChannelDeliveryStatus,
    ChannelInboundTextMessage,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTurnReceipt,
    ChannelTurnStatus,
)
from chatwaifu_protocol.session import ConversationState, GenerationState, SessionState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationTurnOptions, GenerationAccepted
from chatwaifu_runtime.external_channels.service import (
    ChannelAuthenticationError,
    ChannelConflictError,
    ChannelDeliveryBusyError,
    ChannelPolicyError,
)


def _configuration(connection_id: UUID) -> ChannelConnectionConfiguration:
    return ChannelConnectionConfiguration(
        connection_id=connection_id,
        provider_id="weixin_ilink",
        name="我的微信",
        character_id="default",
        principal_scope="local",
        account_key="wechat-owner-account",
        allowed_sender_keys=["wechat-owner-sender"],
        presentation_policy=ChannelPresentationPolicy(
            profile=ChannelPresentationProfile.SINGLE_TEXT
        ),
    )


def _message(
    connection_id: UUID,
    *,
    external_message_id: str = "wechat-update-20260831-1",
    text: str = "上午在微信上说, 晚上回家继续聊 Python。",
    chat_type: ChannelChatType = ChannelChatType.DIRECT,
) -> ChannelInboundTextMessage:
    return ChannelInboundTextMessage(
        connection_id=connection_id,
        account_key="wechat-owner-account",
        external_message_id=external_message_id,
        conversation_key="wechat-direct-owner",
        sender_key="wechat-owner-sender",
        principal_scope="local",
        chat_type=chat_type,
        text=text,
        conversation_label="与木白的微信私聊",
        sender_display_name="木白",
        received_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_external_turn_is_idempotent_text_only_and_cross_surface_visible(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        message = _message(connection_id)

        receipt = await container.external_channels.ingest(
            message, access_token=created.access_token
        )
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )

        assert snapshot.status is ChannelTurnStatus.COMPLETED
        assert snapshot.reply_text
        assert snapshot.delivery_id is not None
        assert snapshot.delivery_status is ChannelDeliveryStatus.PENDING
        events = await container.event_store.read_stream(snapshot.session_id, limit=200)
        event_types = {str(item["event_type"]) for item in events}
        assert "assistant.generation_started" in event_types
        assert "assistant.text_delta" not in event_types
        assert not any(event_type.startswith("assistant.audio") for event_type in event_types)
        assert "avatar.cue_emitted" not in event_types

        duplicate = await container.external_channels.ingest(
            message, access_token=created.access_token
        )
        assert duplicate.duplicate is True
        assert duplicate.channel_turn_id == receipt.channel_turn_id
        assert duplicate.generation_id == receipt.generation_id
        with pytest.raises(ChannelConflictError):
            await container.external_channels.ingest(
                _message(connection_id, text="同一个 ID 却换了内容"),
                access_token=created.access_token,
            )

        local_session = await container.sessions.create_session("default")
        history = await container.conversation_repository.recent_history(
            local_session.session_id, uuid4(), limit=16
        )
        sourced = [entry for entry in history if entry.source_context is not None]
        assert [entry.role for entry in sourced] == ["user", "assistant"]
        assert sourced[0].source_context is not None
        assert sourced[0].source_context.provider_id == "weixin_ilink"
        assert sourced[0].source_context.principal_scope == "local"
        assert sourced[0].source_context.conversation_key == "wechat-direct-owner"
        assert sourced[0].source_context.sender_key == "wechat-owner-sender"
        assert sourced[0].source_context.received_at == message.received_at
        assert sourced[0].source_context.conversation_label == "与木白的微信私聊"

        # Current desktop sessions belong to the local owner scope. Even a
        # future/malformed sourced row for another principal must not leak into
        # its bounded cross-session ledger.
        await container.database.execute(
            """
            UPDATE turns
            SET source_context_json = json_set(
                source_context_json, '$.principal_scope', 'other-principal'
            )
            WHERE session_id = ? AND role = 'assistant'
            """,
            (str(snapshot.session_id),),
        )
        scoped_history = await container.conversation_repository.recent_history(
            local_session.session_id, uuid4(), limit=16
        )
        assert [entry.role for entry in scoped_history if entry.source_context is not None] == [
            "user"
        ]

        lease_id = uuid4()
        claimed = await container.external_channels.claim_delivery(
            connection_id,
            snapshot.delivery_id,
            ChannelDeliveryClaimRequest(
                delivery_id=snapshot.delivery_id,
                channel_turn_id=snapshot.channel_turn_id,
                lease_id=lease_id,
            ),
            access_token=created.access_token,
        )
        assert claimed.status is ChannelDeliveryStatus.SENDING
        assert claimed.lease_id == lease_id

        acknowledgement = ChannelDeliveryAcknowledgement(
            delivery_id=snapshot.delivery_id,
            channel_turn_id=snapshot.channel_turn_id,
            lease_id=lease_id,
            status=ChannelDeliveryStatus.DELIVERED,
            provider_message_id="weixin-provider-reply-1",
            acknowledged_at=datetime.now(UTC),
        )
        delivered = await container.external_channels.acknowledge_delivery(
            connection_id,
            acknowledgement.delivery_id,
            acknowledgement,
            access_token=created.access_token,
        )
        assert delivered.status is ChannelDeliveryStatus.DELIVERED
        repeated = await container.external_channels.acknowledge_delivery(
            connection_id,
            acknowledgement.delivery_id,
            acknowledgement,
            access_token=created.access_token,
        )
        assert repeated == delivered
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_delivery_claim_is_exclusive_reclaimable_and_ack_is_lease_bound(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        receipt = await container.external_channels.ingest(
            _message(connection_id, external_message_id="wechat-delivery-lease-1"),
            access_token=created.access_token,
        )
        snapshot = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert snapshot.delivery_id is not None

        claims = [
            ChannelDeliveryClaimRequest(
                delivery_id=snapshot.delivery_id,
                channel_turn_id=snapshot.channel_turn_id,
                lease_id=uuid4(),
                lease_seconds=5,
            )
            for _ in range(2)
        ]
        results = await asyncio.gather(
            *(
                container.external_channels.claim_delivery(
                    connection_id,
                    snapshot.delivery_id,
                    claim,
                    access_token=created.access_token,
                )
                for claim in claims
            ),
            return_exceptions=True,
        )
        winners = [item for item in results if not isinstance(item, BaseException)]
        losers = [item for item in results if isinstance(item, BaseException)]
        assert len(winners) == 1
        assert len(losers) == 1
        assert isinstance(losers[0], ChannelDeliveryBusyError)
        winner = winners[0]
        assert winner.status is ChannelDeliveryStatus.SENDING
        assert winner.lease_id is not None
        assert winner.lease_expires_at is not None

        losing_claim = next(item for item in claims if item.lease_id != winner.lease_id)
        reclaimed = await container.external_channel_repository.claim_delivery(
            losing_claim,
            claimed_at=winner.lease_expires_at + timedelta(milliseconds=1),
        )
        assert reclaimed is not None
        assert reclaimed.status is ChannelDeliveryStatus.SENDING
        assert reclaimed.lease_id == losing_claim.lease_id
        assert reclaimed.attempt == 2

        stale_ack = ChannelDeliveryAcknowledgement(
            delivery_id=snapshot.delivery_id,
            channel_turn_id=snapshot.channel_turn_id,
            lease_id=winner.lease_id,
            status=ChannelDeliveryStatus.DELIVERED,
            acknowledged_at=datetime.now(UTC),
        )
        with pytest.raises(ChannelConflictError, match="lease_id mismatch"):
            await container.external_channels.acknowledge_delivery(
                connection_id,
                snapshot.delivery_id,
                stale_ack,
                access_token=created.access_token,
            )

        current_ack = stale_ack.model_copy(update={"lease_id": losing_claim.lease_id})
        delivered = await container.external_channels.acknowledge_delivery(
            connection_id,
            snapshot.delivery_id,
            current_ack,
            access_token=created.access_token,
        )
        assert delivered.status is ChannelDeliveryStatus.DELIVERED
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_slow_channel_admission_does_not_block_another_connection(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    release = asyncio.Event()
    slow_task: asyncio.Task[ChannelTurnReceipt] | None = None
    try:
        slow_connection = uuid4()
        fast_connection = uuid4()
        slow_created = await container.external_channels.create_connection(
            _configuration(slow_connection)
        )
        fast_created = await container.external_channels.create_connection(
            _configuration(fast_connection).model_copy(
                update={
                    "account_key": "wechat-owner-account-fast",
                    "allowed_sender_keys": ["wechat-owner-sender-fast"],
                }
            )
        )
        entered = asyncio.Event()
        original_submit = container.conversation.submit_text

        async def controlled_submit(
            session_id: UUID,
            text: str,
            *,
            options: ConversationTurnOptions | None = None,
            turn_id: UUID | None = None,
            generation_id: UUID | None = None,
        ) -> GenerationAccepted:
            if text == "slow channel admission":
                entered.set()
                await release.wait()
            return await original_submit(
                session_id,
                text,
                options=options,
                turn_id=turn_id,
                generation_id=generation_id,
            )

        monkeypatch.setattr(container.conversation, "submit_text", controlled_submit)
        slow_task = asyncio.create_task(
            container.external_channels.ingest(
                _message(
                    slow_connection,
                    external_message_id="slow-admission-1",
                    text="slow channel admission",
                ),
                access_token=slow_created.access_token,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)

        fast_message = _message(
            fast_connection,
            external_message_id="fast-admission-1",
            text="fast channel admission",
        ).model_copy(
            update={
                "account_key": "wechat-owner-account-fast",
                "conversation_key": "wechat-direct-owner-fast",
                "sender_key": "wechat-owner-sender-fast",
            }
        )
        fast_receipt = await asyncio.wait_for(
            container.external_channels.ingest(
                fast_message,
                access_token=fast_created.access_token,
            ),
            timeout=2,
        )
        assert fast_receipt.duplicate is False
    finally:
        release.set()
        if slow_task is not None:
            await slow_task
        await container.stop()


@pytest.mark.asyncio
async def test_external_memory_keeps_channel_source_after_recent_ledger_eviction(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        first = _message(
            connection_id,
            external_message_id="wechat-memory-source-1",
            text="请记住我上午通过微信约好晚上继续聊 Python",
        )
        receipt = await container.external_channels.ingest(first, access_token=created.access_token)
        completed = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=5,
        )
        assert completed.status is ChannelTurnStatus.COMPLETED

        # Seven later exchanges produce fourteen sourced turns, pushing the
        # original exchange beyond the bounded twelve-turn cross-surface ledger.
        for index in range(2, 9):
            later = _message(
                connection_id,
                external_message_id=f"wechat-memory-source-{index}",
                text=f"这是后续短消息 {index}",
            )
            later_receipt = await container.external_channels.ingest(
                later, access_token=created.access_token
            )
            later_snapshot = await container.external_channels.wait_for_turn(
                connection_id,
                later_receipt.channel_turn_id,
                access_token=created.access_token,
                wait_seconds=5,
            )
            assert later_snapshot.status is ChannelTurnStatus.COMPLETED

        local_session = await container.sessions.create_session("default")
        recent = await container.conversation_repository.recent_history(
            local_session.session_id, uuid4(), limit=16
        )
        assert not any(entry.role == "user" and entry.text == first.text for entry in recent)

        packet = await container.memory.retrieve_context(
            local_session.session_id,
            uuid4(),
            "default",
            "我上午在哪里约好继续聊 Python？",
        )
        excerpts = (
            packet.pinned_facts
            + packet.recent_episodes
            + packet.relevant_memories
            + packet.open_commitments
            + packet.relationship_context
        )
        recalled = next(item for item in excerpts if "继续聊 Python" in item.text)
        attribution = recalled.channel_attributions[0]
        assert attribution.provider_id == "weixin_ilink"
        assert attribution.account_key == "wechat-owner-account"
        assert attribution.principal_scope == "local"
        assert attribution.chat_type == "direct"
        assert attribution.conversation_key == "wechat-direct-owner"
        assert attribution.sender_key == "wechat-owner-sender"
        assert attribution.received_at == first.received_at
        assert attribution.conversation_label == "与木白的微信私聊"
        assert attribution.sender_display_name == "木白"
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_external_gateway_fails_closed_for_auth_sender_and_group(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        created = await container.external_channels.create_connection(_configuration(connection_id))
        with pytest.raises(ChannelAuthenticationError):
            await container.external_channels.ingest(_message(connection_id), access_token="wrong")

        wrong_sender = _message(connection_id).model_copy(update={"sender_key": "someone-else"})
        with pytest.raises(ChannelPolicyError):
            await container.external_channels.ingest(
                wrong_sender, access_token=created.access_token
            )

        with pytest.raises(ChannelPolicyError):
            await container.external_channels.ingest(
                _message(connection_id, chat_type=ChannelChatType.GROUP),
                access_token=created.access_token,
            )

        future_message = _message(connection_id).model_copy(
            update={"received_at": datetime.now(UTC) + timedelta(minutes=6)}
        )
        with pytest.raises(ChannelPolicyError, match="future"):
            await container.external_channels.ingest(
                future_message,
                access_token=created.access_token,
            )

        missing_account = _configuration(uuid4()).model_copy(update={"account_key": None})
        with pytest.raises(ChannelPolicyError, match="stable account_key"):
            await container.external_channels.create_connection(missing_account)

        multiple_senders = _configuration(uuid4()).model_copy(
            update={"allowed_sender_keys": ["owner-a", "owner-b"]}
        )
        with pytest.raises(ChannelPolicyError, match="exactly one"):
            await container.external_channels.create_connection(multiple_senders)
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_channel_route_identity_is_immutable_after_creation(
    runtime_settings: Settings,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        configuration = _configuration(connection_id)
        created = await container.external_channels.create_connection(configuration)
        snapshot = created.snapshot

        for changed, expected in (
            (configuration.model_copy(update={"provider_id": "another_provider"}), "provider_id"),
            (configuration.model_copy(update={"account_key": "another-account"}), "account_key"),
            (
                configuration.model_copy(update={"principal_scope": "another-scope"}),
                "principal_scope",
            ),
            (
                configuration.model_copy(update={"character_id": "another-character"}),
                "character_id",
            ),
        ):
            with pytest.raises(ChannelConflictError, match=expected):
                await container.external_channels.update_connection(
                    changed,
                    expected_revision=snapshot.revision,
                    rotate_access_token=False,
                )
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_cancel_during_conversation_admission_cleans_generation_and_session(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        external_message_id = "cancel-during-admission"
        created = await container.external_channels.create_connection(_configuration(connection_id))
        entered = asyncio.Event()

        async def blocked_retrieval(*args: object, **kwargs: object) -> object:
            del args, kwargs
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(container.memory, "retrieve_context", blocked_retrieval)
        ingest_task = asyncio.create_task(
            container.external_channels.ingest(
                _message(connection_id, external_message_id=external_message_id),
                access_token=created.access_token,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        ingest_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ingest_task

        turn = await container.external_channel_repository.find_turn_by_external_message(
            connection_id, external_message_id
        )
        assert turn is not None
        assert turn.status is ChannelTurnStatus.CANCELLED
        generation = await container.conversation_repository.generation_result(turn.generation_id)
        assert generation is not None
        assert generation.state is GenerationState.CANCELLED
        session = await container.sessions.get_session(turn.session_id)
        assert session is not None
        assert session.state is SessionState.READY
        assert session.conversation_state is ConversationState.IDLE
        assert container.conversation.active_generation_id(turn.session_id) is None
    finally:
        await container.stop()


@pytest.mark.asyncio
async def test_restart_recovers_committed_generation_before_channel_sync(
    runtime_settings: Settings,
) -> None:
    first = RuntimeContainer(runtime_settings)
    await first.start()
    connection_id = uuid4()
    created = await first.external_channels.create_connection(_configuration(connection_id))
    receipt = await first.external_channels.ingest(
        _message(connection_id, external_message_id="restart-window-message"),
        access_token=created.access_token,
    )
    completed = await first.external_channels.wait_for_turn(
        connection_id,
        receipt.channel_turn_id,
        access_token=created.access_token,
        wait_seconds=5,
    )
    assert completed.status is ChannelTurnStatus.COMPLETED
    assert completed.reply_text
    assert completed.delivery_id is not None

    # Recreate the crash window: generation output is durable, while the
    # channel projection still says processing and has no delivery row.
    await first.database.execute(
        "DELETE FROM channel_deliveries WHERE channel_turn_id = ?",
        (str(receipt.channel_turn_id),),
    )
    await first.database.execute(
        """
        UPDATE channel_turns
        SET status = 'processing', reply_text = NULL, delivery_id = NULL,
            completed_at = NULL
        WHERE channel_turn_id = ?
        """,
        (str(receipt.channel_turn_id),),
    )
    await first.stop()

    restarted = RuntimeContainer(runtime_settings)
    await restarted.start()
    try:
        recovered = await restarted.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=created.access_token,
            wait_seconds=0,
        )
        assert recovered.status is ChannelTurnStatus.COMPLETED
        assert recovered.reply_text == completed.reply_text
        assert recovered.delivery_id is not None
        assert recovered.delivery_status is ChannelDeliveryStatus.PENDING
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_update_channel_connection_retains_cadence_and_profile_with_false_default(
    runtime_settings: Settings,
) -> None:
    from chatwaifu_runtime.main import create_app
    from httpx import ASGITransport, AsyncClient

    app = create_app(runtime_settings)
    container = app.state.container
    await container.start()
    try:
        connection_id = uuid4()
        initial_policy = ChannelPresentationPolicy(
            profile=ChannelPresentationProfile.INSTANT_MESSAGE,
            cadence_enabled=True,
            min_delay_ms=800,
            max_delay_ms=3000,
            total_cadence_delay_ceiling_ms=6000,
        )
        assert initial_policy.stickers_enabled is False

        configuration = ChannelConnectionConfiguration(
            connection_id=connection_id,
            provider_id="weixin_ilink",
            name="我的微信",
            character_id="default",
            principal_scope="local",
            account_key="test-owner-account",
            allowed_sender_keys=["test-sender"],
            enabled=True,
            presentation_policy=initial_policy,
        )
        created = await container.external_channels.create_connection(configuration)
        snapshot = created.snapshot
        assert snapshot.configuration.presentation_policy is not None
        assert snapshot.configuration.presentation_policy.stickers_enabled is False
        assert (
            snapshot.configuration.presentation_policy.profile
            == ChannelPresentationProfile.INSTANT_MESSAGE
        )
        assert snapshot.configuration.presentation_policy.cadence_enabled is True
        assert snapshot.configuration.presentation_policy.min_delay_ms == 800

        token = container.capability_token
        headers = {"Authorization": f"Bearer {token}"}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            updated_policy = initial_policy.model_copy(update={"stickers_enabled": True})
            update_payload = configuration.model_copy(
                update={"presentation_policy": updated_policy}
            ).model_dump(mode="json")

            put_res = await client.put(
                f"/v1/channel-connections/{connection_id}?expected_revision={snapshot.revision}",
                json=update_payload,
                headers=headers,
            )
            assert put_res.status_code == 200
            data = put_res.json()
            persisted_policy = data["connection"]["configuration"]["presentation_policy"]
            assert persisted_policy["stickers_enabled"] is True
            assert persisted_policy["profile"] == "instant_message"
            assert persisted_policy["cadence_enabled"] is True
            assert persisted_policy["min_delay_ms"] == 800
            assert persisted_policy["max_delay_ms"] == 3000
            assert persisted_policy["total_cadence_delay_ceiling_ms"] == 6000

            get_res = await client.get(
                f"/v1/channel-connections/{connection_id}",
                headers=headers,
            )
            assert get_res.status_code == 200
            get_policy = get_res.json()["configuration"]["presentation_policy"]
            assert get_policy["stickers_enabled"] is True
            assert get_policy["profile"] == "instant_message"
            assert get_policy["cadence_enabled"] is True
            assert get_policy["min_delay_ms"] == 800
            assert get_policy["max_delay_ms"] == 3000
            assert get_policy["total_cadence_delay_ceiling_ms"] == 6000
    finally:
        await container.stop()
