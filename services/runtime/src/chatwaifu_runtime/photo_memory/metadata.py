"""Bounded extraction of allowlisted photo source metadata and EXIF stripping."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from PIL import ExifTags, Image, ImageOps

from chatwaifu_runtime.providers.contracts import LlmInputImage

logger = logging.getLogger(__name__)

_DATE_TIME_RE = re.compile(r"^(\d{4})[:\-](\d{2})[:\-](\d{2})[T ](\d{2}):(\d{2}):(\d{2})$")
_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


@dataclass(frozen=True, slots=True)
class PhotoSourceMetadata:
    """Allowlist of basic source metadata extracted from inbound original bytes."""

    captured_at: str | None = None
    captured_at_offset: str | None = None
    original_width: int | None = None
    original_height: int | None = None
    original_mime_type: Literal["image/png", "image/jpeg"] | None = None


def extract_photo_metadata(
    data: bytes,
    fallback_mime: str | None = None,
) -> PhotoSourceMetadata:
    """Extract allowlisted source metadata from original image bytes before normalization.

    Allowlist:
    - EXIF DateTimeOriginal with optional OffsetTimeOriginal (preserves unknown timezone explicitly)
    - Original dimensions, taking EXIF orientation transposition into account
    - Original MIME type (image/png or image/jpeg)

    Strict Exclusions:
    - No GPS information
    - No camera/lens serial numbers
    - No arbitrary EXIF dump
    - No file paths

    Fault Tolerance:
    - Malformed or missing EXIF safely yields unknown/None fields without raising.
    """
    if not data or len(data) > 5 * 1024 * 1024:
        return PhotoSourceMetadata()

    try:
        with Image.open(io.BytesIO(data)) as img:
            # 1. Format & original MIME type
            orig_mime: Literal["image/png", "image/jpeg"] | None = None
            if img.format == "PNG":
                orig_mime = "image/png"
            elif img.format == "JPEG":
                orig_mime = "image/jpeg"
            elif fallback_mime in ("image/png", "image/jpeg"):
                orig_mime = fallback_mime  # type: ignore[assignment]

            # 2. Dimensions & EXIF Orientation
            raw_w, raw_h = img.size
            orig_w: int | None = None
            orig_h: int | None = None
            exif: Image.Exif | None = None

            try:
                exif = img.getexif()
            except Exception:
                exif = None

            orientation: int | None = None
            if exif:
                try:
                    raw_orientation = exif.get(ExifTags.Base.Orientation)
                    if isinstance(raw_orientation, int) and 1 <= raw_orientation <= 8:
                        orientation = raw_orientation
                except Exception:
                    orientation = None

            # Orientation tags 5, 6, 7, 8 swap width and height in visual display
            if orientation in (5, 6, 7, 8):
                orig_w, orig_h = raw_h, raw_w
            else:
                orig_w, orig_h = raw_w, raw_h

            if orig_w <= 0 or orig_w > 65536 or orig_h <= 0 or orig_h > 65536:
                orig_w, orig_h = None, None

            # 3. Allowlisted EXIF DateTimeOriginal & OffsetTimeOriginal
            captured_at: str | None = None
            captured_at_offset: str | None = None

            if exif:
                captured_at, captured_at_offset = _extract_capture_datetime(exif)

            return PhotoSourceMetadata(
                captured_at=captured_at,
                captured_at_offset=captured_at_offset,
                original_width=orig_w,
                original_height=orig_h,
                original_mime_type=orig_mime,
            )
    except Exception as exc:
        logger.debug("bounded photo metadata extraction safely skipped: %s", exc)
        return PhotoSourceMetadata()


def _extract_capture_datetime(exif: Image.Exif) -> tuple[str | None, str | None]:
    """Extract and validate DateTimeOriginal and OffsetTimeOriginal from EXIF."""
    try:
        sub_ifd: dict[int, object] = {}
        try:
            sub_ifd = dict(exif.get_ifd(ExifTags.IFD.Exif))
        except Exception:
            sub_ifd = {}

        # 36867 = DateTimeOriginal
        raw_dt = sub_ifd.get(ExifTags.Base.DateTimeOriginal) or exif.get(
            ExifTags.Base.DateTimeOriginal
        )
        if not raw_dt:
            return None, None

        if isinstance(raw_dt, bytes):
            try:
                dt_str = raw_dt.decode("utf-8", errors="replace").strip("\x00 \t\r\n")
            except Exception:
                return None, None
        elif isinstance(raw_dt, str):
            dt_str = raw_dt.strip("\x00 \t\r\n")
        else:
            return None, None

        match = _DATE_TIME_RE.match(dt_str)
        if not match:
            return None, None

        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        hour = int(match.group(4))
        minute = int(match.group(5))
        second = int(match.group(6))

        try:
            # Validates calendar day (e.g. rejects Feb 30 or month 13)
            datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None, None

        iso_base = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"

        # 36881 = OffsetTimeOriginal, 36880 = OffsetTime
        raw_offset = sub_ifd.get(ExifTags.Base.OffsetTimeOriginal) or exif.get(
            ExifTags.Base.OffsetTimeOriginal
        )

        offset_str: str | None = None
        if raw_offset:
            if isinstance(raw_offset, bytes):
                try:
                    cleaned_offset = raw_offset.decode("utf-8", errors="replace").strip(
                        "\x00 \t\r\n"
                    )
                except Exception:
                    cleaned_offset = ""
            elif isinstance(raw_offset, str):
                cleaned_offset = raw_offset.strip("\x00 \t\r\n")
            else:
                cleaned_offset = ""

            offset_match = _OFFSET_RE.match(cleaned_offset)
            if offset_match:
                sign = offset_match.group(1)
                off_h = int(offset_match.group(2))
                off_m = int(offset_match.group(3))
                if 0 <= off_h <= 14 and 0 <= off_m <= 59 and (off_h < 14 or off_m == 0):
                    offset_str = f"{sign}{off_h:02d}:{off_m:02d}"
            elif cleaned_offset.upper() in ("Z", "UTC"):
                offset_str = "+00:00"

        if offset_str is not None:
            # Timezone is known
            return f"{iso_base}{offset_str}", offset_str

        # Timezone is unknown: explicitly preserve naive ISO without assuming host timezone
        return iso_base, None
    except Exception:
        return None, None


def strip_image_exif(image: LlmInputImage) -> LlmInputImage:
    """Strip EXIF and private metadata so raw EXIF is never sent to vision providers.

    Images without EXIF are unchanged. Unreadable metadata is discarded by
    re-encoding pixels; unreadable images fail rather than forwarding raw metadata.
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
        raise ValueError("cannot sanitize inbound image") from exc
