"""Ephemeral, bounded image loading owned by the admitted QQ generation."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from typing import Literal, Protocol

from PIL import Image

from chatwaifu_runtime.external_channels.adapters.weixin_ilink.image import (
    MAX_IMAGE_BYTES,
    MAX_INBOUND_IMAGES_PER_MESSAGE,
    MAX_TOTAL_IMAGE_BYTES,
    sniff_image_mime_type,
    validate_image,
)
from chatwaifu_runtime.external_channels.models import ChannelInboundImageInput
from chatwaifu_runtime.photo_memory.metadata import strip_image_exif
from chatwaifu_runtime.providers.contracts import LlmInputImage
from chatwaifu_runtime.sticker_library.models import StickerSavedObserver

from .client import NapCatError, validate_image_file_ref
from .messages import NapCatImageReference

IMAGE_BATCH_TIMEOUT_SECONDS = 20.0


class NapCatImageTransport(Protocol):
    async def download_image(self, file_ref: str, *, max_bytes: int) -> bytes: ...


def image_input(
    transport: NapCatImageTransport,
    images: tuple[NapCatImageReference, ...],
    *,
    on_sticker_saved: StickerSavedObserver | None = None,
) -> ChannelInboundImageInput:
    """Freeze only the source digest; download happens after durable admission."""
    references = [
        {
            "file": image.file_ref,
            "file_size": image.file_size,
            "invalid_reason": image.invalid_reason,
            **({"expected_md5": image.expected_md5} if image.expected_md5 is not None else {}),
        }
        for image in images
    ]
    fingerprint = hashlib.sha256(
        json.dumps(references, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    eligible: tuple[bool, ...] = ()

    def learning_images(loaded: tuple[LlmInputImage, ...]) -> tuple[LlmInputImage, ...]:
        if len(loaded) != len(eligible):
            raise ValueError("QQ image learning projection is unavailable")
        return tuple(image for image, allowed in zip(loaded, eligible, strict=True) if allowed)

    async def load() -> tuple[LlmInputImage, ...]:
        nonlocal eligible
        try:
            if not 1 <= len(images) <= MAX_INBOUND_IMAGES_PER_MESSAGE:
                raise NapCatError("QQ image count exceeds the supported limit")
            # Validate the whole descriptor batch before making any provider request.
            for image in images:
                validate_image_file_ref(image.file_ref)
                if image.invalid_reason is not None or (
                    image.file_size is not None and not 0 < image.file_size <= MAX_IMAGE_BYTES
                ):
                    raise NapCatError("QQ image descriptor is invalid or oversized")
            async with asyncio.timeout(IMAGE_BATCH_TIMEOUT_SECONDS):
                loaded: list[LlmInputImage] = []
                flags: list[bool] = []
                total_bytes = 0
                for image in images:
                    data = await transport.download_image(image.file_ref, max_bytes=MAX_IMAGE_BYTES)
                    if image.expected_md5 is not None and (
                        hashlib.md5(data, usedforsecurity=False).hexdigest() != image.expected_md5
                    ):
                        raise NapCatError("QQ group image checksum does not match its source")
                    data, may_learn = _static_preview(data)
                    detected = sniff_image_mime_type(data)
                    mime_type: Literal["image/png", "image/jpeg"] = (
                        "image/png" if detected == "image/png" else "image/jpeg"
                    )
                    validate_image(data, mime_type)
                    total_bytes += len(data)
                    if total_bytes > MAX_TOTAL_IMAGE_BYTES:
                        raise NapCatError("QQ image batch exceeds the supported limit")
                    loaded.append(strip_image_exif(LlmInputImage(data=data, mime_type=mime_type)))
                    flags.append(may_learn)
                eligible = tuple(flags)
                return tuple(loaded)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Provider errors must not leak a filename, signed URL or image content.
            raise NapCatError("QQ image is unavailable; please send it again") from None

    return ChannelInboundImageInput(
        source_fingerprint=fingerprint,
        load=load,
        on_sticker_saved=on_sticker_saved,
        sticker_learning_images=learning_images,
    )


def _static_preview(data: bytes) -> tuple[bytes, bool]:
    """QQ may label GIF bytes as .png. Preview one bounded frame, never retain animation."""
    if not data.startswith((b"GIF87a", b"GIF89a")):
        return data, True
    with Image.open(io.BytesIO(data)) as source:
        if (
            source.format != "GIF"
            or source.width > 8192
            or source.height > 8192
            or source.width * source.height > 16_777_216
        ):
            raise NapCatError("QQ GIF dimensions exceed their limit")
        first = source.convert("RGBA")
        try:
            source.seek(1)  # Bound inspection to two frames; do not enumerate an entire GIF.
        except EOFError:
            may_learn = True
        else:
            may_learn = False
        clean = Image.new("RGBA", first.size)
        clean.paste(first)
        output = io.BytesIO()
        clean.save(output, format="PNG")
        preview = output.getvalue()
        if len(preview) > MAX_IMAGE_BYTES:
            raise NapCatError("QQ GIF preview exceeds its byte limit")
        return preview, may_learn
