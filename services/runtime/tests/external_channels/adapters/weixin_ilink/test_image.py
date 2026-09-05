"""Tests for bounded Phase 17.2 WeChat image wire transport."""

from __future__ import annotations

import asyncio
import base64
import io
import json

import httpx
import pytest
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import (
    WeixinILinkClient,
    WeixinILinkError,
)
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinCredentials,
)
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image


def _make_test_credentials() -> WeixinCredentials:
    return WeixinCredentials(
        bot_token="test_bot_token_abc",
        bot_id="bot_id_123",
        user_id="user_id_123",
        base_url="https://ilinkai.weixin.qq.com/",
        gateway_access_token="gateway_token_123",
    )


def _make_test_png(width: int = 1, height: int = 1, animated: bool = False) -> bytes:
    if animated:
        img1 = Image.new("RGB", (width, height), color="red")
        img2 = Image.new("RGB", (width, height), color="blue")
        out = io.BytesIO()
        img1.save(out, format="PNG", save_all=True, append_images=[img2])
        return out.getvalue()
    img = Image.new("RGB", (width, height), color="red")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _make_test_jpeg(width: int = 1, height: int = 1) -> bytes:
    img = Image.new("RGB", (width, height), color="blue")
    out = io.BytesIO()
    img.save(out, format="JPEG")
    return out.getvalue()


def _decrypt_aes_128_ecb(ciphertext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


@pytest.mark.asyncio
async def test_send_image_success_with_full_url() -> None:
    png_data = _make_test_png(1, 1)
    recorded_requests: list[dict[str, object]] = []
    captured_aeskey_hex: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        recorded_requests.append(
            {
                "method": request.method,
                "url": url_str,
                "headers": dict(request.headers),
                "content": request.content,
            }
        )

        if "ilink/bot/getuploadurl" in url_str:
            body = json.loads(request.content)
            assert body["media_type"] == 1
            assert body["to_user_id"] == "user_target_1"
            assert body["rawsize"] == len(png_data)
            assert body["no_need_thumb"] is True
            assert len(body["filekey"]) == 32
            assert len(body["aeskey"]) == 32
            captured_aeskey_hex.append(body["aeskey"])
            return httpx.Response(
                200,
                json={
                    "ret": 0,
                    "upload_full_url": "https://novac2c.cdn.weixin.qq.com/c2c/upload?param=mock_up",
                },
            )

        if "novac2c.cdn.weixin.qq.com/c2c/upload" in url_str:
            assert request.headers["content-type"] == "application/octet-stream"
            assert "authorization" not in request.headers
            assert "authorizationtype" not in request.headers
            assert "x-wechat-uin" not in request.headers
            assert "ilink-app-id" not in request.headers
            assert "cookie" not in request.headers

            # Extensions timeout enforces 20s
            timeout_dict = request.extensions.get("timeout")
            assert isinstance(timeout_dict, dict)
            assert timeout_dict == httpx.Timeout(20.0).as_dict()

            # Verify ciphertext decodes back to original PNG
            raw_key = bytes.fromhex(captured_aeskey_hex[0])
            decrypted = _decrypt_aes_128_ecb(request.content, raw_key)
            assert decrypted == png_data

            return httpx.Response(200, headers={"x-encrypted-param": "enc_param_token_999"})

        if "ilink/bot/sendmessage" in url_str:
            body = json.loads(request.content)
            msg = body["msg"]
            assert msg["client_id"] == "cid_fixed_123"
            assert msg["to_user_id"] == "user_target_1"
            assert msg["context_token"] == "ctx_wire_99"
            assert len(msg["item_list"]) == 1
            item = msg["item_list"][0]
            assert item["type"] == 2
            media = item["image_item"]["media"]
            assert media["encrypt_query_param"] == "enc_param_token_999"
            assert media["encrypt_type"] == 1
            expected_b64 = base64.b64encode(captured_aeskey_hex[0].encode("ascii")).decode("ascii")
            assert media["aes_key"] == expected_b64
            return httpx.Response(200, json={"ret": 0})

        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        client = WeixinILinkClient(client=mock_client)
        res = await client.send_image(
            _make_test_credentials(),
            recipient_user_id="user_target_1",
            context_token="ctx_wire_99",
            client_id="cid_fixed_123",
            image_bytes=png_data,
            mime_type="image/png",
        )
        assert res == "cid_fixed_123"
        assert len(recorded_requests) == 3


@pytest.mark.asyncio
async def test_send_image_fallback_fixed_url() -> None:
    jpeg_data = _make_test_jpeg(2, 2)
    recorded_cdn_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "ilink/bot/getuploadurl" in url_str:
            return httpx.Response(200, json={"ret": 0, "upload_param": "param_fallback_123"})
        if "novac2c.cdn.weixin.qq.com/c2c/upload" in url_str:
            recorded_cdn_urls.append(url_str)
            return httpx.Response(200, headers={"x-encrypted-param": "enc_param_fixed"})
        if "ilink/bot/sendmessage" in url_str:
            return httpx.Response(200, json={"ret": 0})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        client = WeixinILinkClient(client=mock_client)
        await client.send_image(
            _make_test_credentials(),
            recipient_user_id="user_target_1",
            context_token="ctx_wire_99",
            client_id="cid_fallback_1",
            image_bytes=jpeg_data,
            mime_type="image/jpeg",
        )
        assert len(recorded_cdn_urls) == 1
        assert "encrypted_query_param=param_fallback_123" in recorded_cdn_urls[0]
        assert "filekey=" in recorded_cdn_urls[0]


@pytest.mark.asyncio
async def test_send_image_cdn_isolation_no_leaks_no_follow() -> None:
    png_data = _make_test_png(1, 1)
    cdn_request_headers: dict[str, str] = {}
    cdn_request_extensions: dict[str, object] = {}
    redirect_followed = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal redirect_followed
        url_str = str(request.url)
        if "ilink/bot/getuploadurl" in url_str:
            return httpx.Response(
                200,
                json={
                    "ret": 0,
                    "upload_full_url": "https://novac2c.cdn.weixin.qq.com/c2c/upload?param=leak_test",
                },
            )
        if "novac2c.cdn.weixin.qq.com/c2c/upload" in url_str:
            cdn_request_headers.update(dict(request.headers))
            cdn_request_extensions.update(dict(request.extensions))
            return httpx.Response(
                302,
                headers={"Location": "https://novac2c.cdn.weixin.qq.com/c2c/leak_target"},
            )
        if "leak_target" in url_str:
            redirect_followed = True
            return httpx.Response(200)
        return httpx.Response(404)

    client_with_secrets = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={
            "X-Secret-Token": "secret_token_abc",
            "Authorization": "Bearer leaked_client_auth",
        },
        cookies={"session": "secret_cookie_val"},
        auth=httpx.BasicAuth("secret_user", "secret_pass"),
        follow_redirects=True,
    )
    async with client_with_secrets:
        client = WeixinILinkClient(client=client_with_secrets)
        with pytest.raises(WeixinILinkError) as exc_info:
            await client.send_image(
                _make_test_credentials(),
                recipient_user_id="user_target_1",
                context_token="ctx_wire_99",
                client_id="cid_isolate_1",
                image_bytes=png_data,
                mime_type="image/png",
            )

        assert exc_info.value.code == "weixin.cdn_redirect_rejected"
        assert exc_info.value.retryable is False

        assert "x-secret-token" not in cdn_request_headers
        assert "cookie" not in cdn_request_headers
        assert "authorization" not in cdn_request_headers
        assert cdn_request_headers.get("content-type") == "application/octet-stream"

        timeout_ext = cdn_request_extensions.get("timeout")
        assert isinstance(timeout_ext, dict)
        assert timeout_ext == httpx.Timeout(20.0).as_dict()

        assert not redirect_followed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://novac2c.cdn.weixin.qq.com/c2c/upload?param=1",  # non-https
        "https://evil.cdn.weixin.qq.com/c2c/upload?param=1",  # wrong host
        "https://novac2c.cdn.weixin.qq.com:8443/c2c/upload",  # non-443 port
        "https://user:pass@novac2c.cdn.weixin.qq.com/c2c/upload",  # userinfo
        "https://novac2c.cdn.weixin.qq.com/c2c/other_path",  # wrong path
        "https://novac2c.cdn.weixin.qq.com/c2c/upload#frag",  # fragment
    ],
)
async def test_send_image_unsafe_cdn_urls_rejected(bad_url: str) -> None:
    png_data = _make_test_png(1, 1)
    cdn_contacted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cdn_contacted
        url_str = str(request.url)
        if "ilink/bot/getuploadurl" in url_str:
            return httpx.Response(200, json={"ret": 0, "upload_full_url": bad_url})
        if "upload" in url_str:
            cdn_contacted = True
            return httpx.Response(200, headers={"x-encrypted-param": "abc"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        client = WeixinILinkClient(client=mock_client)
        with pytest.raises(WeixinILinkError) as exc_info:
            await client.send_image(
                _make_test_credentials(),
                recipient_user_id="user_target_1",
                context_token="ctx_wire_99",
                client_id="cid_bad_url",
                image_bytes=png_data,
                mime_type="image/png",
            )
        assert exc_info.value.code == "weixin.cdn_url_rejected"
        assert exc_info.value.retryable is False
        assert not cdn_contacted


@pytest.mark.asyncio
async def test_send_image_cdn_redirect_rejected() -> None:
    png_data = _make_test_png(1, 1)

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "ilink/bot/getuploadurl" in url_str:
            return httpx.Response(
                200,
                json={
                    "ret": 0,
                    "upload_full_url": "https://novac2c.cdn.weixin.qq.com/c2c/upload?param=redirect",
                },
            )
        if "novac2c.cdn.weixin.qq.com/c2c/upload" in url_str:
            return httpx.Response(
                302, headers={"Location": "https://novac2c.cdn.weixin.qq.com/c2c/other"}
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as mock_client:
        client = WeixinILinkClient(client=mock_client)
        with pytest.raises(WeixinILinkError) as exc_info:
            await client.send_image(
                _make_test_credentials(),
                recipient_user_id="u1",
                context_token="c1",
                client_id="cid_redir",
                image_bytes=png_data,
                mime_type="image/png",
            )
        assert exc_info.value.code == "weixin.cdn_redirect_rejected"
        assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_send_image_cdn_upload_failure_modes() -> None:
    png_data = _make_test_png(1, 1)
    status_to_return = 500

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "ilink/bot/getuploadurl" in url_str:
            return httpx.Response(
                200,
                json={
                    "ret": 0,
                    "upload_full_url": "https://novac2c.cdn.weixin.qq.com/c2c/upload?p=1",
                },
            )
        if "novac2c.cdn.weixin.qq.com/c2c/upload" in url_str:
            return httpx.Response(status_to_return)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        client = WeixinILinkClient(client=mock_client)

        status_to_return = 500
        with pytest.raises(WeixinILinkError) as exc_500:
            await client.send_image(
                _make_test_credentials(),
                recipient_user_id="u1",
                context_token="c1",
                client_id="cid_fail",
                image_bytes=png_data,
                mime_type="image/png",
            )
        assert exc_500.value.code == "weixin.cdn_upload_failed"
        assert exc_500.value.retryable is True

        status_to_return = 400
        with pytest.raises(WeixinILinkError) as exc_400:
            await client.send_image(
                _make_test_credentials(),
                recipient_user_id="u1",
                context_token="c1",
                client_id="cid_fail_client",
                image_bytes=png_data,
                mime_type="image/png",
            )
        assert exc_400.value.code == "weixin.cdn_upload_failed"
        assert exc_400.value.retryable is False


@pytest.mark.asyncio
async def test_send_image_validation_rejections() -> None:
    client = WeixinILinkClient()
    creds = _make_test_credentials()

    # Empty bytes
    with pytest.raises(WeixinILinkError) as exc_empty:
        await client.send_image(
            creds,
            recipient_user_id="u1",
            context_token="c1",
            client_id="cid1",
            image_bytes=b"",
            mime_type="image/png",
        )
    assert exc_empty.value.code == "weixin.image_invalid"

    # Oversized > 5 MiB
    with pytest.raises(WeixinILinkError) as exc_large:
        await client.send_image(
            creds,
            recipient_user_id="u1",
            context_token="c1",
            client_id="cid1",
            image_bytes=b"0" * (5 * 1024 * 1024 + 1),
            mime_type="image/png",
        )
    assert exc_large.value.code == "weixin.image_invalid"

    # Unsupported MIME
    with pytest.raises(WeixinILinkError) as exc_mime:
        await client.send_image(
            creds,
            recipient_user_id="u1",
            context_token="c1",
            client_id="cid1",
            image_bytes=_make_test_png(1, 1),
            mime_type="image/gif",
        )
    assert exc_mime.value.code == "weixin.image_invalid"

    # Animated PNG (multiple frames)
    apng_data = _make_test_png(1, 1, animated=True)
    with pytest.raises(WeixinILinkError) as exc_apng:
        await client.send_image(
            creds,
            recipient_user_id="u1",
            context_token="c1",
            client_id="cid1",
            image_bytes=apng_data,
            mime_type="image/png",
        )
    assert exc_apng.value.code == "weixin.image_invalid"

    # Exceeds max dimension (width > 8192)
    large_dim_png = _make_test_png(8193, 1)
    with pytest.raises(WeixinILinkError) as exc_dim:
        await client.send_image(
            creds,
            recipient_user_id="u1",
            context_token="c1",
            client_id="cid1",
            image_bytes=large_dim_png,
            mime_type="image/png",
        )
    assert exc_dim.value.code == "weixin.image_invalid"

    # MIME format mismatch (PNG bytes with image/jpeg)
    with pytest.raises(WeixinILinkError) as exc_mismatch:
        await client.send_image(
            creds,
            recipient_user_id="u1",
            context_token="c1",
            client_id="cid1",
            image_bytes=_make_test_png(1, 1),
            mime_type="image/jpeg",
        )
    assert exc_mismatch.value.code == "weixin.image_invalid"

    # Corrupted image bytes
    with pytest.raises(WeixinILinkError) as exc_corrupt:
        await client.send_image(
            creds,
            recipient_user_id="u1",
            context_token="c1",
            client_id="cid1",
            image_bytes=b"invalid_non_image_bytes",
            mime_type="image/png",
        )
    assert exc_corrupt.value.code == "weixin.image_invalid"


@pytest.mark.asyncio
async def test_send_image_cancellation_propagates() -> None:
    png_data = _make_test_png(1, 1)
    entered = asyncio.Event()
    unresolved = asyncio.Event()

    async def hanging_handler(_request: httpx.Request) -> httpx.Response:
        entered.set()
        await unresolved.wait()
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(hanging_handler)) as mock_client:
        client = WeixinILinkClient(client=mock_client)
        task = asyncio.create_task(
            client.send_image(
                _make_test_credentials(),
                recipient_user_id="u1",
                context_token="c1",
                client_id="cid_cancel",
                image_bytes=png_data,
                mime_type="image/png",
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
