# pyright: reportPrivateUsage=false
"""Comprehensive safety and boundary validation suite for animated GIF/APNG media pipeline.

Covers:
1. Self-contained dynamic generation (no local absolute fixtures).
2. Transparent GIF pixel equality and disposal cleanliness (alpha 0, no trails).
3. APNG blend and default poster handling, including 60 animation frames + poster (61 physical).
4. Preservation of loop intent (absent loop, loop 0 infinite, finite loops).
5. Static GIF conversion to clean PNG raster container and complete metadata stripping.
6. Authoritative total frame count and PTS-based temporal sampling in storyboards.
7. Rejections for >60 frames, cumulative decoded pixels >32M, and total duration >60s.
8. Bounded concurrency (max 2 outstanding decoder jobs) under repeated cancellation.
9. Non-blocking stop responsiveness and graceful rejection of new requests.
"""

from __future__ import annotations

import asyncio
import io
import threading
from typing import Any

import pytest
from chatwaifu_runtime.media import (
    MediaExecutionPool,
    MediaInvalidError,
    async_decode_and_sanitize_inbound_media,
    async_extract_static_poster,
    async_normalize_animated_sticker,
    async_validate_image_bounds,
    decode_and_sanitize_inbound_media,
    normalize_animated_sticker,
    strip_static_image_exif,
    validate_image_bounds,
)
from chatwaifu_runtime.providers.contracts import LlmInputImage
from PIL import Image, PngImagePlugin

# =============================================================================
# Self-contained fixture generators (No local absolute fixtures)
# =============================================================================


def _make_transparent_disposal_gif(
    num_frames: int = 8,
    size: tuple[int, int] = (100, 60),
    loop: int | None = 2,
    duration: int = 200,
) -> bytes:
    """Generate a multi-frame GIF with transparent background and moving shape with disposal 2."""
    frames: list[Image.Image] = []
    w, h = size
    box_size = 16

    for i in range(num_frames):
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        # Moving box: red for first half, blue for second half
        color = (255, 0, 0, 255) if i < (num_frames // 2) else (0, 0, 255, 255)
        x_start = 5 + i * 10
        y_start = 10
        for bx in range(x_start, min(w, x_start + box_size)):
            for by in range(y_start, min(h, y_start + box_size)):
                img.putpixel((bx, by), color)

        alpha = img.getchannel("A")
        rgb = img.convert("RGB")
        p_img = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        mask = Image.eval(alpha, lambda a: 255 if a < 128 else 0)
        p_img.paste(255, mask)
        p_img.info["transparency"] = 255
        frames.append(p_img)

    out = io.BytesIO()
    save_kwargs: dict[str, Any] = {
        "format": "GIF",
        "save_all": True,
        "append_images": frames[1:],
        "duration": [duration] * num_frames,
        "disposal": 2,
        "transparency": 255,
    }
    if loop is not None:
        save_kwargs["loop"] = loop
    frames[0].save(out, **save_kwargs)
    return out.getvalue()


def _make_apng_with_default_poster(
    num_animation_frames: int = 8,
    size: tuple[int, int] = (80, 50),
    loop: int | None = 2,
    duration: int = 150,
) -> bytes:
    """Generate an APNG with default_image=True (frame 0 poster, frames 1..N animation)."""
    w, h = size
    # Frame 0: solid green poster
    poster = Image.new("RGBA", size, (0, 255, 0, 255))
    frames = [poster]

    box_size = min(12, max(2, w // 4))
    for i in range(num_animation_frames):
        f = Image.new("RGBA", size, (0, 0, 0, 0))
        color = ((i * 25) % 240 + 10, (i * 15) % 240 + 10, 200, 255)
        x_start = (2 + i * 2) % max(1, w - box_size)
        y_start = 2
        for bx in range(x_start, min(w, x_start + box_size)):
            for by in range(y_start, min(h, y_start + box_size)):
                f.putpixel((bx, by), color)
        frames.append(f)

    out = io.BytesIO()
    save_kwargs: dict[str, Any] = {
        "format": "PNG",
        "save_all": True,
        "append_images": frames[1:],
        "duration": [duration] * len(frames),
        "default_image": True,
    }
    if loop is not None:
        save_kwargs["loop"] = loop
    frames[0].save(out, **save_kwargs)
    return out.getvalue()


# =============================================================================
# 1. Transparent GIF pixel equality and no trails
# =============================================================================


def _get_rgba(img: Image.Image, xy: tuple[int, int]) -> tuple[int, int, int, int]:
    px = img.getpixel(xy)
    assert isinstance(px, tuple) and len(px) >= 4
    return (int(px[0]), int(px[1]), int(px[2]), int(px[3]))


def test_transparent_gif_pixel_fidelity_and_no_trails() -> None:
    gif_bytes = _make_transparent_disposal_gif(num_frames=8, loop=2, duration=200)
    norm_bytes, mime_type, is_animated = normalize_animated_sticker(gif_bytes, "image/gif")

    assert is_animated is True
    assert mime_type == "image/gif"

    with Image.open(io.BytesIO(norm_bytes)) as out_img:
        assert getattr(out_img, "n_frames", 1) == 8
        assert out_img.info.get("loop") == 2

        for i in range(8):
            out_img.seek(i)
            rgba = out_img.convert("RGBA")

            # Corner pixel MUST be transparent alpha 0, NOT opaque black (0,0,0,255)
            corner_px = _get_rgba(rgba, (0, 0))
            assert corner_px[3] == 0, f"Frame {i} corner was not transparent: {corner_px}"

            # Current box position: 5 + i * 10
            curr_box_x = 5 + i * 10 + 2
            curr_box_y = 12
            box_px = _get_rgba(rgba, (curr_box_x, curr_box_y))
            assert box_px[3] > 0, f"Frame {i} expected opaque pixel at box: {box_px}"

            # If i > 0, previous frame's box position MUST be disposed to transparent
            if i > 0:
                prev_box_x = 5 + (i - 1) * 10 + 2
                prev_box_y = 12
                if prev_box_x < (5 + i * 10):
                    prev_px = _get_rgba(rgba, (prev_box_x, prev_box_y))
                    assert prev_px[3] == 0, f"Frame {i} had trail from frame {i - 1}: {prev_px}"


# =============================================================================
# 2. APNG blend and 60 animation frames + default poster validity
# =============================================================================


def test_apng_blend_and_default_poster_60_plus_poster_valid() -> None:
    # Exactly 60 animation frames + 1 default poster = 61 physical frames total
    apng_60_bytes = _make_apng_with_default_poster(
        num_animation_frames=60, size=(16, 16), duration=50
    )

    # validate_image_bounds MUST accept 60 animation frames + default poster
    validate_image_bounds(apng_60_bytes, "image/png")

    # decode_and_sanitize_inbound_media should exclude poster from count and report 60
    item = decode_and_sanitize_inbound_media(apng_60_bytes, "image/png")
    assert item.is_animated is True
    assert item.frame_count == 60
    assert item.storyboard is not None
    assert item.storyboard.total_frames == 60
    assert len(item.storyboard.frames) == 4

    # 61 animation frames + 1 default poster = 62 physical frames total -> MUST BE REJECTED
    apng_61_bytes = _make_apng_with_default_poster(
        num_animation_frames=61, size=(16, 16), duration=50
    )
    with pytest.raises(MediaInvalidError, match="frame count"):
        validate_image_bounds(apng_61_bytes, "image/png")


def test_apng_source_blend_pixel_fidelity() -> None:
    apng_bytes = _make_apng_with_default_poster(num_animation_frames=6, size=(60, 40))
    norm_bytes, mime_type, is_animated = normalize_animated_sticker(apng_bytes, "image/png")

    assert is_animated is True
    assert mime_type == "image/png"

    with Image.open(io.BytesIO(norm_bytes)) as img:
        assert getattr(img, "n_frames", 1) == 6
        for i in range(6):
            img.seek(i)
            rgba = img.convert("RGBA")
            # Corner must be transparent alpha 0
            corner_px = _get_rgba(rgba, (0, 0))
            assert corner_px[3] == 0, f"APNG frame {i} corner had alpha {corner_px[3]}"


# =============================================================================
# 3. Loop intent preservation: absent, zero, finite
# =============================================================================


def test_loop_intent_preservation_gif_and_apng() -> None:
    # 1. Absent loop in GIF -> MUST REMAIN ABSENT (None), NOT CONVERTED TO 0
    gif_no_loop = _make_transparent_disposal_gif(num_frames=2, loop=None)
    norm_no_loop, _, _ = normalize_animated_sticker(gif_no_loop, "image/gif")
    with Image.open(io.BytesIO(norm_no_loop)) as img:
        assert img.info.get("loop") is None

    # 2. Loop 0 in GIF -> MUST REMAIN 0 (infinite)
    gif_loop_0 = _make_transparent_disposal_gif(num_frames=2, loop=0)
    norm_loop_0, _, _ = normalize_animated_sticker(gif_loop_0, "image/gif")
    with Image.open(io.BytesIO(norm_loop_0)) as img:
        assert img.info.get("loop") == 0

    # 3. Loop 5 in GIF -> MUST REMAIN 5 (finite)
    gif_loop_5 = _make_transparent_disposal_gif(num_frames=2, loop=5)
    norm_loop_5, _, _ = normalize_animated_sticker(gif_loop_5, "image/gif")
    with Image.open(io.BytesIO(norm_loop_5)) as img:
        assert img.info.get("loop") == 5

    # 4. Loop 3 in APNG -> MUST REMAIN 3
    apng_loop_3 = _make_apng_with_default_poster(num_animation_frames=2, loop=3)
    norm_apng_3, _, _ = normalize_animated_sticker(apng_loop_3, "image/png")
    with Image.open(io.BytesIO(norm_apng_3)) as img:
        assert img.info.get("loop") == 3


# =============================================================================
# 4. Static GIF converted to clean PNG and all metadata stripped
# =============================================================================


def test_static_gif_converted_to_sanitized_png_and_metadata_stripped() -> None:
    # Create static GIF with comment metadata
    img = Image.new("RGB", (32, 32), (180, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="GIF", comment=b"confidential-gps-metadata")
    static_gif_bytes = buf.getvalue()
    assert b"confidential-gps-metadata" in static_gif_bytes

    # decode_and_sanitize_inbound_media
    item = decode_and_sanitize_inbound_media(static_gif_bytes, "image/gif")

    assert item.is_animated is False
    assert item.frame_count == 1
    assert item.original_mime_type == "image/gif"
    assert item.raw_data == static_gif_bytes

    # Raster image must be clean PNG with PNG magic, NOT labeled JPEG while remaining GIF
    assert item.raster_image.mime_type == "image/png"
    assert item.raster_image.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"confidential-gps-metadata" not in item.raster_image.data


def test_strip_static_image_exif_on_empty_exif_png_and_gif() -> None:
    # PNG with text chunks but empty EXIF
    meta = PngImagePlugin.PngInfo()
    meta.add_text("SecretTag", "super-secret-user-id")
    png_buf = io.BytesIO()
    Image.new("RGBA", (20, 20), (50, 100, 150, 255)).save(png_buf, format="PNG", pnginfo=meta)
    png_data = png_buf.getvalue()
    assert b"super-secret-user-id" in png_data

    cleaned = strip_static_image_exif(LlmInputImage(data=png_data, mime_type="image/png"))
    assert cleaned.mime_type == "image/png"
    assert b"super-secret-user-id" not in cleaned.data


# =============================================================================
# 5. Authoritative total frame count and PTS temporal sampling
# =============================================================================


def test_storyboard_real_total_frames_and_pts_temporal_sampling() -> None:
    # 12-frame animation: frame 0 is 600ms, next 10 are 30ms, last is 100ms. Total = 1000ms.
    frames: list[Image.Image] = []
    durations: list[int] = [600] + [30] * 10 + [100]
    for i in range(12):
        frames.append(Image.new("RGBA", (40, 40), (i * 20, 50, 100, 255)))

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
    )
    data = buf.getvalue()

    item = decode_and_sanitize_inbound_media(data, "image/gif")
    assert item.is_animated is True
    assert item.frame_count == 12
    assert item.storyboard is not None

    # Authoritative total frames MUST be 12, NOT 4!
    assert item.storyboard.total_frames == 12
    assert len(item.storyboard.frames) == 4

    # First sampled frame MUST be frame 0 (pts 0)
    assert item.storyboard.frames[0].frame_index == 0
    assert item.storyboard.frames[0].pts_ms == 0

    # Last sampled frame MUST be frame 11 (pts 900)
    assert item.storyboard.frames[-1].frame_index == 11
    assert item.storyboard.frames[-1].pts_ms == 900

    # Description format includes authoritative count and frame timestamps
    desc = item.storyboard.format_vision_description()
    assert "total 12 frames" in desc
    assert "Frame 1 at" in desc
    assert "Frame 12 at" in desc


# =============================================================================
# 6. Guardrail limits (>60 frames, >32M cumulative pixels, >60s duration)
# =============================================================================


def test_animation_guardrail_rejections() -> None:
    # 1. >60 frames without poster (61 frames with distinct colors)
    frames_61 = [Image.new("RGB", (10, 10), (i, i, i)) for i in range(61)]
    buf61 = io.BytesIO()
    frames_61[0].save(buf61, format="GIF", save_all=True, append_images=frames_61[1:])
    with pytest.raises(MediaInvalidError, match="frame count"):
        validate_image_bounds(buf61.getvalue(), "image/gif")

    # 2. Total duration > 60s (60,000ms)
    frames_dur = [Image.new("RGB", (10, 10), (i * 50, 0, 0)) for i in range(2)]
    buf_dur = io.BytesIO()
    frames_dur[0].save(
        buf_dur, format="GIF", save_all=True, append_images=[frames_dur[1]], duration=35000
    )
    # Total duration = 35000 * 2 = 70000ms > 60000ms
    with pytest.raises(MediaInvalidError, match="duration"):
        validate_image_bounds(buf_dur.getvalue(), "image/gif")
    with pytest.raises(MediaInvalidError, match="duration"):
        normalize_animated_sticker(buf_dur.getvalue(), "image/gif")

    # 3. Cumulative decoded pixels > 32M
    # 40 frames of 1000x1000 = 40,000,000 pixels > 32M limit
    large_frames = [Image.new("RGB", (1000, 1000), (i, 50, 50)) for i in range(40)]
    buf_px = io.BytesIO()
    large_frames[0].save(buf_px, format="GIF", save_all=True, append_images=large_frames[1:])
    with pytest.raises(MediaInvalidError, match="decoded pixels"):
        validate_image_bounds(buf_px.getvalue(), "image/gif")


# =============================================================================
# 7. Bounded concurrency, repeated cancellation, and no worker leaks
# =============================================================================


@pytest.mark.asyncio
async def test_bounded_execution_pool_under_repeated_cancel() -> None:
    pool = MediaExecutionPool(max_jobs=2, max_queue=4)
    loop = asyncio.get_running_loop()

    started_events = [asyncio.Event() for _ in range(2)]
    done_events = [asyncio.Event() for _ in range(2)]
    release_flags = [threading.Event() for _ in range(2)]

    def slow_task(
        task_id: int,
        *,
        deadline: float | None = None,
        check_cancelled: Any = None,
    ) -> str:
        loop.call_soon_threadsafe(started_events[task_id].set)
        try:
            assert release_flags[task_id].wait(5)
            if check_cancelled is not None and check_cancelled():
                return "cancelled"
            return f"done-{task_id}"
        finally:
            loop.call_soon_threadsafe(done_events[task_id].set)

    # Launch task 0 and task 1: they should immediately acquire the 2 active slots
    t0 = asyncio.create_task(pool.run_bounded(slow_task, 0))
    t1 = asyncio.create_task(pool.run_bounded(slow_task, 1))

    # Wait for both workers to start running without arbitrary sleep
    await asyncio.gather(started_events[0].wait(), started_events[1].wait())
    assert pool.active_jobs == 2

    # Now cancel t0: awaiter must return promptly with CancelledError
    t0.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t0

    # Awaiter returned promptly, but slot is held until thread finishes
    assert pool.active_jobs == 2

    # Unblock the simulated single C operation; cancellation is observed afterward.
    release_flags[0].set()
    await done_events[0].wait()

    # A barrier job observes released capacity after wrapper cleanup, not merely
    # the worker function's finally block.
    def barrier(**_kwargs: Any) -> str:
        return "barrier"

    assert await pool.run_bounded(barrier) == "barrier"
    assert pool.active_jobs == 1

    # Release worker 1 and wait for completion
    release_flags[1].set()
    res1 = await t1
    assert res1 == "done-1"
    await done_events[1].wait()
    assert pool.active_jobs == 0

    pool.stop()


@pytest.mark.asyncio
async def test_stop_responsiveness_and_busy_rejection() -> None:
    pool = MediaExecutionPool(max_jobs=2, max_queue=2)
    pool.stop()

    # After stop, immediate graceful rejection without blocking
    with pytest.raises(MediaInvalidError, match="stopping"):
        await pool.run_bounded(lambda *args, **kwargs: "never-run")


# =============================================================================
# 8. Async wrapper operations across actual validation and stickers
# =============================================================================


@pytest.mark.asyncio
async def test_async_validation_and_poster_helpers() -> None:
    gif_bytes = _make_transparent_disposal_gif(num_frames=4, loop=0)

    # Test async_validate_image_bounds
    await async_validate_image_bounds(gif_bytes, "image/gif")

    # Test async_decode_and_sanitize_inbound_media
    item = await async_decode_and_sanitize_inbound_media(gif_bytes, "image/gif")
    assert item.is_animated is True
    assert item.frame_count == 4

    # Test async_normalize_animated_sticker
    _norm_bytes, mime, is_anim = await async_normalize_animated_sticker(gif_bytes, "image/gif")
    assert is_anim is True
    assert mime == "image/gif"

    # Test async_extract_static_poster
    poster_bytes = await async_extract_static_poster(gif_bytes, "image/gif")
    assert poster_bytes.startswith(b"\x89PNG\r\n\x1a\n")
