"""Tencent iLink wire-contract and trust-boundary tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import (
    WeixinILinkClient,
    WeixinILinkError,
    validated_weixin_base_url,
)
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinAuthorizationState,
    WeixinCredentials,
)


class _OversizedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.chunks_read = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(10):
            self.chunks_read += 1
            yield b"x" * (512 * 1024)

    async def aclose(self) -> None:
        self.closed = True


def _credentials() -> WeixinCredentials:
    return WeixinCredentials(
        bot_token="provider-token",
        bot_id="bot-1",
        user_id="owner-1",
        base_url="https://api.weixin.qq.com/",
        gateway_access_token="g" * 43,
    )


@pytest.mark.asyncio
async def test_qr_authorization_uses_versioned_ilink_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"qrcode": "opaque-qr", "qrcode_img_content": "qr-image-content"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await WeixinILinkClient(http).start_authorization()

    assert result.qrcode == "opaque-qr"
    assert result.qr_code_content == "qr-image-content"
    request = requests[0]
    assert request.url == "https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3"
    assert request.headers["ilink-app-id"] == "bot"
    assert request.headers["ilink-app-clientversion"] == str((2 << 16) | (4 << 8) | 6)
    assert json.loads(request.content) == {"local_token_list": []}


@pytest.mark.asyncio
async def test_authorization_redirect_is_restricted_to_weixin_https_hosts() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["qrcode"] == "opaque qr"
        return httpx.Response(
            200,
            json={"status": "scaned_but_redirect", "redirect_host": "edge.weixin.qq.com"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await WeixinILinkClient(http).poll_authorization(
            qrcode="opaque qr",
            poll_base_url="https://ilinkai.weixin.qq.com/",
        )

    assert result.state is WeixinAuthorizationState.REDIRECT
    assert result.redirect_base_url == "https://edge.weixin.qq.com/"
    for rejected in (
        "http://api.weixin.qq.com/",
        "https://weixin.qq.com.evil.example/",
        "https://user:secret@api.weixin.qq.com/",
        "https://api.weixin.qq.com/path",
        "https://api.weixin.qq.com/?token=secret",
    ):
        with pytest.raises(WeixinILinkError, match="allowed HTTPS domain"):
            validated_weixin_base_url(rejected)


@pytest.mark.asyncio
async def test_updates_preserve_stable_identity_context_and_cursor() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer provider-token"
        body = json.loads(request.content)
        assert body["get_updates_buf"] == "cursor-before"
        assert body["base_info"]["channel_version"] == "2.4.6"
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "get_updates_buf": "cursor-after",
                "msgs": [
                    {
                        "message_id": 987654321,
                        "message_type": 1,
                        "message_state": 2,
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "context_token": "reply-context-token",
                        "create_time_ms": 1_788_000_000_000,
                        "item_list": [{"type": 1, "text_item": {"text": "  你好，宁宁  "}}],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        updates = await WeixinILinkClient(http).get_updates(_credentials(), "cursor-before")

    assert updates.cursor == "cursor-after"
    assert len(updates.messages) == 1
    message = updates.messages[0]
    assert message.external_message_id == "987654321"
    assert message.sender_user_id == "owner-1"
    assert message.text == "你好，宁宁"
    assert message.context_token == "reply-context-token"


@pytest.mark.asyncio
async def test_text_reply_reuses_context_and_caller_stable_client_id() -> None:
    requests: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ret": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider_message_id = await WeixinILinkClient(http).send_text(
            _credentials(),
            recipient_user_id="owner-1",
            context_token="reply-context-token",
            client_id="chatwaifu-stable-delivery",
            text="晚上继续聊 Python 吧。",
        )

    assert provider_message_id == "chatwaifu-stable-delivery"
    message = requests[0]["msg"]
    assert isinstance(message, dict)
    assert message["to_user_id"] == "owner-1"
    assert message["context_token"] == "reply-context-token"
    assert message["client_id"] == "chatwaifu-stable-delivery"


@pytest.mark.asyncio
async def test_inbound_text_without_stable_message_id_fails_closed() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "get_updates_buf": "cursor-after",
                "msgs": [
                    {
                        "message_type": 1,
                        "from_user_id": "owner-1",
                        "to_user_id": "bot-1",
                        "context_token": "reply-context-token",
                        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(WeixinILinkError) as caught:
            await WeixinILinkClient(http).get_updates(_credentials(), "")

    assert caught.value.code == "weixin.message_identity_missing"


@pytest.mark.asyncio
async def test_oversized_response_is_stopped_during_streaming() -> None:
    stream = _OversizedStream()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(WeixinILinkError) as caught:
            await WeixinILinkClient(http).start_authorization()

    assert caught.value.code == "weixin.response_too_large"
    assert stream.chunks_read == 5
    assert stream.closed is True
