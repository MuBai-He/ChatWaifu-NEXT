"""Image validation, encryption, and CDN helpers for WeChat iLink wire transport."""

from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image

if TYPE_CHECKING:
    from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import (
        WeixinILinkError,
    )

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 16_777_216

_ALLOWED_MIME_FORMATS = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
}
ALLOWED_MIME_TYPES = frozenset(_ALLOWED_MIME_FORMATS.keys())
_ALLOWED_CDN_SCHEME = "https"
_ALLOWED_CDN_HOST = "novac2c.cdn.weixin.qq.com"
_ALLOWED_CDN_PATH = "/c2c/upload"
_ALLOWED_CDN_PORTS = frozenset({None, 443})


def _make_error(code: str, message: str, *, retryable: bool) -> WeixinILinkError:
    from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import (
        WeixinILinkError,
    )

    return WeixinILinkError(code, message, retryable=retryable)


def validate_image(image_bytes: bytes, mime_type: str) -> None:
    """Validate static PNG/JPEG format, size constraints, and pixel bounds."""
    from chatwaifu_runtime.external_channels.adapters.weixin_ilink.client import WeixinILinkError

    if not image_bytes:
        raise _make_error("weixin.image_invalid", "Image data is empty.", retryable=False)

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise _make_error(
            "weixin.image_invalid",
            "Image size exceeds 5 MiB maximum limit.",
            retryable=False,
        )

    clean_mime = mime_type.split(";")[0].strip().lower()
    expected_format = _ALLOWED_MIME_FORMATS.get(clean_mime)
    if expected_format is None:
        raise _make_error(
            "weixin.image_invalid",
            f"Unsupported image MIME type: {mime_type}",
            retryable=False,
        )

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.format != expected_format:
                raise _make_error(
                    "weixin.image_invalid",
                    f"Image format mismatch: {img.format}",
                    retryable=False,
                )
            if getattr(img, "n_frames", 1) != 1 or getattr(img, "is_animated", False):
                raise _make_error(
                    "weixin.image_invalid",
                    "Animated images are not supported.",
                    retryable=False,
                )
            w, h = img.size
            if (
                w <= 0
                or h <= 0
                or w > MAX_IMAGE_DIMENSION
                or h > MAX_IMAGE_DIMENSION
                or (w * h) > MAX_IMAGE_PIXELS
            ):
                raise _make_error(
                    "weixin.image_invalid",
                    f"Image dimensions {w}x{h} exceed allowed bounds.",
                    retryable=False,
                )
            img.verify()
    except WeixinILinkError:
        raise
    except Exception as error:
        raise _make_error(
            "weixin.image_invalid",
            "Image decoding verification failed.",
            retryable=False,
        ) from error


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
