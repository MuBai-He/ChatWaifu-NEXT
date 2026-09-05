"""Small cancellation-safe HTTP client for Tencent's iLink JSON protocol."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import cast
from urllib.parse import quote, urljoin, urlsplit

import httpx

from chatwaifu_runtime.external_channels.adapters.weixin_ilink.image import (
    encode_media_aes_key,
    encrypt_aes_128_ecb,
    resolve_cdn_upload_url,
    validate_image,
)
from chatwaifu_runtime.external_channels.adapters.weixin_ilink.models import (
    WeixinAuthorizationPoll,
    WeixinAuthorizationStart,
    WeixinAuthorizationState,
    WeixinCredentials,
    WeixinInboundText,
    WeixinUpdates,
)

_FIXED_QR_BASE_URL = "https://ilinkai.weixin.qq.com/"
_APP_ID = "bot"
_APP_CLIENT_VERSION = str((2 << 16) | (4 << 8) | 6)
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_UPDATE_MESSAGES = 64


class WeixinILinkError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class WeixinILinkClient:
    """Provider-private transport adapter.

    Redirect following and environment proxy inheritance are disabled. Every
    dynamically returned API host is validated before it is used.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(15.0),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def start_authorization(self) -> WeixinAuthorizationStart:
        payload = await self._post_json(
            _FIXED_QR_BASE_URL,
            "ilink/bot/get_bot_qrcode?bot_type=3",
            {"local_token_list": []},
            token=None,
            timeout_seconds=15,
        )
        qrcode = _required_text(payload, "qrcode", max_length=4_096)
        content = _required_text(payload, "qrcode_img_content", max_length=8_192)
        return WeixinAuthorizationStart(qrcode=qrcode, qr_code_content=content)

    async def poll_authorization(
        self,
        *,
        qrcode: str,
        poll_base_url: str = _FIXED_QR_BASE_URL,
        verification_code: str | None = None,
    ) -> WeixinAuthorizationPoll:
        base_url = validated_weixin_base_url(poll_base_url)
        endpoint = f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode, safe='')}"
        if verification_code:
            endpoint += f"&verify_code={quote(verification_code, safe='')}"
        payload = await self._get_json(base_url, endpoint, timeout_seconds=40)
        raw_state = _required_text(payload, "status", max_length=64)
        try:
            state = WeixinAuthorizationState(raw_state)
        except ValueError as error:
            raise WeixinILinkError(
                "weixin.authorization_status_unknown",
                "WeChat returned an unsupported authorization state.",
                retryable=False,
            ) from error
        redirect_base_url = None
        redirect_host = payload.get("redirect_host")
        if state is WeixinAuthorizationState.REDIRECT:
            if not isinstance(redirect_host, str) or not redirect_host:
                raise WeixinILinkError(
                    "weixin.authorization_redirect_invalid",
                    "WeChat returned an invalid authorization redirect.",
                    retryable=False,
                )
            redirect_base_url = validated_weixin_base_url(f"https://{redirect_host}/")
        returned_base = _optional_text(payload, "baseurl", max_length=2_048)
        return WeixinAuthorizationPoll(
            state=state,
            bot_token=_optional_text(payload, "bot_token", max_length=16_384),
            bot_id=_optional_text(payload, "ilink_bot_id", max_length=512),
            user_id=_optional_text(payload, "ilink_user_id", max_length=512),
            base_url=(validated_weixin_base_url(returned_base) if returned_base else None),
            redirect_base_url=redirect_base_url,
        )

    async def notify_start(self, credentials: WeixinCredentials) -> None:
        await self._post_json(
            credentials.base_url,
            "ilink/bot/msg/notifystart",
            {"base_info": _base_info()},
            token=credentials.bot_token,
            timeout_seconds=10,
        )

    async def notify_stop(self, credentials: WeixinCredentials) -> None:
        await self._post_json(
            credentials.base_url,
            "ilink/bot/msg/notifystop",
            {"base_info": _base_info()},
            token=credentials.bot_token,
            timeout_seconds=10,
        )

    async def get_updates(self, credentials: WeixinCredentials, cursor: str) -> WeixinUpdates:
        payload = await self._post_json(
            credentials.base_url,
            "ilink/bot/getupdates",
            {"get_updates_buf": cursor, "base_info": _base_info()},
            token=credentials.bot_token,
            timeout_seconds=40,
        )
        ret = payload.get("ret", 0)
        if not isinstance(ret, int) or isinstance(ret, bool):
            raise WeixinILinkError(
                "weixin.response_invalid", "WeChat returned an invalid response.", retryable=True
            )
        if ret != 0:
            raise WeixinILinkError(
                "weixin.get_updates_failed",
                "WeChat rejected the update request.",
                retryable=True,
            )
        raw_messages = payload.get("msgs", [])
        if not isinstance(raw_messages, list):
            raise WeixinILinkError(
                "weixin.update_batch_invalid",
                "WeChat returned an invalid or oversized message batch.",
                retryable=False,
            )
        typed_messages = cast(list[object], raw_messages)
        if len(typed_messages) > _MAX_UPDATE_MESSAGES:
            raise WeixinILinkError(
                "weixin.update_batch_invalid",
                "WeChat returned an invalid or oversized message batch.",
                retryable=False,
            )
        messages = tuple(
            message
            for raw in typed_messages
            if (message := _parse_inbound_text(raw, credentials.bot_id)) is not None
        )
        next_cursor = payload.get("get_updates_buf", cursor)
        if not isinstance(next_cursor, str) or len(next_cursor) > 1_000_000:
            raise WeixinILinkError(
                "weixin.cursor_invalid",
                "WeChat returned an invalid update cursor.",
                retryable=False,
            )
        return WeixinUpdates(cursor=next_cursor, messages=messages)

    async def get_typing_ticket(
        self, credentials: WeixinCredentials, *, recipient_user_id: str, context_token: str
    ) -> str | None:
        payload = await self._post_json(
            credentials.base_url,
            "ilink/bot/getconfig",
            {
                "ilink_user_id": recipient_user_id,
                "context_token": context_token,
                "base_info": _base_info(),
            },
            token=credentials.bot_token,
            timeout_seconds=2,
        )
        ret = payload.get("ret", 0)
        if not isinstance(ret, int) or isinstance(ret, bool) or ret != 0:
            raise WeixinILinkError(
                "weixin.typing_config_failed", "WeChat typing is unavailable.", retryable=True
            )
        ticket = payload.get("typing_ticket")
        if ticket is None or ticket == "":
            return None
        return _required_text(payload, "typing_ticket", max_length=16_384)

    async def send_typing(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        typing_ticket: str,
        active: bool,
    ) -> None:
        payload = await self._post_json(
            credentials.base_url,
            "ilink/bot/sendtyping",
            {
                "ilink_user_id": recipient_user_id,
                "typing_ticket": typing_ticket,
                "status": 1 if active else 2,
                "base_info": _base_info(),
            },
            token=credentials.bot_token,
            timeout_seconds=2,
        )
        ret = payload.get("ret", 0)
        if not isinstance(ret, int) or isinstance(ret, bool) or ret != 0:
            raise WeixinILinkError(
                "weixin.typing_send_failed", "WeChat rejected typing status.", retryable=True
            )

    async def send_text(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        context_token: str,
        client_id: str,
        text: str,
    ) -> str:
        payload = await self._post_json(
            credentials.base_url,
            "ilink/bot/sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": recipient_user_id,
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "item_list": [{"type": 1, "text_item": {"text": text}}],
                    "context_token": context_token,
                },
                "base_info": _base_info(),
            },
            token=credentials.bot_token,
            timeout_seconds=15,
        )
        ret = payload.get("ret", 0)
        if not isinstance(ret, int) or isinstance(ret, bool) or ret != 0:
            raise WeixinILinkError(
                "weixin.send_failed", "WeChat rejected the reply.", retryable=True
            )
        return client_id

    async def send_image(
        self,
        credentials: WeixinCredentials,
        *,
        recipient_user_id: str,
        context_token: str,
        client_id: str,
        image_bytes: bytes,
        mime_type: str,
    ) -> str | None:
        try:
            async with asyncio.timeout(45.0):
                validate_image(image_bytes, mime_type)

                raw_size = len(image_bytes)
                raw_file_md5 = hashlib.md5(image_bytes).hexdigest()
                raw_key = secrets.token_bytes(16)
                hex_key = raw_key.hex()
                filekey = secrets.token_hex(16)
                ciphertext = encrypt_aes_128_ecb(image_bytes, raw_key)
                ciphertext_size = len(ciphertext)

                upload_url_payload = await self._post_json(
                    credentials.base_url,
                    "ilink/bot/getuploadurl",
                    {
                        "filekey": filekey,
                        "media_type": 1,
                        "to_user_id": recipient_user_id,
                        "rawsize": raw_size,
                        "rawfilemd5": raw_file_md5,
                        "filesize": ciphertext_size,
                        "no_need_thumb": True,
                        "aeskey": hex_key,
                        "base_info": _base_info(),
                    },
                    token=credentials.bot_token,
                    timeout_seconds=15,
                )
                ret = upload_url_payload.get("ret", 0)
                if not isinstance(ret, int) or isinstance(ret, bool) or ret != 0:
                    raise WeixinILinkError(
                        "weixin.get_upload_url_failed",
                        "WeChat rejected the upload URL request.",
                        retryable=True,
                    )

                raw_full_url = upload_url_payload.get("upload_full_url")
                raw_upload_param = upload_url_payload.get("upload_param")
                cdn_url = resolve_cdn_upload_url(raw_full_url, raw_upload_param, filekey)
                download_param = await self._upload_ciphertext_to_cdn(cdn_url, ciphertext)
                base64_aes_key = encode_media_aes_key(hex_key)

                send_payload = await self._post_json(
                    credentials.base_url,
                    "ilink/bot/sendmessage",
                    {
                        "msg": {
                            "from_user_id": "",
                            "to_user_id": recipient_user_id,
                            "client_id": client_id,
                            "message_type": 2,
                            "message_state": 2,
                            "item_list": [
                                {
                                    "type": 2,
                                    "image_item": {
                                        "media": {
                                            "encrypt_query_param": download_param,
                                            "aes_key": base64_aes_key,
                                            "encrypt_type": 1,
                                        },
                                        "mid_size": ciphertext_size,
                                    },
                                }
                            ],
                            "context_token": context_token,
                        },
                        "base_info": _base_info(),
                    },
                    token=credentials.bot_token,
                    timeout_seconds=15,
                )
                send_ret = send_payload.get("ret", 0)
                if not isinstance(send_ret, int) or isinstance(send_ret, bool) or send_ret != 0:
                    raise WeixinILinkError(
                        "weixin.send_failed", "WeChat rejected the reply.", retryable=True
                    )
                return client_id
        except TimeoutError:
            raise WeixinILinkError(
                "weixin.request_timeout", "WeChat image send timed out.", retryable=True
            ) from None

    async def _upload_ciphertext_to_cdn(self, cdn_url: str, ciphertext: bytes) -> str:
        headers = {"Content-Type": "application/octet-stream"}
        request = httpx.Request(
            "POST",
            cdn_url,
            headers=headers,
            content=ciphertext,
            extensions={"timeout": httpx.Timeout(20.0).as_dict()},
        )
        response: httpx.Response | None = None
        try:
            try:
                response = await self._client.send(
                    request,
                    stream=True,
                    auth=None,
                    follow_redirects=False,
                )
                if response.is_redirect or (300 <= response.status_code < 400):
                    raise WeixinILinkError(
                        "weixin.cdn_redirect_rejected",
                        "CDN redirected the upload request.",
                        retryable=False,
                    )
                if 400 <= response.status_code < 500:
                    raise WeixinILinkError(
                        "weixin.cdn_upload_failed",
                        f"CDN upload rejected with status {response.status_code}.",
                        retryable=False,
                    )
                if response.status_code != 200:
                    raise WeixinILinkError(
                        "weixin.cdn_upload_failed",
                        f"CDN upload failed with status {response.status_code}.",
                        retryable=True,
                    )
                download_param = response.headers.get("x-encrypted-param")
                if not download_param or not download_param.strip():
                    raise WeixinILinkError(
                        "weixin.cdn_upload_failed",
                        "CDN upload response missing x-encrypted-param header.",
                        retryable=True,
                    )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > 65536:
                        raise WeixinILinkError(
                            "weixin.response_too_large",
                            "CDN returned oversized response.",
                            retryable=False,
                        )
                    content.extend(chunk)
                return download_param.strip()
            finally:
                if response is not None:
                    await response.aclose()
        except WeixinILinkError:
            raise
        except httpx.TimeoutException:
            raise WeixinILinkError(
                "weixin.request_timeout", "CDN upload request timed out.", retryable=True
            ) from None
        except httpx.HTTPError:
            raise WeixinILinkError(
                "weixin.request_failed", "CDN upload request failed.", retryable=True
            ) from None

    async def _get_json(
        self, base_url: str, endpoint: str, *, timeout_seconds: float
    ) -> dict[str, object]:
        url = _endpoint_url(base_url, endpoint)
        return await self._request_json(
            "GET",
            url,
            headers=_common_headers(),
            body=None,
            timeout_seconds=timeout_seconds,
        )

    async def _post_json(
        self,
        base_url: str,
        endpoint: str,
        body: dict[str, object],
        *,
        token: str | None,
        timeout_seconds: float,
    ) -> dict[str, object]:
        url = _endpoint_url(validated_weixin_base_url(base_url), endpoint)
        headers = _common_headers()
        headers.update(
            {
                "Content-Type": "application/json",
                "AuthorizationType": "ilink_bot_token",
                "X-WECHAT-UIN": base64.b64encode(str(secrets.randbits(32)).encode("ascii")).decode(
                    "ascii"
                ),
            }
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return await self._request_json(
            "POST",
            url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, object] | None,
        timeout_seconds: float,
    ) -> dict[str, object]:
        try:
            async with self._client.stream(
                method,
                url,
                headers=headers,
                json=body,
                timeout=timeout_seconds,
            ) as response:
                response.raise_for_status()
                return await _bounded_json(response)
        except WeixinILinkError:
            raise
        except httpx.TimeoutException:
            raise WeixinILinkError(
                "weixin.request_timeout", "WeChat did not respond in time.", retryable=True
            ) from None
        except httpx.HTTPError:
            raise WeixinILinkError(
                "weixin.request_failed", "WeChat could not be reached.", retryable=True
            ) from None


def validated_weixin_base_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not (hostname == "weixin.qq.com" or hostname.endswith(".weixin.qq.com"))
    ):
        raise WeixinILinkError(
            "weixin.endpoint_rejected",
            "WeChat returned an endpoint outside the allowed HTTPS domain.",
            retryable=False,
        )
    return f"https://{hostname}/"


def _endpoint_url(base_url: str, endpoint: str) -> str:
    return urljoin(validated_weixin_base_url(base_url), endpoint)


def _common_headers() -> dict[str, str]:
    return {
        "iLink-App-Id": _APP_ID,
        "iLink-App-ClientVersion": _APP_CLIENT_VERSION,
    }


def _base_info() -> dict[str, str]:
    return {
        "channel_version": "2.4.6",
        "bot_agent": "ChatWaifuNEXT/0.1.0",
    }


async def _bounded_json(response: httpx.Response) -> dict[str, object]:
    content_length = response.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > _MAX_RESPONSE_BYTES:
        raise WeixinILinkError(
            "weixin.response_too_large", "WeChat returned an oversized response.", retryable=False
        )
    content = bytearray()
    async for chunk in response.aiter_bytes():
        if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
            raise WeixinILinkError(
                "weixin.response_too_large",
                "WeChat returned an oversized response.",
                retryable=False,
            )
        content.extend(chunk)
    try:
        value: object = json.loads(bytes(content))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WeixinILinkError(
            "weixin.response_invalid", "WeChat returned invalid JSON.", retryable=True
        ) from error
    if not isinstance(value, dict):
        raise WeixinILinkError(
            "weixin.response_invalid", "WeChat returned an invalid response.", retryable=True
        )
    return cast(dict[str, object], value)


def _required_text(payload: dict[str, object], key: str, *, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise WeixinILinkError(
            "weixin.response_invalid", "WeChat returned an invalid response.", retryable=False
        )
    return value


def _optional_text(payload: dict[str, object], key: str, *, max_length: int) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise WeixinILinkError(
            "weixin.response_invalid", "WeChat returned an invalid response.", retryable=False
        )
    return value


def _parse_inbound_text(raw: object, expected_bot_id: str) -> WeixinInboundText | None:
    if not isinstance(raw, dict):
        return None
    message = cast(dict[str, object], raw)
    if message.get("message_type") != 1 or message.get("message_state") not in {None, 2}:
        return None
    if message.get("group_id") not in {None, ""}:
        return None
    message_id = message.get("message_id")
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
        raise WeixinILinkError(
            "weixin.message_identity_missing",
            "WeChat returned a text message without a stable message id.",
            retryable=False,
        )
    sender = message.get("from_user_id")
    recipient = message.get("to_user_id")
    context_token = message.get("context_token")
    if (
        not isinstance(sender, str)
        or not sender
        or not isinstance(recipient, str)
        or recipient != expected_bot_id
        or not isinstance(context_token, str)
        or not context_token
        or len(context_token) > 8_192
    ):
        raise WeixinILinkError(
            "weixin.message_context_invalid",
            "WeChat returned a text message with invalid routing context.",
            retryable=False,
        )
    text = _text_from_items(message.get("item_list"))
    if text is None:
        return None
    created_ms = message.get("create_time_ms")
    if isinstance(created_ms, int) and not isinstance(created_ms, bool) and created_ms > 0:
        try:
            received_at = datetime.fromtimestamp(created_ms / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            received_at = datetime.now(UTC)
    else:
        received_at = datetime.now(UTC)
    return WeixinInboundText(
        external_message_id=str(message_id),
        sender_user_id=sender,
        recipient_bot_id=recipient,
        text=text,
        context_token=context_token,
        received_at=received_at,
    )


def _text_from_items(raw: object) -> str | None:
    if not isinstance(raw, list):
        return None
    items = cast(list[object], raw)
    if len(items) > 64:
        return None
    for item_raw in items:
        if not isinstance(item_raw, dict):
            continue
        item = cast(dict[str, object], item_raw)
        if item.get("type") != 1 or not isinstance(item.get("text_item"), dict):
            continue
        text = cast(dict[str, object], item["text_item"]).get("text")
        if isinstance(text, str) and text.strip() and len(text) <= 20_000:
            return text.strip()
    return None
