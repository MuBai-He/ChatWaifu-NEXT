"""Tencent iLink wire-contract and trust-boundary tests."""

from __future__ import annotations

import json
import logging
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
@pytest.mark.parametrize("operation", ["updates", "text", "typing_config", "notify_start"])
@pytest.mark.parametrize("field", ["ret", "errcode"])
async def test_http_200_session_timeout_is_not_a_successful_provider_response(
    operation: str,
    field: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={field: -14, "errmsg": "private-provider-detail"})
        )
    ) as http:
        client = WeixinILinkClient(http)
        with pytest.raises(WeixinILinkError) as caught:
            if operation == "updates":
                await client.get_updates(_credentials(), "cursor-before")
            elif operation == "text":
                await client.send_text(
                    _credentials(),
                    recipient_user_id="owner-1",
                    context_token="private-context",
                    client_id="stable-client-id",
                    text="private-reply",
                )
            elif operation == "typing_config":
                await client.get_typing_ticket(
                    _credentials(),
                    recipient_user_id="owner-1",
                    context_token="private-context",
                )
            else:
                await client.notify_start(_credentials())
    assert caught.value.code == "weixin.session_expired"
    assert caught.value.retryable is False
    assert "private-provider-detail" not in str(caught.value)
    if operation == "text":
        entry = next(
            json.loads(record.getMessage())
            for record in caplog.records
            if '"event": "weixin.send_response"' in record.getMessage()
        )
        assert entry[field] == -14
        assert "private-provider-detail" not in json.dumps(entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("errcode", ["-14", True, None, 7])
async def test_error_code_without_ret_cannot_be_accepted_as_an_empty_update(
    errcode: object,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"errcode": errcode}))
    ) as http:
        with pytest.raises(WeixinILinkError):
            await WeixinILinkClient(http).get_updates(_credentials(), "cursor-before")


@pytest.mark.asyncio
async def test_zero_error_code_with_optional_ret_remains_compatible() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"errcode": 0}))
    ) as http:
        result = await WeixinILinkClient(http).get_updates(_credentials(), "cursor-before")
    assert result.messages == ()
    assert result.cursor == "cursor-before"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("server_id", "expected_id"),
    [
        (123456, "123456"),
        ("18446744073709551615", "18446744073709551615"),
        (None, "stable-client-id"),
        (True, "stable-client-id"),
        ("private-invalid-id", "stable-client-id"),
        (0, "stable-client-id"),
        (-1, "stable-client-id"),
        (18446744073709551616, "stable-client-id"),
    ],
)
async def test_send_preserves_server_receipt_and_logs_only_safe_metadata(
    server_id: object,
    expected_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"message_id": server_id, "errmsg": "private-provider-detail"},
            )
        )
    ) as http:
        result = await WeixinILinkClient(http).send_text(
            _credentials(),
            recipient_user_id="owner-1",
            context_token="private-context",
            client_id="stable-client-id",
            text="private-reply",
        )
    assert result == expected_id
    entries = [
        json.loads(record.getMessage())
        for record in caplog.records
        if '"event": "weixin.send_response"' in record.getMessage()
    ]
    assert len(entries) == 1
    assert entries[0]["ret_present"] is False
    assert entries[0]["receipt_kind"] == (
        "server_message_id" if expected_id != "stable-client-id" else "client_id"
    )
    for value in (
        "provider-token",
        "owner-1",
        "bot-1",
        "private-context",
        "private-reply",
        "private-provider-detail",
        "private-invalid-id",
    ):
        assert value not in json.dumps(entries)


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


@pytest.mark.asyncio
async def test_typing_ticket_and_status_wire_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getconfig"):
            return httpx.Response(200, json={"ret": 0, "typing_ticket": "private-ticket"})
        return httpx.Response(200, json={"ret": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = WeixinILinkClient(http)
        ticket = await client.get_typing_ticket(
            _credentials(), recipient_user_id="owner-1", context_token="private-context"
        )
        assert ticket == "private-ticket"
        for active in (True, False):
            await client.send_typing(
                _credentials(), recipient_user_id="owner-1", typing_ticket=ticket, active=active
            )
    config = json.loads(requests[0].content)
    assert config["ilink_user_id"] == "owner-1"
    assert config["context_token"] == "private-context"
    assert [json.loads(r.content)["status"] for r in requests[1:]] == [1, 2]
    for request in requests:
        assert request.headers["authorization"] == "Bearer provider-token"
        assert request.extensions["timeout"]["read"] == 2
        assert "base_info" in json.loads(request.content)
    for request in requests[1:]:
        assert request.url.path == "/ilink/bot/sendtyping"
        assert json.loads(request.content)["typing_ticket"] == ticket
        assert "context_token" not in json.loads(request.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"ret": 1}, {"ret": True}, {"ret": "0"}])
async def test_typing_provider_errors_are_normalized_without_echoing_private_data(
    payload: dict[str, object],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={**payload, "errmsg": "private-ticket"})
        )
    ) as http:
        client = WeixinILinkClient(http)
        with pytest.raises(WeixinILinkError, match="typing") as error:
            await client.send_typing(
                _credentials(),
                recipient_user_id="owner-1",
                typing_ticket="private-ticket",
                active=True,
            )
        assert "private-ticket" not in str(error.value)
        with pytest.raises(WeixinILinkError):
            await client.get_typing_ticket(
                _credentials(), recipient_user_id="owner-1", context_token="private-context"
            )
