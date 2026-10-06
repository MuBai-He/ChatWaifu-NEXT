"""QQ image batches remain lazy, bounded, private and cancellable."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from typing import Literal

import pytest
from chatwaifu_runtime.external_channels.adapters.qq_napcat import media
from chatwaifu_runtime.external_channels.adapters.qq_napcat.client import NapCatError
from chatwaifu_runtime.external_channels.adapters.qq_napcat.media import image_input
from chatwaifu_runtime.external_channels.adapters.qq_napcat.messages import NapCatImageReference
from PIL import Image


def picture(fmt: Literal["PNG", "JPEG", "GIF"] = "PNG", *, with_exif: bool = False) -> bytes:
    image = Image.new("RGB", (12, 8), "red")
    output = io.BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[271] = "private-camera-label"
        image.save(output, format=fmt, exif=exif)
    else:
        image.save(output, format=fmt)
    return output.getvalue()


@dataclass
class Transport:
    files: dict[str, bytes | Exception]
    calls: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])

    async def download_image(self, file_ref: str, *, max_bytes: int) -> bytes:
        self.calls.append((file_ref, max_bytes))
        result = self.files[file_ref]
        if isinstance(result, Exception):
            raise result
        return result


async def test_image_input_is_lazy_ordered_and_fingerprint_tracks_private_descriptors() -> None:
    first, second = picture("PNG"), picture("JPEG")
    transport = Transport({"one.png": first, "two.jpg": second})
    images = (
        NapCatImageReference("one.png", len(first)),
        NapCatImageReference("two.jpg", len(second)),
    )
    attachment = image_input(transport, images)
    assert transport.calls == []
    assert attachment.source_fingerprint == image_input(transport, images).source_fingerprint
    assert (
        attachment.source_fingerprint
        != image_input(transport, tuple(reversed(images))).source_fingerprint
    )
    changed = (NapCatImageReference("one.png", len(first) + 1), images[1])
    assert attachment.source_fingerprint != image_input(transport, changed).source_fingerprint
    assert "one.png" not in repr(attachment)
    loaded = await attachment.load()
    assert isinstance(loaded, tuple)
    assert [item.mime_type for item in loaded] == ["image/png", "image/jpeg"]
    assert [item.data for item in loaded] == [first, second]
    assert transport.calls == [("one.png", 5 * 1024 * 1024), ("two.jpg", 5 * 1024 * 1024)]


@pytest.mark.parametrize(
    "images",
    [
        (),
        tuple(NapCatImageReference(f"photo{i}.png") for i in range(5)),
        (NapCatImageReference("photo.png", 5 * 1024 * 1024 + 1),),
        (NapCatImageReference("photo.png", invalid_reason="invalid_size"),),
        (NapCatImageReference("../private.png"),),
    ],
)
async def test_invalid_whole_batch_performs_no_download(
    images: tuple[NapCatImageReference, ...],
) -> None:
    transport = Transport({})
    with pytest.raises(NapCatError, match="QQ image is unavailable"):
        await image_input(transport, images).load()
    assert transport.calls == []


@pytest.mark.parametrize(
    "second",
    [
        picture("GIF"),
        b"not-an-image",
        b"\x89PNG\r\n\x1a\ninvalid",
        RuntimeError("private-image-url-and-token"),
    ],
)
async def test_second_image_failure_aborts_batch_and_sanitizes_error(
    second: bytes | Exception,
) -> None:
    transport = Transport({"one.png": picture(), "two.png": second})
    with pytest.raises(NapCatError) as failure:
        await image_input(
            transport, (NapCatImageReference("one.png"), NapCatImageReference("two.png"))
        ).load()
    assert "private" not in str(failure.value)
    assert len(transport.calls) == 2


async def test_original_exif_is_removed_before_image_reaches_vision() -> None:
    original = picture("JPEG", with_exif=True)
    transport = Transport({"photo.jpg": original})
    loaded = await image_input(transport, (NapCatImageReference("photo.jpg"),)).load()
    assert isinstance(loaded, tuple)
    assert loaded[0].data != original
    with Image.open(io.BytesIO(loaded[0].data)) as image:
        assert not image.getexif()


async def test_download_byte_bound_and_aggregate_bound_remain_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = picture()
    transport = Transport({"one.png": data, "two.png": data})
    monkeypatch.setattr(media, "MAX_TOTAL_IMAGE_BYTES", len(data))
    with pytest.raises(NapCatError):
        await image_input(
            transport, (NapCatImageReference("one.png"), NapCatImageReference("two.png"))
        ).load()
    assert len(transport.calls) == 2


async def test_download_cancellation_propagates_and_never_starts_next_image() -> None:
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    class BlockedTransport:
        async def download_image(self, file_ref: str, *, max_bytes: int) -> bytes:
            calls.append(file_ref)
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return picture()

    async def load() -> None:
        await image_input(
            BlockedTransport(), (NapCatImageReference("one.png"), NapCatImageReference("two.png"))
        ).load()

    task = asyncio.create_task(load())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert calls == ["one.png"]


async def test_one_timeout_covers_the_entire_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()

    class BlockedTransport:
        async def download_image(self, file_ref: str, *, max_bytes: int) -> bytes:
            entered.set()
            await asyncio.Event().wait()
            return picture()

    monkeypatch.setattr(media, "IMAGE_BATCH_TIMEOUT_SECONDS", 0.02)
    with pytest.raises(NapCatError, match="QQ image is unavailable"):
        await image_input(BlockedTransport(), (NapCatImageReference("one.png"),)).load()
    assert entered.is_set()
