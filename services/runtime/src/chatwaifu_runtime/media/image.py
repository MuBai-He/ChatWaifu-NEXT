"""Central bounded media helper for image inspection, decoding, bounds enforcement,
temporal storyboard synthesis, and sticker normalization.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal

from PIL import Image, ImageOps

from chatwaifu_runtime.media.contracts import (
    InboundMediaItem,
    StoryboardFrame,
    StoryboardMetadata,
)
from chatwaifu_runtime.media.executor import (
    DEFAULT_MEDIA_TIMEOUT_SECONDS,
    get_default_media_pool,
)
from chatwaifu_runtime.providers.contracts import LlmInputImage

# Hard finite bounds for DoS / decompression bomb prevention
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MiB encoded per source
MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MiB decoded aggregate batch limit

# Static image bounds
MAX_STATIC_DIMENSION = 8192
MAX_STATIC_PIXELS = 16_777_216

# Animated image bounds
MAX_ANIMATED_FRAMES = 60
MAX_ANIMATED_DIMENSION = 4096
MAX_CUMULATIVE_DECODED_CANVAS_PIXELS = 32_000_000
MAX_ANIMATION_DURATION_MS = 60_000  # 60 seconds

# Storyboard bounds
MAX_STORYBOARD_DIMENSION = 1024
MAX_STORYBOARD_FRAMES = 4
DEFAULT_DECODE_TIMEOUT_SECONDS = DEFAULT_MEDIA_TIMEOUT_SECONDS

# Sticker bounds
MAX_STICKER_DIMENSION = 1024

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
GIF87A_MAGIC = b"GIF87a"
GIF89A_MAGIC = b"GIF89a"

_ALLOWED_MIME_FORMATS = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/gif": "GIF",
}


class MediaInvalidError(ValueError):
    """Raised when an image payload is invalid, unsupported, or exceeds bounds."""


def sniff_image_mime_type(data: bytes) -> str:
    """Sniff MIME type from image magic bytes (PNG, JPEG, or GIF)."""
    if data.startswith(PNG_MAGIC):
        return "image/png"
    if data.startswith(JPEG_MAGIC):
        return "image/jpeg"
    if data.startswith(GIF87A_MAGIC) or data.startswith(GIF89A_MAGIC):
        return "image/gif"
    raise MediaInvalidError("Unsupported image format: expected PNG, JPEG, or GIF.")


def validate_image_bounds(
    data: bytes,
    mime_type: str,
    *,
    deadline: float | None = None,
    check_cancelled: Callable[[], bool] | None = None,
) -> None:
    """Validate format, size constraints, and pixel bounds for static and animated media.

    Enforces finite sequential seek capped at 60 animation frames (APNG default poster
    excluded from frame count but counted in decoded canvas budget).
    Verifies frame integrity, cumulative decoded pixels <= 32M, and animation duration <= 60s.
    Never reads unbounded PIL n_frames or is_animated.
    Checks deadline and cancellation before inspection and between bounded operations.
    """
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError("Image validation deadline exceeded.")
    if check_cancelled is not None and check_cancelled():
        raise asyncio.CancelledError("Image validation cancelled.")

    if not data:
        raise MediaInvalidError("Image data is empty.")

    if len(data) > MAX_IMAGE_BYTES:
        raise MediaInvalidError(f"Image size {len(data)} exceeds {MAX_IMAGE_BYTES} bytes limit.")

    clean_mime = mime_type.split(";")[0].strip().lower()
    expected_format = _ALLOWED_MIME_FORMATS.get(clean_mime)
    if expected_format is None:
        raise MediaInvalidError(f"Unsupported image MIME type: {mime_type}")

    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format != expected_format:
                raise MediaInvalidError(
                    f"unsupported format mismatch: expected {expected_format}, got {img.format}"
                )

            w, h = img.size
            if w <= 0 or h <= 0:
                raise MediaInvalidError(f"Invalid image dimensions: {w}x{h}")

            # Early dimensions check BEFORE traversal or allocation
            if w > MAX_STATIC_DIMENSION or h > MAX_STATIC_DIMENSION or (w * h) > MAX_STATIC_PIXELS:
                raise MediaInvalidError(f"Static image dimensions {w}x{h} exceed allowed bounds.")

            is_apng = clean_mime == "image/png"
            default_image = bool(
                is_apng
                and (getattr(img, "default_image", False) or img.info.get("default_image", False))
            )

            is_animated = False
            try:
                img.seek(1)
                is_animated = True
            except EOFError:
                is_animated = False

            if default_image and not is_animated:
                raise MediaInvalidError("APNG with default_image has no animation frames.")

            if not is_animated:
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Image validation deadline exceeded.")
                if check_cancelled is not None and check_cancelled():
                    raise asyncio.CancelledError("Image validation cancelled.")
                img.seek(0)
                img.load()
                return

            # Animated image bounds enforcement
            if w > MAX_ANIMATED_DIMENSION or h > MAX_ANIMATED_DIMENSION:
                raise MediaInvalidError(
                    f"Animated image dimensions {w}x{h} exceed {MAX_ANIMATED_DIMENSION} limit."
                )

            start_frame = 1 if default_image else 0
            cumulative_pixels = 0
            if default_image:
                # Frame 0 is default poster: counted in decoded budget
                cumulative_pixels += w * h
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Image validation deadline exceeded.")
                if check_cancelled is not None and check_cancelled():
                    raise asyncio.CancelledError("Image validation cancelled.")
                img.seek(0)
                img.load()

            animation_frames = 0
            total_duration_ms = 0
            frame_idx = start_frame

            while True:
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Image validation deadline exceeded.")
                if check_cancelled is not None and check_cancelled():
                    raise asyncio.CancelledError("Image validation cancelled.")

                if animation_frames >= MAX_ANIMATED_FRAMES:
                    try:
                        img.seek(frame_idx)
                        raise MediaInvalidError(
                            f"Animated frame count exceeds {MAX_ANIMATED_FRAMES} limit."
                        )
                    except EOFError:
                        break

                try:
                    img.seek(frame_idx)
                except EOFError:
                    break

                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Image validation deadline exceeded.")
                if check_cancelled is not None and check_cancelled():
                    raise asyncio.CancelledError("Image validation cancelled.")

                # GIF frame descriptors can enlarge the logical canvas on seek.
                frame_w, frame_h = img.size
                if frame_w > MAX_ANIMATED_DIMENSION or frame_h > MAX_ANIMATED_DIMENSION:
                    raise MediaInvalidError("Animated frame dimensions exceed allowed bounds.")
                cumulative_pixels += frame_w * frame_h
                if cumulative_pixels > MAX_CUMULATIVE_DECODED_CANVAS_PIXELS:
                    raise MediaInvalidError(
                        "Animated image potential decoded pixels exceed 32 million limit."
                    )
                img.load()

                dur = img.info.get("duration")
                if dur is None or int(dur) <= 0:
                    frame_dur = 100
                else:
                    frame_dur = int(dur)

                total_duration_ms += frame_dur
                if total_duration_ms > MAX_ANIMATION_DURATION_MS:
                    raise MediaInvalidError(
                        f"Animation duration exceeds {MAX_ANIMATION_DURATION_MS}ms limit."
                    )

                animation_frames += 1
                frame_idx += 1

            if animation_frames < 1:
                raise MediaInvalidError("Animated image has no valid animation frames.")

    except MediaInvalidError:
        raise
    except (TimeoutError, asyncio.CancelledError):
        raise
    except Exception as exc:
        raise MediaInvalidError(f"Image decoding verification failed: {exc}") from None


async def async_validate_image_bounds(
    data: bytes,
    mime_type: str,
    *,
    timeout_seconds: float = DEFAULT_DECODE_TIMEOUT_SECONDS,
) -> None:
    """Asynchronously validate image bounds within the bounded execution pool."""
    pool = get_default_media_pool()
    await pool.run_bounded(
        validate_image_bounds,
        data,
        mime_type,
        timeout_seconds=timeout_seconds,
    )


def strip_static_image_exif(image: LlmInputImage) -> LlmInputImage:
    """Remove EXIF, ICC profiles, text comments, and container metadata from a static raster image.

    Always re-encodes to a clean raster container (PNG for GIF/PNG or images with alpha,
    JPEG for opaque JPEG images). Strips all private metadata while preserving pixel data.
    """
    if not image.data:
        return image

    try:
        with Image.open(io.BytesIO(image.data)) as img:
            fmt = img.format
            try:
                transposed = ImageOps.exif_transpose(img)
            except Exception:
                transposed = img.copy()

            has_alpha = transposed.mode in ("RGBA", "LA", "PA") or (
                transposed.mode == "P" and "transparency" in transposed.info
            )

            # Convert static GIF, PNG, or any image with transparency to a clean PNG
            if fmt in ("GIF", "PNG") or has_alpha or image.mime_type == "image/png":
                target_mode = "RGBA" if has_alpha else "RGB"
                clean = Image.new(target_mode, transposed.size)
                clean.paste(transposed.convert(target_mode))
                output = io.BytesIO()
                clean.save(output, format="PNG")
                return LlmInputImage(data=output.getvalue(), mime_type="image/png")
            else:
                clean = Image.new("RGB", transposed.size)
                clean.paste(transposed.convert("RGB"))
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=90)
                return LlmInputImage(data=output.getvalue(), mime_type="image/jpeg")
    except Exception as exc:
        raise MediaInvalidError("cannot sanitize inbound image") from exc


def _sample_frames_by_pts(
    durations: Sequence[int],
    max_samples: int = MAX_STORYBOARD_FRAMES,
) -> list[int]:
    """Sample up to max_samples frame indices temporally based on Presentation Timestamp (PTS).

    Guarantees:
    - Frame 0 (start) is included.
    - Frame N-1 (end) is included when N > 1.
    - Intermediate samples reflect temporal progression across the animation duration.
    - Resulting indices are sorted and deduplicated.
    """
    n = len(durations)
    if n <= max_samples:
        return list(range(n))
    if max_samples == 1:
        return [0]

    total_dur = sum(durations)
    pts_list = [0]
    for d in durations[:-1]:
        pts_list.append(pts_list[-1] + d)

    def _find_frame_at_t(target_t: float) -> int:
        for idx, (pts, dur) in enumerate(zip(pts_list, durations, strict=True)):
            if pts <= target_t < pts + dur:
                return idx
        return n - 1

    if max_samples == 2:
        return [0, n - 1]

    if max_samples == 3:
        i1 = _find_frame_at_t(total_dur / 2.0)
        i1 = max(1, min(n - 2, i1))
        return [0, i1, n - 1]

    # For max_samples == 4:
    t1 = total_dur / 3.0
    t2 = total_dur * 2.0 / 3.0
    i0 = 0
    i3 = n - 1
    i1 = _find_frame_at_t(t1)
    i2 = _find_frame_at_t(t2)

    # Ensure 0 < i1 < i2 < i3 for n >= 4
    i1 = max(1, min(n - 3, i1))
    i2 = max(i1 + 1, min(n - 2, i2))
    return [i0, i1, i2, i3]


def _has_transparent_pixels(alpha_channel: Image.Image) -> bool:
    extrema = alpha_channel.getextrema()
    first = extrema[0]
    min_alpha = first[0] if isinstance(first, tuple) else first
    return float(min_alpha) < 255.0


def synthesize_storyboard(
    frames: Sequence[Image.Image],
    frame_metadata: Sequence[StoryboardFrame],
    total_duration_ms: int,
    total_frames: int | None = None,
) -> tuple[LlmInputImage, StoryboardMetadata]:
    """Composite up to 4 frames into a clean storyboard grid (bounded to max 1024x1024).

    Composites alpha over an explicit neutral background (240, 240, 240) before RGB conversion,
    ensuring transparent pixels do not turn black.
    Records the authoritative total_frames count in StoryboardMetadata.
    """
    if not frames:
        raise MediaInvalidError("No frames provided for storyboard synthesis.")

    count = len(frames)
    if count > MAX_STORYBOARD_FRAMES:
        frames = frames[:MAX_STORYBOARD_FRAMES]
        frame_metadata = frame_metadata[:MAX_STORYBOARD_FRAMES]
        count = len(frames)

    def _flatten_frame(f: Image.Image) -> Image.Image:
        if f.mode != "RGBA":
            f = f.convert("RGBA")
        alpha = f.getchannel("A")
        if _has_transparent_pixels(alpha):
            bg = Image.new("RGBA", f.size, (240, 240, 240, 255))
            f = Image.alpha_composite(bg, f)
        return f.convert("RGB")

    flat_frames = [_flatten_frame(f) for f in frames]
    orig_w, orig_h = flat_frames[0].size
    gap = 4

    if count == 1:
        max_dim = MAX_STORYBOARD_DIMENSION
        scale = min(max_dim / max(orig_w, 1), max_dim / max(orig_h, 1), 1.0)
        out_w = max(1, int(orig_w * scale))
        out_h = max(1, int(orig_h * scale))
        board = flat_frames[0].resize((out_w, out_h), Image.Resampling.LANCZOS)
        layout_desc = "single frame"
    elif count == 2:
        max_cell_w = (MAX_STORYBOARD_DIMENSION - gap * 3) // 2
        max_cell_h = MAX_STORYBOARD_DIMENSION - gap * 2
        scale = min(max_cell_w / max(orig_w, 1), max_cell_h / max(orig_h, 1), 1.0)
        tw = max(1, int(orig_w * scale))
        th = max(1, int(orig_h * scale))
        board_w = min(MAX_STORYBOARD_DIMENSION, tw * 2 + gap * 3)
        board_h = min(MAX_STORYBOARD_DIMENSION, th + gap * 2)
        board = Image.new("RGB", (board_w, board_h), (240, 240, 240))
        board.paste(flat_frames[0].resize((tw, th), Image.Resampling.LANCZOS), (gap, gap))
        board.paste(flat_frames[1].resize((tw, th), Image.Resampling.LANCZOS), (gap * 2 + tw, gap))
        layout_desc = "1x2 horizontal sequence (reading order: left, right)"
    else:
        max_cell_w = (MAX_STORYBOARD_DIMENSION - gap * 3) // 2
        max_cell_h = (MAX_STORYBOARD_DIMENSION - gap * 3) // 2
        scale = min(max_cell_w / max(orig_w, 1), max_cell_h / max(orig_h, 1), 1.0)
        tw = max(1, int(orig_w * scale))
        th = max(1, int(orig_h * scale))
        board_w = min(MAX_STORYBOARD_DIMENSION, tw * 2 + gap * 3)
        board_h = min(MAX_STORYBOARD_DIMENSION, th * 2 + gap * 3)
        board = Image.new("RGB", (board_w, board_h), (240, 240, 240))
        positions = [
            (gap, gap),
            (gap * 2 + tw, gap),
            (gap, gap * 2 + th),
            (gap * 2 + tw, gap * 2 + th),
        ]
        for idx, f in enumerate(flat_frames):
            board.paste(f.resize((tw, th), Image.Resampling.LANCZOS), positions[idx])
        layout_desc = "2x2 grid (reading order: top-left, top-right, bottom-left, bottom-right)"

    buf = io.BytesIO()
    board.save(buf, format="JPEG", quality=85)
    board_bytes = buf.getvalue()

    metadata = StoryboardMetadata(
        total_frames=total_frames if total_frames is not None else len(frame_metadata),
        duration_ms=total_duration_ms,
        sampled_frames=tuple(frame_metadata),
        layout=layout_desc,
    )
    return LlmInputImage(data=board_bytes, mime_type="image/jpeg"), metadata


def decode_and_sanitize_inbound_media(
    data: bytes,
    mime_type: str,
    *,
    deadline: float | None = None,
    check_cancelled: Callable[[], bool] | None = None,
) -> InboundMediaItem:
    """Sequentially inspect, bounds-check, and decode inbound media.

    For static images (including static GIF), strips metadata, converts static GIF to clean PNG,
    and returns a sanitized InboundMediaItem.
    For animated media (GIF / APNG), enforces finite bounds without unbounded n_frames,
    honors compositing, APNG default_image, samples <=4 frames based on PTS,
    and synthesizes a cleaned storyboard.
    """
    validate_image_bounds(data, mime_type, deadline=deadline, check_cancelled=check_cancelled)
    clean_mime = mime_type.split(";")[0].strip().lower()

    with Image.open(io.BytesIO(data)) as img:
        w, h = img.size
        is_apng = clean_mime == "image/png"
        default_image = bool(
            is_apng
            and (getattr(img, "default_image", False) or img.info.get("default_image", False))
        )

        is_animated = False
        try:
            img.seek(1)
            is_animated = True
        except EOFError:
            is_animated = False

        if not is_animated:
            # Static image: static GIF converted to clean PNG, PNG to PNG, JPEG to JPEG
            raster_mime: Literal["image/png", "image/jpeg"] = (
                "image/png" if clean_mime in ("image/png", "image/gif") else "image/jpeg"
            )
            raw_input = LlmInputImage(data=data, mime_type=raster_mime)
            sanitized = strip_static_image_exif(raw_input)
            return InboundMediaItem(
                raw_data=data,
                original_mime_type=clean_mime,
                raster_image=sanitized,
                is_animated=False,
                storyboard=None,
                width=w,
                height=h,
                frame_count=1,
                duration_ms=0,
            )

        # Animated image: GIF or APNG
        start_frame_offset = 1 if default_image else 0

        # First, sequentially collect animation frame durations and total animation frame count
        animation_durations: list[int] = []
        frame_idx = start_frame_offset
        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("Animation decode exceeded time budget.")
            if check_cancelled is not None and check_cancelled():
                raise asyncio.CancelledError("Animation decode cancelled.")

            if len(animation_durations) >= MAX_ANIMATED_FRAMES:
                break

            try:
                img.seek(frame_idx)
            except EOFError:
                break

            dur = img.info.get("duration")
            if dur is None or int(dur) <= 0:
                frame_dur = 100
            else:
                frame_dur = int(dur)
            animation_durations.append(frame_dur)
            frame_idx += 1

        animation_frame_count = len(animation_durations)
        if animation_frame_count < 1:
            raise MediaInvalidError("Animated image has no animation frames.")

        total_duration_ms = sum(animation_durations)
        sample_offsets = _sample_frames_by_pts(
            animation_durations, max_samples=MAX_STORYBOARD_FRAMES
        )
        target_physical_frames = set(start_frame_offset + offset for offset in sample_offsets)

        # Decode frames sequentially to preserve compositing / disposal
        decoded_frames: dict[int, Image.Image] = {}
        max_target_physical = max(target_physical_frames)

        for physical_idx in range(max_target_physical + 1):
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("Animation decode exceeded time budget.")
            if check_cancelled is not None and check_cancelled():
                raise asyncio.CancelledError("Animation decode cancelled.")

            img.seek(physical_idx)
            img.load()

            if physical_idx in target_physical_frames:
                decoded_frames[physical_idx] = img.convert("RGBA").copy()

        # Build sampled frames in sequential reading order
        pts_list = [0]
        for d in animation_durations[:-1]:
            pts_list.append(pts_list[-1] + d)

        sampled_images: list[Image.Image] = []
        sampled_metadata: list[StoryboardFrame] = []

        for offset in sample_offsets:
            physical_idx = start_frame_offset + offset
            frame_img = decoded_frames.get(physical_idx)
            if frame_img is None:
                raise MediaInvalidError(f"Failed to decode animation frame {physical_idx}.")

            pts_ms = pts_list[offset]
            duration_ms = animation_durations[offset]
            sampled_images.append(frame_img)
            sampled_metadata.append(
                StoryboardFrame(
                    frame_index=offset,
                    pts_ms=pts_ms,
                    duration_ms=duration_ms,
                )
            )

        raster_storyboard, storyboard_meta = synthesize_storyboard(
            sampled_images,
            sampled_metadata,
            total_duration_ms,
            total_frames=animation_frame_count,
        )

        return InboundMediaItem(
            raw_data=data,
            original_mime_type=clean_mime,
            raster_image=raster_storyboard,
            is_animated=True,
            storyboard=storyboard_meta,
            width=w,
            height=h,
            frame_count=animation_frame_count,
            duration_ms=total_duration_ms,
        )


async def async_decode_and_sanitize_inbound_media(
    data: bytes,
    mime_type: str,
    *,
    timeout_seconds: float = DEFAULT_DECODE_TIMEOUT_SECONDS,
) -> InboundMediaItem:
    """Asynchronously execute bounded decode and sanitization with cooperative cancellation."""
    pool = get_default_media_pool()
    return await pool.run_bounded(
        decode_and_sanitize_inbound_media,
        data,
        mime_type,
        timeout_seconds=timeout_seconds,
    )


def normalize_animated_sticker(
    data: bytes,
    mime_type: str,
    *,
    deadline: float | None = None,
    check_cancelled: Callable[[], bool] | None = None,
) -> tuple[bytes, Literal["image/gif", "image/png"], bool]:
    """Deterministically normalize an accepted sticker asset, stripping metadata.

    Preserves timing, loop intent (absent, zero, finite), and alpha transparency.
    Returns (normalized_bytes, mime_type, is_animated).
    """
    clean_mime = mime_type.split(";")[0].strip().lower()
    validate_image_bounds(data, clean_mime, deadline=deadline, check_cancelled=check_cancelled)

    with Image.open(io.BytesIO(data)) as img:
        w, h = img.size
        is_apng = clean_mime == "image/png"
        default_image = bool(
            is_apng
            and (getattr(img, "default_image", False) or img.info.get("default_image", False))
        )

        is_animated = False
        try:
            img.seek(1)
            is_animated = True
        except EOFError:
            is_animated = False

        # Static case: normalize to max 1024x1024 PNG
        if not is_animated:
            img.seek(0)
            img.load()
            converted = img.convert("RGBA")
            if converted.width > MAX_STICKER_DIMENSION or converted.height > MAX_STICKER_DIMENSION:
                converted.thumbnail(
                    (MAX_STICKER_DIMENSION, MAX_STICKER_DIMENSION), Image.Resampling.LANCZOS
                )
            clean = Image.new("RGBA", converted.size)
            clean.paste(converted)
            out = io.BytesIO()
            clean.save(out, format="PNG")
            out_bytes = out.getvalue()
            if len(out_bytes) > MAX_IMAGE_BYTES:
                raise MediaInvalidError("Normalized static sticker exceeds 5 MiB limit.")
            return out_bytes, "image/png", False

        # Animated GIF
        if clean_mime == "image/gif":
            frames: list[Image.Image] = []
            durations: list[int] = []
            disposals: list[int] = []
            raw_loop = img.info.get("loop")
            loop = int(raw_loop) if raw_loop is not None else None
            cumulative_pixels = 0
            total_duration_ms = 0
            has_any_transparency = False

            idx = 0
            while True:
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Sticker normalization exceeded time budget.")
                if check_cancelled is not None and check_cancelled():
                    raise asyncio.CancelledError("Sticker normalization cancelled.")

                if len(frames) >= MAX_ANIMATED_FRAMES:
                    break

                try:
                    img.seek(idx)
                except EOFError:
                    break

                img.load()
                cumulative_pixels += w * h
                if cumulative_pixels > MAX_CUMULATIVE_DECODED_CANVAS_PIXELS:
                    raise MediaInvalidError("Cumulative decoded pixels exceed limit.")

                dur = img.info.get("duration")
                if dur is None or int(dur) <= 0:
                    dur_val = 100
                else:
                    dur_val = int(dur)

                durations.append(dur_val)
                total_duration_ms += dur_val
                if total_duration_ms > MAX_ANIMATION_DURATION_MS:
                    raise MediaInvalidError(
                        f"Animation duration exceeds {MAX_ANIMATION_DURATION_MS}ms limit."
                    )

                # Frames are already composited full canvases. Clear before the
                # next canvas so transparent pixels cannot expose stale content.
                disposals.append(2)

                frame_rgba = img.convert("RGBA")
                if (
                    frame_rgba.width > MAX_STICKER_DIMENSION
                    or frame_rgba.height > MAX_STICKER_DIMENSION
                ):
                    frame_rgba.thumbnail(
                        (MAX_STICKER_DIMENSION, MAX_STICKER_DIMENSION), Image.Resampling.LANCZOS
                    )

                alpha = frame_rgba.getchannel("A")
                if _has_transparent_pixels(alpha):
                    has_any_transparency = True
                    rgb = frame_rgba.convert("RGB")
                    palette_frame = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
                    mask = Image.eval(alpha, lambda a: 255 if a < 128 else 0)
                    palette_frame.paste(255, mask)
                    palette_frame.info["transparency"] = 255
                    frames.append(palette_frame)
                else:
                    rgb = frame_rgba.convert("RGB")
                    palette_frame = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
                    frames.append(palette_frame)

                idx += 1

            if not frames:
                raise MediaInvalidError("No frames extracted from animated GIF.")

            out = io.BytesIO()
            save_kwargs: dict[str, Any] = {
                "format": "GIF",
                "save_all": True,
                "append_images": frames[1:],
                "duration": durations,
                "disposal": disposals,
            }
            if loop is not None:
                save_kwargs["loop"] = loop
            if has_any_transparency:
                save_kwargs["transparency"] = 255

            frames[0].save(out, **save_kwargs)
            out_bytes = out.getvalue()
            if len(out_bytes) > MAX_IMAGE_BYTES:
                raise MediaInvalidError("Normalized GIF sticker exceeds 5 MiB limit.")
            return out_bytes, "image/gif", True

        # Animated APNG (image/png)
        if clean_mime == "image/png":
            start_frame = 1 if default_image else 0
            frames_apng: list[Image.Image] = []
            durations_apng: list[int] = []
            raw_loop = img.info.get("loop")
            loop = int(raw_loop) if raw_loop is not None else None
            cumulative_pixels = 0
            total_duration_ms = 0

            idx = start_frame
            while True:
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Sticker normalization exceeded time budget.")
                if check_cancelled is not None and check_cancelled():
                    raise asyncio.CancelledError("Sticker normalization cancelled.")

                if len(frames_apng) >= MAX_ANIMATED_FRAMES:
                    break

                try:
                    img.seek(idx)
                except EOFError:
                    break

                img.load()
                cumulative_pixels += w * h
                if cumulative_pixels > MAX_CUMULATIVE_DECODED_CANVAS_PIXELS:
                    raise MediaInvalidError("Cumulative decoded pixels exceed limit.")

                dur = img.info.get("duration")
                if dur is None or int(dur) <= 0:
                    dur_val = 100
                else:
                    dur_val = int(dur)

                durations_apng.append(dur_val)
                total_duration_ms += dur_val
                if total_duration_ms > MAX_ANIMATION_DURATION_MS:
                    raise MediaInvalidError(
                        f"Animation duration exceeds {MAX_ANIMATION_DURATION_MS}ms limit."
                    )

                frame_rgba = img.convert("RGBA")
                if (
                    frame_rgba.width > MAX_STICKER_DIMENSION
                    or frame_rgba.height > MAX_STICKER_DIMENSION
                ):
                    frame_rgba.thumbnail(
                        (MAX_STICKER_DIMENSION, MAX_STICKER_DIMENSION), Image.Resampling.LANCZOS
                    )

                clean_frame = Image.new("RGBA", frame_rgba.size)
                clean_frame.paste(frame_rgba)
                frames_apng.append(clean_frame)
                idx += 1

            if not frames_apng:
                raise MediaInvalidError("No frames extracted from animated APNG.")

            out = io.BytesIO()
            save_kwargs_apng: dict[str, Any] = {
                "format": "PNG",
                "save_all": True,
                "append_images": frames_apng[1:],
                "duration": durations_apng,
            }
            if loop is not None:
                save_kwargs_apng["loop"] = loop

            frames_apng[0].save(out, **save_kwargs_apng)
            out_bytes = out.getvalue()
            if len(out_bytes) > MAX_IMAGE_BYTES:
                raise MediaInvalidError("Normalized APNG sticker exceeds 5 MiB limit.")
            return out_bytes, "image/png", True

        raise MediaInvalidError(f"Unsupported format for sticker normalization: {clean_mime}")


async def async_normalize_animated_sticker(
    data: bytes,
    mime_type: str,
    *,
    timeout_seconds: float = DEFAULT_DECODE_TIMEOUT_SECONDS,
) -> tuple[bytes, Literal["image/gif", "image/png"], bool]:
    """Asynchronously normalize an animated sticker within the bounded execution pool."""
    pool = get_default_media_pool()
    return await pool.run_bounded(
        normalize_animated_sticker,
        data,
        mime_type,
        timeout_seconds=timeout_seconds,
    )


def extract_static_poster(
    data: bytes,
    mime_type: str,
    *,
    deadline: float | None = None,
    check_cancelled: Callable[[], bool] | None = None,
) -> bytes:
    """Extract Frame 0 (or first animated frame) as a clean static PNG poster."""
    clean_mime = mime_type.split(";")[0].strip().lower()
    validate_image_bounds(data, clean_mime, deadline=deadline, check_cancelled=check_cancelled)

    with Image.open(io.BytesIO(data)) as img:
        # For APNG with default_image, frame 0 is already the poster!
        img.seek(0)
        img.load()
        converted = img.convert("RGBA")
        clean = Image.new("RGBA", converted.size)
        clean.paste(converted)
        out = io.BytesIO()
        clean.save(out, format="PNG")
        return out.getvalue()


async def async_extract_static_poster(
    data: bytes,
    mime_type: str,
    *,
    timeout_seconds: float = DEFAULT_DECODE_TIMEOUT_SECONDS,
) -> bytes:
    """Asynchronously extract static poster within the bounded execution pool."""
    pool = get_default_media_pool()
    return await pool.run_bounded(
        extract_static_poster,
        data,
        mime_type,
        timeout_seconds=timeout_seconds,
    )
