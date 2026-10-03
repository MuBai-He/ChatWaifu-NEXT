"""NapCat capabilities; group text still requires explicit trusted local policy."""

from chatwaifu_protocol.channels import (
    ChannelChatType,
    ChannelConnectionConfiguration,
    ChannelMessageKind,
    ChannelProviderCapabilities,
    ChannelProviderRegistration,
)

PROVIDER_ID = "qq_napcat"
VOICE_SKILL_ID = "channel.voice"
NAPCAT_PROVIDER = ChannelProviderRegistration(
    provider_id=PROVIDER_ID,
    version="1.1.0",
    name="QQ (NapCat)",
    description="连接已登录的 NapCat，绑定主人私聊。群聊需确认成员并单独启用，仅回复文字。",
    capabilities=ChannelProviderCapabilities(
        chat_types=[ChannelChatType.DIRECT, ChannelChatType.GROUP],
        supports_proactive_messages=True,
        inbound_message_kinds=[
            ChannelMessageKind.TEXT,
            ChannelMessageKind.IMAGE,
            ChannelMessageKind.AUDIO,
        ],
        outbound_message_kinds=[
            ChannelMessageKind.TEXT,
            ChannelMessageKind.AUDIO,
            ChannelMessageKind.IMAGE,
        ],
    ),
)


def channel_tool_policy(configuration: ChannelConnectionConfiguration, text: str) -> frozenset[str]:
    # Reply medium is chosen by the model. This grants only the current owner
    # reply surface; the executor still checks live scope and generation.
    del text
    if configuration.provider_id == PROVIDER_ID:
        return frozenset({VOICE_SKILL_ID})
    return frozenset()
