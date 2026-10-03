"""NapCat's deliberately narrow first CW2 capability profile."""

from chatwaifu_protocol.channels import (
    ChannelConnectionConfiguration,
    ChannelMessageKind,
    ChannelProviderCapabilities,
    ChannelProviderRegistration,
)

PROVIDER_ID = "qq_napcat"
VOICE_SKILL_ID = "channel.voice"
NAPCAT_PROVIDER = ChannelProviderRegistration(
    provider_id=PROVIDER_ID,
    version="1.0.0",
    name="QQ (NapCat)",
    description="连接已登录的 NapCat，通过一次性配对码绑定主人私聊。",
    capabilities=ChannelProviderCapabilities(
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
