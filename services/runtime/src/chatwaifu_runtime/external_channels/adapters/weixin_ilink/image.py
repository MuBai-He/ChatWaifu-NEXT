"""Image validation, encryption, and CDN helpers for WeChat iLink wire transport."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from chatwaifu_runtime.media.image import (
    MediaInvalidError,
    async_validate_image_bounds,
    validate_image_bounds,
)
from chatwaifu_runtime.media.image import (
    sniff_image_mime_type as _central_sniff_image_mime_type,
)

if TYPE_CHECKING:
    from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import (
        WeixinILinkError,
    )

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_INBOUND_IMAGES_PER_MESSAGE = 4
MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MiB
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 16_777_216

_ALLOWED_MIME_FORMATS = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/gif": "GIF",
}
ALLOWED_MIME_TYPES = frozenset(_ALLOWED_MIME_FORMATS.keys())
_ALLOWED_CDN_SCHEME = "https"
_ALLOWED_CDN_HOST = "novac2c.cdn.weixin.qq.com"
_ALLOWED_CDN_PATH = "/c2c/upload"
_ALLOWED_CDN_DOWNLOAD_PATH = "/c2c/download"
_ALLOWED_CDN_PORTS = frozenset({None, 443})
MAX_CIPHERTEXT_BYTES = MAX_IMAGE_BYTES + 16  # 5 MiB + 16 bytes


def _make_error(code: str, message: str, *, retryable: bool) -> WeixinILinkError:
    from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import (
        WeixinILinkError,
    )

    return WeixinILinkError(code, message, retryable=retryable)


def validate_image(image_bytes: bytes, mime_type: str) -> None:
    """Validate static PNG/JPEG and bounded animated GIF/APNG image format, size, and bounds."""
    try:
        validate_image_bounds(image_bytes, mime_type)
    except MediaInvalidError as exc:
        raise _make_error("weixin.image_invalid", str(exc), retryable=False) from None
    except Exception as exc:
        raise _make_error(
            "weixin.image_invalid",
            f"Image decoding verification failed: {exc}",
            retryable=False,
        ) from None


async def async_validate_image(
    image_bytes: bytes,
    mime_type: str,
    *,
    timeout_seconds: float = 5.0,
) -> None:
    """Asynchronously validate static and bounded animated image format, size, and bounds."""
    try:
        await async_validate_image_bounds(image_bytes, mime_type, timeout_seconds=timeout_seconds)
    except MediaInvalidError as exc:
        raise _make_error("weixin.image_invalid", str(exc), retryable=False) from None
    except TimeoutError:
        raise _make_error(
            "weixin.request_timeout", "Image validation timed out.", retryable=True
        ) from None
    except Exception as exc:
        raise _make_error(
            "weixin.image_invalid",
            f"Image decoding verification failed: {exc}",
            retryable=False,
        ) from None


def encrypt_aes_128_ecb(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt plaintext using AES-128-ECB with PKCS7 padding."""
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def encode_media_aes_key(hex_key: str) -> str:
    """Base64-encode the ASCII hex representation of the 16-byte AES key."""
    return base64.b64encode(hex_key.encode("ascii")).decode("ascii")


def resolve_cdn_upload_url(upload_full_url: object, upload_param: object, filekey: str) -> str:
    """Validate upload_full_url against strict host/scheme/path or build fallback."""
    if isinstance(upload_full_url, str) and upload_full_url.strip():
        url = upload_full_url.strip()
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise _make_error(
                "weixin.cdn_url_rejected", "Invalid CDN upload URL.", retryable=False
            ) from None
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != _ALLOWED_CDN_SCHEME
            or hostname != _ALLOWED_CDN_HOST
            or port not in _ALLOWED_CDN_PORTS
            or parsed.path != _ALLOWED_CDN_PATH
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise _make_error(
                "weixin.cdn_url_rejected",
                "WeChat returned an unsafe or invalid CDN upload URL.",
                retryable=False,
            )
        return url

    if upload_full_url is not None and not isinstance(upload_full_url, str):
        raise _make_error(
            "weixin.cdn_url_rejected",
            "WeChat returned an invalid CDN upload URL.",
            retryable=False,
        )

    if isinstance(upload_param, str) and upload_param.strip():
        param = upload_param.strip()
        return (
            f"https://{_ALLOWED_CDN_HOST}{_ALLOWED_CDN_PATH}"
            f"?encrypted_query_param={quote(param, safe='')}&filekey={quote(filekey, safe='')}"
        )

    raise _make_error(
        "weixin.upload_url_missing",
        "WeChat returned no valid CDN upload URL or parameter.",
        retryable=True,
    )


def decrypt_aes_128_ecb(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt ciphertext using AES-128-ECB with strict PKCS7 unpadding."""
    if not ciphertext or len(ciphertext) % 16 != 0:
        raise _make_error(
            "weixin.image_decrypt_failed",
            "Ciphertext length must be a non-zero multiple of 16 bytes.",
            retryable=False,
        )
    if len(key) != 16:
        raise _make_error(
            "weixin.image_key_invalid",
            "WeChat image AES key format is invalid.",
            retryable=False,
        )
    try:
        cipher = Cipher(algorithms.AES(key), modes.ECB())
        decryptor = cipher.decryptor()
        decrypted_padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(decrypted_padded) + unpadder.finalize()
    except ValueError:
        raise _make_error(
            "weixin.image_decrypt_failed",
            "Failed to decrypt image: invalid padding.",
            retryable=False,
        ) from None
    except Exception:
        raise _make_error(
            "weixin.image_decrypt_failed",
            "Failed to decrypt image.",
            retryable=False,
        ) from None


def resolve_image_aes_key(aeskey: str | None, aes_key: str | None) -> bytes | None:
    """Resolve 16-byte AES-128 key from image_item.aeskey or media.aes_key."""
    if aeskey is not None and aeskey.strip():
        s = aeskey.strip()
        try:
            raw = bytes.fromhex(s)
            if len(raw) == 16:
                return raw
        except ValueError:
            pass
        raise _make_error(
            "weixin.image_key_invalid",
            "WeChat image AES key format is invalid.",
            retryable=False,
        )
    if aes_key is not None and aes_key.strip():
        s = aes_key.strip()
        try:
            decoded = base64.b64decode(s, validate=True)
            if len(decoded) == 16:
                return decoded
            if len(decoded) == 32:
                hex_str = decoded.decode("ascii")
                raw = bytes.fromhex(hex_str)
                if len(raw) == 16:
                    return raw
        except Exception:
            pass
        raise _make_error(
            "weixin.image_key_invalid",
            "WeChat image AES key format is invalid.",
            retryable=False,
        )
    return None


def resolve_cdn_download_url(full_url: object, encrypt_query_param: object) -> str:
    """Validate full_url against strict host/scheme/path or build fallback download URL."""
    if isinstance(full_url, str) and full_url.strip():
        url = full_url.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in url):
            raise _make_error(
                "weixin.cdn_url_rejected",
                "WeChat returned a CDN download URL with control characters.",
                retryable=False,
            )
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise _make_error(
                "weixin.cdn_url_rejected", "Invalid CDN download URL.", retryable=False
            ) from None
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != _ALLOWED_CDN_SCHEME
            or hostname != _ALLOWED_CDN_HOST
            or port not in _ALLOWED_CDN_PORTS
            or parsed.path != _ALLOWED_CDN_DOWNLOAD_PATH
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.fragment)
        ):
            raise _make_error(
                "weixin.cdn_url_rejected",
                "WeChat returned an unsafe or invalid CDN download URL.",
                retryable=False,
            )
        canonical = f"https://{_ALLOWED_CDN_HOST}{_ALLOWED_CDN_DOWNLOAD_PATH}"
        if parsed.query:
            canonical = f"{canonical}?{parsed.query}"
        return canonical

    if full_url is not None and not isinstance(full_url, str):
        raise _make_error(
            "weixin.cdn_url_rejected",
            "WeChat returned an invalid CDN download URL.",
            retryable=False,
        )

    if isinstance(encrypt_query_param, str) and encrypt_query_param.strip():
        param = encrypt_query_param.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in param):
            raise _make_error(
                "weixin.cdn_url_rejected",
                "WeChat returned an encrypted query param with control characters.",
                retryable=False,
            )
        return (
            f"https://{_ALLOWED_CDN_HOST}{_ALLOWED_CDN_DOWNLOAD_PATH}"
            f"?encrypted_query_param={quote(param, safe='')}"
        )

    raise _make_error(
        "weixin.cdn_url_rejected",
        "WeChat returned no valid CDN download URL or parameter.",
        retryable=False,
    )


def sniff_image_mime_type(data: bytes) -> str:
    """Sniff MIME type from image magic bytes (PNG, JPEG, or GIF)."""
    try:
        return _central_sniff_image_mime_type(data)
    except MediaInvalidError as exc:
        raise _make_error("weixin.image_invalid", str(exc), retryable=False) from None
