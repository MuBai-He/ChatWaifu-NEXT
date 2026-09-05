"""Accepted stickers strip metadata, bound resolution, and deduplicate normalized pixels."""
# pyright: reportPrivateUsage=false

import io

import pytest
from chatwaifu_runtime.providers.contracts import LlmInputImage
from chatwaifu_runtime.sticker_library.service import _normalize_sticker
from PIL import Image, PngImagePlugin


def _png(*, caption: str, size: tuple[int, int] = (2000, 1000)) -> LlmInputImage:
    canvas = Image.new("RGBA", size, (100, 80, 30, 100))
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", caption)
    output = io.BytesIO()
    canvas.save(output, format="PNG", pnginfo=metadata)
    return LlmInputImage(data=output.getvalue(), mime_type="image/png")


def test_normalization_strips_metadata_and_preserves_bounded_pixels() -> None:
    data = _normalize_sticker(_png(caption="private source metadata"))
    with Image.open(io.BytesIO(data)) as result:
        assert result.size == (1024, 512)
        assert result.mode == "RGBA"
        assert result.info == {}
        pixel = result.getpixel((100, 100))
        assert isinstance(pixel, tuple)
        assert pixel[3] == 100
    assert b"private source metadata" not in data
    assert data == _normalize_sticker(_png(caption="different metadata"))


def test_normalization_rejects_decodable_non_supported_format() -> None:
    output = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(output, format="GIF")
    with pytest.raises(ValueError, match="unsupported"):
        _normalize_sticker(LlmInputImage(data=output.getvalue(), mime_type="image/png"))


def test_normalization_rejects_oversized_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        _normalize_sticker(_png(caption="", size=(8193, 1)))
