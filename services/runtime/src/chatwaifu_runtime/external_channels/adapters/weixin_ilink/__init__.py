"""Native Tencent iLink adapter for owner-only WeChat text chat."""

from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import WeixinILinkClient
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinAuthorizationPoll,
    WeixinAuthorizationStart,
    WeixinCredentials,
    WeixinInboundText,
    WeixinUpdates,
)

__all__ = [
    "WeixinAuthorizationPoll",
    "WeixinAuthorizationStart",
    "WeixinCredentials",
    "WeixinILinkClient",
    "WeixinInboundText",
    "WeixinUpdates",
]
