# pyright: reportPrivateUsage=false
"""Deterministic tests for WeChat iLink inbound image transport and decryption."""

from __future__ import annotations

import asyncio
import base64
import io
import secrets
from collections.abc import AsyncIterator

import httpx
import pytest
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import (
    WeixinILinkClient,
    WeixinILinkError,
    _parse_inbound_text,
)
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.image import (
    decrypt_aes_128_ecb,
    encrypt_aes_128_ecb,
    resolve_cdn_download_url,
    resolve_image_aes_key,
    sniff_image_mime_type,
    validate_image,
)
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinInboundImage,
    WeixinInboundText,
)
from PIL import Image


def _make_png_bytes(
    width: int = 2,
    height: int = 2,
    color: tuple[int, int, int] = (255, 0, 0),
) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=color)
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_bytes(
    width: int = 2,
    height: int = 2,
    color: tuple[int, int, int] = (0, 255, 0),
) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=color)
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_animated_gif_bytes() -> bytes:
    buf = io.BytesIO()
    f1 = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    f2 = Image.new("RGBA", (4, 4), (0, 255, 0, 255))
    f1.save(buf, format="GIF", save_all=True, append_images=[f2])
    return buf.getvalue()


def _make_raw_inbound_msg(
    items: list[dict[str, object]],
    *,
    bot_id: str = "bot_123",
    user_id: str = "user_456",
    message_id: int = 1001,
    message_type: int = 1,
    message_state: int = 2,
    group_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "message_id": message_id,
        "from_user_id": user_id,
        "to_user_id": bot_id,
        "message_type": message_type,
        "message_state": message_state,
        "context_token": "ctx_token_abc",
        "create_time_ms": 1700000000000,
        "item_list": items,
    }
    if group_id is not None:
        payload["group_id"] = group_id
    return payload


# --- 1. Parser tests ---


def test_parser_image_only():
    raw = _make_raw_inbound_msg(
        [
            {
                "type": 2,
                "image_item": {
                    "media": {
                        "encrypt_query_param": "query_param_123",
                        "aes_key": "k" * 24,
                    }
                },
            }
        ]
    )
    parsed = _parse_inbound_text(raw, "bot_123")
    assert isinstance(parsed, WeixinInboundText)
    assert parsed.text == "[图片]"
    assert parsed.image is not None
    assert parsed.image.encrypt_query_param == "query_param_123"
    assert parsed.image.aes_key == "k" * 24
    assert parsed.image.invalid_reason is None


def test_parser_caption_and_image():
    raw = _make_raw_inbound_msg(
        [
            {"type": 1, "text_item": {"text": "look at this photo"}},
            {
                "type": 2,
                "image_item": {
                    "full_url": (
                        "https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=xyz"
                    ),
                    "aeskey": "0123456789abcdef0123456789abcdef",
                },
            },
        ]
    )
    parsed = _parse_inbound_text(raw, "bot_123")
    assert isinstance(parsed, WeixinInboundText)
    assert parsed.text == "look at this photo"
    assert parsed.image is not None
    assert parsed.image.full_url == (
        "https://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=xyz"
    )
    assert parsed.image.aeskey == "0123456789abcdef0123456789abcdef"


def test_parser_text_only():
    raw = _make_raw_inbound_msg([{"type": 1, "text_item": {"text": "plain text only"}}])
    parsed = _parse_inbound_text(raw, "bot_123")
    assert isinstance(parsed, WeixinInboundText)
    assert parsed.text == "plain text only"
    assert parsed.image is None


def test_parser_extra_images_marked_unavailable():
    raw = _make_raw_inbound_msg(
        [
            {"type": 1, "text_item": {"text": "two pictures"}},
            {"type": 2, "image_item": {"media": {"encrypt_query_param": "p1"}}},
            {"type": 2, "image_item": {"media": {"encrypt_query_param": "p2"}}},
        ]
    )
    parsed = _parse_inbound_text(raw, "bot_123")
    assert isinstance(parsed, WeixinInboundText)
    assert parsed.text == "two pictures"
    assert parsed.image is not None
    assert parsed.image.invalid_reason == "multiple_images"

    # Image-only multiple images gets placeholder text
    raw2 = _make_raw_inbound_msg(
        [
            {"type": 2, "image_item": {"media": {"encrypt_query_param": "p1"}}},
            {"type": 2, "image_item": {"media": {"encrypt_query_param": "p2"}}},
        ]
    )
    parsed2 = _parse_inbound_text(raw2, "bot_123")
    assert isinstance(parsed2, WeixinInboundText)
    assert parsed2.text == "[图片]"
    assert parsed2.image is not None
    assert parsed2.image.invalid_reason == "multiple_images"


def test_parser_malformed_image():
    raw = _make_raw_inbound_msg([{"type": 2, "image_item": {}}])
    parsed = _parse_inbound_text(raw, "bot_123")
    assert isinstance(parsed, WeixinInboundText)
    assert parsed.text == "[图片]"
    assert parsed.image is not None
    assert parsed.image.invalid_reason == "malformed_image"


def test_parser_routing_and_validation():
    # Wrong recipient
    raw = _make_raw_inbound_msg(
        [{"type": 1, "text_item": {"text": "hi"}}],
        bot_id="different_bot",
    )
    with pytest.raises(WeixinILinkError) as exc:
        _parse_inbound_text(raw, "bot_123")
    assert exc.value.code == "weixin.message_context_invalid"

    # Group message ignored
    raw_group = _make_raw_inbound_msg(
        [{"type": 1, "text_item": {"text": "hi"}}],
        group_id="group_999",
    )
    assert _parse_inbound_text(raw_group, "bot_123") is None

    # Message type not 1
    raw_type2 = _make_raw_inbound_msg(
        [{"type": 1, "text_item": {"text": "hi"}}],
        message_type=2,
    )
    assert _parse_inbound_text(raw_type2, "bot_123") is None

    # Invalid message ID
    raw_bad_id = _make_raw_inbound_msg(
        [{"type": 1, "text_item": {"text": "hi"}}],
        message_id=0,
    )
    with pytest.raises(WeixinILinkError) as exc2:
        _parse_inbound_text(raw_bad_id, "bot_123")
    assert exc2.value.code == "weixin.message_identity_missing"


def test_inbound_image_model_repr_secrets():
    img = WeixinInboundImage(
        encrypt_query_param="private_download_param",
        full_url="https://novac2c.cdn.weixin.qq.com/c2c/download?param=1",
        aes_key="super_secret_key_base64",
        aeskey="super_secret_hex",
    )
    repr_str = repr(img)
    assert "super_secret_key_base64" not in repr_str
    assert "super_secret_hex" not in repr_str
    assert "https://novac2c" not in repr_str
    assert "param=1" not in repr_str
    assert "private_download_param" not in repr_str


# --- 2. Direct cryptographic and validation unit tests ---


def test_decrypt_aes_128_ecb_direct():
    key = secrets.token_bytes(16)
    data = b"hello world 1234"
    ciphertext = encrypt_aes_128_ecb(data, key)
    decrypted = decrypt_aes_128_ecb(ciphertext, key)
    assert decrypted == data

    with pytest.raises(WeixinILinkError) as exc_len:
        decrypt_aes_128_ecb(b"short", key)
    assert exc_len.value.code == "weixin.image_decrypt_failed"

    with pytest.raises(WeixinILinkError) as exc_key:
        decrypt_aes_128_ecb(ciphertext, b"short_key")
    assert exc_key.value.code == "weixin.image_key_invalid"


def test_resolve_image_aes_key_direct():
    raw_key = secrets.token_bytes(16)
    hex_key = raw_key.hex()
    b64_raw = base64.b64encode(raw_key).decode("ascii")
    b64_hex = base64.b64encode(hex_key.encode("ascii")).decode("ascii")

    assert resolve_image_aes_key(hex_key, None) == raw_key
    assert resolve_image_aes_key(None, b64_raw) == raw_key
    assert resolve_image_aes_key(None, b64_hex) == raw_key
    assert resolve_image_aes_key(None, None) is None

    with pytest.raises(WeixinILinkError):
        resolve_image_aes_key("not_hex", None)

    with pytest.raises(WeixinILinkError):
        resolve_image_aes_key(None, "not_b64!!!")


def test_sniff_image_mime_type_direct():
    png_bytes = _make_png_bytes(1, 1)
    jpeg_bytes = _make_jpeg_bytes(1, 1)
    assert sniff_image_mime_type(png_bytes) == "image/png"
    assert sniff_image_mime_type(jpeg_bytes) == "image/jpeg"

    with pytest.raises(WeixinILinkError) as exc:
        sniff_image_mime_type(b"not_an_image")
    assert exc.value.code == "weixin.image_invalid"


def test_validate_image_direct():
    png_bytes = _make_png_bytes(1, 1)
    validate_image(png_bytes, "image/png")

    with pytest.raises(WeixinILinkError) as exc_mime:
        validate_image(png_bytes, "image/gif")
    assert exc_mime.value.code == "weixin.image_invalid"


# --- 3. Decryption (both key types and plaintext) ---


@pytest.mark.asyncio
async def test_download_image_aeskey_hex():
    raw_key = secrets.token_bytes(16)
    hex_key = raw_key.hex()  # 32 ASCII hex chars
    plain_png = _make_png_bytes(2, 2)
    ciphertext = encrypt_aes_128_ecb(plain_png, raw_key)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "novac2c.cdn.weixin.qq.com" in str(request.url)
        return httpx.Response(200, content=ciphertext)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        inbound_image = WeixinInboundImage(
            encrypt_query_param="valid_param",
            aeskey=hex_key,
        )
        result_bytes, mime = await client.download_image(inbound_image)
        assert result_bytes == plain_png
        assert mime == "image/png"


@pytest.mark.asyncio
async def test_download_image_aes_key_base64_raw():
    raw_key = secrets.token_bytes(16)
    b64_raw_key = base64.b64encode(raw_key).decode("ascii")
    plain_png = _make_png_bytes(2, 2)
    ciphertext = encrypt_aes_128_ecb(plain_png, raw_key)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ciphertext)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        inbound_image = WeixinInboundImage(
            encrypt_query_param="valid_param",
            aes_key=b64_raw_key,
        )
        result_bytes, mime = await client.download_image(inbound_image)
        assert result_bytes == plain_png
        assert mime == "image/png"


@pytest.mark.asyncio
async def test_download_image_aes_key_base64_ascii_hex():
    raw_key = secrets.token_bytes(16)
    hex_key = raw_key.hex()
    b64_ascii_hex = base64.b64encode(hex_key.encode("ascii")).decode("ascii")
    plain_png = _make_png_bytes(2, 2)
    ciphertext = encrypt_aes_128_ecb(plain_png, raw_key)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ciphertext)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        inbound_image = WeixinInboundImage(
            encrypt_query_param="valid_param",
            aes_key=b64_ascii_hex,
        )
        result_bytes, mime = await client.download_image(inbound_image)
        assert result_bytes == plain_png
        assert mime == "image/png"


@pytest.mark.asyncio
async def test_download_image_plaintext():
    plain_jpeg = _make_jpeg_bytes(2, 2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=plain_jpeg)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        inbound_image = WeixinInboundImage(encrypt_query_param="param_plaintext")
        result_bytes, mime = await client.download_image(inbound_image)
        assert result_bytes == plain_jpeg
        assert mime == "image/jpeg"


# --- 4. SSRF, route, and header rejection ---


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=1",
        "https://attacker.com/c2c/download?encrypted_query_param=1",
        "https://evil.novac2c.cdn.weixin.qq.com/c2c/download?encrypted_query_param=1",
        "https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param=1",
        "https://novac2c.cdn.weixin.qq.com:8443/c2c/download?encrypted_query_param=1",
        "https://user:pass@novac2c.cdn.weixin.qq.com/c2c/download?param=1",
        "https://novac2c.cdn.weixin.qq.com/c2c/download?param=1#frag",
        "https://novac2c.cdn.weixin.qq.com/c2c/download\r\n?param=1",
    ],
)
def test_resolve_cdn_download_url_ssrf_rejection(bad_url: str):
    with pytest.raises(WeixinILinkError) as exc:
        resolve_cdn_download_url(bad_url, None)
    assert exc.value.code == "weixin.cdn_url_rejected"


@pytest.mark.asyncio
async def test_download_headers_do_not_leak_credentials():
    captured_headers: dict[str, str] = {}
    plain_png = _make_png_bytes(1, 1)

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, content=plain_png)

    transport = httpx.MockTransport(handler)
    client_with_secrets = httpx.AsyncClient(
        transport=transport,
        headers={
            "X-Secret-Token": "secret_token_abc",
            "Authorization": "Bearer leaked_client_auth",
        },
        cookies={"session": "secret_cookie_val"},
        auth=httpx.BasicAuth("secret_user", "secret_pass"),
        follow_redirects=True,
    )
    async with client_with_secrets:
        client = WeixinILinkClient(client_with_secrets)
        inbound_image = WeixinInboundImage(encrypt_query_param="safe_param")
        await client.download_image(inbound_image)

    assert "authorization" not in captured_headers
    assert "cookie" not in captured_headers
    assert "x-wechat-uin" not in captured_headers
    assert "x-secret-token" not in captured_headers


# --- 5. Redirect, oversize, timeout, and cancellation close ---


@pytest.mark.asyncio
async def test_download_rejects_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"Location": "https://novac2c.cdn.weixin.qq.com/c2c/download"}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        with pytest.raises(WeixinILinkError) as exc:
            await client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        assert exc.value.code == "weixin.cdn_redirect_rejected"


@pytest.mark.asyncio
async def test_download_rejects_error_status_sanitized():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="secret error details that must not leak")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        with pytest.raises(WeixinILinkError) as exc:
            await client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        assert exc.value.code == "weixin.cdn_download_failed"
        assert "secret error details" not in str(exc.value)
        assert "param" not in str(exc.value)


@pytest.mark.asyncio
async def test_download_rejects_oversized_content_length():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(6 * 1024 * 1024)},
            content=b"a" * 1024,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        with pytest.raises(WeixinILinkError) as exc:
            await client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        assert exc.value.code == "weixin.response_too_large"


@pytest.mark.asyncio
async def test_download_rejects_oversized_streaming():
    async def stream_chunks() -> AsyncIterator[bytes]:
        chunk = b"x" * (1024 * 1024)
        for _ in range(6):  # 6 MiB > 5 MiB + 16 bytes
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream_chunks())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        with pytest.raises(WeixinILinkError) as exc:
            await client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        assert exc.value.code == "weixin.response_too_large"


@pytest.mark.asyncio
async def test_download_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Read timed out")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        with pytest.raises(WeixinILinkError) as exc:
            await client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        assert exc.value.code == "weixin.request_timeout"


@pytest.mark.asyncio
async def test_download_response_closed_on_error_or_cancellation():
    closed = False

    async def stream_with_close_tracking() -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            yield b"first chunk"
            raise httpx.ProtocolError("connection severed")
        finally:
            closed = True

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream_with_close_tracking())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        with pytest.raises(WeixinILinkError) as exc:
            await client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        assert exc.value.code == "weixin.request_failed"
        assert closed is True


@pytest.mark.asyncio
async def test_download_image_cancellation_propagates():
    entered = asyncio.Event()
    unresolved = asyncio.Event()

    async def hanging_handler(_request: httpx.Request) -> httpx.Response:
        entered.set()
        await unresolved.wait()
        return httpx.Response(200, content=b"fake")

    transport = httpx.MockTransport(hanging_handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        client = WeixinILinkClient(mock_client)
        task = asyncio.create_task(
            client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --- 6. Invalid image rejection ---


@pytest.mark.asyncio
async def test_download_rejects_empty_data():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        with pytest.raises(WeixinILinkError) as exc:
            await client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        assert exc.value.code == "weixin.image_invalid"


@pytest.mark.asyncio
async def test_download_rejects_corrupt_or_non_image():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"plain text that is definitely not an image")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        with pytest.raises(WeixinILinkError) as exc:
            await client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        assert exc.value.code == "weixin.image_invalid"


@pytest.mark.asyncio
async def test_download_rejects_animated_image():
    animated_gif = _make_animated_gif_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=animated_gif)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = WeixinILinkClient(http_client)
        with pytest.raises(WeixinILinkError) as exc:
            await client.download_image(WeixinInboundImage(encrypt_query_param="param"))
        assert exc.value.code == "weixin.image_invalid"


@pytest.mark.asyncio
async def test_download_unavailable_reason_fails():
    client = WeixinILinkClient()
    with pytest.raises(WeixinILinkError) as exc:
        await client.download_image(WeixinInboundImage(invalid_reason="multiple_images"))
    assert exc.value.code == "weixin.image_unavailable"


@pytest.mark.asyncio
async def test_download_rejects_compressed_response_before_decoding() -> None:
    import gzip

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200, content=gzip.compress(b"x" * 100), headers={"Content-Encoding": "gzip"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(WeixinILinkError) as raised:
            await WeixinILinkClient(http).download_image(
                WeixinInboundImage(encrypt_query_param="p")
            )
        assert raised.value.code == "weixin.cdn_encoding_rejected"


def test_truncated_jpeg_pixel_data_is_rejected() -> None:
    original = _make_jpeg_bytes(width=64, height=64)
    with pytest.raises(WeixinILinkError):
        validate_image(original[:-20], "image/jpeg")
