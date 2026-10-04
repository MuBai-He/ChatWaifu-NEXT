"""Message-channel style reaches the model without changing local conversation contracts."""

# pyright: reportPrivateUsage=false

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelInboundTextMessage,
    ChannelPresentationPolicy,
    ChannelPresentationProfile,
    ChannelTextDeliveryPartPayload,
    ChannelTurnStatus,
)
from chatwaifu_protocol.character import (
    PROMPT_TEMPLATE_VERSION,
    AffectState,
    CharacterKernelSnapshot,
    CharacterPromptCompiledPayload,
    RelationshipState,
    ResponsePlan,
)
from chatwaifu_protocol.memory import MemoryContextPacket
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.character_kernel.prompt import PromptCompilation
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.conversation.models import ConversationOrigin, ConversationSourceContext
from chatwaifu_runtime.external_channels.service import WEIXIN_ILINK_PROVIDER
from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.model_config import ModelRoleConfig


class _Answer:
    kind = "demo"
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request)
        yield LlmTextDelta("嗯，在呀。\n\n怎么啦？")
        yield LlmResponseCompleted("stop")


@pytest.mark.parametrize("provider_id", ["weixin_ilink", "qq_napcat", "future_chat"])
@pytest.mark.parametrize("single_text", [False, True])
async def test_all_external_providers_use_short_style_with_optional_single_delivery(
    runtime_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    single_text: bool,
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        provider = _Answer()

        def create(_configuration: ModelRoleConfig) -> LlmProvider:
            return provider

        monkeypatch.setattr(container.model_configurations, "create_chat_provider", create)
        if provider_id == "future_chat":
            container.external_channels._providers[provider_id] = WEIXIN_ILINK_PROVIDER.model_copy(
                update={"provider_id": provider_id}
            )
        policy = (
            ChannelPresentationPolicy(profile=ChannelPresentationProfile.SINGLE_TEXT)
            if single_text
            else None
        )
        connection_id = uuid4()
        connection = await container.external_channels.create_connection(
            ChannelConnectionConfiguration(
                connection_id=connection_id,
                provider_id=provider_id,
                name="local message fixture",
                character_id="default",
                principal_scope="local",
                account_key="fixture-account",
                allowed_sender_keys=["fixture-sender"],
                presentation_policy=policy,
            )
        )
        receipt = await container.external_channels.ingest(
            ChannelInboundTextMessage(
                connection_id=connection_id,
                account_key="fixture-account",
                external_message_id="fixture-1",
                conversation_key="fixture-direct",
                sender_key="fixture-sender",
                principal_scope="local",
                chat_type=ChannelChatType.DIRECT,
                text="在吗？",
                received_at=datetime.now(UTC),
            ),
            access_token=connection.access_token,
        )
        result = await container.external_channels.wait_for_turn(
            connection_id,
            receipt.channel_turn_id,
            access_token=connection.access_token,
            wait_seconds=5,
        )
        assert result.status is ChannelTurnStatus.COMPLETED and result.delivery_id is not None
        assert len(provider.requests) == 1
        assert "the WHOLE reply is usually 5-30 Chinese characters" in (
            provider.requests[0].system_prompt
        )
        assert "a short opening reaction with a longer explanation" in (
            provider.requests[0].system_prompt
        )
        prompt_events = await container.event_store.read_stream(receipt.session_id)
        compiled = next(
            event for event in prompt_events if event["event_type"] == "character.prompt_compiled"
        )
        identity = CharacterPromptCompiledPayload.model_validate(compiled["payload"]).identity
        assert identity is not None
        assert identity.prompt_template_version == (f"{PROMPT_TEMPLATE_VERSION}.messaging2")
        plan = await container.external_channel_repository.get_delivery_plan(result.delivery_id)
        assert plan is not None and plan.part_count == (1 if single_text else 2)
        text = "".join(
            part.payload.text
            for part in plan.parts
            if isinstance(part.payload, ChannelTextDeliveryPartPayload)
        )
        assert text == result.reply_text == "嗯，在呀。\n\n怎么啦？"
    finally:
        await container.stop()


@pytest.mark.parametrize("origin", ["local_text", "voice", "proactive"])
async def test_local_origins_keep_standard_contract_despite_message_history_or_profile(
    runtime_settings: Settings, origin: ConversationOrigin
) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        character = container.characters.get("default")
        assert character is not None
        now = datetime(2026, 10, 5, tzinfo=UTC)
        source = ConversationSourceContext(
            provider_id="qq_napcat",
            connection_id=uuid4(),
            principal_scope="local",
            account_key="fixture-account",
            chat_type="direct",
            conversation_key="old-message-channel",
            sender_key="fixture-sender",
        )

        async def compile(profile: str | None) -> PromptCompilation:
            return await container.prompt_compiler.compile(
                character=character,
                kernel=CharacterKernelSnapshot(
                    character_id="default",
                    user_scope="local",
                    revision=1,
                    affect=AffectState(updated_at=now),
                    relationship=RelationshipState(updated_at=now),
                ),
                plan=ResponsePlan(
                    intent="answer", tone="gentle", expression="neutral", rationale="fixture"
                ),
                memory=MemoryContextPacket(token_budget_used=0),
                history=(),
                user_text="请详细解释这段代码。",
                source_context=source,
                conversation_origin=origin,
                presentation_profile=profile,
                as_of=now,
            )

        standard = await compile(None)
        with_profile = await compile("instant_message")
        assert standard.system_prompt == with_profile.system_prompt
        assert "usually 5-30 Chinese characters" not in standard.system_prompt
        assert "Detailed, technical, code and question-list requests" in standard.system_prompt
    finally:
        await container.stop()
