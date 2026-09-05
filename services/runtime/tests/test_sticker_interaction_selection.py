# pyright: reportPrivateUsage=false
"""Real channel delivery regressions for bounded interaction-based sticker reuse."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelDeliveryPartAcknowledgement,
    ChannelDeliveryStatus,
    ChannelImageDeliveryPartPayload,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTurnReceipt,
)
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinInboundText,
    WeixinUpdates,
)
from chatwaifu_runtime.external_channels.credentials import InMemoryChannelCredentialStore
from chatwaifu_runtime.external_channels.management import ChannelManagementService
from chatwaifu_runtime.external_channels.models import (
    ChannelDeliveryPlanRecord,
    DeliveryTransitionResult,
)
from test_channel_management import _configuration, _credentials
from test_learned_sticker_delivery import (
    _LEARNED_PNG_BYTES,
    _RecordedImageTransport,
    _seed_learned_sticker,
)


@pytest.mark.asyncio
async def test_owner_sequence(runtime_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    container = RuntimeContainer(runtime_settings)
    store = InMemoryChannelCredentialStore()
    transport = _RecordedImageTransport()
    management = ChannelManagementService(
        container.external_channels,
        container.external_channel_repository,
        store,
        transport,
        sticker_catalog=container.sticker_catalog,
        sticker_library=container.sticker_library,
    )
    container.channel_management = management
    terminals: asyncio.Queue[ChannelDeliveryPlanRecord] = asyncio.Queue()
    original = container.external_channel_repository.acknowledge_delivery_part

    async def observe(
        acknowledgement: ChannelDeliveryPartAcknowledgement, *, updated_at: datetime
    ) -> DeliveryTransitionResult:
        result = await original(acknowledgement, updated_at=updated_at)
        if result.plan.status is ChannelDeliveryStatus.DELIVERED:
            terminals.put_nowait(result.plan)
        return result

    monkeypatch.setattr(container.external_channel_repository, "acknowledge_delivery_part", observe)
    await container.start()
    try:
        learned_id, sha, _, _ = await _seed_learned_sticker(
            container.database,
            container,
            principal_scope="local",
            character_id="default",
            expression="happy",
            label="捏脸互动卡通插画",
            description="一个长发女孩伸手捏另一个短发猫耳女孩的脸颊，两人表情可爱生动。",
        )
        connection_id = uuid4()
        config = _configuration(connection_id).model_copy(
            update={
                "presentation_policy": ChannelPresentationPolicy(
                    profile=ChannelPresentationProfile.INSTANT_MESSAGE,
                    stickers_enabled=True,
                    cadence_enabled=False,
                )
            }
        )
        created = await container.external_channels.create_connection(config, access_token="g" * 43)
        await store.set(f"weixin_ilink:{connection_id}", _credentials("g" * 43).to_json())
        await management.connection_configuration_changed(created.snapshot)
        for index, (text, expected) in enumerate(
            [
                ("捏捏我", True),
                ("球球你了嘛", True),
                ("停一下，喜欢你也别发了", False),
                ("球球你了嘛", False),
                ("捏捏我", True),
                ("晚饭吃什么", False),
                ("球球你了嘛", False),
                ("不要捏我的脸", False),
                ("捏脸工具怎么用", False),
                ("捏捏我", True),
            ]
        ):
            key = f"owner-repro-{index}"
            before = len(transport.images)
            await transport.updates.put(
                WeixinUpdates(
                    cursor=key,
                    messages=(
                        WeixinInboundText(
                            external_message_id=key,
                            sender_user_id="owner-1",
                            recipient_bot_id="bot-1",
                            text=text,
                            context_token=key,
                            received_at=datetime.now(UTC),
                        ),
                    ),
                )
            )
            plan = await asyncio.wait_for(terminals.get(), timeout=5)
            images = [
                p.payload
                for p in plan.parts
                if isinstance(p.payload, ChannelImageDeliveryPartPayload)
            ]
            assert len(images) == int(expected), (
                text,
                [(p.payload.kind, p.payload.model_dump()) for p in plan.parts],
            )
            assert len(transport.images) - before == int(expected), text
            if expected:
                assert images[0].sticker_id == learned_id
                assert images[0].sha256 == sha
                assert transport.images[-1][3] == _LEARNED_PNG_BYTES
        # A second route/session cannot inherit the first conversation's interaction.
        other_id = uuid4()
        other_config = config.model_copy(update={"connection_id": other_id})
        await container.external_channels.create_connection(other_config, access_token="h" * 43)
        await store.set(f"weixin_ilink:{other_id}", _credentials("h" * 43).to_json())
        # Drive this second connection directly, using its independent binding/session.
        from chatwaifu_protocol.channels import ChannelChatType, ChannelInboundTextMessage

        receipt = await container.external_channels.ingest(
            ChannelInboundTextMessage(
                connection_id=other_id,
                received_at=datetime.now(UTC),
                external_message_id="other-plea",
                account_key="bot-1",
                conversation_key="owner-1",
                sender_key="owner-1",
                principal_scope="local",
                chat_type=ChannelChatType.DIRECT,
                text="球球你了嘛",
            ),
            access_token="h" * 43,
        )
        terminal = await container.external_channels.wait_for_turn(
            other_id, receipt.channel_turn_id, access_token="h" * 43, wait_seconds=5
        )
        context = await container.conversation_repository.generation_user_input_context(
            terminal.generation_id
        )
        assert context is not None and context.previous_user_text is None
        assert terminal.delivery_id is not None
        plan = await container.external_channel_repository.get_delivery_plan(terminal.delivery_id)
        assert plan is not None and all(
            not isinstance(p.payload, ChannelImageDeliveryPartPayload) for p in plan.parts
        )
    finally:
        await container.stop()


@pytest.mark.parametrize(
    ("text", "previous", "interaction", "blocked"),
    [
        ("捏捏我", None, "face_pinch", False),
        ("可以捏一下我的脸吗？", None, "face_pinch", False),
        ("球球你了嘛", "捏捏我", "face_pinch", False),
        ("求求你了吧", "可以捏一下我的脸吗？", "face_pinch", False),
        ("捏捏我，别停", None, "face_pinch", False),
        ("不要停，捏捏我", None, "face_pinch", False),
        ("捏捏我，停一下", None, None, True),
        ("喜欢你，别发了", None, None, True),
        ("住手，喜欢你也别捏了", None, None, True),
        ("停", "捏捏我", None, True),
        ("别停，先停手", "捏捏我", None, True),
        ("不要捏我的脸", None, None, True),
        ("别老捏我的脸", None, None, True),
        ("不要了", "捏捏我", None, True),
        ("球球你了嘛", "停一下", None, True),
        ("球球你了嘛", "晚饭吃什么", None, True),
        ("球球你了嘛", "球球你了嘛", None, True),
        ("球球你了嘛", None, None, True),
        ("捏脸工具怎么用", None, None, True),
        ("为什么大家喜欢捏脸", None, None, True),
        ("普通回答", "捏捏我", None, False),
    ],
)
def test_selection_context_rules(
    text: str, previous: str | None, interaction: str | None, blocked: bool
) -> None:
    from chatwaifu_runtime.conversation.models import ConversationUserInputContext
    from chatwaifu_runtime.sticker_library.selection import selection_hints

    hints = selection_hints(ConversationUserInputContext(text, previous))
    assert hints.interaction == interaction
    assert hints.blocked is blocked


def test_interaction_content_requires_related_non_refusal_asset() -> None:
    from chatwaifu_runtime.conversation.models import ConversationUserInputContext
    from chatwaifu_runtime.sticker_library.selection import matches_interaction, selection_hints

    hints = selection_hints(ConversationUserInputContext("捏捏我"))
    assert matches_interaction("捏脸互动", "伸手捏女孩的脸颊", hints)
    assert not matches_interaction("开心小猫", "一只笑着的小猫", hints)
    assert not matches_interaction("不要捏脸", "生气地拒绝", hints)
    assert selection_hints(None).blocked


async def test_input_context_is_anchored_scoped_and_time_bounded(
    runtime_settings: Settings,
) -> None:
    from chatwaifu_protocol.channels import ChannelInboundTextMessage

    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        connection_id = uuid4()
        await container.external_channels.create_connection(
            _configuration(connection_id), access_token="g" * 43
        )
        receipts: list[ChannelTurnReceipt] = []
        for text in ("捏捏我", "球球你了嘛", "之后的话题"):
            receipt = await container.external_channels.ingest(
                ChannelInboundTextMessage(
                    connection_id=connection_id,
                    external_message_id=str(uuid4()),
                    account_key="bot-1",
                    conversation_key="owner-1",
                    sender_key="owner-1",
                    principal_scope="local",
                    text=text,
                    received_at=datetime.now(UTC),
                ),
                access_token="g" * 43,
            )
            await container.external_channels.wait_for_turn(
                connection_id, receipt.channel_turn_id, wait_seconds=5
            )
            receipts.append(receipt)
        repository = container.conversation_repository
        context = await repository.generation_user_input_context(receipts[1].generation_id)
        assert context is not None
        assert context.user_text == "球球你了嘛"
        assert context.previous_user_text == "捏捏我"  # Later input cannot change the anchor.
        assert await repository.generation_user_input_context(uuid4()) is None

        # Scope and peer must agree; do not read through a foreign predecessor.
        for column in ("principal_scope", "sender_key"):
            async with container.database.transaction() as conn:
                cursor = await conn.execute(
                    f"SELECT {column} FROM channel_turns WHERE generation_id = ?",
                    (str(receipts[0].generation_id),),
                )
                row = await cursor.fetchone()
                assert row is not None
                original_value = row[0]
                await cursor.close()
                await conn.execute(
                    f"UPDATE channel_turns SET {column} = ? WHERE generation_id = ?",
                    (str(uuid4()), str(receipts[0].generation_id)),
                )
            fenced = await repository.generation_user_input_context(receipts[1].generation_id)
            assert fenced is not None and fenced.previous_user_text is None, column
            async with container.database.transaction() as conn:
                await conn.execute(
                    f"UPDATE channel_turns SET {column} = ? WHERE generation_id = ?",
                    (original_value, str(receipts[0].generation_id)),
                )
        async with container.database.transaction() as conn:
            await conn.execute(
                "UPDATE turns SET committed_at = '2000-01-01T00:00:00+00:00' WHERE turn_id = ?",
                (str(receipts[0].turn_id),),
            )
        expired = await repository.generation_user_input_context(receipts[1].generation_id)
        assert expired is not None and expired.previous_user_text is None
        async with container.database.transaction() as conn:
            await conn.execute(
                "UPDATE turns SET committed_text = ? WHERE turn_id = ?",
                ("长" * 2001, str(receipts[1].turn_id)),
            )
        assert await repository.generation_user_input_context(receipts[1].generation_id) is None
    finally:
        await container.stop()


async def test_no_emotional_fallback_for_missing_interaction_asset(
    runtime_settings: Settings,
) -> None:
    from chatwaifu_protocol.character import ResponsePlan
    from chatwaifu_runtime.conversation.models import ConversationUserInputContext
    from chatwaifu_runtime.sticker_library.selection import selection_hints

    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        await _seed_learned_sticker(
            container.database,
            container,
            principal_scope="local",
            character_id="default",
            expression="happy",
            label="开心小猫",
            description="笑着挥手的小猫",
        )
        hints = selection_hints(ConversationUserInputContext("捏捏我"))
        plan = ResponsePlan(
            intent="reassure",
            expression="happy",
            tone="gentle",
            response_length="short",
            rationale="test",
        )
        assert await container.sticker_library.match("local", "default", plan, hints=hints) is None
        # Ordinary happy answers also remain text-only without a grounded action.
        ordinary = plan.model_copy(update={"intent": "answer"})
        assert await container.sticker_library.match("local", "default", ordinary) is None
    finally:
        await container.stop()
