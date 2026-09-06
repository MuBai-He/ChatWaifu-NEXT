"""Central bounded media helper for image inspection, decoding, bounds enforcement,
temporal storyboard synthesis, and sticker normalization.
"""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Callable, Sequence
from typing import Literal

from PIL import Image, ImageOps

from chatwaifu_runtime.media.contracts import (
    InboundMediaItem,
    StoryboardFrame,
    StoryboardMetadata,
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
DEFAULT_DECODE_TIMEOUT_SECONDS = 5.0

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


def validate_image_bounds(data: bytes, mime_type: str) -> None:
    """Validate format, size constraints, and pixel bounds for static and animated media."""
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

            n_frames = getattr(img, "n_frames", 1)
            is_animated = bool(getattr(img, "is_animated", False) or n_frames > 1)

            if is_animated:
                if w > MAX_ANIMATED_DIMENSION or h > MAX_ANIMATED_DIMENSION:
                    raise MediaInvalidError(
                        f"Animated image dimensions {w}x{h} exceed {MAX_ANIMATED_DIMENSION} limit."
                    )
                if n_frames > MAX_ANIMATED_FRAMES:
                    raise MediaInvalidError(
                        f"Animated frame count {n_frames} exceeds {MAX_ANIMATED_FRAMES} limit."
                    )
                if (w * h * n_frames) > MAX_CUMULATIVE_DECODED_CANVAS_PIXELS:
                    raise MediaInvalidError(
                        "Animated image potential decoded pixels exceed 32 million limit."
                    )
            else:
                if (
                    w > MAX_STATIC_DIMENSION
                    or h > MAX_STATIC_DIMENSION
                    or (w * h) > MAX_STATIC_PIXELS
                ):
                    raise MediaInvalidError(
                        f"Static image dimensions {w}x{h} exceed allowed bounds."
                    )

            img.verify()

        # Extra check: load first frame to ensure decode integrity
        with Image.open(io.BytesIO(data)) as decoded:
            decoded.load()
    except MediaInvalidError:
        raise
    except Exception as exc:
        raise MediaInvalidError(f"Image decoding verification failed: {exc}") from None


def strip_static_image_exif(image: LlmInputImage) -> LlmInputImage:
    """Remove EXIF and container metadata from a single static raster image.

    Images without EXIF are preserved unchanged.
    """
    if not image.data:
        return image

    try:
        with Image.open(io.BytesIO(image.data)) as img:
            try:
                exif = img.getexif()
            except Exception:
                exif = None
            if exif is not None and not exif:
                return image
            try:
                transposed = ImageOps.exif_transpose(img)
            except Exception:
                transposed = img.copy()
            if transposed.mode in ("RGBA", "P"):
                converted = transposed.convert("RGBA")
                fmt = "PNG"
                mime: Literal["image/png", "image/jpeg"] = "image/png"
            else:
                converted = transposed.convert("RGB")
                fmt = "JPEG"
                mime = "image/jpeg"

            clean = Image.new(converted.mode, converted.size)
            clean.paste(converted)

            output = io.BytesIO()
            clean.save(output, format=fmt)
            return LlmInputImage(data=output.getvalue(), mime_type=mime)
    except Exception as exc:
        raise MediaInvalidError("cannot sanitize inbound image") from exc


def _sample_frame_indices(total_frames: int, max_samples: int = MAX_STORYBOARD_FRAMES) -> list[int]:
    """Uniformly sample up to max_samples frame indices across total_frames."""
    if total_frames <= max_samples:
        return list(range(total_frames))
    if max_samples == 1:
        return [0]
    # For max_samples == 4: sample start (0), two intermediate points, and end (total_frames - 1)
    step = (total_frames - 1) / (max_samples - 1)
    indices = [round(i * step) for i in range(max_samples)]
    # Deduplicate while preserving order
    seen: set[int] = set()
    result: list[int] = []
    for idx in indices:
        clamped = max(0, min(total_frames - 1, idx))
        if clamped not in seen:
            seen.add(clamped)
            result.append(clamped)
    return result


def synthesize_storyboard(
    frames: Sequence[Image.Image],
    frame_metadata: Sequence[StoryboardFrame],
    total_duration_ms: int,
) -> tuple[LlmInputImage, StoryboardMetadata]:
    """Composite up to 4 frames into a clean, metadata-free storyboard grid (max 1024x1024)."""
    if not frames:
        raise MediaInvalidError("No frames provided for storyboard synthesis.")

    count = len(frames)
    if count > MAX_STORYBOARD_FRAMES:
        frames = frames[:MAX_STORYBOARD_FRAMES]
        frame_metadata = frame_metadata[:MAX_STORYBOARD_FRAMES]
        count = len(frames)

    orig_w, orig_h = frames[0].size
    gap = 4

    if count == 1:
        max_dim = MAX_STORYBOARD_DIMENSION
        scale = min(max_dim / max(orig_w, 1), max_dim / max(orig_h, 1), 1.0)
        out_w = max(1, int(orig_w * scale))
        out_h = max(1, int(orig_h * scale))
        board = frames[0].resize((out_w, out_h), Image.Resampling.LANCZOS).convert("RGB")
        layout_desc = "single frame"
    elif count == 2:
        max_cell = (MAX_STORYBOARD_DIMENSION - gap * 3) // 2
        scale = min(
            max_cell / max(orig_w, 1), (MAX_STORYBOARD_DIMENSION - gap * 2) / max(orig_h, 1), 1.0
        )
        tw = max(1, int(orig_w * scale))
        th = max(1, int(orig_h * scale))
        board = Image.new("RGB", (tw * 2 + gap * 3, th + gap * 2), (240, 240, 240))
        board.paste(frames[0].resize((tw, th), Image.Resampling.LANCZOS).convert("RGB"), (gap, gap))
        board.paste(
            frames[1].resize((tw, th), Image.Resampling.LANCZOS).convert("RGB"), (gap * 2 + tw, gap)
        )
        layout_desc = "1x2 horizontal sequence (reading order: left, right)"
    else:
        max_cell = (MAX_STORYBOARD_DIMENSION - gap * 3) // 2
        scale = min(max_cell / max(orig_w, 1), max_cell / max(orig_h, 1), 1.0)
        tw = max(1, int(orig_w * scale))
        th = max(1, int(orig_h * scale))
        board = Image.new("RGB", (tw * 2 + gap * 3, th * 2 + gap * 3), (240, 240, 240))
        positions = [
            (gap, gap),
            (gap * 2 + tw, gap),
            (gap, gap * 2 + th),
            (gap * 2 + tw, gap * 2 + th),
        ]
        for idx, frame in enumerate(frames):
            board.paste(
                frame.resize((tw, th), Image.Resampling.LANCZOS).convert("RGB"), positions[idx]
            )
        layout_desc = "2x2 grid (reading order: top-left, top-right, bottom-left, bottom-right)"

    buf = io.BytesIO()
    board.save(buf, format="JPEG", quality=85)
    board_bytes = buf.getvalue()

    metadata = StoryboardMetadata(
        total_frames=len(frame_metadata),
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

    For static images, strips EXIF and returns a sanitized InboundMediaItem.
    For animated media (GIF / APNG), enforces finite bounds, honors compositing,
    APNG default_image, samples <=4 frames, and synthesizes a cleaned storyboard.
    """
    validate_image_bounds(data, mime_type)
    clean_mime = mime_type.split(";")[0].strip().lower()

    with Image.open(io.BytesIO(data)) as img:
        w, h = img.size
        n_frames = getattr(img, "n_frames", 1)
        is_animated = bool(getattr(img, "is_animated", False) or n_frames > 1)

        if not is_animated:
            raster_mime: Literal["image/png", "image/jpeg"] = (
                "image/png" if clean_mime == "image/png" else "image/jpeg"
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
        is_apng = clean_mime == "image/png"
        default_image = bool(
            is_apng
            and (getattr(img, "default_image", False) or img.info.get("default_image", False))
        )

        if default_image:
            # Frame 0 is the fallback static image; animation frames are 1 .. n_frames - 1
            if n_frames <= 1:
                raise MediaInvalidError("APNG with default_image has no animation frames.")
            animation_frame_count = n_frames - 1
            start_frame_offset = 1
        else:
            animation_frame_count = n_frames
            start_frame_offset = 0

        sample_offsets = _sample_frame_indices(
            animation_frame_count, max_samples=MAX_STORYBOARD_FRAMES
        )
        target_physical_frames = set(start_frame_offset + offset for offset in sample_offsets)

        # Sequential decode honoring compositing and time budget
        decoded_frames: dict[int, Image.Image] = {}
        frame_durations: list[int] = []
        cumulative_pixels = 0
        total_duration_ms = 0

        try:
            for physical_idx in range(n_frames):
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Animation decode exceeded time budget.")
                if check_cancelled is not None and check_cancelled():
                    raise asyncio.CancelledError("Animation decode cancelled.")

                img.seek(physical_idx)

                # Duration for this frame
                frame_dur = int(img.info.get("duration", 100) or 100)
                if frame_dur <= 0:
                    frame_dur = 100
                if physical_idx >= start_frame_offset:
                    frame_durations.append(frame_dur)
                    total_duration_ms += frame_dur
                    if total_duration_ms > MAX_ANIMATION_DURATION_MS:
                        raise MediaInvalidError(
                            f"Animation duration exceeds {MAX_ANIMATION_DURATION_MS}ms limit."
                        )

                cumulative_pixels += w * h
                if cumulative_pixels > MAX_CUMULATIVE_DECODED_CANVAS_PIXELS:
                    raise MediaInvalidError("Cumulative decoded pixels exceed 32 million limit.")

                if physical_idx in target_physical_frames:
                    # Capture composited frame
                    img.load()
                    decoded_frames[physical_idx] = img.convert("RGBA").copy()

                if len(decoded_frames) == len(target_physical_frames) and physical_idx >= (
                    start_frame_offset + animation_frame_count - 1
                ):
                    break
        except (EOFError, SyntaxError) as exc:
            raise MediaInvalidError(f"Malformed or truncated animation: {exc}") from None

        # Build sampled frames list in sequential reading order
        sampled_images: list[Image.Image] = []
        sampled_metadata: list[StoryboardFrame] = []

        for offset in sample_offsets:
            physical_idx = start_frame_offset + offset
            frame_img = decoded_frames.get(physical_idx)
            if frame_img is None:
                raise MediaInvalidError(f"Failed to decode animation frame {physical_idx}.")

            # Calculate PTS (presentation timestamp) from animation start
            pts_ms = sum(frame_durations[:offset])
            duration_ms = frame_durations[offset] if offset < len(frame_durations) else 100
            sampled_images.append(frame_img)
            sampled_metadata.append(
                StoryboardFrame(
                    frame_index=offset,
                    pts_ms=pts_ms,
                    duration_ms=duration_ms,
                )
            )

        raster_storyboard, storyboard_meta = synthesize_storyboard(
            sampled_images, sampled_metadata, total_duration_ms
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
    cancelled = False
    deadline = time.monotonic() + timeout_seconds

    def _worker() -> InboundMediaItem:
        return decode_and_sanitize_inbound_media(
            data,
            mime_type,
            deadline=deadline,
            check_cancelled=lambda: cancelled,
        )

    try:
        return await asyncio.to_thread(_worker)
    except asyncio.CancelledError:
        cancelled = True
        raise


def normalize_animated_sticker(
    data: bytes,
    mime_type: str,
    *,
    deadline: float | None = None,
    check_cancelled: Callable[[], bool] | None = None,
) -> tuple[bytes, Literal["image/gif", "image/png"], bool]:
    """Deterministically normalize an accepted sticker asset, stripping metadata.

    Preserves timing, loops, and alpha transparency.
    Returns (normalized_bytes, mime_type, is_animated).
    """
    clean_mime = mime_type.split(";")[0].strip().lower()
    validate_image_bounds(data, clean_mime)

    with Image.open(io.BytesIO(data)) as img:
        w, h = img.size
        n_frames = getattr(img, "n_frames", 1)
        is_animated = bool(getattr(img, "is_animated", False) or n_frames > 1)

        # Static case: normalize to 1024x1024 PNG
        if not is_animated:
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
            loop = int(img.info.get("loop", 0) or 0)
            cumulative_pixels = 0

            for idx in range(min(n_frames, MAX_ANIMATED_FRAMES)):
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Sticker normalization exceeded time budget.")
                if check_cancelled is not None and check_cancelled():
                    raise asyncio.CancelledError("Sticker normalization cancelled.")

                img.seek(idx)
                img.load()
                cumulative_pixels += w * h
                if cumulative_pixels > MAX_CUMULATIVE_DECODED_CANVAS_PIXELS:
                    raise MediaInvalidError("Cumulative decoded pixels exceed limit.")

                dur = int(img.info.get("duration", 100) or 100)
                durations.append(max(20, dur))

                frame_rgba = img.convert("RGBA")
                if (
                    frame_rgba.width > MAX_STICKER_DIMENSION
                    or frame_rgba.height > MAX_STICKER_DIMENSION
                ):
                    frame_rgba.thumbnail(
                        (MAX_STICKER_DIMENSION, MAX_STICKER_DIMENSION), Image.Resampling.LANCZOS
                    )

                # Convert to palette mode preserving alpha
                clean_frame = Image.new("RGBA", frame_rgba.size)
                clean_frame.paste(frame_rgba)
                palette_frame = clean_frame.convert("P", palette=Image.Palette.ADAPTIVE)
                frames.append(palette_frame)

            if not frames:
                raise MediaInvalidError("No frames extracted from animated GIF.")

            out = io.BytesIO()
            frames[0].save(
                out,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=loop,
                disposal=2,
            )
            out_bytes = out.getvalue()
            if len(out_bytes) > MAX_IMAGE_BYTES:
                raise MediaInvalidError("Normalized GIF sticker exceeds 5 MiB limit.")
            return out_bytes, "image/gif", True

        # Animated APNG (image/png)
        if clean_mime == "image/png":
            default_image = bool(
                getattr(img, "default_image", False) or img.info.get("default_image", False)
            )
            start_frame = 1 if default_image else 0
            frames_apng: list[Image.Image] = []
            durations_apng: list[int] = []
            loop = int(img.info.get("loop", 0) or 0)
            cumulative_pixels = 0

            for idx in range(start_frame, min(n_frames, MAX_ANIMATED_FRAMES + start_frame)):
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("Sticker normalization exceeded time budget.")
                if check_cancelled is not None and check_cancelled():
                    raise asyncio.CancelledError("Sticker normalization cancelled.")

                img.seek(idx)
                img.load()
                cumulative_pixels += w * h
                if cumulative_pixels > MAX_CUMULATIVE_DECODED_CANVAS_PIXELS:
                    raise MediaInvalidError("Cumulative decoded pixels exceed limit.")

                dur = int(img.info.get("duration", 100) or 100)
                durations_apng.append(max(20, dur))

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

            if not frames_apng:
                raise MediaInvalidError("No frames extracted from animated APNG.")

            out = io.BytesIO()
            frames_apng[0].save(
                out,
                format="PNG",
                save_all=True,
                append_images=frames_apng[1:],
                duration=durations_apng,
                loop=loop,
            )
            out_bytes = out.getvalue()
            if len(out_bytes) > MAX_IMAGE_BYTES:
                raise MediaInvalidError("Normalized APNG sticker exceeds 5 MiB limit.")
            return out_bytes, "image/png", True

        raise MediaInvalidError(f"Unsupported format for sticker normalization: {clean_mime}")


def extract_static_poster(data: bytes, mime_type: str) -> bytes:
    """Extract Frame 0 (or first animated frame) as a clean static PNG poster."""
    clean_mime = mime_type.split(";")[0].strip().lower()
    validate_image_bounds(data, clean_mime)

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
